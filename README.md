# Combustion Inc Integration for Home Assistant

This integration sets up a single [Combustion Inc.](https://combustion.inc) entry. It creates a device for each detected probe or MeatNet node, including Gauges. Each probe has 22 sensors; Gauges have additional temperature and alarm sensors.

Sensors become unavailable when no Bluetooth advertisement or status update has been received for 60 seconds. Home Assistant's Bluetooth integration provides the scan data.

The integration uses the local Bluetooth MeatNet as needed. It prefers a direct probe connection unless a connected MeatNet repeater offers a stronger signal.

## Installation

Copy `custom_components/combustion` into your Home Assistant `custom_components` directory, restart Home Assistant, and add **Combustion Inc** under Devices & Services. Bluetooth discovery can also prompt you to add it.

### Dev Container (VS Code) with Bluetooth (Windows / WSL / Docker Desktop)

To access a host Bluetooth adapter from inside the devcontainer you need BlueZ and access to the host D-Bus system bus.

The provided `.devcontainer/devcontainer.json` now:
* Installs `bluez`, `dbus`, and `bluetooth` packages.
* Adds Linux capabilities (NET_ADMIN / NET_RAW, etc.) and USB device passthrough.
* Binds the host D-Bus socket at `/var/run/dbus` so BlueZ inside the container talks to the host bluetoothd.

Notes / Troubleshooting:
1. Windows + Docker Desktop: Enable experimental Bluetooth device sharing (currently requires Docker Desktop >= 4.27) OR use WSL and run VS Code "Dev Containers" against the WSL distro where the adapter is visible.
2. If `bluetoothd` is not running on the host, the container will not be able to interact with BLE. On most Linux hosts: `sudo systemctl status bluetooth`.
3. Verify inside the container: `bluetoothctl list` should show at least one controller. Then `bluetoothctl scan on` to see advertisements.
4. If the D-Bus mount is missing you'll see errors like `org.bluez.Error.NotReady`. Rebuild the container after ensuring the host path `/var/run/dbus/system_bus_socket` exists.
5. For USB BLE dongles ensure they are passed through: On Linux this usually works via `--device=/dev/bus/usb`; on Windows you may need to enable USB device sharing in Docker Desktop settings.

Security: The devcontainer grants additional capabilities. If you don't need BLE, you can comment out the `runArgs` and `mounts` entries to reduce privileges.

## Credits
This integration is just a wrapper around the excellent python library [combustion_ble](https://github.com/legrego/combustion_ble). All of the heavy lifting is done by that library, and simply exposed as sensors here.
