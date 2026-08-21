"""Modelos de configuración de NiloCardmed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, str):
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    raise TypeError("Se esperaba lista de enteros o string separado por comas")


def _parse_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise TypeError("Se esperaba lista de strings o string separado por comas")


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("Se esperaba un objeto JSON")
        return parsed
    if value is None:
        return {}
    raise TypeError("Se esperaba dict o JSON object")


IntList = Annotated[list[int], BeforeValidator(_parse_int_list)]
StrList = Annotated[list[str], BeforeValidator(_parse_str_list)]
JsonDict = Annotated[dict[str, Any], BeforeValidator(_parse_json_dict)]


class SerSettings(BaseModel):
    """Conexión y contrato HTTP configurable hacia la API REST de SER."""

    enabled: bool = True
    url: str = "http://localhost:8080/api/samples"
    method: Literal["POST", "PUT", "PATCH"] = "POST"

    payload_mode: Literal[
        "multipart",
        "json_base64",
        "json_base64_data_uri",
        "raw_binary",
        "octet_stream",
    ] = "multipart"
    image_field_name: str = "image"
    json_image_field: str = "image_base64"
    filename: str | None = Field(
        default=None,
        description="Nombre de fichero forzado. None = usar el de la muestra",
    )
    content_disposition: str | None = Field(
        default='attachment; filename="{filename}"',
        description="Header Content-Disposition en modos raw_binary",
    )

    extra_fields: JsonDict = Field(
        default_factory=dict,
        description="Campos extra estáticos (multipart data o JSON)",
    )
    headers: JsonDict = Field(
        default_factory=dict,
        description='Headers HTTP extra, p. ej. {"X-Client":"nilocardmed"}',
    )

    auth_type: Literal["none", "bearer", "header", "query", "basic"] = "none"
    api_key: SecretStr | None = None
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    auth_query_param: str = "api_key"
    basic_username: str | None = None
    basic_password: SecretStr | None = None

    device_id: str | None = None
    device_id_field: str = "device_id"
    captured_at_field: str = "captured_at"
    include_captured_at: bool = True

    timeout_seconds: int = Field(default=30, ge=1, le=300)
    verify_ssl: bool = True
    success_status_codes: IntList = Field(default=[200, 201, 202, 204])

    max_retries: int = Field(default=3, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_max_backoff_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    retry_on_status_codes: IntList = Field(default=[408, 429, 500, 502, 503, 504])

    sample_id_response_fields: StrList = Field(
        default=["id", "sample_id", "sampleId"],
        description="Campos JSON de respuesta que identifican la muestra creada",
    )
    max_response_body_log_chars: int = Field(default=500, ge=0, le=10000)
    redact_header_names: StrList = Field(
        default=["authorization", "x-api-key", "api-key"],
        description="Headers a ocultar en dry-run",
    )

    @field_validator("headers", "extra_fields", mode="before")
    @classmethod
    def _empty_dict_if_none(cls, value: Any) -> Any:
        return {} if value is None else value


class WifiSettings(BaseModel):
    """Red WiFi y parámetros de gestión en el dispositivo."""

    enabled: bool = True
    ssid: str | None = None
    password: SecretStr | None = None
    backend: Literal["auto", "host_script", "nmcli", "mock"] = "auto"
    interface: str = "wlan0"
    host_script_path: str = "/host/scripts/wifi-host.sh"
    nmcli_binary: str = "nmcli"
    scan_timeout_seconds: int = Field(default=15, ge=1, le=120)
    connect_timeout_seconds: int = Field(default=30, ge=5, le=300)
    verify_connectivity: bool = True
    connectivity_check_url: str = "http://connectivitycheck.gstatic.com/generate_204"
    connectivity_timeout_seconds: int = Field(default=10, ge=1, le=60)
    persist_to_config: bool = True
    auto_connect_on_startup: bool = False


class SamplingSettings(BaseModel):
    """Parámetros del muestreo periódico de imágenes."""

    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=1)
    monitor_start: int = Field(default=-1, description="Epoch de inicio; -1 = sin límite")
    monitor_end: int = Field(default=-1, description="Epoch de fin; -1 = sin límite")
    after_window_behavior: Literal["stop", "idle"] = Field(
        default="stop",
        description="Comportamiento al superar monitor_end",
    )
    upload_enabled: bool = Field(
        default=True,
        description="Si false, solo captura sin enviar a SER",
    )
    delete_capture_after_upload: bool = True
    keep_capture_on_upload_failure: bool = True
    initial_delay_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)
    tick_sleep_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Granularidad de esperas interruptibles",
    )
    config_reload_seconds: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="Recarga config.json periódicamente; 0 = solo al cambiar mtime",
    )
    max_consecutive_failures: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="0 = sin límite de fallos consecutivos",
    )
    failure_backoff_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=3600.0,
        description="Segundos extra de espera tras un ciclo fallido",
    )


class BluetoothSettings(BaseModel):
    """Servicio Bluetooth BLE/GATT de configuración local."""

    enabled: bool = True
    device_name: str = "NiloCardmed"
    password: SecretStr = Field(default=SecretStr("changeme"))
    backend: Literal["auto", "bluez", "mock"] = "auto"
    adapter: str = "hci0"
    adapter_address: str | None = None
    advertise: bool = True
    appearance: int = Field(default=833, description="Apariencia BLE (Generic Sensor)")
    service_uuid: str = "6e400010-b5a3-f393-e0a9-e50e24dcca9e"
    rx_characteristic_uuid: str = "6e400011-b5a3-f393-e0a9-e50e24dcca9e"
    tx_characteristic_uuid: str = "6e400012-b5a3-f393-e0a9-e50e24dcca9e"
    max_message_bytes: int = Field(default=512, ge=64, le=4096)
    token_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    privileged_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Duración de sesión elevada tras reintroducir contraseña",
    )
    require_auth: bool = True
    allowed_commands_without_auth: StrList = Field(default=["auth"])
    max_response_bytes: int = Field(
        default=4096,
        ge=256,
        le=65536,
        description="Tamaño máximo de respuesta JSON genérica",
    )
    max_image_response_bytes: int = Field(
        default=32768,
        ge=1024,
        le=262144,
        description="Límite para camera_capture_test en mode=base64",
    )
    max_chunk_response_bytes: int = Field(
        default=4096,
        ge=256,
        le=65536,
        description="Límite para cada respuesta camera_capture_chunk",
    )
    capture_test_mode: Literal["base64", "path", "chunked"] = Field(
        default="chunked",
        description="Modo por defecto de camera_capture_test si el cliente no indica mode",
    )
    capture_chunk_size: int = Field(
        default=200,
        ge=64,
        le=2048,
        description="Bytes por chunk al transferir imágenes por BLE",
    )
    ble_framing_enabled: bool = Field(
        default=True,
        description="Fragmenta respuestas JSON que superan el MTU ATT",
    )
    ble_max_notification_bytes: int = Field(
        default=512,
        ge=64,
        le=4096,
        description="Tamaño máximo por notificación BLE (ATT MTU efectivo)",
    )
    ble_frame_payload_bytes: int = Field(
        default=200,
        ge=32,
        le=1024,
        description="Bytes UTF-8 de payload JSON por frame de transporte",
    )
    ble_inter_frame_delay_ms: float = Field(
        default=15.0,
        ge=0.0,
        le=500.0,
        description="Pausa entre notificaciones TX consecutivas",
    )
    dbus_system_bus_address: str | None = Field(
        default=None,
        description="unix:path=/var/run/dbus/system_bus_socket si hace falta",
    )


class CardMedSettings(BaseModel):
    """Configuración de negocio CardMed (parametrizable vía config.json y env)."""

    enabled: bool = True
    site_id: str | None = Field(
        default=None,
        description="Identificador del sitio/instalación (p. ej. para SER)",
    )
    device_label: str | None = Field(
        default=None,
        description="Etiqueta legible del dispositivo para operadores",
    )
    location: str | None = Field(default=None, description="Ubicación física del equipo")
    operator_id: str | None = Field(
        default=None,
        description="Último operador que configuró el sistema",
    )
    sync_device_id_to_ser: bool = Field(
        default=True,
        description="Si true, copia site_id (o device_label) a ser.device_id al configurar",
    )
    metadata: JsonDict = Field(
        default_factory=dict,
        description="Metadatos extra incluidos en envíos SER",
    )
    extra: JsonDict = Field(
        default_factory=dict,
        description="Campos extensibles de negocio (sin validación estricta)",
    )
    test_upload_enabled: bool = Field(
        default=True,
        description="Si true, la prueba CardMed intenta subir la imagen a SER",
    )
    test_require_wifi: bool = Field(
        default=True,
        description="Exige WiFi conectado antes de la prueba",
    )
    test_require_connectivity: bool = Field(
        default=True,
        description="Exige conectividad HTTP antes de la prueba",
    )
    test_min_image_bytes: int = Field(default=1024, ge=0, le=50_000_000)
    test_min_width: int = Field(default=320, ge=0, le=7680)
    test_min_height: int = Field(default=240, ge=0, le=4320)
    test_delete_capture_after_success: bool = False
    test_dry_run_default: bool = Field(
        default=False,
        description="Si true, cardmed_test omite envío salvo dry_run=false explícito",
    )

    @field_validator("metadata", "extra", mode="before")
    @classmethod
    def _empty_dict_if_none(cls, value: Any) -> Any:
        return {} if value is None else value


class ResilienceSettings(BaseModel):
    """Endurecimiento operativo para despliegue en Pi (Fase 9)."""

    enabled: bool = True
    supervisor_tick_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    wifi_reconnect_enabled: bool = Field(
        default=True,
        description="Intenta reconectar WiFi con credenciales guardadas",
    )
    wifi_reconnect_interval_seconds: int = Field(default=120, ge=10, le=3600)
    wifi_reconnect_on_connectivity_loss: bool = True
    log_health_summary_interval_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        description="0 = no loguear resumen periódico",
    )
    check_connectivity_in_health: bool = True
    ser_health_check_enabled: bool = Field(
        default=False,
        description="Probe HTTP a SER en health check (activar cuando URL sea estable)",
    )
    pause_sampling_without_wifi: bool = Field(
        default=True,
        description="Omite ciclos de muestreo si no hay WiFi conectado",
    )
    pause_sampling_without_camera: bool = Field(
        default=True,
        description="Omite ciclos si no hay cámara detectada",
    )
    min_free_disk_mb: int = Field(default=50, ge=0, le=100_000)
    low_memory_mb_threshold: int = Field(
        default=80,
        ge=0,
        le=4096,
        description="0 = no comprobar memoria en health",
    )
    watchdog_enabled: bool = Field(
        default=True,
        description="Reinicia el proceso si el muestreo queda colgado",
    )
    watchdog_max_stale_seconds: int = Field(
        default=1800,
        ge=60,
        le=86400,
        description="Segundos sin ciclo exitoso antes de reinicio",
    )
    watchdog_restart_exit_code: int = Field(default=75, ge=1, le=255)
    pending_retry_interval_seconds: int = Field(
        default=120,
        ge=10,
        le=3600,
        description="Intervalo de reintento de cola pending",
    )
    disk_purge_check_interval_seconds: int = Field(default=60, ge=10, le=3600)
    sampler_thread_supervisor_enabled: bool = Field(
        default=True,
        description="Reinicia el hilo de muestreo si muere o queda colgado",
    )
    sampler_thread_max_stale_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="Segundos sin tick del sampler antes de reiniciar el hilo",
    )
    health_treat_wifi_provisioning_as_degraded: bool = Field(
        default=True,
        description="WiFi sin SSID o desconectado en provisioning = degraded, no unhealthy",
    )
    health_treat_missing_camera_as_degraded: bool = Field(
        default=True,
        description="Sin cámara USB (desconectada) = degraded, no unhealthy",
    )


class StorageSettings(BaseModel):
    """Política de almacenamiento local de capturas."""

    enabled: bool = True
    pending_subdir: str = "pending"
    pending_dir: str | None = Field(
        default=None,
        description="Ruta absoluta pending; None = DATA_DIR/pending_subdir",
    )
    min_free_percent: float = Field(
        default=10.0,
        ge=1.0,
        le=50.0,
        description="Si libre < este %, borrar JPG más antiguos",
    )
    purge_oldest_when_low: bool = True
    delete_after_successful_upload: bool = True
    retry_pending_on_startup: bool = True
    purge_pending_when_low: bool = Field(
        default=False,
        description="Si false, nunca purga pending/ aunque el disco esté bajo",
    )
    pending_upload_max_per_batch: int = Field(
        default=1,
        ge=1,
        le=20,
        description="Máximo de fotos pending subidas por tanda",
    )
    pending_upload_min_interval_seconds: float = Field(
        default=45.0,
        ge=5.0,
        le=600.0,
        description="Separación mínima entre subidas de pending",
    )
    pending_upload_outside_window_only: bool = Field(
        default=True,
        description="Durante ventana activa solo sube pending en huecos del intervalo",
    )
    pending_retry_backoff_base_seconds: float = Field(default=60.0, ge=10.0, le=3600.0)
    pending_retry_backoff_max_seconds: float = Field(default=3600.0, ge=60.0, le=86400.0)


class CameraSettings(BaseModel):
    """Parámetros de detección y captura de cámara USB."""

    device_path: str | None = Field(
        default=None,
        description="Dispositivo V4L2 (/dev/video0). None = autodetectar",
    )
    backend: Literal["auto", "fswebcam", "ffmpeg"] = "auto"
    device_glob: str = "/dev/video*"
    include_non_capture: bool = False
    width: int = Field(default=1280, ge=160, le=7680)
    height: int = Field(default=720, ge=120, le=4320)
    jpeg_quality: int = Field(default=85, ge=1, le=100)
    input_format: str = Field(
        default="mjpeg",
        description="Formato V4L2 para ffmpeg (-input_format)",
    )
    warmup_frames: int = Field(default=2, ge=0, le=30)
    warmup_seconds: int = Field(default=1, ge=0, le=10)
    capture_timeout_seconds: int = Field(default=15, ge=1, le=120)
    discovery_timeout_seconds: int = Field(default=5, ge=1, le=30)
    capture_dir: str | None = Field(
        default=None,
        description="Directorio de capturas. None = DATA_DIR/captures",
    )
    fswebcam_binary: str = "fswebcam"
    ffmpeg_binary: str = "ffmpeg"
    v4l2_ctl_binary: str = "v4l2-ctl"
    output_width: int = Field(
        default=1280,
        ge=160,
        le=7680,
        description="Ancho objetivo tras procesado (reescalado)",
    )
    output_height: int = Field(
        default=720,
        ge=120,
        le=4320,
        description="Alto objetivo tras procesado (reescalado)",
    )
    resize_after_capture: bool = Field(
        default=True,
        description="Reescalar JPEG tras captura si supera output_*",
    )
    resize_only_if_larger: bool = Field(
        default=True,
        description="Solo reescalar si la captura es mayor que output_*",
    )
    jpeg_quality_after_resize: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Calidad JPEG tras reescalado; None = jpeg_quality",
    )
    capture_min_bytes: int = Field(default=1024, ge=256, le=1_000_000)
    capture_max_attempts: int = Field(default=3, ge=1, le=10)
    capture_retry_delay_seconds: float = Field(default=2.0, ge=0.5, le=15.0)


class AppConfig(BaseModel):
    """Configuración completa persistida en disco."""

    ser: SerSettings = Field(default_factory=SerSettings)
    wifi: WifiSettings = Field(default_factory=WifiSettings)
    sampling: SamplingSettings = Field(default_factory=SamplingSettings)
    bluetooth: BluetoothSettings = Field(default_factory=BluetoothSettings)
    cardmed: CardMedSettings = Field(default_factory=CardMedSettings)
    resilience: ResilienceSettings = Field(default_factory=ResilienceSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)


class EnvironmentSettings(BaseSettings):
    """Variables de entorno que sobreescriben la configuración persistida."""

    model_config = SettingsConfigDict(
        env_prefix="NILOCARDMED_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Path("/data")
    log_dir: Path | None = None
    log_level: str = "INFO"
    log_structured: bool = True
    config_filename: str = "config.json"

    ser: SerSettings = Field(default_factory=SerSettings)
    wifi: WifiSettings = Field(default_factory=WifiSettings)
    sampling: SamplingSettings = Field(default_factory=SamplingSettings)
    bluetooth: BluetoothSettings = Field(default_factory=BluetoothSettings)
    cardmed: CardMedSettings = Field(default_factory=CardMedSettings)
    resilience: ResilienceSettings = Field(default_factory=ResilienceSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)

    @property
    def config_path(self) -> Path:
        return self.data_dir / self.config_filename

    def apply_to(self, config: AppConfig) -> AppConfig:
        """Fusiona valores de entorno sobre la configuración cargada."""
        merged = config.model_copy(deep=True)
        for section_name in (
            "ser",
            "wifi",
            "sampling",
            "bluetooth",
            "cardmed",
            "resilience",
            "storage",
            "camera",
        ):
            section_env = getattr(self, section_name)
            section_merged = getattr(merged, section_name)
            defaults = type(section_env)().model_dump(mode="json")
            env_values = section_env.model_dump(mode="json")
            for key, value in env_values.items():
                if value != defaults.get(key):
                    setattr(section_merged, key, getattr(section_env, key))
        return merged

    def public_summary(self) -> dict[str, Any]:
        """Resumen seguro para logs (sin secretos)."""
        return {
            "data_dir": str(self.data_dir),
            "config_path": str(self.config_path),
            "log_dir": str(self.log_dir) if self.log_dir else None,
            "log_level": self.log_level,
            "log_structured": self.log_structured,
            "ser_url": self.ser.url,
            "ser_enabled": self.ser.enabled,
            "ser_payload_mode": self.ser.payload_mode,
            "wifi_ssid": self.wifi.ssid,
            "wifi_enabled": self.wifi.enabled,
            "wifi_backend": self.wifi.backend,
            "sample_interval": self.sampling.interval_seconds,
            "sampling_enabled": self.sampling.enabled,
            "monitor_start": self.sampling.monitor_start,
            "monitor_end": self.sampling.monitor_end,
            "bluetooth_name": self.bluetooth.device_name,
            "bluetooth_enabled": self.bluetooth.enabled,
            "bluetooth_backend": self.bluetooth.backend,
            "camera_device": self.camera.device_path,
            "camera_backend": self.camera.backend,
        }
