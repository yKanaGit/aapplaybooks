"""Discover a pending AAP demo approval and start the existing Slack Bolt app."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo_config import ConfigError, load_config, prompt_secret, prompt_value, save_config


INSTALL = "python3 -m pip install -r slack-bolt/requirements.txt"


def check_environment():
    if sys.version_info < (3, 10) or sys.prefix == sys.base_prefix:
        raise ConfigError("Python 3.10 以降の仮想環境を有効にしてください。初回セットアップは SETUP.md を参照してください。")
    requirements = ROOT / "slack-bolt" / "requirements.txt"
    if not requirements.is_file():
        raise ConfigError("slack-bolt/requirements.txt が見つかりません。")
    # pip resolves the trusted repository requirements locally and cannot install anything.
    pip = [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check"]
    for args in (
        ["install", "--dry-run", "--no-index", "--no-cache-dir", "-r", str(requirements)],
        ["check"],
    ):
        try:
            check = subprocess.run(
                pip + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                env={**os.environ, "PIP_CONFIG_FILE": os.devnull},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ConfigError(f"依存関係を確認できません。仮想環境で {INSTALL} を実行してください。") from None
        if check.returncode:
            raise ConfigError(f"仮想環境の依存関係が不足または不整合です。{INSTALL} を実行してください。")


def choose(candidates):
    if len(candidates) == 1:
        return candidates[0]
    while True:
        value = input("使用する候補の番号を入力（空欄で中止） > ").strip()
        if not value:
            return None
        if value.isascii() and value.isdecimal() and len(value) <= len(str(len(candidates))):
            index = int(value)
            if 1 <= index <= len(candidates):
                return candidates[index - 1]
        print("一覧にある候補番号を指定してください。")


def slack_preferences(local):
    from slack_app import validate_slack_settings

    def setting(label, key, env, default=""):
        return prompt_value(label, os.environ.get(env) or local.get(key) or default)

    team = setting("Slack Team ID", "slack_team_id", "SLACK_TEAM_ID")
    channel = setting("Slack Channel ID", "slack_channel_id", "SLACK_CHANNEL_ID")
    allowed = setting("Slack Allowed User IDs（カンマ区切り）", "slack_allowed_user_ids", "SLACK_ALLOWED_USER_IDS")
    hold = setting("保留時の動作（hold / deny）", "hold_action", "AAP_HOLD_ACTION", "hold")
    users = validate_slack_settings(team, channel, allowed, hold)
    return team, channel, users, hold


def launch(args):
    from controller import ApprovalError, Controller
    from slack_app import serve

    local = load_config()
    origin = prompt_value("AAP Controller または Gateway URL", os.environ.get("AAP_URL") or local.get("aap_url", ""))
    aap_token = prompt_secret("Slack 承認用 AAP OAuth トークン", "AAP_OAUTH_TOKEN")
    controller = Controller(origin, aap_token)
    version = controller.check_version()
    print(f"Controller {version}、API {controller.api_root}")
    candidates, reasons = controller.discover_pending()
    for reason in reasons:
        print(f"  - {reason}")
    if not candidates:
        print("承認待ちの候補がありません。AAP で Workflow 実行履歴と承認権限を確認してください。")
        return 1
    for index, (workflow_job, approval) in enumerate(candidates, 1):
        print(f"  {index}. Workflow Job ID {workflow_job} / Approval ID {approval}")
    if args.dry_run and not args.save_config:
        print("Dry Run 完了。AAP への書き込みと Slack 投稿は 0 件です。")
        return 0

    selected = None if args.dry_run else choose(candidates)
    if not args.dry_run and selected is None:
        print("起動を中止しました。")
        return 1

    team, channel, users, hold = slack_preferences(local)
    updates = {
        "aap_url": controller.origin, "slack_team_id": team, "slack_channel_id": channel,
        "slack_allowed_user_ids": ",".join(sorted(users)), "hold_action": hold,
    }

    if args.dry_run:
        save_config(updates, secrets=(aap_token,))
        print("Dry Run 完了。AAP への書き込みと Slack 投稿は 0 件です。")
        return 0

    target = controller.for_approval(*selected)
    target.pending()
    if input(f"Workflow Job ID {target.workflow_job} / Approval ID {target.approval} を待ち受ける場合は START と入力 > ") != "START":
        print("起動を中止しました。")
        return 1
    bot_token = prompt_secret("Slack Bot Token", "SLACK_BOT_TOKEN")
    app_token = prompt_secret("Slack App Token", "SLACK_APP_TOKEN")
    if not bot_token.startswith("xoxb-") or not app_token.startswith("xapp-"):
        raise ApprovalError("Slack Bot Token と App Token の種類を確認してください。")
    if args.save_config:
        save_config(updates, secrets=(aap_token, bot_token, app_token))
    serve(target, team=team, channel=channel, users=users, hold_action=hold, bot_token=bot_token, app_token=app_token)
    return 0


def main():
    parser = argparse.ArgumentParser(prog="start-slack-demo.sh", description="承認待ちの AAP Workflow を検索して Slack Bolt を起動します。")
    parser.add_argument("--dry-run", action="store_true", help="AAP を読み取り、承認候補だけを表示します。")
    parser.add_argument("--save-config", action="store_true", help="確認後に非秘密設定をローカルへ保存します。")
    args = parser.parse_args()
    try:
        check_environment()
        from controller import ApprovalError
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    except ImportError:
        print(f"必要な Python モジュールを読み込めません。{INSTALL} を実行してください。", file=sys.stderr)
        return 1
    try:
        return launch(args)
    except (ApprovalError, ConfigError) as error:
        print(error, file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\n起動を中止しました。", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Slack デモ起動エラー ({type(error).__name__})。TROUBLESHOOTING.md を確認してください。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
