"""Config flow: URL + token invoeren, daarna repeaters kiezen."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .const import CONF_AUTO_ADD, CONF_BASE_URL, CONF_PASSWORDS, CONF_REPEATERS, CONF_TOKEN, DOMAIN
from .pusher import discover_repeaters, validate_connection


def _repeater_options(hass) -> dict[str, str]:
    found = discover_repeaters(hass)
    return {prefix: f"{name} ({prefix})" for prefix, name in sorted(found.items(), key=lambda x: x[1])}


class McRepeaterStatsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._base_url: str | None = None
        self._token: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            try:
                ok = await validate_connection(self.hass, base_url, user_input[CONF_TOKEN])
            except Exception:  # noqa: BLE001
                ok = False
                errors["base"] = "cannot_connect"
            if ok:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                self._base_url = base_url
                self._token = user_input[CONF_TOKEN]
                return await self.async_step_repeaters()
            errors.setdefault("base", "invalid_auth")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_BASE_URL, default="https://"): cv.string,
                vol.Required(CONF_TOKEN): cv.string,
            }),
            errors=errors,
        )

    async def async_step_repeaters(self, user_input: dict[str, Any] | None = None):
        options = _repeater_options(self.hass)
        if user_input is not None:
            return self.async_create_entry(
                title=self._base_url,
                data={CONF_BASE_URL: self._base_url, CONF_TOKEN: self._token},
                options={
                    CONF_REPEATERS: user_input[CONF_REPEATERS],
                    CONF_AUTO_ADD: user_input.get(CONF_AUTO_ADD, True),
                },
            )
        return self.async_show_form(
            step_id="repeaters",
            data_schema=vol.Schema({
                vol.Required(CONF_REPEATERS, default=list(options)): cv.multi_select(options),
                vol.Required(CONF_AUTO_ADD, default=True): cv.boolean,
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return McRepeaterStatsOptionsFlow()


class McRepeaterStatsOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._selection: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._selection = {
                CONF_REPEATERS: user_input[CONF_REPEATERS],
                CONF_AUTO_ADD: user_input.get(CONF_AUTO_ADD, True),
            }
            return await self.async_step_passwords()
        options = _repeater_options(self.hass)
        current = self.config_entry.options.get(CONF_REPEATERS, [])
        # bewaar ook eerder gekozen prefixen die nu (tijdelijk) geen entiteiten hebben
        for prefix in current:
            options.setdefault(prefix, prefix)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_REPEATERS, default=current or list(options)): cv.multi_select(options),
                vol.Required(CONF_AUTO_ADD,
                             default=self.config_entry.options.get(CONF_AUTO_ADD, True)): cv.boolean,
            }),
        )

    async def async_step_passwords(self, user_input: dict[str, Any] | None = None):
        """Optioneel: admin-wachtwoord per repeater (voor CLI-settings-opvraging)."""
        selected = self._selection.get(CONF_REPEATERS, [])
        stored: dict[str, str] = dict(self.config_entry.options.get(CONF_PASSWORDS, {}))
        if user_input is not None:
            for prefix in selected:
                value = (user_input.get(f"pw_{prefix}") or "").strip()
                if value:
                    stored[prefix] = value
                elif prefix in stored and user_input.get(f"pw_{prefix}") == "":
                    # leeg gelaten veld behoudt het bestaande wachtwoord
                    pass
            return self.async_create_entry(data={**self._selection, CONF_PASSWORDS: stored})
        names = _repeater_options(self.hass)
        schema: dict = {}
        for prefix in selected:
            schema[vol.Optional(f"pw_{prefix}",
                                description={"suggested_value": ""})] = cv.string
        return self.async_show_form(
            step_id="passwords",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "repeaters": ", ".join(names.get(p, p) for p in selected),
            },
        )
