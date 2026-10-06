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

OnMessage = Callable[[str, str, Optional[int], Optional[float], Optional[int]], Awaitable[None]]


class MeshLink:
    def __init__(self, host: str, port: int, keepalive_s: int, on_message: OnMessage,
                 on_connect: Optional[Callable[[], Awaitable[None]]] = None):
        self.host, self.port, self.keepalive_s = host, port, keepalive_s
        self.on_message = on_message
        self.on_connect = on_connect
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
                try:
                    await self.mc.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.mc = None
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
        mc.subscribe(EventType.DISCONNECTED, lambda _e: self._lost.set())
        self.self_info = dict(mc.self_info or {})
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

    async def _on_msg(self, event) -> None:
        p = event.payload or {}
        self.last_rx = int(time.time())
        try:
            await self.on_message(p.get("pubkey_prefix", ""), p.get("text", ""), p.get("sender_timestamp"),
                                  p.get("SNR"), p.get("path_len"))
        except Exception:  # noqa: BLE001 - één slecht bericht mag de lus niet stoppen
            log.exception("fout bij verwerken van bericht")

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
