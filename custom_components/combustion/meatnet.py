"""Bridge Home Assistant Bluetooth advertisements to the bundled library."""

import logging

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .combustion_ble.ble_manager import BleManager, BluetoothMode
from .combustion_ble.const import BT_MANUFACTURER_ID
from .combustion_ble.device_manager import DeviceManager
from .combustion_ble.devices.device import Device
from .combustion_ble.devices.meat_net_node import MeatNetNode
from .const import EVENT_DISCOVERED

_LOGGER = logging.getLogger(__name__)


class MeatNetManager:
    """Own the library manager and its Home Assistant Bluetooth subscription."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.device_manager: DeviceManager | None = None
        self._unsubscribe = None
        self._device_kind: dict[str, bool] = {}

    async def async_start(self) -> None:
        """Start processing advertisements on Home Assistant's event loop."""
        manager = DeviceManager()
        self.device_manager = manager
        try:
            manager.enable_meatnet()
            await manager.init_bluetooth(mode=BluetoothMode.PASSIVE)
            manager.add_device_listener(self._devices_changed)
            self._unsubscribe = bluetooth.async_register_callback(
                self.hass,
                self._advertisement,
                {"manufacturer_id": BT_MANUFACTURER_ID},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        except Exception:
            await self.async_stop()
            raise

    @callback
    def _devices_changed(self, added: list[Device], removed: list[Device]) -> None:
        for device in added:
            self._device_kind[device.unique_identifier] = bool(
                isinstance(device, MeatNetNode) and device.gauge_serial
            )
            async_dispatcher_send(self.hass, EVENT_DISCOVERED, device)
        for device in removed:
            self._device_kind.pop(device.unique_identifier, None)

    @callback
    def _advertisement(self, service_info, change) -> None:
        """Feed one HA advertisement to the library and detect Gauge upgrades."""
        manager = self.device_manager
        if manager is None:
            return
        try:
            BleManager.shared.process_advertisement(
                service_info.device,
                service_info.manufacturer_data,
                service_info.rssi,
                service_info.connectable,
            )
            node = manager.devices.get(service_info.address)
            if isinstance(node, MeatNetNode) and node.gauge_serial:
                if not self._device_kind.get(node.unique_identifier, False):
                    self._device_kind[node.unique_identifier] = True
                    async_dispatcher_send(self.hass, EVENT_DISCOVERED, node)
        except Exception:
            _LOGGER.exception("Failed to process Combustion advertisement")

    async def async_stop(self) -> None:
        """Remove the callback before shutting down the library."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        manager = self.device_manager
        self.device_manager = None
        if manager is not None:
            await manager.async_stop()
        self._device_kind.clear()
