"""Verbinding met de openHop-companion via het companion frame protocol (TCP).

openHop laat één client per companion toe en verbreekt na 8 u zonder verkeer
van de client; daarom een periodieke keepalive. Bij verlies: opnieuw verbinden
met oplopende wachttijd.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from meshcore import EventType, MeshCore

log = logging.getLogger("meshtrack.mesh")

PATH_HASH_MODE = 1   # 0 = 1 byte, 1 = 2 bytes, 2 = 3 bytes per hop

OnMessage = Callable[[str, str, Optional[int], Optional[float], Optional[int]], Awaitable[None]]


class MeshLink:
    def __init__(self, host: str, port: int, keepalive_s: int, on_message: OnMessage,
                 on_connect: Optional[Callable[[], Awaitable[None]]] = None):
        # Hooguit 60 s: de companion-server van openHop verbreekt een verbinding na 120 s zonder verkeer
        # (idle_timeout), en een verbroken verbinding kost bevestigingen en berichten.
        self.host, self.port, self.keepalive_s = host, port, min(int(keepalive_s or 60), 60)
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_channel: Optional[Callable[..., Awaitable[None]]] = None   # (slot, tekst, ts, snr, padlengte)
        self.mc: Optional[MeshCore] = None
        self.connected = False
        self.self_info: dict[str, Any] = {}
        self.last_error = ""
        self.connected_since: Optional[int] = None
        self.last_rx: Optional[int] = None
        self._stop = asyncio.Event()
        self._lost = asyncio.Event()

    # ---- levenscyclus -------------------------------------------------------

    async def run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - alles opvangen en opnieuw proberen
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("meshverbinding: %s", self.last_error)
            self.connected = False
            self.connected_since = None
            if self.mc:
                try:   # met tijdslimiet: na een weggevallen verbinding kan disconnect() blijven hangen
                    await asyncio.wait_for(self.mc.disconnect(), timeout=5)
                except Exception:  # noqa: BLE001 - ook TimeoutError
                    pass
                self.mc = None
            log.info("meshverbinding: nieuwe poging over %s s", backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60)

    async def stop(self) -> None:
        self._stop.set()
        self._lost.set()

    async def _session(self) -> None:
        self._lost.clear()
        mc = await MeshCore.create_tcp(self.host, self.port, auto_reconnect=False)
        if mc is None:
            raise ConnectionError(f"geen antwoord van {self.host}:{self.port}")
        self.mc = mc
        mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_msg)
        mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_chan)
        mc.subscribe(EventType.DISCONNECTED, lambda _e: self._lost.set())
        self.self_info = dict(mc.self_info or {})
        await self._ensure_path_hash_mode(mc)
        await mc.commands.get_contacts()
        await mc.start_auto_message_fetching()
        self.connected = True
        self.connected_since = int(time.time())
        self.last_error = ""
        log.info("verbonden met %s (%s:%s)", self.self_info.get("name"), self.host, self.port)
        if self.on_connect:
            try:
                await self.on_connect()
            except Exception:  # noqa: BLE001
                log.exception("on_connect mislukt")

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._lost.wait(), timeout=self.keepalive_s)
                raise ConnectionError("verbinding verbroken")
            except asyncio.TimeoutError:
                res = await mc.commands.get_bat()
                if res is None or res.type == EventType.ERROR:
                    raise ConnectionError("keepalive kreeg geen antwoord")

    async def _ensure_path_hash_mode(self, mc: MeshCore) -> None:
        """Eigen berichten met 2-byte padhashes, zoals de trackers. Repeaters zoals e3d3 sturen
        pakketten met 1-byte padhashes niet door, en dan bereikt een SOS-bevestiging de tracker niet.
        De companion van openHop staat standaard op 1 byte en bewaart de keuze zelf."""
        try:
            if await mc.commands.get_path_hash_mode() != PATH_HASH_MODE:
                res = await mc.commands.set_path_hash_mode(PATH_HASH_MODE)
                ok = res is not None and res.type != EventType.ERROR
                log.info("padhashes van de companion op %d bytes gezet%s", PATH_HASH_MODE + 1, "" if ok else ": MISLUKT")
        except Exception as e:  # noqa: BLE001 - oudere companion zonder dit commando
            log.warning("padhashgrootte niet ingesteld: %s", e)

    async def _on_msg(self, event) -> None:
        p = event.payload or {}
        self.last_rx = int(time.time())
        try:
            await self.on_message(p.get("pubkey_prefix", ""), p.get("text", ""), p.get("sender_timestamp"),
                                  p.get("SNR"), p.get("path_len"))
        except Exception:  # noqa: BLE001 - één slecht bericht mag de lus niet stoppen
            log.exception("fout bij verwerken van bericht")

    async def _on_chan(self, event) -> None:
        p = event.payload or {}
        self.last_rx = int(time.time())
        if not self.on_channel:
            return
        try:
            await self.on_channel(p.get("channel_idx"), p.get("text", ""), p.get("sender_timestamp"),
                                  p.get("SNR"), p.get("path_len"))
        except Exception:  # noqa: BLE001
            log.exception("fout bij verwerken van kanaalbericht")

    # ---- kanalen op de companion ----------------------------------------------

    async def channel_slots(self, n: int = 40) -> list[dict[str, Any]]:
        """Kanalen die nu op de companion staan (nummer, naam, of de sleutel klopt met de onze kan de beller nagaan)."""
        mc = self._require()
        out = []
        for i in range(n):
            res = await mc.commands.get_channel(i)
            if res is None or res.type == EventType.ERROR:
                break
            p = res.payload or {}
            out.append({"slot": i, "name": p.get("channel_name", ""), "secret": bytes(p.get("channel_secret") or b"").hex()})
        return out

    async def send_channel(self, slot: int, text: str, scope: str = "") -> None:
        """Groepsbericht op een kanaal van de companion, met de regio (scope) van dat kanaal;
        daarna weer de standaardregio van de companion."""
        # De regio wordt niet teruggezet: dat antwoord kwam bij de companion van openHop soms pas bij het
        # volgende commando aan (ERR_CODE_NOT_FOUND voor een geldig bericht). Alle kanalen gebruiken toch 'be'.
        mc = self._require()
        if scope:
            try:
                await mc.commands.set_flood_scope(scope if scope.startswith("#") else "#" + scope)
            except Exception:  # noqa: BLE001
                pass
        res = None
        for attempt in range(2):
            res = await mc.commands.send_chan_msg(slot, text[:140])
            if res is not None and res.type != EventType.ERROR:
                return
            await asyncio.sleep(1)
        raise RuntimeError(f"kanaalbericht niet verstuurd: {getattr(res, 'payload', None)}")

    async def set_channel(self, slot: int, name: str, secret_hex: str) -> None:
        mc = self._require()
        secret = bytes.fromhex(secret_hex) if secret_hex else bytes(16)
        res = await mc.commands.set_channel(slot, name, secret)
        if res is None or res.type == EventType.ERROR:
            raise RuntimeError(f"kanaal {slot} instellen mislukt: {getattr(res, 'payload', None)}")

    # ---- contacten ----------------------------------------------------------

    def _require(self) -> MeshCore:
        if not self.mc or not self.connected:
            raise ConnectionError("niet verbonden met de companion")
        return self.mc

    async def contacts(self) -> list[dict[str, Any]]:
        mc = self._require()
        await mc.commands.get_contacts()
        return [
            {"public_key": k, "name": c.get("adv_name", ""), "type": c.get("type"),
             "last_advert": c.get("last_advert"), "path_len": c.get("out_path_len")}
            for k, c in (mc.contacts or {}).items()
        ]

    async def ensure_contact(self, pubkey: str, name: str) -> None:
        """Tracker als chat-contact op de companion zetten (nodig om zijn DM's
        te kunnen ontcijferen). Bestaat hij al, dan blijft zijn pad behouden."""
        mc = self._require()
        await mc.commands.get_contacts()
        if pubkey.lower() in {k.lower() for k in (mc.contacts or {})}:
            return
        contact = {
            "public_key": pubkey.lower(), "type": 1, "flags": 0,
            "out_path_len": -1, "out_path": "", "out_path_hash_mode": 0,
            "adv_name": name[:31], "last_advert": 0, "adv_lat": 0.0, "adv_lon": 0.0,
        }
        res = await mc.commands.add_contact(contact)
        if res is None or res.type == EventType.ERROR:
            raise RuntimeError(f"contact toevoegen mislukt: {getattr(res, 'payload', None)}")

    async def remove_contact(self, pubkey: str) -> None:
        mc = self._require()
        res = await mc.commands.remove_contact(pubkey.lower())
        if res is not None and res.type == EventType.ERROR:
            log.info("contact %s niet verwijderd (bestond niet?)", pubkey[:12])

    async def nodes(self, max_age_s: int = 60) -> list[dict[str, Any]]:
        """Alle bekende meshnodes met een advertpositie (repeaters, rooms,
        companions, sensoren). Contactenlijst hooguit elke max_age_s verversen."""
        mc = self._require()
        if time.time() - getattr(self, "_nodes_at", 0) > max_age_s:
            await mc.commands.get_contacts()
            self._nodes_at = time.time()
        out = []
        for k, c in (mc.contacts or {}).items():
            lat, lon = c.get("adv_lat") or 0, c.get("adv_lon") or 0
            if abs(lat) < 0.001 and abs(lon) < 0.001:
                continue
            out.append({"key": k[:12], "name": c.get("adv_name", ""), "type": c.get("type"),
                        "lat": lat, "lon": lon, "last_advert": c.get("last_advert"),
                        "hops": c.get("out_path_len")})
        return out

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected, "host": self.host, "port": self.port,
            "name": self.self_info.get("name"), "pubkey": self.self_info.get("public_key"),
            "connected_since": self.connected_since, "last_rx": self.last_rx,
            "last_error": self.last_error,
        }


async def send_text(link: MeshLink, pubkey: str, text: str) -> None:
    """Tekstbericht (DM) via de companion. De ontvanger moet een contact zijn;
    zo niet, dan wordt hij eerst toegevoegd."""
    mc = link._require()
    await link.ensure_contact(pubkey, f"melding-{pubkey[:8]}")
    res = await mc.commands.send_msg(pubkey.lower(), text[:140])
    if res is None or res.type == EventType.ERROR:
        raise RuntimeError(f"bericht niet verstuurd: {getattr(res, 'payload', None)}")
