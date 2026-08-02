"""Excepciones del módulo Bluetooth."""


class BluetoothError(Exception):
    """Error base de Bluetooth."""


class BluetoothBackendError(BluetoothError):
    """Backend Bluetooth no disponible o falló."""


class BluetoothConfigError(BluetoothError):
    """Configuración Bluetooth inválida."""


class BluetoothProtocolError(BluetoothError):
    """Mensaje de protocolo inválido."""


class BluetoothAuthError(BluetoothError):
    """Error de autenticación."""
