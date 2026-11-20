from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant import config_entries, core
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .combustion_ble.ble_data.prediction_mode import PredictionMode
from .combustion_ble.ble_data.prediction_state import PredictionState
from .combustion_ble.ble_data.prediction_type import PredictionType
from .combustion_ble.devices.device import Device
from .combustion_ble.devices.meat_net_node import MeatNetNode
from .combustion_ble.devices.probe import Probe
from .const import DOMAIN, EVENT_DISCOVERED, EVENT_REFRESH, TempUnit


_LOGGER = logging.getLogger(__name__)

# [ name, unit_of_measurement, device_class, state_id, options]
SENSOR_TYPES = {
    "prob_temp_t1": ["Probe T1", "temp", "temperature", "t1"],
    "prob_temp_t2": ["Probe T2", "temp", "temperature", "t2"],
    "prob_temp_t3": ["Probe T3", "temp", "temperature", "t3"],
    "prob_temp_t4": ["Probe T4", "temp", "temperature", "t4"],
    "prob_temp_t5": ["Probe T5", "temp", "temperature", "t5"],
    "prob_temp_t6": ["Probe T6", "temp", "temperature", "t6"],
    "prob_temp_t7": ["Probe T7", "temp", "temperature", "t7"],
    "prob_temp_t8": ["Probe T8", "temp", "temperature", "t8"],
    "prob_temp_instant": ["Instant Temperature", "temp", "temperature", "instant"],
    "prob_temp_core": ["Core Temperature", "temp", "temperature", "core"],
    "prob_temp_ambient": ["Ambient Temperature", "temp", "temperature", "ambient"],
    "prob_temp_surface": ["Surface Temperature", "temp", "temperature", "surface"],
    "rssi": ["RSSI", "dBm", "signal_strength", "rssi"],
    "battery": ["Battery", None, SensorDeviceClass.ENUM, "battery", ["OK", "LOW"]],
    "prediction_mode": ["Prediction Mode", None, SensorDeviceClass.ENUM, "prediction_mode", [item.name for item in PredictionMode]],
    "prediction_state": ["Prediction State", None, SensorDeviceClass.ENUM, "prediction_state", [item.name for item in PredictionState]],
    "prediction_type": ["Prediction Type", None, SensorDeviceClass.ENUM, "prediction_type", [item.name for item in PredictionType]],
    "prediction_setpoint": ["Prediction Setpoint", "temp", "temperature", "prediction_setpoint"],
    "prediction_through": ["Prediction % Done", "%", "", "prediction_through"],
    "prediction_value": ["Prediction Remaining", "s", "duration", "prediction_value"],
    "prediction_core": ["Prediction Core Temp", "temp", "temperature", "prediction_core"],

    "hop_count": ["Hop Count", "hops", "", "hop_count"],

}

NODE_SENSOR_TYPES = {
    "node_rssi": ["RSSI", "dBm", "signal_strength", "rssi"],
    "node_probes": ["Networked Probes", None, "", "probes_count"],

    # Gauge-only (will be created only when Gauge MSD has been seen)
    "gauge_temp": ["Gauge Temperature", "temp", "temperature", "gauge_temp"],
    "gauge_sensor_present": [
        "Gauge Sensor Present",
        None,
        SensorDeviceClass.ENUM,
        "gauge_sensor_present",
        ["PRESENT", "NOT_PRESENT"],
    ],
    "gauge_low_battery": [
        "Gauge Low Battery",
        None,
        SensorDeviceClass.ENUM,
        "gauge_low_battery",
        ["OK", "LOW"],
    ],
    "gauge_overheating": [
        "Gauge Sensor Overheating",
        None,
        SensorDeviceClass.ENUM,
        "gauge_overheating",
        ["OK", "OVERHEATING"],
    ],
    "gauge_alarm_high_state": [
        "Gauge High Alarm State",
        None,
        SensorDeviceClass.ENUM,
        "gauge_alarm_high_state",
        ["OFF", "SET", "TRIPPED", "ALARMING"],
    ],
    "gauge_alarm_low_state": [
        "Gauge Low Alarm State",
        None,
        SensorDeviceClass.ENUM,
        "gauge_alarm_low_state",
        ["OFF", "SET", "TRIPPED", "ALARMING"],
    ],
    "gauge_alarm_low_temp": ["Gauge Alarm Low", "temp", "temperature", "gauge_alarm_low_temp"],
    "gauge_alarm_high_temp": ["Gauge Alarm High", "temp", "temperature", "gauge_alarm_high_temp"],
}


def _decode_gauge_alarm_status_to_temperature_c(alarm_status: int | None) -> float | None:
    """Decode the Gauge 'Alarm Status' packed 16-bit field to a threshold temperature in °C.

    Gauge spec (bit 1 is LSB):
      - Bit 1: Set
      - Bit 2: Tripped
      - Bit 3: Alarming
      - Bits 4-16: Alarm Temperature (13-bit) encoded like Raw Temperature Data
        Temperature = (raw * 0.1) - 20
    """
    if alarm_status is None:
        return None

    is_set = bool(alarm_status & 0x0001)
    if not is_set:
        return None

    raw_temp = (alarm_status >> 3) & 0x1FFF
    if raw_temp == 0:
        return None

    return (raw_temp * 0.1) - 20.0


def _decode_gauge_alarm_status_flags(alarm_status: int | None) -> dict[str, bool] | None:
    if alarm_status is None:
        return None
    return {
        "set": bool(alarm_status & 0x0001),
        "tripped": bool(alarm_status & 0x0002),
        "alarming": bool(alarm_status & 0x0004),
    }


def _decode_gauge_alarm_status_to_state(alarm_status: int | None) -> str | None:
    """Return a single HA-friendly alarm state from the packed Alarm Status flags."""
    if alarm_status is None:
        return None

    flags = _decode_gauge_alarm_status_flags(alarm_status)
    if not flags:
        return None

    if not flags["set"]:
        return "OFF"
    if flags["alarming"]:
        return "ALARMING"
    if flags["tripped"]:
        return "TRIPPED"
    return "SET"


def format_device_name(device: Device):
    if isinstance(device, Probe):
        device_name = f"Probe {device.serial_number_string}"
    elif isinstance(device, MeatNetNode):
        if getattr(device, "gauge_serial", None):
            device_name = f"Gauge {device.gauge_serial}"
        else:
            ble_address = device.ble_identifier[-5:] if device.ble_identifier else ""
            device_name = f"MeatNet Node {ble_address}"
    return device_name


def format_device_id(device: Device):
    if isinstance(device, Probe):
        device_name = f"probe_{device.serial_number_string}"
    elif isinstance(device, MeatNetNode):
        ble_address = device.ble_identifier[-5:] if device.ble_identifier else ""
        device_name = f"node_{ble_address}"
    return device_name


def convert_temp(temp: float, unit: TempUnit) -> float:
    if (temp is None):
        return None
    if unit == TempUnit.FAHRENHEIT:
        return (temp * 9 / 5) + 32
    return temp


def _probe_unique_id(serial_number: str, type_data: list[Any]) -> str:
    """Keep the existing internal identity when changing visible entity names."""
    return f"Combustion {type_data[0]} {serial_number}"


def _entity_object_id(kind: str, identifier: str, label: str) -> str:
    """Put the device identifier before the sensor's logical name."""
    kind_id = slugify(kind)
    identifier_id = slugify(identifier)
    label_id = slugify(label)
    label_id = label_id.removeprefix(f"{kind_id}_")
    if identifier_id:
        label_id = re.sub(
            rf"(?<![a-z0-9]){re.escape(identifier_id)}(?![a-z0-9])", "", label_id
        )
        label_id = re.sub(r"_+", "_", label_id).strip("_")
    return "_".join(part for part in (DOMAIN, kind_id, identifier_id, label_id) if part)


def _migrate_entity_registry(
    hass: core.HomeAssistant,
    unique_id: str,
    legacy_unique_ids: list[str],
    name: str,
    identifiers: list[str],
    entity_identifier: str,
    preferred_object_id: str,
) -> None:
    """Clean names and migrate generated IDs to device-first ordering."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    updates = {}
    if entity_id is None:
        for old_unique_id in dict.fromkeys(legacy_unique_ids):
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
            if entity_id is not None:
                updates["new_unique_id"] = unique_id
                break
    if entity_id is None:
        return

    entry = registry.async_get(entity_id)
    if entry is not None and (current_name := entry.name or entry.original_name):
        cleaned_name = current_name
        for identifier in identifiers:
            if identifier:
                cleaned_name = re.sub(re.escape(identifier), "", cleaned_name, flags=re.IGNORECASE)
        cleaned_name = " ".join(cleaned_name.split()) or name
        if cleaned_name != current_name:
            updates["name"] = cleaned_name if entry.name else name

    object_id = entity_id.removeprefix("sensor.")
    identifier_token = slugify(entity_identifier) if entity_identifier else ""
    identifier_pattern = (
        rf"(?<![a-z0-9]){re.escape(identifier_token)}(?![a-z0-9])"
        if identifier_token else None
    )
    other_tokens = {
        token for identifier in identifiers
        if identifier and (token := slugify(identifier)) and token != identifier_token
    }
    other_patterns = [
        rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
        for token in other_tokens
    ]
    has_one_identifier = (
        bool(identifier_pattern)
        and len(re.findall(identifier_pattern, object_id)) == 1
        and not any(re.search(pattern, object_id) for pattern in other_patterns)
    )
    old_default_id = slugify(entry.original_name) if entry and entry.original_name else ""
    old_name_id = slugify(name)
    auto_prefix = (
        rf"(?:combustion_)?(?:probe|gauge|meatnet_node)_"
        rf"(?:{'|'.join(re.escape(token) for token in sorted(other_tokens | {identifier_token}) if token)})_"
    )
    generated_ids = [rf"{re.escape(old_name_id)}(?:_{re.escape(identifier_token)})*"]
    if old_default_id:
        generated_ids.append(rf"(?:{auto_prefix})?{re.escape(old_default_id)}")
    generated_match = re.fullmatch(
        rf"(?:{'|'.join(generated_ids)})(?:_([0-9]+))?", object_id
    )
    # The first probe IDs included the serial in both the device prefix and
    # entity name. Match that shape independently of entry.original_name,
    # which Home Assistant may have refreshed since the ID was created.
    original_probe_match = (
        re.fullmatch(
            rf"probe_{re.escape(identifier_token)}_"
            rf"{re.escape(old_name_id)}_{re.escape(identifier_token)}"
            r"(?:_([0-9]+))?",
            object_id,
        )
        if preferred_object_id.startswith(f"{DOMAIN}_probe_{identifier_token}_")
        else None
    )
    if original_probe_match or generated_match or not has_one_identifier:
        cleaned_id = object_id
        for pattern in ([identifier_pattern] if identifier_pattern else []) + other_patterns:
            cleaned_id = re.sub(pattern, "", cleaned_id)
        cleaned_id = re.sub(r"_+", "_", cleaned_id).strip("_")
        if original_probe_match or generated_match:
            base = f"sensor.{preferred_object_id}"
            collision_suffix = (original_probe_match or generated_match).group(1)
            candidate = f"{base}_{collision_suffix}" if collision_suffix else base
            suffix = int(collision_suffix) + 1 if collision_suffix else 2
        else:
            logical_suffix = preferred_object_id.partition(f"_{identifier_token}_")[2]
            if logical_suffix and cleaned_id.endswith(f"_{logical_suffix}"):
                prefix = cleaned_id[: -len(logical_suffix)]
                custom_id = f"{prefix}{identifier_token}_{logical_suffix}"
            elif cleaned_id == logical_suffix:
                custom_id = f"{identifier_token}_{logical_suffix}"
            else:
                custom_id = "_".join(part for part in (cleaned_id, identifier_token) if part)
            base = f"sensor.{custom_id or slugify(name)}"
            candidate = base
            suffix = 2
        while candidate != entity_id and (
            registry.async_get(candidate) is not None or hass.states.get(candidate) is not None
        ):
            candidate = f"{base}_{suffix}"
            suffix += 1
        if candidate != entity_id:
            updates["new_entity_id"] = candidate

    if updates:
        registry.async_update_entity(entity_id, **updates)


def _migrate_node_entity_registry(
    hass: core.HomeAssistant, device: MeatNetNode, type_data: list[Any], name: str
) -> None:
    """Migrate node and Gauge names and keep one ID identifier."""
    legacy_names = []
    identifiers = []
    if device.ble_identifier:
        address_suffix = device.ble_identifier[-5:]
        identifiers.append(address_suffix)
        legacy_names.append(f"Combustion {type_data[0]} MeatNet Node {address_suffix}")
    if device.gauge_serial:
        identifiers.append(device.gauge_serial)
        legacy_names.append(f"Combustion {type_data[0]} Gauge {device.gauge_serial}")
    _migrate_entity_registry(
        hass,
        f"{DOMAIN}_{device.unique_identifier}_{type_data[3]}",
        legacy_names,
        name,
        identifiers,
        device.gauge_serial or (device.ble_identifier[-5:] if device.ble_identifier else ""),
        _entity_object_id(
            "MeatNet Node" if type_data[3] in ("rssi", "probes_count") else "Gauge",
            device.gauge_serial or (device.ble_identifier[-5:] if device.ble_identifier else ""),
            type_data[0],
        ),
    )


def _migrate_offline_entities(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
) -> None:
    """Rename stored entities before their devices advertise again."""
    registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.domain != "sensor" or entry.platform != DOMAIN:
            continue
        migrated = False
        for type_data in SENSOR_TYPES.values():
            prefix = f"Combustion {type_data[0]} "
            if not entry.unique_id.startswith(prefix):
                continue
            serial_number = entry.unique_id[len(prefix):]
            if re.fullmatch(r"[0-9A-Fa-f]{8}", serial_number):
                _migrate_entity_registry(
                    hass,
                    _probe_unique_id(serial_number, type_data),
                    [],
                    f"Combustion {type_data[0]}",
                    [serial_number],
                    serial_number,
                    _entity_object_id("Probe", serial_number, type_data[0]),
                )
                migrated = True
            break
        if migrated:
            continue

        for sensor_type, type_data in NODE_SENSOR_TYPES.items():
            preferred_kind = (
                "MeatNet Node" if sensor_type in ("node_rssi", "node_probes") else "Gauge"
            )
            name = (
                f"Combustion MeatNet Node {type_data[0]}"
                if sensor_type in ("node_rssi", "node_probes")
                else f"Combustion {type_data[0]}"
            )
            for source in (entry.unique_id, entry.name, entry.original_name):
                if source is None:
                    continue
                for kind in ("MeatNet Node", "Gauge"):
                    prefix = f"Combustion {type_data[0]} {kind} "
                    if source.startswith(prefix):
                        _migrate_entity_registry(
                            hass,
                            entry.unique_id,
                            [],
                            name,
                            [source[len(prefix):]],
                            source[len(prefix):],
                            _entity_object_id(
                                preferred_kind, source[len(prefix):], type_data[0]
                            ),
                        )
                        migrated = True
                        break
                if migrated:
                    break
            if migrated:
                break


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):

    config = hass.data[DOMAIN][config_entry.entry_id]
    _migrate_offline_entities(hass, config_entry)
    created_ids: set[tuple[str, str]] = set()

    @callback
    def event_create_entity(device: Device) -> None:
        if isinstance(device, Probe):
            _LOGGER.info(f"Discovered device: {device}")

            sensors = []
            for sensor_type in SENSOR_TYPES:
                type_data = SENSOR_TYPES[sensor_type]
                name = f"Combustion {type_data[0]}"
                key = (device.unique_identifier, sensor_type)
                if key in created_ids:
                    continue
                created_ids.add(key)
                _migrate_entity_registry(
                    hass,
                    _probe_unique_id(device.serial_number_string, type_data),
                    [],
                    name,
                    [device.serial_number_string],
                    device.serial_number_string,
                    _entity_object_id("Probe", device.serial_number_string, type_data[0]),
                )
                sensors.append(CombustionProbeEntity(hass, device, config, type_data, name))

            if sensors:
                async_add_entities(sensors)

        elif isinstance(device, MeatNetNode):
            _LOGGER.info(f"Discovered MeatNet device: {device}")

            sensors = []
            # Base repeater/node sensors
            for sensor_type in ["node_rssi", "node_probes"]:
                type_data = NODE_SENSOR_TYPES[sensor_type]
                name = f"Combustion MeatNet Node {type_data[0]}"
                key = (device.unique_identifier, sensor_type)
                if key in created_ids:
                    continue
                created_ids.add(key)
                _migrate_node_entity_registry(hass, device, type_data, name)
                sensors.append(CombustionNodeEntity(hass, device, config, type_data, name))

            # Gauge-only sensors (create only when Gauge MSD has been observed)
            if getattr(device, "gauge_serial", None):
                for sensor_type in [
                    "gauge_temp",
                    "gauge_sensor_present",
                    "gauge_low_battery",
                    "gauge_overheating",
                    "gauge_alarm_high_state",
                    "gauge_alarm_low_state",
                    "gauge_alarm_low_temp",
                    "gauge_alarm_high_temp",
                ]:
                    type_data = NODE_SENSOR_TYPES[sensor_type]
                    name = f"Combustion {type_data[0]}"
                    key = (device.unique_identifier, sensor_type)
                    if key in created_ids:
                        continue
                    created_ids.add(key)
                    _migrate_node_entity_registry(hass, device, type_data, name)
                    sensors.append(CombustionNodeEntity(hass, device, config, type_data, name))

            if sensors:
                async_add_entities(sensors)

    config_entry.async_on_unload(async_dispatcher_connect(hass, EVENT_DISCOVERED, event_create_entity))


class CombustionProbeEntity(SensorEntity):

    device: Probe
    sensor_type_data: list[Any]
    config: dict
    sensor_name: str
    _attr_should_poll = False
    unit_type: TempUnit = TempUnit.CELSIUS

    def __init__(self, hass: core.HomeAssistant, device: Probe, config: dict, sensor_type_data: list[Any], name: str = None):
        super().__init__()
        self.device = device
        self.config = config
        self.sensor_name = name
        self.sensor_type_data = sensor_type_data
        self._attr_suggested_object_id = _entity_object_id(
            "Probe", device.serial_number_string, sensor_type_data[0]
        )

        self.unit_type = TempUnit(self.config.get("unit_type", TempUnit.CELSIUS))

        _LOGGER.info(f"Creating entity for {self.sensor_name}")

    async def async_added_to_hass(self):
        """Register callbacks."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, EVENT_REFRESH, self.sync
            )
        )

    @callback
    def sync(self):
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.device.serial_number_string)},
            "name": format_device_name(self.device),
            "manufacturer": "Combustion Inc",
            "model": self.device.sku,
            "sw_version": self.device.firmware_version,
        }

    @property
    def name(self) -> str:
        return self.sensor_name

    @property
    def unique_id(self) -> str:
        return _probe_unique_id(self.device.serial_number_string, self.sensor_type_data)

    @property
    def available(self) -> bool:
        return not getattr(self.device, "stale", False)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.sensor_type_data[1] == "temp":
            return ("°F" if self.unit_type == TempUnit.FAHRENHEIT else "°C")
        else:
            return self.sensor_type_data[1]

    @property
    def device_class(self) -> str | None:
        return self.sensor_type_data[2] or None

    @property
    def state_class(self) -> SensorStateClass | None:
        if self.device_class == SensorDeviceClass.ENUM:
            return None
        return SensorStateClass.MEASUREMENT

    @property
    def options(self) -> list[str] | None:
        if self.device_class == SensorDeviceClass.ENUM:
            return self.sensor_type_data[4]
        return None

    @property
    def native_value(self) -> Any:

        state_id = self.sensor_type_data[3]
        if state_id == "t1":
            return convert_temp(self.device.current_temperatures.values[0], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t2":
            return convert_temp(self.device.current_temperatures.values[1], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t3":
            return convert_temp(self.device.current_temperatures.values[2], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t4":
            return convert_temp(self.device.current_temperatures.values[3], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t5":
            return convert_temp(self.device.current_temperatures.values[4], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t6":
            return convert_temp(self.device.current_temperatures.values[5], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t7":
            return convert_temp(self.device.current_temperatures.values[6], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "t8":
            return convert_temp(self.device.current_temperatures.values[7], self.unit_type) if self.device.current_temperatures else None
        elif state_id == "instant":
            return convert_temp(self.device._instant_read_celsius, self.unit_type)
        elif state_id == "core":
            return convert_temp(self.device.virtual_temperatures.core_temperature, self.unit_type)
        elif state_id == "ambient":
            return convert_temp(self.device.virtual_temperatures.ambient_temperature, self.unit_type)
        elif state_id == "surface":
            return convert_temp(self.device.virtual_temperatures.surface_temperature, self.unit_type)
        elif state_id == "rssi":
            return self.device.rssi
        elif state_id == "battery":
            return self.device.batery_status.name
        elif state_id == "prediction_mode":
            return self.device.prediction_info.prediction_mode.name if self.device.prediction_info else None
        elif state_id == "prediction_state":
            return self.device.prediction_info.prediction_state.name if self.device.prediction_info else None
        elif state_id == "prediction_type":
            return self.device.prediction_info.prediction_type.name if self.device.prediction_info else None
        elif state_id == "prediction_setpoint":
            return convert_temp(self.device.prediction_info.prediction_set_point_temperature, self.unit_type) if self.device.prediction_info else None
        elif state_id == "prediction_through":
            return self.device.prediction_info.percent_through_cook if self.device.prediction_info else None
        elif state_id == "prediction_value":
            return self.device.prediction_info.seconds_remaining if self.device.prediction_info else None
        elif state_id == "prediction_core":
            return convert_temp(self.device.prediction_info.estimated_core_temperature, self.unit_type) if self.device.prediction_info else None
        elif state_id == "hop_count":
            # 0 = direct, 1..4 = via MeatNet hops
            return getattr(self.device, "_hops", None)

        return None


class CombustionNodeEntity(SensorEntity):

    device: MeatNetNode
    sensor_type_data: list[Any]
    config: dict
    sensor_name: str
    _attr_should_poll = False
    unit_type: TempUnit = TempUnit.CELSIUS

    def __init__(
        self,
        hass: core.HomeAssistant,
        device: MeatNetNode,
        config: dict,
        sensor_type_data: list[Any],
        name: str | None = None,
    ):
        super().__init__()
        self.device = device
        self.config = config
        self.sensor_name = name
        self.sensor_type_data = sensor_type_data
        identifier = device.gauge_serial or (
            device.ble_identifier[-5:] if device.ble_identifier else ""
        )
        kind = "MeatNet Node" if sensor_type_data[3] in ("rssi", "probes_count") else "Gauge"
        self._attr_suggested_object_id = _entity_object_id(kind, identifier, sensor_type_data[0])
        self.unit_type = TempUnit(self.config.get("unit_type", TempUnit.CELSIUS))

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, EVENT_REFRESH, self.sync))

    @callback
    def sync(self):
        self.async_write_ha_state()

    @property
    def device_info(self):
        identifier = self.device.ble_identifier or self.device.unique_identifier
        model = "Gauge" if getattr(self.device, "gauge_serial", None) else "MeatNet Node"
        return {
            "identifiers": {(DOMAIN, identifier)},
            "name": format_device_name(self.device),
            "manufacturer": "Combustion Inc",
            "model": model,
        }

    @property
    def name(self) -> str:
        return self.sensor_name

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.device.unique_identifier}_{self.sensor_type_data[3]}"

    @property
    def available(self) -> bool:
        return not getattr(self.device, "stale", False)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.sensor_type_data[1] == "temp":
            return ("°F" if self.unit_type == TempUnit.FAHRENHEIT else "°C")
        return self.sensor_type_data[1]

    @property
    def device_class(self) -> str | None:
        return self.sensor_type_data[2] or None

    @property
    def state_class(self) -> SensorStateClass | None:
        if self.device_class == SensorDeviceClass.ENUM:
            return None
        return SensorStateClass.MEASUREMENT

    @property
    def options(self) -> list[str] | None:
        if self.device_class == SensorDeviceClass.ENUM:
            return self.sensor_type_data[4]
        return None

    @property
    def native_value(self) -> Any:
        state_id = self.sensor_type_data[3]
        if state_id == "rssi":
            return self.device.rssi
        if state_id == "probes_count":
            return sum(not probe.stale for probe in self.device.probes.values())

        if state_id == "gauge_temp":
            temp_c = getattr(self.device, "gauge_temperature_c", None)
            return convert_temp(temp_c, self.unit_type)
        if state_id == "gauge_sensor_present":
            present = getattr(self.device, "gauge_sensor_present", None)
            return None if present is None else ("PRESENT" if present else "NOT_PRESENT")
        if state_id == "gauge_low_battery":
            low = getattr(self.device, "gauge_low_battery", None)
            return None if low is None else ("LOW" if low else "OK")
        if state_id == "gauge_overheating":
            over = getattr(self.device, "gauge_sensor_overheating", None)
            return None if over is None else ("OVERHEATING" if over else "OK")
        if state_id == "gauge_alarm_high_state":
            alarm_status = getattr(self.device, "gauge_alarm_high_raw", None)
            return _decode_gauge_alarm_status_to_state(alarm_status)
        if state_id == "gauge_alarm_low_state":
            alarm_status = getattr(self.device, "gauge_alarm_low_raw", None)
            return _decode_gauge_alarm_status_to_state(alarm_status)
        if state_id == "gauge_alarm_low_temp":
            alarm_status = getattr(self.device, "gauge_alarm_low_raw", None)
            temp_c = _decode_gauge_alarm_status_to_temperature_c(alarm_status)
            return convert_temp(temp_c, self.unit_type)
        if state_id == "gauge_alarm_high_temp":
            alarm_status = getattr(self.device, "gauge_alarm_high_raw", None)
            temp_c = _decode_gauge_alarm_status_to_temperature_c(alarm_status)
            return convert_temp(temp_c, self.unit_type)

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attribs: dict[str, Any] = {"last_seen": self.device.last_update_time.isoformat()}

        if getattr(self.device, "gauge_serial", None):
            attribs["gauge_serial"] = self.device.gauge_serial

        # Add decoded alarm flags for gauge alarm sensors
        if self.sensor_type_data[3] in (
            "gauge_alarm_low_temp",
            "gauge_alarm_high_temp",
            "gauge_alarm_low_state",
            "gauge_alarm_high_state",
        ):
            alarm_status_attr = (
                "gauge_alarm_low_raw"
                if self.sensor_type_data[3] in ("gauge_alarm_low_temp", "gauge_alarm_low_state")
                else "gauge_alarm_high_raw"
            )
            alarm_status = getattr(self.device, alarm_status_attr, None)
            flags = _decode_gauge_alarm_status_flags(alarm_status)
            if flags is not None:
                attribs["alarm_set"] = flags["set"]
                attribs["alarm_tripped"] = flags["tripped"]
                attribs["alarm_alarming"] = flags["alarming"]
                attribs["alarm_status_raw"] = alarm_status

        return attribs
