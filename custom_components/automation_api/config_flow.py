from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


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
