"""Actuator models with different output types on one physical address."""

from ..const import (
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_MIXED_OUTPUT,
    DEVICE_TYPE_RELAY,
)


_MHIOU_CHANNEL_TYPES = {
    1: DEVICE_TYPE_DIMMER,
    2: DEVICE_TYPE_DIMMER,
    **{channel: DEVICE_TYPE_RELAY for channel in range(3, 13)},
}


MIXED_OUTPUT_MODELS = {
    model: {
        "device_type": DEVICE_TYPE_MIXED_OUTPUT,
        "channels": 12,
        "channel_types": dict(_MHIOU_CHANNEL_TYPES),
    }
    for model in ("HDL-MHIOU.432", "HDL-MHIOU-II.432")
}
