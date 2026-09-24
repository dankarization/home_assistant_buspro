"""Pure helpers for Buspro managed-device configuration."""


def managed_unique_ids(devices):
    """Return unique IDs owned by a collection of managed devices."""
    return {
        channel["unique_id"]
        for device in devices
        for channel in device.get("channels", ())
        if channel.get("unique_id")
    }


def removed_managed_unique_ids(old_devices, new_devices):
    """Return only managed entity IDs removed by an options update."""
    return managed_unique_ids(old_devices) - managed_unique_ids(new_devices)


def fixed_channel_count(device_catalog, model):
    """Return a catalogued model's physical channel count, if fixed."""
    spec = device_catalog.get(model)
    if (
        spec is None
        or "channels" not in spec
        or spec.get("configurable_channels", False)
    ):
        return None
    return int(spec["channels"])


def models_for_device_type(device_catalog, device_type):
    """Return catalog models exposed by one GUI device-type choice."""
    return [
        model
        for model, spec in device_catalog.items()
        if spec.get("device_type") == device_type
    ]


def is_channel_configured(name):
    """Return whether a channel name opts the channel into runtime setup."""
    return bool((name or "").strip())


def is_runtime_channel(channel):
    """Return whether a managed channel should create a protocol object."""
    return bool(channel.get("enabled", True))


def channel_device_type(device_type, channel):
    """Return a channel's platform type, falling back to its parent device."""
    return channel.get("device_type", device_type)


def channels_for_device_type(device, device_type):
    """Return channels routed to a Home Assistant platform type."""
    parent_type = device["device_type"]
    return [
        channel
        for channel in device.get("channels", ())
        if channel_device_type(parent_type, channel) == device_type
    ]


def build_channel_records(
    domain,
    address,
    device_type,
    channel_keys,
    names=None,
    existing_channels=None,
    channel_types=None,
):
    """Build channels while preserving existing registry identities."""
    names = names or {}
    existing_channels = existing_channels or {}
    channel_types = channel_types or {}
    address_part = address.replace(".", "_")
    records = []
    for channel in channel_keys:
        name = names.get(channel, "")
        channel_part = str(channel).replace("-", "_")
        existing = existing_channels.get(channel, {})
        effective_type = channel_types.get(channel, device_type)
        record = {
            "number": channel,
            "name": name,
            "enabled": is_channel_configured(name),
            "object_id": existing.get(
                "object_id",
                f"hdl_buspro_{effective_type}_{address_part}_{channel_part}",
            ),
            "unique_id": existing.get(
                "unique_id", f"{domain}-{address}-{effective_type}-{channel}"
            ),
        }
        if channel in channel_types:
            record["device_type"] = effective_type
        records.append(record)
    return records


def registry_disabled_update(channel_enabled, current_disabled_by):
    """Return whether and how integration-owned disabled state should change."""
    if not channel_enabled and current_disabled_by is None:
        return True, "integration"
    if channel_enabled and current_disabled_by == "integration":
        return True, None
    return False, current_disabled_by
