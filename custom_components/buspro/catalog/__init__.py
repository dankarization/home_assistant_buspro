"""Buspro device-catalog subpackage."""

from .climate import CLIMATE_MODELS
from .dimmer import DIMMER_MODELS
from .infrastructure import INFRASTRUCTURE_MODELS
from .mixed_output import MIXED_OUTPUT_MODELS
from .output import OUTPUT_MODELS
from .panel import PANEL_MODELS
from .relay import RELAY_MODELS
from .sensor import SENSOR_MODELS
from .model_notes import MODEL_NOTES, emit_model_support_notes

DEVICE_CATALOG = {
    **INFRASTRUCTURE_MODELS,
    **RELAY_MODELS,
    **DIMMER_MODELS,
    **MIXED_OUTPUT_MODELS,
    **SENSOR_MODELS,
    **PANEL_MODELS,
    **CLIMATE_MODELS,
    **OUTPUT_MODELS,
}
