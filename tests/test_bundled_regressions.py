"""Regression checks for the bundled BLE library."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.combustion.combustion_ble.ble_manager import BleManager
from custom_components.combustion.combustion_ble.ble_data.advertising_data import CombustionProductType
from custom_components.combustion.combustion_ble.ble_data.mode_id import ProbeMode
from custom_components.combustion.combustion_ble.connection_manager import ConnectionManager
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


async def test_connection_uses_current_proxy_route():
    manager = BleManager()
    old_device = SimpleNamespace(address="AA", name="Probe")
    new_device = SimpleNamespace(address="AA", name="Probe")
    manager.ble_devices["AA"] = old_device
    manager.ble_device_resolver = Mock(return_value=new_device)
    manager.delegate = SimpleNamespace(did_connect_to=Mock(), did_fail_to_connect_to=Mock())
    manager.handle_discovered_services = Mock()
    client = SimpleNamespace(is_connected=True)

    with patch(
        "custom_components.combustion.combustion_ble.ble_manager.establish_connection",
        new=AsyncMock(return_value=client),
    ) as establish:
        await manager.connect("AA")

    assert establish.await_args.args[1:3] == (new_device, "Probe")
    manager.ble_device_resolver.assert_called_once_with("AA")
    manager.delegate.did_connect_to.assert_called_once_with("AA")


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


def test_connected_probe_uses_advertising_when_notifications_go_stale():
    probe = Probe.__new__(Probe)
    probe.connection_state = Probe.ConnectionState.CONNECTED
    probe._last_status_notification_time = datetime.now()
    probe._last_normal_mode = None
    probe._last_normal_mode_hop_count = None
    probe._rssi = Monitorable(-70)
    probe._update_id_color_battery = Mock()
    probe._update_temperatures = Mock()
    advertising = SimpleNamespace(
        type=CombustionProductType.PROBE,
        hop_count=None,
        mode_id=SimpleNamespace(mode=ProbeMode.NORMAL, id=1, color=1),
        battery_status_virtual_sensors=SimpleNamespace(battery_status=1, virtual_sensors=object()),
        temperatures=object(),
    )

    probe.update_with_advertising(advertising, True, -60, "AA")
    probe._update_temperatures.assert_not_called()

    probe._last_status_notification_time -= timedelta(seconds=Probe.STATUS_NOTIFICATION_STALE_TIMEOUT + 1)
    probe.update_with_advertising(advertising, True, -55, "AA")
    probe._update_temperatures.assert_called_once()


async def test_stalled_probe_connection_is_released_for_proxy_failover():
    device_manager = SimpleNamespace(did_disconnect_from=Mock())
    connection_manager = ConnectionManager(device_manager)
    probe = SimpleNamespace(
        serial_number_string="DEADBEEF",
        ble_identifier="AA",
        is_connectable=True,
        connection_state=Probe.ConnectionState.CONNECTED,
        disconnect=AsyncMock(),
    )
    connection_manager.note_connect_succeeded(probe)
    connection_manager.received_probe_advertising(probe)
    assert not connection_manager.stale_disconnect_tasks

    connection_manager._probe_connected_at[probe.serial_number_string] -= timedelta(
        seconds=connection_manager.STALE_CONNECTION_TIMEOUT_SECONDS + 1
    )
    connection_manager.last_status_update[probe.serial_number_string] = datetime.now()
    connection_manager.received_probe_advertising(probe)
    assert not connection_manager.stale_disconnect_tasks

    connection_manager.last_status_update[probe.serial_number_string] -= timedelta(
        seconds=connection_manager.STALE_CONNECTION_TIMEOUT_SECONDS + 1
    )
    connection_manager.received_probe_advertising(probe)
    task = connection_manager.stale_disconnect_tasks[probe.serial_number_string]
    with patch.object(BleManager.shared, "get_connected_peripheral", return_value=None):
        await task

    probe.disconnect.assert_awaited_once()
    device_manager.did_disconnect_from.assert_called_once_with("AA")
    assert not connection_manager.stale_disconnect_tasks
