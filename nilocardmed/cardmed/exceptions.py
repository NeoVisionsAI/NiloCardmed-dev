"""Excepciones del módulo CardMed."""


class CardMedError(Exception):
    """Error base de CardMed."""


class CardMedConfigError(CardMedError):
    """Configuración CardMed inválida."""


class CardMedValidationError(CardMedError):
    """Validación de prueba CardMed fallida."""


class CardMedTestError(CardMedError):
    """Error durante la prueba end-to-end CardMed."""
