"""Excepciones del módulo WiFi."""


class WifiError(Exception):
    """Error base de WiFi."""


class WifiBackendError(WifiError):
    """Backend WiFi no disponible o falló."""


class WifiConfigError(WifiError):
    """Configuración WiFi inválida."""


class WifiConnectionError(WifiError):
    """No se pudo conectar a la red solicitada."""

    def __init__(
        self,
        message: str,
        *,
        restored_previous: bool = False,
        previous_ssid: str | None = None,
        attempted_ssid: str | None = None,
    ) -> None:
        super().__init__(message)
        self.restored_previous = restored_previous
        self.previous_ssid = previous_ssid
        self.attempted_ssid = attempted_ssid
