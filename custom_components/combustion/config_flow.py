from __future__ import annotations
from typing import Any

from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN, TempUnit

CONFIG_SCHEMA = vol.Schema(
    {vol.Required("unit_type", default=TempUnit.CELSIUS.value): vol.In([unit.value for unit in TempUnit])}
)


class CombustionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_bluetooth(self, discovery_info):
        """Offer the single hub entry when a Combustion device is discovered."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Combustion Inc", data=user_input)

        return self.async_show_form(step_id="user", data_schema=CONFIG_SCHEMA)
