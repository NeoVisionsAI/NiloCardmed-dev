"""Excepciones del módulo WiFi."""


class WifiError(Exception):
    """Error base de WiFi."""


class WifiBackendError(WifiError):
    """Backend WiFi no disponible o falló."""


class WifiConfigError(WifiError):
    """Configuración WiFi inválida."""


class WifiConnectionError(WifiError):
    """No se pudo conectar a la red solicitada."""
