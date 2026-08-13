"""Verzamelt MeshCore-repeaterdata uit de HA-state-machine en pusht die naar de site."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    COMMAND_POLL_INTERVAL,
    DEBOUNCE_SECONDS,
    FULL_PUSH_INTERVAL,
    KNOWN_METRICS,
    RE_CONTACT,
    RE_ENTITY,
    RE_NAME,
    RE_NEIGHBOR,
    RE_NEIGHBOR_NAME,
    RE_NEIGHBOR_SEEN,
    REFRESH_PUSH_DELAY,
    SETTINGS_LOGIN_WAIT,
    SETTINGS_PARAM_CAP,
    SETTINGS_QUIET_GAP,
    SETTINGS_RESPONSE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def discover_repeaters(hass: HomeAssistant) -> dict[str, str]:
    """Alle meshcore-prefixen in de state-machine -> weergavenaam."""
    found: dict[str, str] = {}
    for state in hass.states.async_all(("sensor", "binary_sensor")):
        m = RE_ENTITY.match(state.entity_id)
        if not m:
            continue
        prefix = m.group(1)
        name = found.get(prefix, "")
        if not name:
            friendly = state.attributes.get("friendly_name") or ""
            nm = RE_NAME.search(friendly)
            found[prefix] = nm.group(1) if nm else prefix
    return found


def collect_contacts(hass: HomeAssistant) -> list[dict]:
    """Locaties van alle bekende contacts (uit de advert-data van meshcore)."""
    out: list[dict] = []
    for state in hass.states.async_all("binary_sensor"):
        m = RE_CONTACT.match(state.entity_id)
        if not m:
            continue
        a = state.attributes
        lat = a.get("adv_lat") or a.get("latitude")
        lon = a.get("adv_lon") or a.get("longitude")
        if not lat or not lon:
            continue  # geen (bruikbare) locatie geadverteerd
        out.append({
            "prefix": a.get("pubkey_prefix", m.group(1)),
            "name": a.get("adv_name"),
            "lat": lat,
            "lon": lon,
            "type": a.get("node_type_str"),
        })
    return out


def discover_repeater_prefixes(hass: HomeAssistant) -> set[str]:
    """Alleen échte repeaters: entiteiten met een 'MeshCore Repeater:'-naam."""
    out: set[str] = set()
    for state in hass.states.async_all(("sensor", "binary_sensor")):
        m = RE_ENTITY.match(state.entity_id)
        if not m or m.group(1) in out:
            continue
        friendly = state.attributes.get("friendly_name") or ""
        if RE_NAME.search(friendly):
            out.add(m.group(1))
    return out


def extract_metric(rest: str) -> str | None:
    """Metricnaam uit het entity-id-deel na de prefix (knipt de nodenaam-suffix af)."""
    for metric in KNOWN_METRICS:
        if rest == metric or rest.startswith(metric + "_"):
            return metric
    return None


class Pusher:
    """Luistert naar state-wijzigingen en pusht (met debounce) snapshots per repeater."""

    def __init__(self, hass: HomeAssistant, base_url: str, token: str, prefixes: list[str],
                 entry=None, auto_add: bool = False, passwords: dict[str, str] | None = None) -> None:
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.prefixes = set(prefixes)
        self._entry = entry
        self._auto_add = auto_add
        self._passwords = passwords or {}
        self._session = async_get_clientsession(hass)
        self._unsub: list = []
        self._debounce: dict[str, Any] = {}
        # settings-opvragingen serialiseren: één tegelijk over de LoRa-ether
        self._settings_lock = asyncio.Lock()

    async def async_start(self) -> None:
        self._unsub.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))
        self._unsub.append(
            async_track_time_interval(
                self.hass, self._interval_push, timedelta(seconds=FULL_PUSH_INTERVAL)
            )
        )
        self._unsub.append(
            async_track_time_interval(
                self.hass, self._poll_commands, timedelta(seconds=COMMAND_POLL_INTERVAL)
            )
        )
        await self.push_contacts()
        await self.push_all()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        for cancel in self._debounce.values():
            cancel()
        self._debounce.clear()

    @callback
    def _on_state_changed(self, event: Event) -> None:
        m = RE_ENTITY.match(event.data.get("entity_id", ""))
        if not m or m.group(1) not in self.prefixes:
            return
        prefix = m.group(1)
        if prefix in self._debounce:
            return  # er staat al een push gepland
        self._debounce[prefix] = async_call_later(
            self.hass, DEBOUNCE_SECONDS, self._make_debounced(prefix)
        )

    def _make_debounced(self, prefix: str):
        async def _run(_now) -> None:
            self._debounce.pop(prefix, None)
            await self.push_repeater(prefix)
        return _run

    async def push_contacts(self) -> None:
        """Contactlocaties naar de site (voor de linkkaart)."""
        contacts = collect_contacts(self.hass)
        if not contacts:
            return
        try:
            await self._session.post(
                f"{self.base_url}/api/v1/contacts",
                json={"contacts": contacts},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Contact-push naar %s mislukt: %s", self.base_url, err)

    async def _interval_push(self, _now) -> None:
        await self.push_contacts()
        if self._auto_add and self._entry is not None:
            new = discover_repeater_prefixes(self.hass) - self.prefixes
            if new:
                _LOGGER.info("Nieuwe repeaters ontdekt, toevoegen aan sync: %s", ", ".join(sorted(new)))
                options = dict(self._entry.options)
                options["repeaters"] = sorted(self.prefixes | new)
                # update triggert een reload; de nieuwe pusher pusht meteen alles
                self.hass.config_entries.async_update_entry(self._entry, options=options)
                return
        await self.push_all()

    async def push_all(self) -> None:
        for prefix in self.prefixes:
            await self.push_repeater(prefix)

    def _snapshot(self, prefix: str) -> dict | None:
        metrics: dict[str, Any] = {}
        neighbors: dict[str, dict] = {}
        name = prefix
        for state in self.hass.states.async_all(("sensor", "binary_sensor")):
            m = RE_ENTITY.match(state.entity_id)
            if not m or m.group(1) != prefix:
                continue
            friendly = state.attributes.get("friendly_name") or ""
            nm = RE_NAME.search(friendly)
            if nm:
                name = nm.group(1)
            if state.state in ("unknown", "unavailable", ""):
                continue
            rest = m.group(2)
            nbs = RE_NEIGHBOR_SEEN.match(rest)
            if nbs:
                try:
                    seen = float(state.state)  # minuten sinds laatst gehoord
                except ValueError:
                    continue
                neighbors.setdefault(nbs.group(1), {"prefix": nbs.group(1)})["seen_min"] = seen
                continue
            nb = RE_NEIGHBOR.match(rest)
            if nb:
                try:
                    snr = float(state.state)
                except ValueError:
                    continue
                entry = neighbors.setdefault(nb.group(1), {"prefix": nb.group(1)})
                entry["snr"] = snr
                nn = RE_NEIGHBOR_NAME.search(friendly)
                if nn:
                    entry["name"] = nn.group(1)
                continue
            metric = extract_metric(rest)
            if metric is None:
                continue
            if state.entity_id.startswith("binary_sensor."):
                # 'contact' meldt fresh/stale; alles anders dan on/fresh is offline
                metrics[metric] = state.state in ("on", "fresh")
            else:
                try:
                    metrics[metric] = float(state.state)
                except ValueError:
                    metrics[metric] = state.state
        if not metrics:
            return None
        return {
            "repeater": {"pubkey_prefix": prefix, "name": name},
            "metrics": metrics,
            "neighbors": list(neighbors.values()),
        }

    async def _poll_commands(self, _now) -> None:
        """Handmatige statusverzoeken van de site ophalen en uitvoeren."""
        try:
            resp = await self._session.get(
                f"{self.base_url}/api/v1/commands",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=15,
            )
            if resp.status != 200:
                return
            data = await resp.json()
        except Exception:  # noqa: BLE001 - stil falen, volgende poll probeert opnieuw
            return
        for prefix in data.get("refresh", []):
            if prefix in self.prefixes:
                await self._request_status(prefix)
        for req in data.get("settings", []):
            prefix = req.get("prefix")
            params = [str(p)[:64] for p in (req.get("params") or [])][:40]
            if prefix in self.prefixes and params:
                self.hass.async_create_task(self._fetch_settings(prefix, params))

    async def _request_status(self, prefix: str) -> None:
        """Vraag via de meshcore-integratie een verse status + telemetrie op
        en push even later een geforceerd datapunt met het antwoord."""
        short = prefix[:6]
        for command in (f"send_statusreq {short}", f"send_telemetry_req {short}"):
            try:
                await self.hass.services.async_call(
                    "meshcore", "execute_command", {"command": command}, blocking=False
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("meshcore.execute_command '%s' mislukt: %s", command, err)
        _LOGGER.info("Statusverzoek voor %s verstuurd; geforceerde push volgt over %s s",
                     prefix, REFRESH_PUSH_DELAY)

        async def _forced(_now) -> None:
            await self.push_repeater(prefix, force=True)

        async_call_later(self.hass, REFRESH_PUSH_DELAY, _forced)

    async def _fetch_settings(self, prefix: str, params: list[str]) -> None:
        """Log in op de repeater en haal CLI-instellingen op. Antwoorden komen
        binnen als meshcore_message-berichten van de repeater. De lock zorgt
        dat opvragingen voor meerdere repeaters na elkaar lopen."""
        async with self._settings_lock:
            await self._fetch_settings_inner(prefix, params)

    async def _fetch_settings_inner(self, prefix: str, params: list[str]) -> None:
        short = prefix[:6]
        password = self._passwords.get(prefix) or self._passwords.get(short) or ""
        results: dict[str, Any] = {}
        got = asyncio.Event()
        buffer: list[str] = []

        @callback
        def _on_response(event) -> None:
            text = _response_text(event.data)
            if text:
                buffer.append(text)
            got.set()

        @callback
        def _on_message(event) -> None:
            # CLI-antwoorden van de repeater komen binnen als direct bericht
            # (meshcore_message) van dat contact, met "> " ervoor.
            data = event.data or {}
            sender = str(data.get("pubkey_prefix", "")).lower()
            if not sender.startswith(short):
                return
            text = str(data.get("message", "")).strip()
            if text.startswith(">"):
                text = text.lstrip("> ").rstrip()
            if text:
                buffer.append(text)
                got.set()

        unsub_cli = self.hass.bus.async_listen("meshcore_cli_response", _on_response)
        unsub_msg = self.hass.bus.async_listen("meshcore_message", _on_message)
        try:
            login_cmd = f"send_login {short} {password}".strip()
            await self.hass.services.async_call(
                "meshcore", "execute_command", {"command": login_cmd}, blocking=True
            )
            await asyncio.sleep(SETTINGS_LOGIN_WAIT)
            loop = asyncio.get_running_loop()

            async def _get_param(param: str) -> str | None:
                buffer.clear()
                got.clear()
                # 'cmd:xyz' stuurt het commando letterlijk (zonder 'get ' ervoor)
                command = param[4:].strip() if param.startswith("cmd:") else f"get {param}"
                await self.hass.services.async_call(
                    "meshcore", "execute_command",
                    {"command": f'send_cmd {short} "{command}"'}, blocking=True,
                )
                try:
                    await asyncio.wait_for(got.wait(), timeout=SETTINGS_RESPONSE_TIMEOUT)
                except asyncio.TimeoutError:
                    return None
                # Meerregelige antwoorden (bv. region) komen als losse pakketten:
                # blijf verzamelen tot het SETTINGS_QUIET_GAP s stil is.
                deadline = loop.time() + SETTINGS_PARAM_CAP
                while loop.time() < deadline:
                    got.clear()
                    try:
                        await asyncio.wait_for(got.wait(), timeout=SETTINGS_QUIET_GAP)
                    except asyncio.TimeoutError:
                        break
                return "\n".join(buffer) or None

            for param in params:
                results[param] = await _get_param(param)
                await asyncio.sleep(2)  # LoRa even ademruimte geven
            # één herkansingsronde voor antwoorden die over LoRa verloren gingen
            for param in [p for p, v in results.items() if v is None]:
                results[param] = await _get_param(param)
                await asyncio.sleep(2)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Settings-opvraging voor %s mislukt: %s", prefix, err)
        finally:
            unsub_cli()
            unsub_msg()
        answered = sum(1 for v in results.values() if v is not None)
        _LOGGER.info("Settings %s: %s/%s beantwoord", prefix, answered, len(params))
        try:
            await self._session.post(
                f"{self.base_url}/api/v1/repeater_settings",
                json={"repeater": {"pubkey_prefix": prefix}, "settings": results},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Settings-push voor %s mislukt: %s", prefix, err)

    async def push_repeater(self, prefix: str, force: bool = False) -> bool:
        payload = self._snapshot(prefix)
        if payload is None:
            _LOGGER.debug("Geen data voor repeater %s, push overgeslagen", prefix)
            return False
        if force:
            payload["force"] = True
        try:
            resp = await self._session.post(
                f"{self.base_url}/api/v1/ingest",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            )
            if resp.status >= 400:
                _LOGGER.warning(
                    "Push voor %s geweigerd door %s: HTTP %s",
                    prefix, self.base_url, resp.status,
                )
                return False
            return True
        except Exception as err:  # noqa: BLE001 - netwerkfouten mogen de loop niet breken
            _LOGGER.warning("Push voor %s naar %s mislukt: %s", prefix, self.base_url, err)
            return False


def _response_text(data: Any) -> str | None:
    """Haal de leesbare tekst uit een meshcore_cli_response-event, met een
    tolerante fallback voor onbekende veldnamen."""
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("response", "text", "message", "result", "payload"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
        try:
            return json.dumps(data, default=str, ensure_ascii=False)[:500]
        except (TypeError, ValueError):
            return str(data)[:500]
    return str(data)[:500]


async def validate_connection(hass: HomeAssistant, base_url: str, token: str) -> bool:
    session = async_get_clientsession(hass)
    resp = await session.get(
        f"{base_url.rstrip('/')}/api/v1/ping",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    return resp.status == 200
