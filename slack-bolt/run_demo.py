"""Relay simulated RHEL advisory events to Slack and launch the approved workflow."""

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
from rhel_result import STAGES, inspect_rhel_jobs, workflow_result_text


ACTIVE = {"new", "pending", "waiting", "running"}
TERMINAL = {"successful", "failed", "error", "canceled"}
EVENT_FIELDS = ("hostname", "advisory", "cve", "severity", "source")
DETECTION_SOURCES = {
    "satellite": "Red Hat Satellite (simulated)",
    "lightspeed": "Red Hat Lightspeed (simulated)",
}
SAFE_PLAYBOOK_SHA256 = "ee35cb45f45a566750cd42af78da35d00d82c658bf47eb5ea25b25aed8f2debf"
RPM_OBSERVATION_MODULE = "library/rhel_rpm_observation.py"
RPM_OBSERVATION_SHA256 = "29bfa58e99ba42fc9fb44187a4c6daff466b7f67e07d0bf58cc036ed4db62971"
NOTIFICATION_TEMPLATE = "RHEL-Slack-Notification"
NOTIFICATION_PLAYBOOK = "slack_notify.yml"
NOTIFICATION_SHA256 = "607d3beee2790cf3f8c7870fa77a0417ad895e154580474c1d08a5e00d171f05"
NOTIFICATION_POLL_SECONDS = 5
sys.path.insert(0, str(ROOT / "rhel-patch-demo-restore"))


class Receipt:
    """Atomically persist one event before any external write."""

    def __init__(self, path, mutex):
        self.path = path
        self.mutex = mutex

    def read(self):
        try:
            value = json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return None
        except ValueError:
            raise ConfigError("起動記録を解釈できません。重複防止のため起動を中止します。") from None
        if not isinstance(value, dict) or value.get("status") not in {
            "ready", "posting", "pending", "held", "launching", "launched", "finished", "failed", "uncertain",
            "observed", "ignored", "expired", "notification_failed",
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


class RunJournal(Receipt):
    """A local per-controller lock shared by the one-shot and service modes."""

    def __init__(self, state_path, origin):
        key = hashlib.sha256(origin.encode()).hexdigest()[:24]
        super().__init__(state_path.with_name(f"{state_path.name}.event-{key}.json"), threading.Lock())
        self.lock_path = self.path.with_suffix(".lock")
        self.notification_path = self.path.with_suffix(".notifications")

    def notification(self, job_id):
        return Receipt(self.notification_path / f"{job_id}.json", self.mutex)

    def notifications(self):
        for path in self.notification_path.glob("*.json"):
            if re.fullmatch(r"[1-9][0-9]{0,18}", path.stem):
                yield int(path.stem), self.notification(int(path.stem))

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600), "rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConfigError("この Mac では同じ AAP の統合デモが実行中です。") from None
            yield


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
        raise ApprovalError("対象の RHEL-Patch-Slack-Workflow を Organization 内で一意に参照できません。")
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
    if (len(nodes) != len(STAGES) or set(graph) != set(STAGES)
            or any(not positive_id(node.get("id")) or not positive_id(node.get("unified_job_template"))
                   or node.get("workflow_job_template") != template_id or variables(node.get("extra_data"))
                   or any(node.get(key) is not None and node[key] != "" for key in (
                       "inventory", "execution_environment", "scm_branch", "job_type", "limit", "job_tags", "skip_tags",
                   ))
                   or node.get("failure_nodes") != [] or node.get("always_nodes") != []
                   for node in nodes)):
        raise ApprovalError("Workflow のノード構成または実行設定がデモ定義と異なります。")
    check, apply, verify = (graph[key] for key in STAGES)
    if (check.get("success_nodes") != [apply["id"]] or apply.get("success_nodes") != [verify["id"]]
            or verify.get("success_nodes") != []):
        raise ApprovalError("Check → Apply → Verify の成功経路を確認できません。")

    projects = set()
    inventories = set()
    for stage, name in STAGES.items():
        node = graph[stage]
        task = controller.get(f"job_templates/{node['unified_job_template']}/")
        extra = variables(task.get("extra_vars"))
        apply_patch = extra.get("apply_patch")
        valid_patch_mode = apply_patch is False or (stage == "patch-apply" and apply_patch is True)
        if (task.get("id") != node["unified_job_template"] or task.get("name") != name
                or task.get("playbook") != PLAYBOOK or task.get("job_type") != "run"
                or task.get("limit") != event["hostname"] or not positive_id(task.get("project"))
                or not positive_id(task.get("inventory"))
                or any(value is not False for key, value in task.items() if key.startswith("ask_") and key.endswith("_on_launch"))
                or task.get("survey_enabled") is not False or task.get("ask_variables_on_launch") is not False
                or any(task.get(key) for key in ("scm_branch", "job_tags", "skip_tags", "start_at_task"))
                or task.get("job_slice_count") != 1 or set(extra) != {"apply_patch", "target_rhsa"}
                or not valid_patch_mode or extra["target_rhsa"] != event["advisory"]):
            expected_mode = "true または false" if stage == "patch-apply" else "false"
            raise ApprovalError(f"{name} の対象ホスト、RHSA、または apply_patch: {expected_mode} を確認できません。")
        projects.add(task["project"])
        inventories.add(task["inventory"])
    if len(projects) != 1:
        raise ApprovalError("Check、Apply、Verify が同じ Project を参照していません。")
    if len(inventories) != 1:
        raise ApprovalError("Check、Apply、Verify が同じ Inventory を参照していません。")
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
        raise ApprovalError("ローカル Playbook が統合デモ用の確認済み版と異なります。")
    module = ROOT / RPM_OBSERVATION_MODULE
    if not module.is_file() or hashlib.sha256(module.read_bytes()).hexdigest() != RPM_OBSERVATION_SHA256:
        raise ApprovalError("ローカル RPM 照会モジュールが統合デモ用の確認済み版と異なります。")
    key = (repo, revision)
    if key not in verified_revisions:
        try:
            verify_github(repo, branch, revision=revision, playbooks=(PLAYBOOK, RPM_OBSERVATION_MODULE))
        except RestoreError as error:
            raise ApprovalError(f"AAP の同期済み Playbook を確認できません。{error}") from None
        verified_revisions.add(key)
    return revision


def read_workflow(controller, job_id, template_id, *, event=None):
    from controller import ApprovalError, SLACK_WORKFLOW, variables

    job = controller.get(f"workflow_jobs/{job_id}/")
    if (job.get("id") != job_id or job.get("name") != SLACK_WORKFLOW
            or job.get("workflow_job_template") != template_id or job.get("status") not in ACTIVE | TERMINAL):
        raise ApprovalError("実行記録の Workflow Job と AAP の応答が一致しません。")
    if event is not None:
        extra = variables(job.get("extra_vars"))
        if (extra.get("demo_event_id"), extra.get("demo_target_host"), extra.get("demo_target_rhsa")) != (
            event["id"], event["hostname"], event["advisory"]
        ):
            raise ApprovalError("Workflow Job のイベント ID、対象ホスト、RHSA が承認記録と一致しません。")
    return job["status"]


def show_result(controller, job_id, status):
    from controller import positive_id, variables

    print(f"Workflow Job #{job_id} | {status}")
    for node in controller.rows(f"workflow_jobs/{job_id}/workflow_nodes/"):
        identifier = node.get("identifier")
        if identifier not in STAGES or node.get("workflow_job") != job_id:
            continue
        label = f"{STAGES[identifier]} Job"
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
    from slack_app import APPROVE, HOLD, REJECTED_POST, SELECT_HOSTS, StageError, diagnostic_step

    if record["status"] != "ready":
        return
    event = record["event"]
    expires = datetime.fromtimestamp(record["expires"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        "RHEL脆弱性検知通知【デモ】\n\n"
        "Red Hat Satellite / Red Hat Lightspeedによる脆弱性検知を模擬しています。"
        "対象RHELで脆弱性が検出され、対応するセキュリティ修正情報が特定された想定です。\n\n"
        f"CVE：{event['cve']}\n"
        f"RHSA：{event['advisory']}\n"
        f"重要度：{event['severity']}\n"
        f"対象ホスト：{event['hostname']}\n"
        "検知元：Satellite / Lightspeed（模擬）\n"
        f"イベントID：{event['id']}\n"
        f"承認期限：{expires}\n\n"
        "承認後、Ansible Automation Platformが対象ホストのRHSAを再確認し、対応処理を実行します。"
        "承認時に RHEL-Patch-Apply が apply_patch: true に設定されている場合、対象 RHSA を実際に適用します。"
    )
    actions = (
        (APPROVE, "パッチ適用を承認"),
        (HOLD, "保留"),
        (SELECT_HOSTS, "適用対象を選ぶ"),
    )
    blocks = [
        {"type": "section", "text": {"type": "plain_text", "text": text}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": action, "text": {"type": "plain_text", "text": label}, "value": event["id"]}
            for action, label in actions
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


def post_workflow_result(controller, client, receipt, record, status):
    from controller import ApprovalError
    from slack_app import diagnostic_step

    job_id = record["job_id"]
    message = workflow_result_text(controller, job_id, status, record["event"])
    # Persist completion before the external write. An ambiguous response is never retried.
    result = {**record, "status": "finished", "outcome": status}
    receipt.write({**result, "result_delivery": "uncertain"})
    with diagnostic_step("Slack 結果通知"):
        reply = client.chat_postMessage(channel=record["channel"], thread_ts=record["timestamp"],
                                        text=message, mrkdwn=False, parse="none",
                                        unfurl_links=False, unfurl_media=False)
        timestamp = reply.get("ts")
        if reply.get("channel") != record["channel"] or not isinstance(timestamp, str) or not re.fullmatch(r"\d+\.\d+", timestamp):
            raise ApprovalError("Slack の結果通知を確定できません。")
    receipt.write({**result, "result_delivery": "sent"})
    print(message, flush=True)


def monitor_event(controller, template_id, journal, client, seconds):
    from slack_app import StageError

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
            status = read_workflow(controller, job_id, template_id, event=record["event"])
            if status in TERMINAL:
                try:
                    post_workflow_result(controller, client, journal, record, status)
                except StageError as error:
                    print(error, file=sys.stderr)
                return 0 if status == "successful" else 1
        time.sleep(2)
    print("待ち受け時間を超えました。--status でイベントと Workflow の状態を確認してください。")
    return 1


def verify_notification_revision(repo, branch, revision, verified):
    from controller import ApprovalError
    from restore_demo import RestoreError, verify_github

    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ApprovalError("通知 Job の Git Revision を確認できません。")
    if revision not in verified:
        try:
            verify_github(repo, branch, revision=revision, playbooks=(NOTIFICATION_PLAYBOOK,))
        except RestoreError as error:
            raise ApprovalError(f"通知 Job の成果物出力版を確認できません。{error}") from None
        verified.add(revision)


def verify_notification(controller, organization, expected, repo, branch):
    from controller import ApprovalError, ControllerHTTPError, positive_id, variables
    from restore_demo import RestoreError, github_location

    orgs = list(controller.rows("organizations/", name=organization))
    if len(orgs) != 1 or orgs[0].get("name") != organization or not positive_id(orgs[0].get("id")):
        raise ApprovalError("通知 Job の Organization を確認できません。")
    org_id = orgs[0]["id"]
    rows = list(controller.rows("job_templates/", name=NOTIFICATION_TEMPLATE, organization=org_id))
    if len(rows) != 1 or not positive_id(rows[0].get("id")):
        raise ApprovalError("RHEL-Slack-Notification を一意に参照できません。復元手順を確認してください。")
    template_id = rows[0]["id"]
    template = controller.get(f"job_templates/{template_id}/")
    project_id = template.get("project")
    if (template.get("id") != template_id or template.get("name") != NOTIFICATION_TEMPLATE
            or template.get("organization") != org_id or template.get("playbook") != NOTIFICATION_PLAYBOOK
            or not positive_id(project_id) or template.get("limit") != "localhost"
            or template.get("job_type") != "run" or template.get("job_slice_count") != 1
            or template.get("ask_variables_on_launch") is not False or template.get("survey_enabled") is not False
            or any(value is not False for key, value in template.items() if key.startswith("ask_") and key.endswith("_on_launch"))
            or any(template.get(key) for key in ("scm_branch", "job_tags", "skip_tags", "start_at_task", "webhook_service"))
            or variables(template.get("extra_vars")) != {"demo_event": expected}):
        raise ApprovalError("通知 Job Template の設定が成果物出力版と一致しません。restore-demo.sh を確認してください。")
    project = controller.get(f"projects/{project_id}/")
    try:
        same_repo = github_location(project.get("scm_url") or "") == github_location(repo)
    except RestoreError:
        same_repo = False
    if (project.get("id") != project_id or project.get("name") != "RHEL-Patch-Demo"
            or project.get("status") != "successful" or project.get("scm_type") != "git"
            or not same_repo or project.get("scm_branch") != branch
            or project.get("allow_override") is not False or project.get("scm_update_on_launch") is not False
            or hashlib.sha256((ROOT / NOTIFICATION_PLAYBOOK).read_bytes()).hexdigest() != NOTIFICATION_SHA256):
        raise ApprovalError("成果物出力専用の通知 Playbook と Project の同期状態を確認してください。")
    verified = set()
    verify_notification_revision(repo, branch, project.get("scm_revision"), verified)
    try:
        latest_notification_id(controller, template_id)
    except ControllerHTTPError as error:
        raise ApprovalError(f"通知 Job の一覧を参照できません (HTTP {error.status})。") from None
    return template_id, project_id, verified


def latest_notification_id(controller, template_id):
    from controller import ApprovalError, positive_id

    result = controller.get(f"job_templates/{template_id}/jobs/?order_by=-id&page_size=1")
    rows = result.get("results")
    if (not isinstance(rows, list) or len(rows) > 1
            or any(not isinstance(row, dict) or not positive_id(row.get("id")) for row in rows)):
        raise ApprovalError("通知 Job の一覧形式を確認できません。")
    return rows[0]["id"] if rows else 0


def notification_event(job_id, expected):
    return {**expected, "id": f"{job_id:032x}"}


def compare_notification_artifact(job, job_id, expected):
    """Report only known field names and type/match metadata from the API."""
    missing = object()
    root = "artifacts.demo_vulnerability_event"
    results = []

    def field(path, actual, desired):
        actual_type = "未設定" if actual is missing else type(actual).__name__
        if type(actual) is not type(desired):
            note = ""
            if type(desired) is int and type(actual) is str and actual == str(desired):
                note = "（期待する整数と同じ数字の文字列）"
            results.append(
                (False, f"{path}: 型または構造不一致（期待 {type(desired).__name__}、実際 {actual_type}）{note}")
            )
            return False
        if isinstance(desired, dict):
            if not actual:
                results.append((False, f"{path}: 空のオブジェクト"))
                return False
            return True
        matches = actual == desired
        results.append((matches, f"{path}: {'一致' if matches else '値が不一致'}（型 {actual_type}）"))
        return matches

    artifacts = job.get("artifacts", missing)
    if not field("artifacts", artifacts, {}):
        return results
    artifact = artifacts.get("demo_vulnerability_event", missing)
    if not field(root, artifact, {}):
        return results
    field(f"{root}.schema", artifact.get("schema", missing), 1)
    field(f"{root}.notification_job_id", artifact.get("notification_job_id", missing), job_id)
    event = artifact.get("event", missing)
    if field(f"{root}.event", event, {}):
        for key, value in expected.items():
            field(f"{root}.event.{key}", event.get(key, missing), value)
        if event.keys() - expected.keys():
            results.append((False, f"{root}.event: 定義外の項目あり"))
    return results


def inspect_notification_job(controller, job_id, expected):
    from controller import ApprovalError

    endpoint = f"jobs/{job_id}/"
    job = controller.get(endpoint)
    if type(job.get("id")) is not int or job["id"] != job_id:
        raise ApprovalError("AAP の応答に含まれる Job ID が指定と一致しません。")
    print(f"GET {controller.api_root}{endpoint}")
    report = compare_notification_artifact(job, job_id, notification_event(job_id, expected))
    for _, line in report:
        print(line)
    return 0 if all(matches for matches, _ in report) else 1


class NotificationService:
    """Watch UI-launched notification jobs while Socket Mode dispatches button events."""

    def __init__(self, controller, journal, notification_id, project_id, template_id,
                 expected, repo, branch, revisions, team, channel, users, approval_wait, verify):
        self.controller = controller
        self.journal = journal
        self.notification_id = notification_id
        self.project_id = project_id
        self.template_id = template_id
        self.expected = expected
        self.repo = repo
        self.branch = branch
        self.revisions = revisions
        self.team = team
        self.channel = channel
        self.users = users
        self.approval_wait = approval_wait
        self.verify = verify

    def event_for(self, job_id):
        return notification_event(job_id, self.expected)

    def press(self, body, action):
        value = action.get("value")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            return "この Slack 操作は通知イベントと一致しません。"
        job_id = int(value, 16)
        if not 0 < job_id < 10**19:
            return "この Slack 操作は通知イベントと一致しません。"
        receipt = self.journal.notification(job_id)
        record = receipt.read()
        if (not record or record.get("notification_template_id") != self.notification_id
                or record.get("template_id") != self.template_id
                or (record.get("team"), record.get("channel")) != (self.team, self.channel)):
            return "この Slack 操作は通知イベントと一致しません。"
        return EventRelay(self.controller, receipt, self.template_id, self.users,
                          self.event_for(job_id), self.verify_launch).press(body, action)

    def verify_launch(self):
        from controller import ApprovalError

        for _, receipt in self.journal.notifications():
            record = receipt.read()
            if record["status"] in {"launching", "uncertain"}:
                raise ApprovalError("結果未確定の Workflow 起動があります。--status と AAP の履歴を確認してください。")
            if record["status"] == "launched" and read_workflow(
                self.controller, record["job_id"], record["template_id"], event=record["event"]
            ) in ACTIVE:
                raise ApprovalError("同じ Workflow が実行中です。完了後、期限内に改めて承認してください。")
        self.verify()

    def watch(self, client):
        from controller import ApprovalError

        # Snapshot after Socket Mode connects. Unseen jobs from before this startup are excluded.
        baseline = latest_notification_id(self.controller, self.notification_id)
        self.journal.notification_path.mkdir(mode=0o700, exist_ok=True)
        print(f"通知 Job の監視を開始しました (既存 Job の上限 #{baseline})。AAP で {NOTIFICATION_TEMPLATE} を実行してください。", flush=True)
        while True:
            try:
                self.tick(client, baseline)
            except ApprovalError as error:
                print(f"通知 Job 一覧の監視エラー — {error}", file=sys.stderr, flush=True)
            time.sleep(NOTIFICATION_POLL_SECONDS)

    def tick(self, client, baseline):
        from controller import ApprovalError, positive_id

        # Keep an immutable baseline during this session. A lower ID that becomes
        # visible after a higher ID can still be discovered on the next poll.
        try:
            for row in self.controller.rows(f"job_templates/{self.notification_id}/jobs/",
                                            id__gt=baseline, order_by="id"):
                job_id = row.get("id")
                if not positive_id(job_id) or not baseline < job_id < 10**19:
                    raise ApprovalError("通知 Job の ID が検索範囲と一致しません。")
                receipt = self.journal.notification(job_id)
                if receipt.read() is None:
                    receipt.write({"status": "observed", "notification_job_id": job_id, "notification_template_id": self.notification_id,
                                   "template_id": self.template_id})
        finally:
            # A temporary listing failure must not stop already-recorded events.
            for job_id, receipt in self.journal.notifications():
                try:
                    self.process(client, job_id, receipt)
                except ApprovalError as error:
                    print(f"通知 Job #{job_id} — {error}", file=sys.stderr, flush=True)

    def process(self, client, job_id, receipt):
        from controller import ApprovalError, ControllerHTTPError
        from slack_app import StageError

        with receipt.mutex:
            record = receipt.read()
            if record.get("notification_template_id") != self.notification_id:
                return
            state = record["status"]
            if state == "observed":
                try:
                    job = self.controller.get(f"jobs/{job_id}/")
                except ControllerHTTPError as error:
                    if error.status != 404:
                        raise
                    receipt.write({**record, "status": "ignored"})
                    return
                if job.get("status") in ACTIVE:
                    return
                try:
                    event = self.validated_event(job_id, job)
                except ApprovalError:
                    receipt.write({**record, "status": "ignored"})
                    raise
                record = {**record, "event": event, "status": "ready", "team": self.team, "channel": self.channel,
                          "expires": time.time() + self.approval_wait}
                receipt.write(record)
                state = "ready"

            if state in {"ready", "pending"} and pending_expired(record):
                receipt.write({**record, "status": "expired"})
            elif state == "ready":
                if record["event"] != self.event_for(job_id) or (record["team"], record["channel"]) != (self.team, self.channel):
                    receipt.write({**record, "status": "ignored"})
                    raise ApprovalError("保存済みイベントと現在のデモ設定が一致しません。")
                try:
                    notify_event(client, receipt, record)
                except StageError:
                    current = receipt.read()
                    if current["status"] == "ready":
                        receipt.write({**current, "status": "notification_failed"})
                    raise
                print(f"通知 Job #{job_id} のイベント {record['event']['id']} を Slack に投稿しました。", flush=True)
            elif state == "launched":
                self.finish(client, receipt, record)

    def validated_event(self, job_id, job):
        from controller import ApprovalError

        if (job.get("id") != job_id or job.get("job_template") != self.notification_id
                or job.get("project") != self.project_id or job.get("playbook") != NOTIFICATION_PLAYBOOK
                or job.get("launch_type") != "manual" or job.get("status") != "successful"):
            raise ApprovalError("手動実行に成功した対象の通知 Job ではありません。")
        event = self.event_for(job_id)
        differences = [line for matches, line in compare_notification_artifact(job, job_id, event) if not matches]
        if differences:
            raise ApprovalError("成果物が復元定義と一致しません。\n  " + "\n  ".join(differences))
        verify_notification_revision(self.repo, self.branch, job.get("scm_revision"), self.revisions)
        return event

    def finish(self, client, receipt, record):
        job_id = record["job_id"]
        status = read_workflow(self.controller, job_id, record["template_id"], event=record["event"])
        if status not in TERMINAL:
            return
        if (record["team"], record["channel"]) != (self.team, self.channel):
            receipt.write({**record, "status": "finished", "outcome": status, "result_delivery": "skipped"})
            return
        post_workflow_result(self.controller, client, receipt, record, status)


def run(args):
    from controller import ApprovalError, Controller
    from slack_app import STATE_PATH, serve

    local = load_config()
    expected, organization, repo, branch = demo_definition(local)
    notification_defaults = expected
    if args.source:
        expected = {**expected, "source": DETECTION_SOURCES[args.source]}
    origin = prompt_value("AAP Controller または Gateway URL", os.environ.get("AAP_URL") or local.get("aap_url", ""))
    token = prompt_secret("統合デモ用 AAP OAuth トークン", "AAP_DEMO_OAUTH_TOKEN")
    controller = Controller(origin, token)
    version = controller.check_version()
    print(f"Controller {version.split('-')[0]}、API {controller.api_root}")
    if args.inspect_notification_job is not None:
        return inspect_notification_job(controller, args.inspect_notification_job, notification_defaults)
    if args.inspect_rhel_job:
        return inspect_rhel_jobs(controller, args.inspect_rhel_job, expected)
    template_id = demo_template(controller, organization)
    journal = RunJournal(Path(os.environ.get("AAP_SLACK_STATE_PATH", STATE_PATH)).expanduser().resolve(), controller.origin)
    verified_revisions = set()

    def safety_check():
        return verify_safe_workflow(controller, template_id, expected, repo, branch, verified_revisions)

    if args.status:
        found = False
        for job_id, receipt in sorted(journal.notifications()):
            item = receipt.read()
            if item:
                found = True
                print(f"通知 Job #{job_id} | イベント {item.get('event', {}).get('id', '未取得')} | "
                      f"{item['status']} | 操作者 {item.get('actor', '—')} | "
                      f"Workflow Job {item.get('job_id', '—')} | 結果 {item.get('outcome', '—')} | "
                      f"結果通知 {item.get('result_delivery', '—')}")
                if item["status"] == "launched":
                    live = read_workflow(controller, item["job_id"], item["template_id"], event=item["event"])
                    print(f"  AAP の現在の状態 {live}")
        record = journal.read()
        if not record and not found:
            print("統合デモのイベント記録がありません。")
        if record:
            print(f"イベント {record['event']['id']} | {record['status']}")
            if record.get("actor"):
                print(f"Slack 操作者 {record['actor']}")
            if record["status"] in {"launching", "uncertain"}:
                print("AAP の起動結果は未確定です。自動再送は行われません。")
            if record.get("job_id"):
                show_result(controller, record["job_id"], read_workflow(controller, record["job_id"], template_id))
        return 0
    serve_mode = getattr(args, "serve", False)
    if args.dry_run or serve_mode:
        notification_id, notification_project, notify_revisions = verify_notification(
            controller, organization, notification_defaults, repo, branch
        )
    if args.dry_run:
        print(f"通知テンプレート #{notification_id} と成果物出力版 Playbook を確認しました。")
        print(f"構成検証済み | {expected['cve']} | {expected['advisory']} | {expected['hostname']} | Revision {safety_check()}")
        print("Dry Run 完了。AAP 書き込みと Slack 投稿は 0 件です。")
        return 0

    if serve_mode:
        safety_check()
        with journal.locked():
            team, channel, users, _ = slack_preferences(local)
            bot = prompt_secret("Slack Bot Token", "SLACK_BOT_TOKEN")
            app = prompt_secret("Slack App Token", "SLACK_APP_TOKEN")
            service = NotificationService(
                controller, journal, notification_id, notification_project, template_id,
                expected, repo, branch, notify_revisions, team, channel, users, args.approval_wait, safety_check,
            )
            return serve(
                team=team, channel=channel, users=users, bot_token=bot, app_token=app,
                event_session=lambda client: (service, client), monitor=service.watch,
            )

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
    modes.add_argument("--serve", action="store_true", help="AAP の通知 Job を監視し、Slack 操作を常駐処理")
    modes.add_argument("--status", action="store_true", help="記録済み Workflow の状態を読み取りで表示")
    modes.add_argument("--inspect-notification-job", type=int, metavar="JOB_ID", help="通知 Job 成果物の型と一致状況を GET で確認")
    modes.add_argument("--inspect-rhel-job", type=int, nargs=2, metavar=("CHECK_JOB_ID", "VERIFY_JOB_ID"),
                       help="Check と Verify の RPM 成果物を GET で比較")
    modes.add_argument("--new", action="store_true", help="前回が終了済みの場合に新しいデモを開始")
    parser.add_argument("--approval-wait", type=int, default=900, metavar="秒", help="Slack 承認の有効期間")
    parser.add_argument("--source", choices=DETECTION_SOURCES)
    parser.add_argument("--result-wait", type=int, default=3900, metavar="秒", help="単発デモの待ち受け時間")
    args = parser.parse_args()
    if args.inspect_notification_job is not None and not 0 < args.inspect_notification_job < 10**19:
        parser.error("通知 Job ID は 19 桁以下の正の整数で指定してください。")
    if args.inspect_rhel_job and (len(set(args.inspect_rhel_job)) != 2
                                 or any(not 0 < job_id < 10**19 for job_id in args.inspect_rhel_job)):
        parser.error("Check と Verify の異なる Job ID を 19 桁以下の正の整数で指定してください。")
    if args.source and args.serve:
        parser.error("監視するイベントの検知元は current-settings.yml の target.source で設定してください。")
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
