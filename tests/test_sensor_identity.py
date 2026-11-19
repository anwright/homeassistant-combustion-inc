"""Entity identity migration checks."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.combustion.sensor import (
    NODE_SENSOR_TYPES,
    SENSOR_TYPES,
    _migrate_entity_registry,
    _migrate_node_entity_registry,
    _migrate_offline_entities,
    _probe_unique_id,
)


def test_probe_registry_entry_loses_serial_from_name_and_entity_id():
    serial = "DEADBEEF"
    type_data = SENSOR_TYPES["prob_temp_t1"]
    old_unique_id = f"Combustion {type_data[0]} {serial}"
    entry = SimpleNamespace(
        entity_id="sensor.combustion_probe_t1_deadbeef",
        unique_id=old_unique_id,
        name=None,
        original_name=old_unique_id,
        domain="sensor",
        platform="combustion",
    )

    class Registry:
        def async_get_entity_id(self, domain, platform, unique_id):
            if (domain, platform) == ("sensor", "combustion") and unique_id == entry.unique_id:
                return entry.entity_id
            return None

        def async_get(self, entity_id):
            return entry if entity_id == entry.entity_id else None

        def async_update_entity(self, entity_id, **changes):
            assert entity_id == entry.entity_id
            entry.unique_id = changes.get("new_unique_id", entry.unique_id)
            entry.entity_id = changes.get("new_entity_id", entry.entity_id)
            entry.name = changes.get("name", entry.name)

    hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
    with (
        patch("custom_components.combustion.sensor.er.async_get", return_value=Registry()),
        patch("custom_components.combustion.sensor.er.async_entries_for_config_entry", return_value=[entry]),
    ):
        _migrate_offline_entities(hass, SimpleNamespace(entry_id="test"))

    assert entry.unique_id == _probe_unique_id(serial, type_data) == old_unique_id
    assert entry.entity_id == "sensor.combustion_probe_t1"
    assert entry.name == "Combustion Probe T1"


def test_custom_entity_id_keeps_its_prefix_when_serial_is_removed():
    serial = "DEADBEEF"
    entry = SimpleNamespace(
        entity_id="sensor.kitchen_probe_deadbeef",
        unique_id=f"Combustion Probe T1 {serial}",
        name=f"Kitchen Probe {serial}",
        original_name=f"Combustion Probe T1 {serial}",
    )
    occupied_id = "sensor.kitchen_probe"

    class Registry:
        def async_get_entity_id(self, domain, platform, unique_id):
            if (domain, platform, unique_id) == ("sensor", "combustion", entry.unique_id):
                return entry.entity_id
            return None

        def async_get(self, entity_id):
            if entity_id == entry.entity_id:
                return entry
            if entity_id == occupied_id:
                return object()
            return None

        def async_update_entity(self, entity_id, **changes):
            assert entity_id == entry.entity_id
            entry.entity_id = changes.get("new_entity_id", entry.entity_id)
            entry.name = changes.get("name", entry.name)

    hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
    with patch("custom_components.combustion.sensor.er.async_get", return_value=Registry()):
        _migrate_entity_registry(
            hass,
            entry.unique_id,
            [],
            "Combustion Probe T1",
            [serial],
        )

    assert entry.entity_id == "sensor.kitchen_probe_2"
    assert entry.name == "Kitchen Probe"


def test_gauge_registry_entry_keeps_identity_when_serial_is_removed():
    old_unique_id = "Combustion Gauge Temperature Gauge G123"
    entry = SimpleNamespace(
        entity_id="sensor.combustion_gauge_temperature_gauge_g123",
        unique_id=old_unique_id,
        name=None,
        original_name=old_unique_id,
    )
    device = SimpleNamespace(
        ble_identifier="AA:BB",
        unique_identifier="AA:BB",
        gauge_serial="G123",
    )

    class Registry:
        def async_get_entity_id(self, domain, platform, unique_id):
            if (domain, platform, unique_id) == ("sensor", "combustion", entry.unique_id):
                return entry.entity_id
            return None

        def async_get(self, entity_id):
            return entry if entity_id == entry.entity_id else None

        def async_update_entity(self, entity_id, **changes):
            assert entity_id == entry.entity_id
            entry.unique_id = changes.get("new_unique_id", entry.unique_id)
            entry.entity_id = changes.get("new_entity_id", entry.entity_id)
            entry.name = changes.get("name", entry.name)

    hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
    with patch("custom_components.combustion.sensor.er.async_get", return_value=Registry()):
        _migrate_node_entity_registry(
            hass,
            device,
            NODE_SENSOR_TYPES["gauge_temp"],
            "Combustion Gauge Temperature",
        )

    assert entry.unique_id == "combustion_AA:BB_gauge_temp"
    assert entry.entity_id == "sensor.combustion_gauge_temperature"
    assert entry.name == "Combustion Gauge Temperature"
