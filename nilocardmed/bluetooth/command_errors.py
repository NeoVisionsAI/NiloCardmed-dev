"""Errores de comandos Bluetooth con códigos estables para clientes."""


class BluetoothCommandError(Exception):
    """Error de negocio en un comando BT (mapeado a respuesta JSON)."""

    def __init__(self, code: str, message: str | None = None, *, data: dict | None = None) -> None:
        self.code = code
        self.message = message or code
        self.data = data
        super().__init__(self.message)

    def as_error_string(self) -> str:
        if self.message == self.code:
            return self.code
        return f"{self.code}: {self.message}"
