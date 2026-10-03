from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_ALLOW_HACS, DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Nothing to configure: the API uses Home Assistant's own authentication
    (a long-lived access token of an administrator), so the flow only creates
    the single entry that enables the integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Automation API", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    """Opt-in for HACS downloads, which install third-party code."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ALLOW_HACS,
                        default=self.config_entry.options.get(CONF_ALLOW_HACS, False),
                    ): bool,
                }
            ),
        )
