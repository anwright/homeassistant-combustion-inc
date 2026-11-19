from datetime import timedelta
import logging
from homeassistant import config_entries, core
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .meatnet import MeatNetManager
from .const import DOMAIN, EVENT_REFRESH

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=5)
PLATFORMS = ["sensor"]


async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Set up the Combustion Inc Custom component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up sensors before subscribing to Bluetooth advertisements."""
    mgr = MeatNetManager(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = dict(entry.data)
    platforms_loaded = False
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        platforms_loaded = True
        await mgr.async_start()

        @callback
        def hub_refresh(event_time):
            async_dispatcher_send(hass, EVENT_REFRESH)

        entry.async_on_unload(async_track_time_interval(hass, hub_refresh, SCAN_INTERVAL))

        async def cleanup(event):
            await mgr.async_stop()

        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, cleanup))
        hass.data[DOMAIN]["mgr"] = mgr
    except Exception:
        _LOGGER.exception("Error setting up Combustion Inc integration")
        await mgr.async_stop()
        if platforms_loaded:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        entry.async_unload_callbacks()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return False
    return True


async def async_unload_entry(hass: core.HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Unload entities and release Bluetooth and timer callbacks."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    entry.async_unload_callbacks()
    mgr = hass.data[DOMAIN].pop("mgr", None)
    if mgr is not None:
        await mgr.async_stop()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
