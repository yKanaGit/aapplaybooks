"""Run one read-only RHEL patch demonstration from launch through Slack approval."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from start_demo import ROOT, check_environment, slack_preferences
from demo_config import ConfigError, load_config, prompt_secret, prompt_value


ACTIVE = {"new", "pending", "waiting", "running"}
TERMINAL = {"successful", "failed", "error", "canceled"}
NODES = ("patch-check", "patch-apply")
EVENT_FIELDS = ("hostname", "advisory", "cve", "severity", "source")
DETECTION_SOURCES = {
    "satellite": "Red Hat Satellite (simulated)",
    "lightspeed": "Red Hat Lightspeed (simulated)",
}
SAFE_PLAYBOOK_SHA256 = "86121712f034cfad1262ecb88e1956a6e1a25ab8cd4c3a609da8d7b6fedb4a4c"
sys.path.insert(0, str(ROOT / "rhel-patch-demo-restore"))


class RunJournal:
    """A local per-controller lock and durable launch receipt."""

    def __init__(self, state_path, origin):
        key = hashlib.sha256(origin.encode()).hexdigest()[:24]
        self.path = state_path.with_name(f"{state_path.name}.event-{key}.json")
        self.mutex = threading.Lock()
        self.lock_path = self.path.with_suffix(".lock")

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600), "rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConfigError("この Mac では同じ AAP の統合デモが実行中です。") from None
            yield

    def read(self):
        try:
            value = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return None
        except ValueError:
            raise ConfigError("起動記録を解釈できません。重複防止のため起動を中止します。") from None
        if not isinstance(value, dict) or value.get("status") not in {
            "ready", "posting", "pending", "held", "launching", "launched", "finished", "failed", "uncertain",
        }:
            raise ConfigError("起動記録が不正です。重複防止のため起動を中止します。")
        return value

    def write(self, record):
        fd, name = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            Path(name).unlink(missing_ok=True)


def demo_definition(local):
    import yaml

    settings = yaml.safe_load((ROOT / "aap-config/current-settings.yml").read_bytes())
    defaults = yaml.safe_load((ROOT / "rhel-patch-demo-restore/aap-config/demo-vars.yml").read_bytes())
    event = {key: settings["target"][key] for key in EVENT_FIELDS}
    event["hostname"] = local.get("host") or event["hostname"]
    if event["source"] not in DETECTION_SOURCES.values():
        raise ConfigError("検知元には Satellite または Lightspeed の模擬設定を使用してください。")
    for key, pattern in (
        ("hostname", r"[A-Za-z0-9][A-Za-z0-9.-]*"), ("cve", r"CVE-\d{4}-\d+"),
        ("advisory", r"RHSA-\d{4}:\d+"), ("severity", r"Low|Moderate|Important|Critical"),
    ):
        if not isinstance(event.get(key), str) or not re.fullmatch(pattern, event[key]):
            raise ConfigError(f"デモ検知データの {key} を確認してください。")
    if event["hostname"] in ("all", "localhost", "ungrouped"):
        raise ConfigError("対象は Inventory の単一 RHEL ホストを指定してください。")
    return event, local.get("organization") or defaults["demo_organization"], (
        local.get("github_url") or defaults["demo_scm_url"]
    ), settings["project"]["scm_branch"]


def demo_template(controller, organization):
    from controller import ApprovalError, SLACK_WORKFLOW, positive_id

    orgs = list(controller.rows("organizations/", name=organization))
    if len(orgs) != 1 or orgs[0].get("name") != organization or not positive_id(orgs[0].get("id")):
        raise ApprovalError("指定 Organization を一意に参照できません。")
    templates = list(controller.rows("workflow_job_templates/", name=SLACK_WORKFLOW, organization=orgs[0]["id"]))
    if (len(templates) != 1 or templates[0].get("name") != SLACK_WORKFLOW
            or templates[0].get("organization") != orgs[0]["id"] or not positive_id(templates[0].get("id"))):
        raise ApprovalError("対象の RHEL-Patch-Workflow を Organization 内で一意に参照できません。")
    return templates[0]["id"]


def verify_safe_workflow(controller, template_id, event, repo, branch, verified_revisions):
    from controller import PLAYBOOK, SLACK_WORKFLOW, ApprovalError, positive_id, variables
    from restore_demo import RestoreError, github_location, verify_github

    template = controller.get(f"workflow_job_templates/{template_id}/")
    workflow_extra = variables(template.get("extra_vars"))
    if (template.get("id") != template_id or template.get("name") != SLACK_WORKFLOW
            or set(workflow_extra) != {"demo_requires_event"} or workflow_extra["demo_requires_event"] is not True
            or template.get("ask_variables_on_launch") is not True or template.get("inventory") is not None
            or template.get("allow_simultaneous") is not False or template.get("survey_enabled") is not False
            or any(value is not False for key, value in template.items() if key.startswith("ask_") and key.endswith("_on_launch") and key != "ask_variables_on_launch")
            or any(template.get(key) for key in ("scm_branch", "limit", "job_tags", "skip_tags", "webhook_service"))):
        raise ApprovalError("Workflow に同時実行、起動時入力、またはデモ定義外の上書きがあります。")
    nodes = list(controller.rows(f"workflow_job_templates/{template_id}/workflow_nodes/"))
    graph = {node["identifier"]: node for node in nodes if isinstance(node.get("identifier"), str)}
    if (len(nodes) != 2 or set(graph) != set(NODES)
            or any(not positive_id(node.get("id")) or not positive_id(node.get("unified_job_template"))
                   or node.get("workflow_job_template") != template_id or variables(node.get("extra_data"))
                   or any(node.get(key) is not None and node[key] != "" for key in (
                       "inventory", "execution_environment", "scm_branch", "job_type", "limit", "job_tags", "skip_tags",
                   ))
                   or node.get("failure_nodes") != [] or node.get("always_nodes") != []
                   for node in nodes)):
        raise ApprovalError("Workflow のノード構成または実行設定がデモ定義と異なります。")
    check, apply = (graph[key] for key in NODES)
    if check.get("success_nodes") != [apply["id"]] or apply.get("success_nodes") != []:
        raise ApprovalError("Check → Apply の成功経路を確認できません。")

    projects = set()
    inventories = set()
    for node, name in ((check, "RHEL-Patch-Check"), (apply, "RHEL-Patch-Apply")):
        task = controller.get(f"job_templates/{node['unified_job_template']}/")
        extra = variables(task.get("extra_vars"))
        if (task.get("id") != node["unified_job_template"] or task.get("name") != name
                or task.get("playbook") != PLAYBOOK or task.get("job_type") != "run"
                or task.get("limit") != event["hostname"] or not positive_id(task.get("project"))
                or not positive_id(task.get("inventory"))
                or any(value is not False for key, value in task.items() if key.startswith("ask_") and key.endswith("_on_launch"))
                or task.get("survey_enabled") is not False or task.get("ask_variables_on_launch") is not False
                or any(task.get(key) for key in ("scm_branch", "job_tags", "skip_tags", "start_at_task"))
                or task.get("job_slice_count") != 1 or set(extra) != {"apply_patch", "target_rhsa"}
                or extra["apply_patch"] is not False or extra["target_rhsa"] != event["advisory"]):
            raise ApprovalError(f"{name} の対象ホスト、RHSA、または apply_patch: false を確認できません。")
        projects.add(task["project"])
        inventories.add(task["inventory"])
    if len(projects) != 1:
        raise ApprovalError("Check と Apply が同じ Project を参照していません。")
    if len(inventories) != 1:
        raise ApprovalError("Check と Apply が同じ Inventory を参照していません。")
    inventory_id = next(iter(inventories))
    hosts = list(controller.rows(f"inventories/{inventory_id}/hosts/", name=event["hostname"]))
    if len(hosts) != 1 or hosts[0].get("name") != event["hostname"] or hosts[0].get("enabled") is not True:
        raise ApprovalError("許可リストの対象ホストを Inventory の有効な単一ホストとして確認できません。")
    project_id = next(iter(projects))
    project = controller.get(f"projects/{project_id}/")
    revision = project.get("scm_revision")
    try:
        same_repo = github_location(project.get("scm_url") or "") == github_location(repo)
    except RestoreError:
        same_repo = False
    if (project.get("id") != project_id or project.get("name") != "RHEL-Patch-Demo"
            or project.get("status") != "successful" or project.get("scm_type") != "git"
            or not same_repo or project.get("scm_branch") != branch
            or project.get("allow_override") is not False or project.get("scm_update_on_launch") is not False
            or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ApprovalError("Project の GitHub 設定と同期済み Revision を確認できません。")
    if hashlib.sha256((ROOT / PLAYBOOK).read_bytes()).hexdigest() != SAFE_PLAYBOOK_SHA256:
        raise ApprovalError("ローカル Playbook が統合デモ用の確認済み読み取り専用版と異なります。")
    key = (repo, revision)
    if key not in verified_revisions:
        try:
            verify_github(repo, branch, revision=revision, playbooks=(PLAYBOOK,))
        except RestoreError as error:
            raise ApprovalError(f"AAP の同期済み Playbook を確認できません。{error}") from None
        verified_revisions.add(key)
    return revision


def read_workflow(controller, job_id, template_id):
    from controller import ApprovalError, SLACK_WORKFLOW

    job = controller.get(f"workflow_jobs/{job_id}/")
    if (job.get("id") != job_id or job.get("name") != SLACK_WORKFLOW
            or job.get("workflow_job_template") != template_id or job.get("status") not in ACTIVE | TERMINAL):
        raise ApprovalError("実行記録の Workflow Job と AAP の応答が一致しません。")
    return job["status"]


def show_result(controller, job_id, status):
    from controller import positive_id, variables

    print(f"Workflow Job #{job_id} | {status}")
    for node in controller.rows(f"workflow_jobs/{job_id}/workflow_nodes/"):
        identifier = node.get("identifier")
        if identifier not in NODES or node.get("workflow_job") != job_id:
            continue
        label = {"patch-check": "RHEL-Patch-Check Job", "patch-apply": "RHEL-Patch-Apply Job"}[identifier]
        node_job_id = node.get("job")
        if not positive_id(node_job_id):
            print(f"  {label} | 未実行")
            continue
        summary = node.get("summary_fields") or {}
        detail = summary.get("job") or {}
        job_status = detail.get("status")
        flags = ""
        if identifier == "patch-apply" or job_status not in ACTIVE | TERMINAL:
            detail = controller.get(f"jobs/{node_job_id}/")
            job_status = detail.get("status")
            if identifier == "patch-apply" and variables(detail.get("extra_vars")).get("apply_patch") is False:
                flags = " | apply_patch=false"
        print(f"  {label} #{node_job_id} | {job_status if job_status in ACTIVE | TERMINAL else '不明'}{flags}")


def pending_expired(record):
    return time.time() >= record["expires"]


class EventRelay:
    def __init__(self, controller, journal, template_id, allowed, event, verify):
        self.controller = controller
        self.journal = journal
        self.template_id = template_id
        self.allowed = allowed
        self.event = event
        self.verify = verify

    def press(self, body, action):
        from controller import ApprovalError, ControllerHTTPError, SLACK_WORKFLOW
        from slack_app import APPROVE, HOLD

        actor = (body.get("user") or {}).get("id")
        if not isinstance(actor, str) or actor not in self.allowed:
            return "この操作は許可されていません。"
        container = body.get("container") or {}
        with self.journal.mutex:
            record = self.journal.read()
            if (not record or record["event"] != self.event
                    or (body.get("team") or {}).get("id") != record["team"]
                    or (body.get("user") or {}).get("team_id", record["team"]) != record["team"]
                    or container.get("type") != "message" or container.get("channel_id") != record["channel"]
                    or container.get("message_ts") != record.get("timestamp")
                    or action.get("value") != self.event["id"] or action.get("action_id") not in (APPROVE, HOLD)):
                return "この Slack 操作は通知イベントと一致しません。"
            if record["status"] != "pending":
                return "このイベントは処理済み、または処理中です。"
            if pending_expired(record):
                return "Slack 承認期限が切れています。"
            if action["action_id"] == HOLD:
                self.journal.write({**record, "status": "held", "actor": actor})
                return "保留しました。AAP Workflow は起動していません。"
            try:
                self.verify()
                active = self.controller.rows(
                    "workflow_jobs/", workflow_job_template=self.template_id, status__in="new,pending,waiting,running",
                )
                if any(row.get("name") == SLACK_WORKFLOW and row.get("status") in ACTIVE for row in active):
                    return "同じ Workflow が実行中のため、このイベントからは起動できません。"
            except ApprovalError as error:
                return str(error)
            if pending_expired(record):
                return "安全確認中に Slack 承認期限が切れました。"
            claimed = {**record, "status": "launching", "actor": actor, "approved_at": time.time()}
            self.journal.write(claimed)
            try:
                job_id = self.controller.launch_demo_workflow(self.template_id, self.event)
            except ControllerHTTPError as error:
                if error.status in (400, 401, 403, 404, 405):
                    self.journal.write({**claimed, "status": "failed"})
                else:
                    self.journal.write({**claimed, "status": "uncertain"})
                return f"{error} 状態は --status で確認してください。"
            except ApprovalError:
                self.journal.write({**claimed, "status": "uncertain"})
                return "AAP の起動結果が未確定です。再送せず --status で確認してください。"
            self.journal.write({**claimed, "job_id": job_id, "status": "launched"})
            return f"承認を受け付け、Workflow Job #{job_id} を起動しました。"


def notify_event(client, journal, record):
    from controller import ApprovalError
    from slack_app import APPROVE, HOLD, REJECTED_POST, StageError, diagnostic_step

    if record["status"] != "ready":
        return
    event = record["event"]
    expires = datetime.fromtimestamp(record["expires"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (f"脆弱性検知デモ | イベント {event['id']}\n"
            f"CVE {event['cve']} | RHSA {event['advisory']} | 重要度 {event['severity']}\n"
            f"対象 {event['hostname']} | 検知元 {event['source']} | 承認期限 {expires}\n"
            "承認後に AAP が RHSA を再確認します。実パッチはスキップします。")
    blocks = [
        {"type": "section", "text": {"type": "plain_text", "text": text}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": action, "text": {"type": "plain_text", "text": label}, "value": event["id"]}
            for action, label in ((APPROVE, "承認"), (HOLD, "保留"))
        ]},
    ]
    journal.write({**record, "status": "posting"})
    try:
        with diagnostic_step("Slack 脆弱性通知"):
            response = client.chat_postMessage(channel=record["channel"], text=text, blocks=blocks)
            timestamp = response.get("ts")
            if response.get("channel") != record["channel"] or not isinstance(timestamp, str) or not re.fullmatch(r"\d+\.\d+", timestamp):
                raise ApprovalError("Slack 投稿結果が未確定です。再投稿を停止します。")
    except StageError as error:
        if error.slack_code in REJECTED_POST and error.slack_status in (200, 400, 401, 403, 404):
            journal.write(record)
        raise
    journal.write({**record, "status": "pending", "timestamp": timestamp})


def monitor_event(controller, template_id, journal, client, seconds):
    from slack_app import diagnostic_step, StageError

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = journal.read()
        state = record["status"]
        if state == "held":
            print("保留しました。AAP Workflow は起動していません。")
            return 0
        if state == "pending" and pending_expired(record):
            print("Slack 承認期限が切れました。AAP Workflow は起動していません。")
            return 1
        if state == "uncertain":
            print("AAP の起動結果が未確定です。再送せず --status を確認してください。")
            return 1
        if state == "failed":
            print("AAP が起動を拒否しました。--status でイベントを確認してください。")
            return 1
        if state == "launched":
            job_id = record["job_id"]
            status = read_workflow(controller, job_id, template_id)
            if status in TERMINAL:
                show_result(controller, job_id, status)
                # Record completion before posting. An ambiguous Slack result never retries.
                journal.write({**record, "status": "finished", "outcome": status})
                try:
                    with diagnostic_step("Slack 結果通知"):
                        client.chat_postMessage(
                            channel=record["channel"], thread_ts=record["timestamp"],
                            text=f"イベント {record['event']['id']} | Workflow Job #{job_id} | {status} | 実パッチ適用なし",
                        )
                except StageError as error:
                    print(error, file=sys.stderr)
                return 0 if status == "successful" else 1
        time.sleep(2)
    print("待ち受け時間を超えました。--status でイベントと Workflow の状態を確認してください。")
    return 1


def run(args):
    from controller import ApprovalError, Controller
    from slack_app import STATE_PATH, serve

    local = load_config()
    expected, organization, repo, branch = demo_definition(local)
    if args.source:
        expected["source"] = DETECTION_SOURCES[args.source]
    origin = prompt_value("AAP Controller または Gateway URL", os.environ.get("AAP_URL") or local.get("aap_url", ""))
    token = prompt_secret("統合デモ用 AAP OAuth トークン", "AAP_DEMO_OAUTH_TOKEN")
    controller = Controller(origin, token)
    version = controller.check_version()
    print(f"Controller {version.split('-')[0]}、API {controller.api_root}")
    template_id = demo_template(controller, organization)
    journal = RunJournal(Path(os.environ.get("AAP_SLACK_STATE_PATH", STATE_PATH)).expanduser().resolve(), controller.origin)
    verified_revisions = set()

    def safety_check():
        return verify_safe_workflow(controller, template_id, expected, repo, branch, verified_revisions)

    if args.status:
        record = journal.read()
        if not record:
            print("統合デモのイベント記録がありません。")
        else:
            print(f"イベント {record['event']['id']} | {record['status']}")
            if record.get("actor"):
                print(f"Slack 操作者 {record['actor']}")
            if record["status"] in {"launching", "uncertain"}:
                print("AAP の起動結果は未確定です。自動再送は行われません。")
            if record.get("job_id"):
                show_result(controller, record["job_id"], read_workflow(controller, record["job_id"], template_id))
        return 0
    if args.dry_run:
        print(f"構成検証済み | {expected['cve']} | {expected['advisory']} | {expected['hostname']} | Revision {safety_check()}")
        print("Dry Run 完了。AAP 書き込みと Slack 投稿は 0 件です。")
        return 0

    with journal.locked():
        record = journal.read()
        if record and record["status"] in {"posting", "launching", "uncertain"}:
            raise ApprovalError("前回の Slack 投稿または AAP 起動結果が未確定です。重複防止のため再送しません。--status を確認してください。")
        expired = bool(record and record["status"] in {"ready", "pending"} and pending_expired(record))
        closed = bool(record and (record["status"] in {"finished", "held", "failed"} or expired))
        if closed and not args.new:
            print("前回のイベントは終了または期限切れです。--status で確認し、次のイベントは --new を指定してください。")
            return 0
        if args.new and record and not closed:
            raise ApprovalError("処理中のイベントがあります。通常実行で再開してください。")
        if record and not closed and (
            record.get("template_id") != template_id or any(record["event"][key] != expected[key] for key in EVENT_FIELDS)
        ):
            raise ApprovalError("既存イベントが現在の対象設定と異なります。元の設定で再開してください。")
        team, channel, users, _ = slack_preferences(local)
        if record and not closed and (record["team"], record["channel"]) != (team, channel):
            raise ApprovalError("既存通知は別の Workspace またはチャンネルに記録済みです。")
        bot = prompt_secret("Slack Bot Token", "SLACK_BOT_TOKEN")
        app = prompt_secret("Slack App Token", "SLACK_APP_TOKEN")
        if not bot.startswith("xoxb-") or not app.startswith("xapp-"):
            raise ApprovalError("Slack Bot Token と App Token の種類を確認してください。")
        safety_check()
        if input("Slack 脆弱性通知の送信または既存イベントの再開には RUN と入力 > ") != "RUN":
            return 1
        if not record or closed:
            event = {**expected, "id": uuid.uuid4().hex}
            record = {"event": event, "status": "ready", "team": team, "channel": channel,
                      "template_id": template_id, "expires": time.time() + args.approval_wait}
            journal.write(record)

        def session(client):
            notify_event(client, journal, record)
            print(f"イベント {record['event']['id']} を待ち受けます。AAP 起動は許可ユーザーの Slack 承認後に行います。")
            return EventRelay(controller, journal, template_id, users, record["event"], safety_check), client

        return serve(
            team=team, channel=channel, users=users, bot_token=bot, app_token=app, event_session=session,
            monitor=lambda client: monitor_event(controller, template_id, journal, client, args.result_wait),
        )


def main():
    parser = argparse.ArgumentParser(prog="run-demo.sh", description="ダミー CVE 検知から AAP と Slack の統合デモを実行します。")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="読み取りのみで起動前の構成を検証")
    modes.add_argument("--status", action="store_true", help="記録済み Workflow の状態を読み取りで表示")
    modes.add_argument("--new", action="store_true", help="前回が終了済みの場合に新しいデモを開始")
    parser.add_argument("--approval-wait", type=int, default=900, metavar="秒", help="Slack 承認の有効期間")
    parser.add_argument("--source", choices=DETECTION_SOURCES)
    parser.add_argument("--result-wait", type=int, default=3900, metavar="秒")
    args = parser.parse_args()
    if args.approval_wait <= 0 or args.result_wait <= 0:
        parser.error("待ち時間は正の秒数で指定してください。")
    try:
        check_environment()
        from controller import ApprovalError
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    except ImportError:
        print("SETUP.md に従って Python 依存関係をインストールしてください。", file=sys.stderr)
        return 1
    try:
        return run(args)
    except (ConfigError, ApprovalError) as error:
        print(error, file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("\n待ち受けを終了しました。既存実行は --status で確認できます。", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"統合デモエラー ({type(error).__name__})。--status で実行状態を確認してください。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
