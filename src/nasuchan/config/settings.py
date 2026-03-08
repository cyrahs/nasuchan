from __future__ import annotations

from pathlib import Path
import tomllib

from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, ValidationError, field_validator, model_validator

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_DEFAULT_CONFIG_PATH = Path('./config.toml')


class TelegramSettings(BaseModel):
    bot_token: str
    admin_chat_id: int

    @field_validator('bot_token')
    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = 'telegram.bot_token cannot be empty'
            raise ValueError(msg)
        return normalized

    @field_validator('admin_chat_id')
    @classmethod
    def validate_admin_chat_id(cls, value: int) -> int:
        if value <= 0:
            msg = 'telegram.admin_chat_id must be greater than 0'
            raise ValueError(msg)
        return value


class BackendApiSettings(BaseModel):
    base_url: str
    token: str
    request_timeout_seconds: float = 15

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip('/')
        if not normalized:
            msg = 'backend_api.base_url cannot be empty'
            raise ValueError(msg)
        _HTTP_URL_ADAPTER.validate_python(normalized)
        return normalized

    @field_validator('token')
    @classmethod
    def validate_token(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = 'backend_api.token cannot be empty'
            raise ValueError(msg)
        return normalized

    @field_validator('request_timeout_seconds')
    @classmethod
    def validate_request_timeout_seconds(cls, value: float) -> float:
        if value <= 0:
            msg = 'backend_api.request_timeout_seconds must be greater than 0'
            raise ValueError(msg)
        return value


class PollingSettings(BaseModel):
    control_poll_interval_seconds: float = 2
    control_poll_timeout_seconds: float = 600
    notification_poll_interval_seconds: float = 5
    notification_batch_limit: int = 50

    @field_validator(
        'control_poll_interval_seconds',
        'control_poll_timeout_seconds',
        'notification_poll_interval_seconds',
    )
    @classmethod
    def validate_positive_seconds(cls, value: float) -> float:
        if value <= 0:
            msg = 'polling intervals must be greater than 0'
            raise ValueError(msg)
        return value

    @field_validator('notification_batch_limit')
    @classmethod
    def validate_notification_batch_limit(cls, value: int) -> int:
        if not (1 <= value <= 200):
            msg = 'polling.notification_batch_limit must be between 1 and 200'
            raise ValueError(msg)
        return value

    @model_validator(mode='after')
    def validate_timeout_vs_interval(self) -> PollingSettings:
        if self.control_poll_timeout_seconds < self.control_poll_interval_seconds:
            msg = 'polling.control_poll_timeout_seconds must be greater than or equal to control_poll_interval_seconds'
            raise ValueError(msg)
        return self


class LoggingSettings(BaseModel):
    level: str = 'INFO'

    @field_validator('level')
    @classmethod
    def validate_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'}
        if normalized not in allowed:
            msg = f'logging.level must be one of {sorted(allowed)}'
            raise ValueError(msg)
        return normalized


class AppConfig(BaseModel):
    telegram: TelegramSettings
    backend_api: BackendApiSettings
    polling: PollingSettings = Field(default_factory=PollingSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def load_config(path: Path = _DEFAULT_CONFIG_PATH) -> AppConfig:
    if not path.is_file():
        msg = f'Config file not found: {path}'
        raise FileNotFoundError(msg)
    with path.open('rb') as handle:
        raw_config = tomllib.load(handle)
    try:
        return AppConfig.model_validate(raw_config)
    except ValidationError as exc:
        msg = f'Invalid config file at {path}'
        raise ValueError(msg) from exc
