"""Bluetooth discovery checks for the Home Assistant bridge."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.components import bluetooth

from custom_components.combustion.combustion_ble.ble_manager import BleManager
from custom_components.combustion.combustion_ble.const import BT_MANUFACTURER_ID
from custom_components.combustion.const import DOMAIN
from custom_components.combustion.meatnet import MeatNetManager
from custom_components.combustion.sensor import SENSOR_TYPES, async_setup_entry


async def test_nonconnectable_advertisement_reaches_bundled_manager(hass):
    """A passive proxy advertisement creates the probe's sensor entities."""
    serial = 0xDEADBEEF
    entry = SimpleNamespace(entry_id="test", async_on_unload=Mock())
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    add_entities = Mock()
    await async_setup_entry(hass, entry, add_entities)
    device = SimpleNamespace(address="AA:BB", name="Probe")
    manufacturer_data = {
        BT_MANUFACTURER_ID: bytes([1]) + serial.to_bytes(4, "little") + bytes(17)
    }
    service_info = SimpleNamespace(
        device=device,
        address=device.address,
        manufacturer_data=manufacturer_data,
        rssi=-70,
        connectable=False,
    )

    with patch("custom_components.combustion.meatnet.bluetooth.async_register_callback") as register:
        manager = MeatNetManager(hass)
        await manager.async_start()
        try:
            assert register.call_args.args[2:] == (
                {"manufacturer_id": BT_MANUFACTURER_ID, "connectable": False},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
            register.call_args.args[1](service_info, None)
            await hass.async_block_till_done()
            probes = manager.device_manager.get_probes()
            assert len(probes) == 1
            assert probes[0].serial_number_string == "DEADBEEF"
            assert probes[0].is_connectable is False
            add_entities.assert_called_once()
            sensors = add_entities.call_args.args[0]
            assert len(sensors) == len(SENSOR_TYPES)
            sensors_by_state = {sensor.sensor_type_data[3]: sensor for sensor in sensors}
            assert set(sensors_by_state) == {sensor[3] for sensor in SENSOR_TYPES.values()}
            assert sensors_by_state["t1"].name == "Combustion Probe T1"
            assert sensors_by_state["t1"].unique_id == "Combustion Probe T1 DEADBEEF"
            temperature_states = [f"t{number}" for number in range(1, 9)]
            temperature_states += ["core", "ambient", "surface"]
            assert all(
                sensors_by_state[state].native_value is not None
                for state in temperature_states
            )
        finally:
            await manager.async_stop()
            entry.async_on_unload.call_args.args[0]()


def test_nonconnectable_probe_payload_is_decoded():
    """The bundled parser must accept probe data without a BLE connection."""
    manager = BleManager()
    manager.delegate = Mock()
    device = SimpleNamespace(address="AA:BB")
    serial = 0xDEADBEEF
    payload = bytes([1]) + serial.to_bytes(4, "little") + bytes(17)

    manager.process_advertisement(device, {BT_MANUFACTURER_ID: payload}, -70, False)

    call = manager.delegate.update_device_with_advertising.call_args
    assert call is not None
    assert call.kwargs["advertising"].serial_number == serial
    assert call.kwargs["is_connectable"] is False
    assert call.kwargs["identifier"] == device.address
