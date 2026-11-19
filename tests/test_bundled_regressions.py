"""Regression checks for the bundled BLE library."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.combustion.combustion_ble.ble_manager import BleManager
from custom_components.combustion.combustion_ble.const import (
    DEVICE_STATUS_CHARACTERISTIC,
    UART_RX_CHARACTERISTIC,
    UART_TX_CHARACTERISTIC,
)
from custom_components.combustion.combustion_ble.device_manager import DeviceManager
from custom_components.combustion.combustion_ble.devices.meat_net_node import MeatNetNode
from custom_components.combustion.combustion_ble.devices.probe import Probe, VirtualTemperatures
from custom_components.combustion.combustion_ble.utilities.monitor import Monitorable


async def test_passive_bluetooth_shutdown_disconnects_clients():
    manager = BleManager()
    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    manager.clients["AA"] = client
    manager.ble_devices["AA"] = object()

    await manager.stop_bluetooth()

    client.disconnect.assert_awaited_once()
    assert manager.clients == {}
    assert manager.ble_devices == {}


async def test_service_order_does_not_drop_status_notifications():
    manager = BleManager()
    uart_tx = SimpleNamespace(uuid=UART_TX_CHARACTERISTIC, properties=["notify"])
    uart_rx = SimpleNamespace(uuid=UART_RX_CHARACTERISTIC, properties=["write"])
    status = SimpleNamespace(uuid=DEVICE_STATUS_CHARACTERISTIC, properties=["notify"])
    client = SimpleNamespace(
        address="AA",
        is_connected=True,
        services=[SimpleNamespace(characteristics=[uart_tx, uart_rx, status])],
        start_notify=AsyncMock(),
        write_gatt_char=AsyncMock(),
    )
    manager.clients["AA"] = client

    manager.handle_discovered_services("AA", client)
    await asyncio.sleep(0)

    assert client.start_notify.await_count == 2
    client.write_gatt_char.assert_awaited_once()


async def test_device_manager_releases_singleton_and_updates_node_serial():
    manager = DeviceManager()
    try:
        node = MeatNetNode(None, manager, True, -60, "AA")
        manager.devices[node.unique_identifier] = node
        manager.update_device_serial_number("AA", "GAUGE123")
        assert node.serial_number_string == "GAUGE123"
    finally:
        await manager.async_stop()

    assert DeviceManager.shared is None
    assert BleManager.shared.delegate is None
    replacement = DeviceManager()
    await replacement.async_stop()


def test_probe_virtual_temperatures_returns_temperature_data():
    probe = Probe.__new__(Probe)
    values = VirtualTemperatures(45.0, 50.0, 25.0)
    probe._virtual_temperatures = Monitorable(values)

    assert probe.virtual_temperatures is values
