"""Cliente HTTP hacia la API REST de SER."""

from nilocardmed.ser_client.client import SerClient
from nilocardmed.ser_client.exceptions import SerClientError, SerConfigError, SerUploadError
from nilocardmed.ser_client.models import SamplePayload, UploadResult

__all__ = [
    "SamplePayload",
    "SerClient",
    "SerClientError",
    "SerConfigError",
    "SerUploadError",
    "UploadResult",
]
