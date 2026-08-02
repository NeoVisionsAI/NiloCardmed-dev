"""Gestión de configuración persistente del dispositivo."""

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig

__all__ = ["AppConfig", "ConfigManager"]
