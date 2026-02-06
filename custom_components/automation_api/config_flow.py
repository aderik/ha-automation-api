from __future__ import annotations

import secrets
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN, CONF_API_KEY


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Automation API", data=user_input)

        schema = vol.Schema({
            vol.Optional(CONF_API_KEY, default=secrets.token_hex(16)): cv.string
        })
        return self.async_show_form(step_id="user", data_schema=schema)
