"""Shared, non-secret configuration for the Mac demo launchers."""

import getpass
import json
import os
import re
import tempfile
import warnings
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / ".demo-local" / "config.json"
FIELDS = {
    "aap_url", "organization", "inventory", "host", "ssh_credential", "execution_environment",
    "github_url", "slack_team_id", "slack_channel_id", "slack_allowed_user_ids", "hold_action",
}
SECRET_PATTERN = re.compile(r"xox[baprs]-|xapp-|hooks\.slack(?:-gov)?\.com/services/|-----BEGIN .*PRIVATE KEY", re.I)


class ConfigError(Exception):
    pass


def validate_config(config):
    if not isinstance(config, dict) or config.keys() - FIELDS:
        raise ConfigError("ローカル設定にはテンプレートにある非秘密項目だけを指定してください。")
    for key, value in config.items():
        if not isinstance(value, str) or any(ord(char) < 32 for char in value) or SECRET_PATTERN.search(value):
            raise ConfigError(f"ローカル設定の {key} に保存できない値があります。")
        if value and key in {"aap_url", "github_url"}:
            try:
                url = urlsplit(value)
                valid = url.scheme == "https" and url.hostname and url.username is None                     and not url.query and not url.fragment and not any(char.isspace() for char in value)
            except ValueError:
                valid = False
            if not valid:
                raise ConfigError(f"ローカル設定の {key} には認証情報を含まない HTTPS URL を指定してください。")
    return config


def load_config(path=None):
    path = CONFIG_PATH if path is None else path
    try:
        with path.open(encoding="utf-8") as stream:
            return validate_config(json.load(stream))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ConfigError("ローカル設定 config.json を読み込めません。JSON の形式とファイル権限を確認してください。") from None


def prompt_value(label, default=""):
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix} > ").strip() or default


def prompt_secret(label, env=None):
    if env and os.environ.get(env):
        return os.environ[env]
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(f"{label} > ").strip()
        except getpass.GetPassWarning:
            raise ConfigError("秘密情報は非表示入力が使える端末で入力してください。") from None


def save_config(updates, *, secrets=(), confirm=None, path=None):
    path = CONFIG_PATH if path is None else path
    config = validate_config({**load_config(path), **updates})
    if any(secret and secret in value for value in config.values() for secret in secrets):
        raise ConfigError("保存予定の設定に入力済みの秘密値が含まれています。")
    print(f"保存先 {path}")
    for key, value in sorted(updates.items()):
        print(f"  {key} = {value}")
    if (confirm or input)("上の非秘密設定を保存する場合は SAVE と入力 > ") != "SAVE":
        print("ローカル設定の保存を中止しました。")
        return False
    if path.parent.is_symlink():
        raise ConfigError("ローカル設定ディレクトリーにシンボリックリンクは使用できません。")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
    print("非秘密設定を保存しました。")
    return True
