"""Bind one Slack message to one already-running demo workflow approval."""

import argparse
import logging
import os
import re
import secrets
import sqlite3
import sys
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from controller import ApprovalError, Controller, ControllerHTTPError


APPROVE = "approve_patch"
HOLD = "reject_patch"
STATE_PATH = "~/.local/state/aap-slack-approval/bindings.sqlite3"
GENERIC_ERROR = "連携処理を完了できませんでした。AAP の承認状態を確認してください。"
# Slack codes outside this list are withheld. Response bodies may contain secrets.
REJECTED_POST = {
    "account_inactive", "channel_not_found", "invalid_arguments", "invalid_auth",
    "invalid_blocks", "is_archived", "missing_scope", "msg_too_long", "no_permission", "no_text",
    "not_allowed_token_type", "not_authed", "not_in_channel", "posting_to_channel_denied",
    "restricted_action", "restricted_action_read_only_channel", "restricted_action_thread_only_channel",
    "token_expired", "token_revoked",
}
SLACK_CODES = REJECTED_POST | {
    "access_denied", "app_not_installed", "fatal_error", "internal_error", "invalid_app_token",
    "invalid_team", "not_allowed", "org_login_required", "rate_limited", "ratelimited",
    "request_timeout", "service_unavailable",
    "slack_connect_not_allowed", "team_not_found", "team_access_not_granted", "user_not_found",
}


class StageError(ApprovalError):
    def __init__(self, stage, error):
        parts = [f"[{stage}] {type(error).__name__}"]
        self.slack_code = None
        self.slack_status = None
        if type(error).__name__ == "SlackApiError":
            response = getattr(error, "response", None)
            get = getattr(response, "get", None)
            code = get("error") if callable(get) else None
            self.slack_code = code if isinstance(code, str) and code in SLACK_CODES else None
            self.slack_status = getattr(response, "status_code", None)
            parts.append(f"Slack API code={self.slack_code or '<未認識・非表示>'}")
            if type(self.slack_status) is int:
                parts.append(f"HTTP {self.slack_status}")
        elif isinstance(error, ApprovalError):
            source = getattr(error, "source_type", None)
            if source:
                parts.append(source)
            parts.append(str(error))
        super().__init__(" — ".join(parts))


@contextmanager
def diagnostic_step(name):
    try:
        yield
    except StageError:
        raise
    except Exception as error:
        raise StageError(name, error) from None


@dataclass(frozen=True)
class Binding:
    team: str
    channel: str
    timestamp: str
    value: str
    status: str = "pending"


class Store:
    def __init__(self, path, controller, *, read_only=False):
        self.path = path
        self.read_only = read_only
        self.key = (controller.origin, controller.approval)
        self.workflow_job = controller.workflow_job
        if read_only:
            return
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS bindings (
                    origin TEXT, approval INTEGER, workflow INTEGER, team TEXT,
                    channel TEXT, timestamp TEXT, value TEXT, status TEXT, actor TEXT,
                    PRIMARY KEY (origin, approval)
                )
            """)

    @contextmanager
    def connect(self):
        filename = self.path.resolve().as_uri() + "?mode=ro" if self.read_only else self.path
        with closing(sqlite3.connect(filename, uri=self.read_only)) as db, db:
            yield db

    def _find(self, db, team, channel):
        row = db.execute(
            "SELECT workflow, team, channel, timestamp, value, status FROM bindings WHERE origin=? AND approval=?",
            self.key,
        ).fetchone()
        if not row:
            return None
        workflow, saved_team, saved_channel, timestamp, value, status = row
        if (workflow, saved_team, saved_channel) != (self.workflow_job, team, channel):
            raise ApprovalError("同じ Approval ID は別の Workflow、Workspace、または通知先に記録済みです。元の設定を確認してください。")
        if status == "posting":
            raise ApprovalError("前回の Slack 投稿結果が未確定です。既存通知を確認できるまで再投稿を止めています。")
        return Binding(saved_team, saved_channel, timestamp, value, status)

    def inspect(self, team, channel):
        if not self.path.exists():
            return None
        with self.connect() as db:
            return self._find(db, team, channel)

    def bind(self, team, channel, publish, *, preflight=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._find(db, team, channel)
            if existing:
                return existing
            if preflight:
                preflight()
            # Commit the reservation before calling Slack. A timeout or a
            # crash after Slack accepts the post must not cause a second post.
            db.execute(
                "INSERT INTO bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (*self.key, self.workflow_job, team, channel, "", "", "posting"),
            )
        try:
            binding = publish()
        except StageError as error:
            if error.slack_code in REJECTED_POST and error.slack_status in (200, 400, 401, 403, 404):
                with self.connect() as db:
                    db.execute("DELETE FROM bindings WHERE origin=? AND approval=? AND status='posting'", self.key)
            raise
        with self.connect() as db:
            saved = db.execute(
                "UPDATE bindings SET timestamp=?, value=?, status=? WHERE origin=? AND approval=? AND status='posting'",
                (binding.timestamp, binding.value, binding.status, *self.key),
            )
            if saved.rowcount != 1:
                raise ApprovalError("Slack 投稿後のローカル記録を確認できません。")
        return binding

    def transition(self, expected, status, actor):
        with self.connect() as db:
            result = db.execute(
                "UPDATE bindings SET status=?, actor=? WHERE origin=? AND approval=? AND status=?",
                (status, actor, *self.key, expected),
            )
            return result.rowcount == 1

    def status(self):
        with self.connect() as db:
            row = db.execute("SELECT status FROM bindings WHERE origin=? AND approval=?", self.key).fetchone()
            return row[0] if row else "missing"


def message_blocks(workflow, approval, value, advisory=None):
    summary = f"AAP デモ承認 | Workflow Job #{workflow} | Approval #{approval}"
    if advisory:
        summary = f"{summary}\nCVE {advisory['cve']} | RHSA {advisory['advisory']} | 対象 {advisory['hostname']} | 重要度 {advisory['severity']}\n実パッチ適用はスキップします。"
    buttons = [
        {"type": "button", "action_id": action, "text": {"type": "plain_text", "text": text}, "value": value}
        for action, text in ((APPROVE, "承認"), (HOLD, "保留"))
    ]
    buttons[0]["style"] = "primary"
    return summary, [
        {"type": "section", "text": {"type": "plain_text", "text": summary}},
        {"type": "actions", "elements": buttons},
    ]


class Relay:
    def __init__(self, controller, store, binding, users, hold_action, before_approve=None):
        self.controller = controller
        self.store = store
        self.binding = binding
        self.users = users
        self.hold_action = hold_action
        self.before_approve = before_approve

    def press(self, body, action):
        workspace = body.get("team") or {}
        user = body.get("user") or {}
        actor = user.get("id")
        if (workspace.get("id") != self.binding.team or not isinstance(actor, str) or actor not in self.users
                or user.get("team_id", self.binding.team) != self.binding.team):
            return "この操作は許可されていません。"
        container = body.get("container") or {}
        value = action.get("value")
        action_id = action.get("action_id")
        if (container.get("type") != "message" or container.get("channel_id") != self.binding.channel
                or container.get("message_ts") != self.binding.timestamp
                or not isinstance(value, str) or value != self.binding.value
                or action_id not in (APPROVE, HOLD)):
            return "この通知と対象の Workflow Approval が一致しません。"
        if self.store.status() != "pending":
            return "この通知は処理済み、または処理中です。AAP の承認状態を確認してください。"

        try:
            if action_id == APPROVE and self.before_approve:
                self.before_approve()
            self.controller.pending()
        except ApprovalError as error:
            return str(error)
        if action_id == HOLD and self.hold_action == "hold":
            if self.store.transition("pending", "held", actor):
                return "保留しました。この通知からの承認受付を終了し、AAP は承認待ちのままです。"
        else:
            if self.store.transition("pending", "claimed", actor):
                decision = "approve" if action_id == APPROVE else "deny"
                return self._decide(decision, actor)
        return "別の操作が先に処理されました。"

    def _decide(self, decision, actor):
        try:
            self.controller.decide(decision)
        except ApprovalError as error:
            # A transport error may mean that the Controller already processed the request.
            # Keep the claim terminal and never send a second POST for this binding.
            self.store.transition("claimed", "unconfirmed", actor)
            if isinstance(error, ControllerHTTPError) and error.status in (400, 409):
                return "AAP が操作を受け付けませんでした。承認済み・拒否・期限切れの状態を確認してください。"
            return f"{error} AAP の承認状態を確認してください。"
        self.store.transition("claimed", decision, actor)
        return "AAP の承認を反映しました。" if decision == "approve" else "保留を AAP の拒否として反映しました。"


def check_slack(client, team, channel, users):
    with diagnostic_step("Slack Bot 認証 auth.test"):
        auth = client.auth_test()
        if auth.get("team_id") != team:
            raise ApprovalError("Bot Token の Workspace が SLACK_TEAM_ID と一致しません。")
        if not auth.get("bot_id"):
            raise ApprovalError("Bot Token として認証できません。")
    print("Slack Bot 認証と Workspace の一致を確認しました。")

    headers = getattr(auth, "headers", None) or {}
    scopes_header = next((value for name, value in headers.items() if name.lower() == "x-oauth-scopes"), None)
    scopes = {scope.strip() for scope in scopes_header.split(",")} if isinstance(scopes_header, str) else set()
    with diagnostic_step("Slack Bot 投稿権限"):
        if scopes and "chat:write" not in scopes:
            raise ApprovalError("Bot Token に chat:write がありません。")
    print("Slack Bot の chat:write を確認しました。" if scopes else "Slack Bot の scope を応答から確認できません。")

    if scopes & {"channels:read", "groups:read", "im:read", "mpim:read"}:
        try:
            with diagnostic_step("Slack チャンネル確認 conversations.info"):
                details = client.conversations_info(channel=channel).get("channel")
                if not isinstance(details, dict) or details.get("id") != channel:
                    raise ApprovalError("SLACK_CHANNEL_ID のチャンネルを確認できません。")
                if details.get("is_archived") is True:
                    raise ApprovalError("指定チャンネルはアーカイブ済みです。")
                public_post = details.get("is_channel") is True and not details.get("is_private") and "chat:write.public" in scopes
                if details.get("is_member") is False and not public_post:
                    raise ApprovalError("Bot が指定チャンネルに参加していません。Slack で Bot を招待してください。")
                if details.get("is_member") is True:
                    print("Slack チャンネルへの Bot 参加を確認しました。")
                elif public_post:
                    print("公開チャンネルへの投稿に chat:write.public を利用できます。")
                else:
                    print("Slack API 応答にチャンネル参加状態がありません。")
        except StageError as error:
            if error.slack_code != "missing_scope":
                raise
            print(f"{error}。チャンネルへの Bot 参加は Slack 画面で確認してください。")
    else:
        print("チャンネルへの Bot 参加は Slack 画面で確認してください。Bot Token にチャンネル情報の読み取り権限がありません。")

    if "users:read" in scopes:
        with diagnostic_step("Slack 許可ユーザー確認 users.info"):
            for user_id in sorted(users):
                member = client.users_info(user=user_id).get("user")
                if (not isinstance(member, dict) or member.get("id") != user_id
                        or member.get("team_id", team) != team or member.get("deleted") or member.get("is_bot")):
                    raise ApprovalError("許可リストに対象 Workspace の有効なユーザーとして確認できない ID があります。")
        print("許可ユーザーが対象 Workspace の有効なユーザーであることを確認しました。")
    else:
        print("許可ユーザー ID の形式を確認しました。所属と許可リストはクリック時に照合します。")


def validate_slack_settings(team, channel, allowed_users, hold_action):
    with diagnostic_step("Slack Workspace・通知先・許可ユーザー設定"):
        for name, value, pattern in (
            ("SLACK_TEAM_ID", team, r"T[A-Z0-9]+"),
            ("SLACK_CHANNEL_ID", channel, r"[CGD][A-Z0-9]+"),
        ):
            if not re.fullmatch(pattern, value):
                raise ApprovalError(f"{name} の形式を確認してください。")
        users = {part.strip() for part in allowed_users.split(",") if part.strip()}
        if not users or any(not re.fullmatch(r"[UW][A-Z0-9]+", user) for user in users):
            raise ApprovalError("SLACK_ALLOWED_USER_IDS に Slack ユーザー ID をカンマ区切りで指定してください。")
        if hold_action not in ("hold", "deny"):
            raise ApprovalError("AAP_HOLD_ACTION には hold または deny を指定してください。")
        return users


def serve(controller=None, *, team=None, channel=None, users=None, hold_action="hold", diagnose=False,
          bot_token=None, app_token=None, advisory=None, before_approve=None, monitor=None, event_session=None):
    with diagnostic_step("Slack Bolt 依存ライブラリ"):
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
        from slack_sdk import WebClient

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "") if bot_token is None else bot_token
    app_token = os.environ.get("SLACK_APP_TOKEN", "") if app_token is None else app_token
    with diagnostic_step("Slack トークン設定"):
        if not bot_token.startswith("xoxb-") or not app_token.startswith("xapp-"):
            raise ApprovalError("SLACK_BOT_TOKEN と SLACK_APP_TOKEN を確認してください。")

    # SDK exceptions can include API responses. Report only fixed messages.
    logger = logging.getLogger("aap-slack-approval")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    with diagnostic_step("Slack API クライアント初期化"):
        client = WebClient(token=bot_token, logger=logger, timeout=10, retry_handlers=[])
    if controller is not None or event_session is not None:
        check_slack(client, team, channel, users)
    state_path = Path(os.environ.get("AAP_SLACK_STATE_PATH", STATE_PATH)).expanduser()
    if diagnose:
        with diagnostic_step("Slack Socket Mode 事前確認 apps.connections.open"):
            result = client.apps_connections_open(app_token=app_token)
            url = result.get("url")
            if not isinstance(url, str) or not url.startswith("wss://"):
                raise ApprovalError("Socket Mode の接続先を確認できません。")
        print("Socket Mode の App Token を確認しました。WebSocket は接続していません。")
        with diagnostic_step("ローカル通知記録"):
            existing = Store(state_path, controller, read_only=True).inspect(team, channel)
        print("既存の通知記録を確認しました。" if existing else "既存の通知記録はありません。")
        return

    with diagnostic_step("Slack Bolt 初期化"):
        app = App(client=client, logger=logger, token_verification_enabled=controller is None and event_session is None)
    relay = None
    if controller is not None:
        with diagnostic_step("ローカル通知記録"):
            store = Store(state_path, controller)

        def publish():
            value = f"aap:{controller.workflow_job}:{controller.approval}:{secrets.token_urlsafe(24)}"
            text, blocks = message_blocks(controller.workflow_job, controller.approval, value, advisory)
            with diagnostic_step("Slack 投稿 chat.postMessage"):
                result = client.chat_postMessage(channel=channel, text=text, blocks=blocks)
                timestamp = result.get("ts")
                if result.get("channel") != channel or not isinstance(timestamp, str) or not re.fullmatch(r"\d+\.\d+", timestamp):
                    raise ApprovalError("Slack 投稿結果が未確定です。投稿先またはメッセージ ID を確認できません。")
            print("Slack 通知を投稿しました。")
            return Binding(team, channel, timestamp, value)

        def verify_pending():
            with diagnostic_step("AAP API 承認待ち確認"):
                controller.pending()
            print("AAP API で対象 Workflow の承認待ちを確認しました。")

        with diagnostic_step("ローカル通知記録"):
            binding = store.bind(team, channel, publish, preflight=verify_pending)
        relay = Relay(controller, store, binding, users, hold_action, before_approve)
        print(f"Workflow Job #{controller.workflow_job} / Approval #{controller.approval} の通知を待ち受けます ({binding.status})。")
    elif event_session is not None:
        relay, store = event_session(client)

    @app.action(APPROVE)
    @app.action(HOLD)
    def handle(ack, body, action, respond):
        ack()
        try:
            if relay is None:
                message = "操作を受信しました。AAP に連携するには対象 ID を指定して --serve を使用してください。"
            else:
                message = relay.press(body, action)
        except Exception as error:
            print(StageError("Slack ボタン処理", error), file=sys.stderr)
            message = GENERIC_ERROR
        respond(text=message, response_type="ephemeral")

    @app.error
    def handle_slack_error(error):
        print(StageError("Slack 操作応答", error), file=sys.stderr)

    print("Slack Socket Mode を開始します。")
    with diagnostic_step("Slack Socket Mode 開始"):
        handler = SocketModeHandler(app, app_token, logger=logger)
        if monitor is None:
            return handler.start()
    try:
        with diagnostic_step("Slack Socket Mode 開始"):
            handler.connect()
        return monitor(store)
    finally:
        handler.close()


def main():
    parser = argparse.ArgumentParser(description="Slack から起動済みの AAP デモ Workflow の承認を処理します。")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="AAP 上の対象と承認待ち状態を読み取りで確認")
    modes.add_argument("--diagnose", action="store_true", help="AAP、Slack 設定、通知記録を投稿せずに診断")
    modes.add_argument("--serve", action="store_true", help="対象に紐づく通知を投稿して操作を待ち受ける")
    parser.add_argument("--workflow-job-id", type=int)
    parser.add_argument("--approval-id", type=int)
    args = parser.parse_args()

    try:
        if not (args.check or args.serve or args.diagnose):
            if args.workflow_job_id is not None or args.approval_id is not None:
                parser.error("対象 ID を指定する場合は --check、--diagnose、または --serve が必要です。")
            serve()
            return 0
        if args.workflow_job_id is None or args.approval_id is None:
            parser.error("--workflow-job-id と --approval-id が必要です。")
        with diagnostic_step("AAP API 接続・バージョン確認"):
            controller = Controller(
                os.environ.get("AAP_URL", ""), os.environ.get("AAP_OAUTH_TOKEN", ""),
                args.workflow_job_id, args.approval_id,
            )
            controller.check_version()
        print("AAP API の接続と Controller バージョンを確認しました。")
        pending_failed = False
        if args.check or args.diagnose:
            try:
                with diagnostic_step("AAP API 承認待ち確認"):
                    controller.pending()
                print(f"Workflow Job #{controller.workflow_job} / Approval #{controller.approval} が承認待ちです。")
            except StageError as error:
                if args.check:
                    raise
                print(error, file=sys.stderr)
                pending_failed = True
        if args.check:
            return 0

        team = os.environ.get("SLACK_TEAM_ID", "")
        channel = os.environ.get("SLACK_CHANNEL_ID", "")
        hold_action = os.environ.get("AAP_HOLD_ACTION", "hold")
        users = validate_slack_settings(team, channel, os.environ.get("SLACK_ALLOWED_USER_IDS", ""), hold_action)
        serve(controller, team=team, channel=channel, users=users, hold_action=hold_action, diagnose=args.diagnose)
        return int(pending_failed)
    except ApprovalError as error:
        print(error, file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(StageError("連携起動", error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
