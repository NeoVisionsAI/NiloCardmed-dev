"""Excepciones del cliente SER."""


class SerClientError(Exception):
    """Error base del cliente hacia SER."""


class SerUploadError(SerClientError):
    """Fallo al enviar una muestra a SER."""


class SerConfigError(SerClientError):
    """Configuración SER inválida o incompleta."""
