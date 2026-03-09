from __future__ import annotations

from pathlib import Path

import pytest

from nasuchan.config import load_config

_VALID_CONFIG = """
[telegram]
bot_token = '123456:telegram-bot-token'
admin_chat_id = 123456789

[backend.fav]
base_url = 'https://fav.example.com'
token = 'shared-token'
request_timeout_seconds = 15

[polling]
control_poll_interval_seconds = 2
control_poll_timeout_seconds = 600

[logging]
level = 'INFO'
"""

_VALID_ANINAMER_ONLY_CONFIG = """
[telegram]
bot_token = '123456:telegram-bot-token'
admin_chat_id = 123456789

[backend.aninamer]
base_url = 'https://aninamer.example.com'
token = 'aninamer-token'
request_timeout_seconds = 15

[polling]
control_poll_interval_seconds = 2
control_poll_timeout_seconds = 600

[logging]
level = 'INFO'
"""

_VALID_CONFIG_WITH_PUBLIC_API = """
[telegram]
bot_token = '123456:telegram-bot-token'
admin_chat_id = 123456789

[backend.fav]
base_url = 'https://fav.example.com'
token = 'shared-token'
request_timeout_seconds = 15

[public_api]
bind = '127.0.0.1'
port = 8092
token = 'public-runtime-api-token'

[polling]
control_poll_interval_seconds = 2
control_poll_timeout_seconds = 600

[logging]
level = 'INFO'
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / 'config.toml'
    path.write_text(text, encoding='utf-8')
    return path


def test_load_config_from_root_toml(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, _VALID_CONFIG))

    assert config.telegram.admin_chat_id == 123456789
    assert config.backend.fav is not None
    assert config.backend.fav.base_url == 'https://fav.example.com'
    assert config.backend.aninamer is None
    assert config.polling.control_poll_timeout_seconds == 600
    assert config.public_api is None


def test_load_config_with_aninamer_only_backend(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, _VALID_ANINAMER_ONLY_CONFIG))

    assert config.backend.fav is None
    assert config.backend.aninamer is not None
    assert config.backend.aninamer.base_url == 'https://aninamer.example.com'


def test_load_config_with_public_api_section(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, _VALID_CONFIG_WITH_PUBLIC_API))

    assert config.public_api is not None
    assert config.public_api.bind == '127.0.0.1'
    assert config.public_api.port == 8092


@pytest.mark.parametrize(
    ('needle', 'replacement'),
    [
        ("bot_token = '123456:telegram-bot-token'", "bot_token = ''"),
        ("token = 'shared-token'", "token = ''"),
        ('admin_chat_id = 123456789', 'admin_chat_id = 0'),
        ("base_url = 'https://fav.example.com'", "base_url = 'not-a-url'"),
        ('control_poll_interval_seconds = 2', 'control_poll_interval_seconds = 0'),
    ],
)
def test_invalid_config_values_raise_value_error(tmp_path: Path, needle: str, replacement: str) -> None:
    config_path = write_config(tmp_path, _VALID_CONFIG.replace(needle, replacement))

    with pytest.raises(ValueError, match='Invalid config file'):
        load_config(config_path)


@pytest.mark.parametrize(
    ('needle', 'replacement'),
    [
        ("token = 'public-runtime-api-token'", "token = ''"),
        ('port = 8092', 'port = 70000'),
    ],
)
def test_invalid_public_api_values_raise_value_error(tmp_path: Path, needle: str, replacement: str) -> None:
    config_path = write_config(tmp_path, _VALID_CONFIG_WITH_PUBLIC_API.replace(needle, replacement, 1))

    with pytest.raises(ValueError, match='Invalid config file'):
        load_config(config_path)
