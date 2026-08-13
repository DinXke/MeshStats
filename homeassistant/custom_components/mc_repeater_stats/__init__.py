"""MC Repeater Stats: pusht MeshCore-repeaterstatistieken naar een externe statistiekensite."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import CONF_AUTO_ADD, CONF_BASE_URL, CONF_PASSWORDS, CONF_REPEATERS, CONF_TOKEN, DOMAIN
from .pusher import Pusher


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    pusher = Pusher(
        hass,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_TOKEN],
        entry.options.get(CONF_REPEATERS, []),
        entry=entry,
        auto_add=entry.options.get(CONF_AUTO_ADD, False),
        passwords=entry.options.get(CONF_PASSWORDS, {}),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = pusher
    await pusher.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _handle_push_now(_call: ServiceCall) -> None:
        for p in hass.data.get(DOMAIN, {}).values():
            await p.push_all()

    if not hass.services.has_service(DOMAIN, "push_now"):
        hass.services.async_register(DOMAIN, "push_now", _handle_push_now)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    pusher: Pusher | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if pusher:
        pusher.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
