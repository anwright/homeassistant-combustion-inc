"""Entity identity migration checks."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.combustion.sensor import (
    CombustionNodeEntity,
    CombustionProbeEntity,
    NODE_SENSOR_TYPES,
    SENSOR_TYPES,
    _migrate_entity_registry,
    _migrate_node_entity_registry,
    _migrate_offline_entities,
    _probe_unique_id,
)


@pytest.mark.parametrize(
    "old_entity_id",
    [
        "sensor.combustion_probe_t1",
        "sensor.combustion_probe_t1_deadbeef",
        "sensor.probe_deadbeef_combustion_probe_t1_deadbeef",
        "sensor.combustion_probe_t1_deadbeef_deadbeef",
        "sensor.combustion_probe_deadbeef_t1",
    ],
)
def test_probe_registry_entry_has_one_serial_in_id_and_none_in_name(old_entity_id):
    serial = "DEADBEEF"
    type_data = SENSOR_TYPES["prob_temp_t1"]
    old_unique_id = f"Combustion {type_data[0]} {serial}"
    entry = SimpleNamespace(
        entity_id=old_entity_id,
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
    assert entry.entity_id == "sensor.combustion_probe_deadbeef_t1"
    assert entry.name == "Combustion Probe T1"


@pytest.mark.parametrize(
    ("old_entity_id", "expected_entity_id", "occupied_id"),
    [
        ("sensor.deadbeef_kitchen_probe", "sensor.deadbeef_kitchen_probe", None),
        ("sensor.kitchen_probe_deadbeef_deadbeef", "sensor.kitchen_probe_deadbeef", None),
        (
            "sensor.kitchen_probe_deadbeef_combustion_probe_t1_deadbeef",
            "sensor.kitchen_probe_combustion_probe_deadbeef_t1",
            None,
        ),
        ("sensor.kitchen_probe", "sensor.kitchen_probe_deadbeef_2", "sensor.kitchen_probe_deadbeef"),
    ],
)
def test_custom_entity_id_keeps_its_prefix_and_one_serial(
    old_entity_id, expected_entity_id, occupied_id
):
    serial = "DEADBEEF"
    entry = SimpleNamespace(
        entity_id=old_entity_id,
        unique_id=f"Combustion Probe T1 {serial}",
        name=f"Kitchen Probe {serial}",
        original_name=f"Combustion Probe T1 {serial}",
    )

    class Registry:
        def async_get_entity_id(self, domain, platform, unique_id):
            if (domain, platform, unique_id) == ("sensor", "combustion", entry.unique_id):
                return entry.entity_id
            return None

        def async_get(self, entity_id):
            if entity_id == entry.entity_id:
                return entry
            if occupied_id is not None and entity_id == occupied_id:
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
            serial,
            "combustion_probe_deadbeef_t1",
        )

    assert entry.entity_id == expected_entity_id
    assert entry.name == "Kitchen Probe"


@pytest.mark.parametrize(
    ("old_entity_id", "expected_entity_id"),
    [
        ("sensor.combustion_gauge_temperature", "sensor.combustion_gauge_g123_temperature"),
        ("sensor.combustion_gauge_temperature_g123", "sensor.combustion_gauge_g123_temperature"),
        (
            "sensor.combustion_gauge_temperature_gauge_g123",
            "sensor.combustion_gauge_g123_temperature",
        ),
        (
            "sensor.gauge_g123_combustion_gauge_temperature_gauge_g123",
            "sensor.combustion_gauge_g123_temperature",
        ),
        ("sensor.combustion_gauge_g123_temperature", "sensor.combustion_gauge_g123_temperature"),
    ],
)
def test_gauge_registry_entry_keeps_identity_and_one_serial(old_entity_id, expected_entity_id):
    old_unique_id = "Combustion Gauge Temperature Gauge G123"
    entry = SimpleNamespace(
        entity_id=old_entity_id,
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
    assert entry.entity_id == expected_entity_id
    assert entry.name == "Combustion Gauge Temperature"


def test_new_entities_suggest_one_identifier_without_changing_their_names():
    probe = SimpleNamespace(serial_number_string="DEADBEEF")
    probe_entity = CombustionProbeEntity(
        None, probe, {}, SENSOR_TYPES["prob_temp_t1"], "Combustion Probe T1"
    )
    assert probe_entity.name == "Combustion Probe T1"
    assert probe_entity._attr_suggested_object_id == "combustion_probe_deadbeef_t1"
    assert probe_entity.unique_id == "Combustion Probe T1 DEADBEEF"

    core_entity = CombustionProbeEntity(
        None, probe, {}, SENSOR_TYPES["prob_temp_core"], "Combustion Core Temperature"
    )
    assert core_entity._attr_suggested_object_id == "combustion_probe_deadbeef_core_temperature"

    gauge = SimpleNamespace(gauge_serial="G123", ble_identifier="AA:BB", unique_identifier="AA:BB")
    gauge_entity = CombustionNodeEntity(
        None, gauge, {}, NODE_SENSOR_TYPES["gauge_temp"], "Combustion Gauge Temperature"
    )
    assert gauge_entity.name == "Combustion Gauge Temperature"
    assert gauge_entity._attr_suggested_object_id == "combustion_gauge_g123_temperature"
    assert gauge_entity.unique_id == "combustion_AA:BB_gauge_temp"

    node = SimpleNamespace(gauge_serial=None, ble_identifier="AA:BB", unique_identifier="AA:BB")
    node_entity = CombustionNodeEntity(
        None, node, {}, NODE_SENSOR_TYPES["node_rssi"], "Combustion MeatNet Node RSSI"
    )
    assert node_entity._attr_suggested_object_id == "combustion_meatnet_node_aa_bb_rssi"
