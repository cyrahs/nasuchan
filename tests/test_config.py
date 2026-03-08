from __future__ import annotations

from pathlib import Path

import pytest

from nasuchan.config import load_config

_VALID_CONFIG = """
[telegram]
bot_token = '123456:telegram-bot-token'
admin_chat_id = 123456789

[backend_api]
base_url = 'https://fav.example.com'
token = 'shared-token'
request_timeout_seconds = 15

[polling]
control_poll_interval_seconds = 2
control_poll_timeout_seconds = 600
notification_poll_interval_seconds = 5
notification_batch_limit = 50

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
    assert config.backend_api.base_url == 'https://fav.example.com'
    assert config.polling.notification_batch_limit == 50


@pytest.mark.parametrize(
    ('needle', 'replacement'),
    [
        ("bot_token = '123456:telegram-bot-token'", "bot_token = ''"),
        ("token = 'shared-token'", "token = ''"),
        ('admin_chat_id = 123456789', 'admin_chat_id = 0'),
        ("base_url = 'https://fav.example.com'", "base_url = 'not-a-url'"),
        ('notification_batch_limit = 50', 'notification_batch_limit = 0'),
    ],
)
def test_invalid_config_values_raise_value_error(tmp_path: Path, needle: str, replacement: str) -> None:
    config_path = write_config(tmp_path, _VALID_CONFIG.replace(needle, replacement))

    with pytest.raises(ValueError, match='Invalid config file'):
        load_config(config_path)
