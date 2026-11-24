"""Restore the demo configuration and reconcile Slack notification variables."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

try:
    import yaml
except ImportError:
    sys.exit("PyYAML が必要です。README.md の初回セットアップを実行してください。")


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from demo_config import ConfigError, load_config, prompt_secret, prompt_value, save_config


LOCAL_INPUTS = {
    "organization": "demo_organization",
    "inventory": "demo_rhel_inventory",
    "host": "demo_rhel_host",
    "ssh_credential": "demo_ssh_credential",
    "execution_environment": "demo_execution_environment",
    "github_url": "demo_scm_url",
}
GATEWAY_API_ROOT = "/api/controller/v2/"
LEGACY_API_ROOT = "/api/v2/"
# POST is only available for creating these resources and completing new objects.
WRITE_PATH = re.compile(
    r"(?:projects|inventories|hosts|credential_types|credentials|job_templates|"
    r"workflow_job_templates|workflow_job_template_nodes)/"
    r"|job_templates/[0-9]+/credentials/"
    r"|workflow_job_template_nodes/[0-9]+/(?:create_approval_template|success_nodes)/"
)
VAR_FIELDS = {"variables", "extra_vars", "extra_data"}
WEBHOOK = re.compile(
    r"https://hooks\.slack(?:-gov)?\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+"
)
PLAYBOOKS = ("rhel_patch.yml", "slack_notify.yml")
SLACK_TEMPLATE = "RHEL-Slack-Notification"


class RestoreError(Exception):
    pass


class HTTPStatusError(RestoreError):
    def __init__(self, label, status):
        super().__init__(f"{label} — HTTP {status}")
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_opener():
    return build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))


def request(opener, url, label, *, token=None, payload=None, headers=None, method=None):
    headers = {"Accept": "application/json", **(headers or {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    try:
        with opener.open(Request(url, data=data, headers=headers, method=method), timeout=25) as response:
            return response.read(4 * 1024 * 1024 + 1)
    except HTTPError as error:
        raise HTTPStatusError(label, error.code) from None
    except (OSError, URLError, ValueError):
        raise RestoreError(f"{label} — HTTPS 接続に失敗しました。URL、証明書、通信経路を確認してください。") from None


class Controller:
    def __init__(self, origin, token):
        self.origin = origin
        self.token = token
        self.opener = https_opener()
        self.api_root = None
        self.write_enabled = False

    def call(self, path, label, *, params=None, payload=None):
        if payload is not None and (not self.write_enabled or not WRITE_PATH.fullmatch(path)):
            raise RestoreError("未承認または対象外の書き込みを拒否しました。")
        if self.api_root is None:
            raise RestoreError("Controller API の接続と認証を先に確認してください。")
        query = "?" + urlencode(params) if params else ""
        return self._json(self.api_root + path + query, label, token=self.token, payload=payload)

    def _json(self, path, label, *, token=None, payload=None, method=None):
        body = request(self.opener, self.origin + path, label, token=token, payload=payload, method=method)
        try:
            return json.loads(body) if body else {}
        except (ValueError, UnicodeError):
            raise RestoreError(f"{label} — API の JSON 応答を確認できません。") from None

    def update_slack_extra_vars(self, update):
        if not self.write_enabled or self.api_root is None or type(update.job_id) is not int or update.job_id < 1:
            raise RestoreError("未承認の Extra Variables 更新を拒否しました。")
        path = f"job_templates/{update.job_id}/"
        current = self.call(path, update.label)
        if (object_id(current) != update.job_id or current.get("name") != SLACK_TEMPLATE
                or current.get("organization") != update.organization
                or current.get("extra_vars") != update.before):
            raise RestoreError(f"{update.label} — 更新直前に対象または変数が変わりました。再度 Dry Run してください。")
        self._json(self.api_root + path, update.label, token=self.token,
                   payload={"extra_vars": update.after}, method="PATCH")

    def connect(self):
        for root, minors in ((GATEWAY_API_ROOT, "[67]"), (LEGACY_API_ROOT, "5")):
            try:
                ping = self._json(root + "ping/", f"Controller API の疎通 ({root}ping/)")
            except HTTPStatusError as error:
                if error.status == 404:
                    continue
                raise
            version = ping.get("version", "") if isinstance(ping, dict) else ""
            match = re.fullmatch(
                rf"(4\.{minors}\.\d+)(?:[-.+][a-zA-Z0-9.-]+)?",
                version if isinstance(version, str) else "",
            )
            if match is None:
                raise RestoreError(f"未対応の Controller バージョンです ({root})。AAP 2.4 (4.5.x) / 2.5 (4.6.x) / 2.6 (4.7.x) を確認してください。")
            user = self._json(root + "me/", f"AAP OAuth 認証 ({root}me/)", token=self.token)
            if not isinstance(user, dict) or not isinstance(user.get("results"), list) or not user["results"]:
                raise RestoreError("AAP API 認証を確認できません。接続先で発行した OAuth アクセストークンを確認してください。")
            self.api_root = root
            print(f"HTTPS と API 認証を確認しました (Controller {match[1]}、{root})。")
            return
        raise RestoreError("Controller API が見つかりません (Gateway と従来型の ping が HTTP 404)。AAP のベース URL を確認してください。")

    def rows(self, path, label, **filters):
        results = []
        # Construct each page locally. Never forward the OAuth token to a URL from a response.
        for page in range(1, 101):
            result = self.call(path, label, params={**filters, "page_size": 100, "page": page})
            if not isinstance(result, dict) or not isinstance(result.get("results"), list) or "next" not in result:
                raise RestoreError(f"{label} — API の一覧形式が未対応です。")
            if any(not isinstance(row, dict) for row in result["results"]):
                raise RestoreError(f"{label} — API のリソース形式が未対応です。")
            results.extend(result["results"])
            if not result["next"]:
                return results
        raise RestoreError(f"{label} — API の一覧がページ上限を超えました。")

    def find(self, collection, label, **identity):
        rows = self.rows(collection + "/", label, **identity)
        if len(rows) > 1:
            raise RestoreError(f"{label} — 同じ条件で複数件が見つかりました。")
        return rows[0] if rows else None

    def post(self, path, label, payload):
        return self.call(path, label, payload=payload)


@dataclass(frozen=True)
class Config:
    origin: str
    organization: str
    inventory: str
    host: str
    ssh_credential: str
    environment: str
    scm_url: str


@dataclass
class Resource:
    collection: str
    label: str
    identity: dict
    payload: dict
    existing_id: int | None = None
    template_credential: str | None = None


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class SlackExtraVarsUpdate:
    job_id: int
    organization: int
    before: str | dict | None = field(repr=False)
    after: str
    label: str = field(default=f"job_templates / {SLACK_TEMPLATE} / extra_vars", init=False)

    def describe(self):
        desired = decode_vars(self.after)
        current = decode_vars(self.before)
        lines = [self.label]
        if not isinstance(current, dict):
            lines.append("  現在の Extra Variables はマッピング形式ではありません。")
            current = {}
        for key, value in desired.items():
            if key == "slack_interactive":
                old = current.get(key)
                if old is value:
                    continue
                old = repr(old) if type(old) is bool else "<未設定または型不正>"
            else:
                if key in current and current[key] == value:
                    continue
                old = "<設定済み・非表示>" if key in current else "<未設定>"
            lines.append(f"  {key}: {old} → {value!r}")
        removed = len(current.keys() - desired.keys())
        if removed:
            lines.append(f"  定義にない変数 {removed} 件を削除します（名前と値は非表示）。")
        return "\n".join(lines)


@dataclass
class Plan:
    resources: dict[str, Resource] = field(default_factory=dict)
    reused: list[str] = field(default_factory=list)
    prerequisites: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    project_sync: tuple[str, str | None] | None = None
    slack_update: SlackExtraVarsUpdate | None = None

    @property
    def missing(self):
        return [item.label for item in self.resources.values() if item.existing_id is None]


def object_id(row):
    value = row.get("id") if isinstance(row, dict) else None
    if type(value) is not int or value < 1:
        raise RestoreError("Controller API 応答に有効なリソース ID がありません。")
    return value


def decode_vars(value):
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError:
            return "<形式不正>"
    return {} if value is None else value


def resolve(values, ids):
    return {key: ids[value.name] if isinstance(value, Ref) else value for key, value in values.items()}


def compare(plan, label, actual, expected):
    for key, desired in expected.items():
        current = actual.get(key)
        if key in VAR_FIELDS:
            current = decode_vars(current)
            desired = decode_vars(desired)
        flags = {}
        if key == "extra_vars" and isinstance(desired, dict):
            flags = {flag: desired[flag] for flag in ("apply_patch", "slack_interactive") if flag in desired}
        matches = current == desired and type(current) is type(desired)
        matches = matches and all(current.get(flag) is value for flag, value in flags.items())
        if matches:
            continue
        # Arbitrary values from existing settings can contain secrets. Show safe
        # scalars and the safety flag, keeping remote strings/maps out of reports.
        safe_current = current if type(current) in (int, bool) or current is None else "<表示省略>"
        if flags and isinstance(current, dict):
            safe_current = {
                flag: current[flag] if type(current.get(flag)) is bool else "<未設定または型不正>"
                for flag in flags
            }
        if key in {"inputs", "injectors"}:
            desired = "<定義済みの形式>"
        plan.errors.append(f"{label} / {key} — 現在 {safe_current!r} → 期待 {desired!r}")


def prerequisite(api, plan, collection, label, **filters):
    row = api.find(collection, label, **filters)
    if row is None:
        plan.errors.append(f"{label} — 見つからないか、参照権限がありません。")
        plan.manual.append(f"{label} を AAP で用意し、アクセス権を確認してください。")
    else:
        plan.reused.append(label)
        plan.prerequisites[collection] = object_id(row)
    return row


def plan_restore(api, config, defaults, definition, has_webhook):
    plan = Plan()
    org = prerequisite(api, plan, "organizations", f"Organization {config.organization}", name=config.organization)
    if not org:
        return plan
    organization = object_id(org)
    scope = {"organization": organization}
    inventory = prerequisite(api, plan, "inventories", f"Inventory {config.inventory}", name=config.inventory, **scope)
    machine = prerequisite(
        api, plan, "credentials", f"SSH Credential {config.ssh_credential}",
        name=config.ssh_credential, **{"credential_type__kind": "ssh", "organization": organization},
    )
    ee = prerequisite(
        api, plan, "execution_environments", f"Execution Environment {config.environment}",
        name=config.environment, **{"or__organization": organization, "or__organization__isnull": "true"},
    )
    if inventory:
        host = prerequisite(
            api, plan, "hosts", f"RHEL Host {config.host}",
            name=config.host, inventory=object_id(inventory),
        )
        if host and host.get("enabled") is not True:
            plan.errors.append("RHEL ホストが無効です。")
            plan.manual.append("選択した Inventory で RHEL ホストを有効にしてください。")
    if plan.errors:
        return plan

    ids = {}

    def add(key, collection, name, payload, identity=None, credential=None, check=None):
        label = f"{collection} / {name}"
        if identity is None:
            identity = {"name": name, **scope}
        row = None
        if all(not isinstance(v, Ref) or v.name in ids for v in identity.values()):
            row = api.find(collection, label, **resolve(identity, ids))
        entry = Resource(collection, label, identity, payload, template_credential=credential)
        plan.resources[key] = entry
        if row is not None:
            entry.existing_id = ids[key] = object_id(row)
            original_errors = len(plan.errors)
            expected = {
                field: ids.get(value.name, f"<作成予定 {plan.resources[value.name].label}>")
                if isinstance(value, Ref) else value
                for field, value in payload.items() if field != "name"
            }
            notification = collection == "job_templates" and name == SLACK_TEMPLATE
            slack_after = expected.pop("extra_vars") if notification else None
            compare(plan, label, row, expected)
            if check:
                check(row, label)
            if len(plan.errors) == original_errors:
                slack_before = row.get("extra_vars")
                if notification:
                    current = decode_vars(slack_before)
                    desired = decode_vars(slack_after)
                    if current != desired or current.get("slack_interactive") is not desired["slack_interactive"]:
                        plan.slack_update = SlackExtraVarsUpdate(ids[key], organization, slack_before, slack_after)
                        return entry
                plan.reused.append(label)
        return entry

    project = definition["project"]
    project_entry = add("project", "projects", project["name"], {
        **project, **scope, "scm_url": config.scm_url, "credential": None, "allow_override": False,
        "scm_track_submodules": False, "scm_delete_on_update": False,
    })
    if project_entry.existing_id is not None:
        path = f"projects/{project_entry.existing_id}/"
        details = api.call(path, "Project 同期状態")
        status = details.get("status")
        revision = details.get("scm_revision")
        plan.project_sync = (
            status if status in (
                "successful", "failed", "error", "canceled", "never updated", "pending",
                "running", "waiting", "new", "missing", "ok", "updating",
            ) else "不明",
            revision if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision) else None,
        )
        if status != "successful" or not project_playbooks_ready(api, path):
            plan.errors.append(f"{project_entry.label} — 同期成功と必要なプレイブックを確認できません。")
            plan.manual.append("AAP で Project の同期結果とプレイブック一覧を確認してください。")
    slack_inventory = defaults["demo_slack_inventory"]
    if config.inventory == slack_inventory:
        plan.errors.append("RHEL Inventory と Slack Inventory は別々の Inventory を指定してください。")
    slack_host = defaults["demo_slack_host"]
    inventory_errors = len(plan.errors)
    slack_inv = add("slack_inventory", "inventories", slack_inventory, {
        "name": slack_inventory, **scope, "kind": "", "variables": "{}",
    })
    compatible_inventory = len(plan.errors) == inventory_errors
    host_entry = add("slack_host", "hosts", slack_host, {
        "name": slack_host, "inventory": Ref("slack_inventory"), "enabled": True,
        "variables": json.dumps(defaults["demo_slack_host_variables"]),
    }, identity={"name": slack_host, "inventory": Ref("slack_inventory")})
    if slack_inv.existing_id is not None and compatible_inventory:
        inv_path = f"inventories/{slack_inv.existing_id}/"
        host_ids = [object_id(row) for row in api.rows(inv_path + "hosts/", "Slack Inventory のホスト一覧")]
        if host_entry.existing_id is None or host_ids != [host_entry.existing_id]:
            plan.errors.append("Slack Inventory / hosts — 現在のホスト構成と期待 [localhost のみ] が異なります。")
        for relation in ("groups", "inventory_sources"):
            if api.rows(inv_path + relation + "/", f"Slack Inventory / {relation}"):
                plan.errors.append(f"Slack Inventory / {relation} — 現在 登録あり → 期待 なし")

    type_name = defaults["demo_slack_credential_type"]
    add("slack_type", "credential_types", type_name, {
        "name": type_name, "kind": "cloud",
        "inputs": {
            "fields": [{"id": "slack_webhook_url", "type": "string", "label": "Slack Webhook URL", "secret": True}],
            "required": ["slack_webhook_url"],
        },
        "injectors": {"env": {"SLACK_WEBHOOK_URL": "{{ slack_webhook_url }}"}},
    }, identity={"name": type_name})

    def check_slack(row, label):
        inputs = row.get("inputs")
        if not isinstance(inputs, dict) or not inputs.get("slack_webhook_url"):
            plan.errors.append(f"{label} / slack_webhook_url — 現在 未設定 → 期待 設定済み")
        if has_webhook:
            plan.errors.append(f"{label} / slack_webhook_url — 現在 保存済みの値（比較不可） → 期待 入力値")
            plan.manual.append("既存 Slack Credential を再利用する場合は Webhook を空欄にしてください。")

    credential_name = defaults["demo_slack_credential"]
    slack_cred = add("slack_credential", "credentials", credential_name, {
        "name": credential_name, **scope, "credential_type": Ref("slack_type"),
    }, check=check_slack)
    if slack_cred.existing_id is None and not has_webhook:
        plan.errors.append("Slack Credential — 新規作成には Slack Webhook URL が必要です。")
        plan.manual.append("Slack の Incoming Webhook を用意してください。")

    def check_template(row, label, credential_id):
        path = f"job_templates/{object_id(row)}/"
        current_credentials = sorted(object_id(item) for item in api.rows(path + "credentials/", label))
        compare(plan, label, {"credentials": current_credentials}, {"credentials": [credential_id]})
        check_launch_settings(api, plan, row, label, path)
        compare(plan, label, row, {"diff_mode": False, "start_at_task": "", "force_handlers": False})

    for template in definition["job_templates"]:
        name = template["name"]
        credential_key = "ssh" if template["playbook"] == "rhel_patch.yml" else "slack_credential"
        payload = {key: value for key, value in template.items() if key not in {
            "organization", "inventory", "project", "execution_environment", "credentials", "limit", "extra_vars",
        }}
        payload.update(project=Ref("project"), execution_environment=object_id(ee))
        payload.update(
            inventory=object_id(inventory) if credential_key == "ssh" else Ref("slack_inventory"),
            limit=config.host if credential_key == "ssh" else slack_host,
            extra_vars=json.dumps(
                {"target_rhsa": definition["target"]["advisory"], "apply_patch": False}
                if credential_key == "ssh" else {
                    **template["extra_vars"],
                    "target_host": config.host,
                    "target_rhsa": definition["target"]["advisory"],
                    "target_cve": definition["target"]["cve"],
                }
            ),
        )
        expected_id = object_id(machine) if credential_key == "ssh" else ids.get("slack_credential")
        add(name, "job_templates", name, payload, credential=credential_key,
            check=lambda row, label: check_template(row, label, expected_id))

    for key in ("workflow", "slack_workflow"):
        workflow = definition[key]
        integrated = key == "slack_workflow"
        extra = {"demo_requires_event": True} if integrated else {}
        wf = add(key, "workflow_job_templates", workflow["name"], {
            "name": workflow["name"], **scope,
            **({"ask_variables_on_launch": True, "extra_vars": json.dumps(extra)} if integrated else {}),
        })
        if wf.existing_id is not None:
            path = f"workflow_job_templates/{wf.existing_id}/"
            row = api.call(path, "Workflow 詳細")
            check_launch_settings(api, plan, row, wf.label, path, allow_variables=integrated)
            compare(plan, wf.label, row, {
                "extra_vars": extra, "inventory": None, "limit": None if row.get("limit") is None else "",
            })
            check_graph(api, plan, workflow, ids, path)
    return plan


def check_launch_settings(api, plan, row, label, path, *, allow_variables=False):
    for key, value in row.items():
        if (re.fullmatch(r"ask_[a-z_]+_on_launch", key)
                and not (allow_variables and key == "ask_variables_on_launch") and value is not False):
            plan.errors.append(f"{label} / {key} — 現在 有効 → 期待 false")
    disabled = {
        "survey_enabled": False, "webhook_service": "", "webhook_credential": None,
        "scm_branch": "", "job_tags": "", "skip_tags": "",
    }
    # The workflow serializer uses null for unset prompts.
    compare(plan, label, {k: "" if v is None and disabled.get(k) == "" else v for k, v in row.items()}, disabled)
    if api.rows(path + "schedules/", label + " の Schedule"):
        plan.errors.append(f"{label} / schedules — 現在 登録あり → 期待 なし")


def check_graph(api, plan, workflow, ids, path):
    nodes = api.rows(path + "workflow_nodes/", "Workflow Nodes")
    current = {node["identifier"]: node for node in nodes}
    desired = [node["id"] for node in workflow["nodes"]]
    if len(nodes) != len(desired) or set(current) != set(desired):
        plan.errors.append(f"Workflow / nodes — 現在の識別子の構成と期待 {desired!r} が異なります。")
        return
    for spec in workflow["nodes"]:
        original_errors = len(plan.errors)
        node = current[spec["id"]]
        label = f"Workflow Node / {spec['id']}"
        node_path = f"workflow_job_template_nodes/{object_id(node)}/"
        expected_success = [object_id(current[link["to"]]) for link in workflow["links"] if link["from"] == spec["id"]]
        compare(plan, label, node, {
            "extra_data": {}, "inventory": None, "execution_environment": None,
            "success_nodes": expected_success, "failure_nodes": [], "always_nodes": [],
        })
        for key in ("job_type", "scm_branch", "limit", "job_tags", "skip_tags", "diff_mode", "verbosity", "forks", "job_slice_count", "timeout"):
            if node.get(key) not in (None, ""):
                plan.errors.append(f"{label} / {key} — 現在 上書きあり → 期待 なし")
        if api.rows(node_path + "credentials/", label + " の Credentials"):
            plan.errors.append(f"{label} / credentials — 現在 上書きあり → 期待 なし")
        if spec["type"] == "approval":
            approval_id = node.get("unified_job_template")
            if type(approval_id) is not int or approval_id < 1:
                plan.errors.append(f"{label} / approval — 現在 未設定 → 期待 AAP Approval")
                continue
            # Do not follow arbitrary URLs provided by the Controller.
            approval_path = f"workflow_approval_templates/{approval_id}/"
            related_path = urlsplit(node.get("related", {}).get("unified_job_template", "")).path
            if related_path not in {GATEWAY_API_ROOT + approval_path, LEGACY_API_ROOT + approval_path}:
                plan.errors.append(f"{label} / approval — 現在 別のテンプレート種別 → 期待 AAP Approval")
                continue
            approval = api.call(approval_path, label)
            compare(plan, label, approval, {"name": spec["name"], "timeout": spec["timeout"]})
        else:
            compare(plan, label, node, {"unified_job_template": ids.get(spec["template"], "<作成予定>")})
        if len(plan.errors) == original_errors:
            plan.reused.append(label)


def project_playbooks_ready(api, path):
    playbooks = api.call(path + "playbooks/", "Project プレイブック一覧")
    return isinstance(playbooks, list) and all(name in playbooks for name in PLAYBOOKS)


def wait_for_project(api, project_id):
    path = f"projects/{project_id}/"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = api.call(path, "Project 同期状態").get("status")
        if status == "successful" and project_playbooks_ready(api, path):
            return
        if status in {"failed", "error", "canceled", "successful"}:
            break
        time.sleep(3)
    raise RestoreError("Project の初回 SCM 同期またはプレイブック検出に失敗しました。AAP の Project 画面を確認してください。")


def apply_plan(api, config, defaults, definition, webhook, plan, created, updated):
    if plan.slack_update:
        api.update_slack_extra_vars(plan.slack_update)
        updated.append(plan.slack_update.label)
    ids = {key: entry.existing_id for key, entry in plan.resources.items() if entry.existing_id is not None}
    ids["ssh"] = plan.prerequisites["credentials"]
    for key, entry in plan.resources.items():
        if entry.existing_id is not None:
            continue
        if api.find(entry.collection, entry.label, **resolve(entry.identity, ids)) is not None:
            raise RestoreError(f"{entry.label} — 承認後に同名のリソースが現れました。再実行して差分を確認してください。")
        payload = resolve(entry.payload, ids)
        if key == "slack_credential":
            payload["inputs"] = {"slack_webhook_url": webhook}
        result = api.post(entry.collection + "/", entry.label, payload)
        ids[key] = object_id(result)
        created.append(entry.label)
        if key == "project":
            wait_for_project(api, ids[key])
        if entry.template_credential:
            api.post(
                f"job_templates/{ids[key]}/credentials/", entry.label + " の Credential 関連付け",
                {"id": ids[entry.template_credential]},
            )
        if key in ("workflow", "slack_workflow"):
            create_graph(api, definition[key], ids[key], ids, created)

    verified = plan_restore(api, config, defaults, definition, has_webhook=False)
    pending = [verified.slack_update.label] if verified.slack_update else []
    if verified.errors or verified.missing or pending:
        raise RestoreError("復元後の照合に失敗しました。\n" + "\n".join(verified.errors + verified.missing + pending))


def create_graph(api, workflow, workflow_id, ids, created):
    node_ids = {}
    for spec in workflow["nodes"]:
        label = f"Workflow Node / {spec['id']}"
        payload = {"workflow_job_template": workflow_id, "identifier": spec["id"]}
        if spec["type"] == "job_template":
            payload["unified_job_template"] = ids[spec["template"]]
        node_id = object_id(api.post("workflow_job_template_nodes/", label, payload))
        node_ids[spec["id"]] = node_id
        created.append(label)
        if spec["type"] == "approval":
            api.post(
                f"workflow_job_template_nodes/{node_id}/create_approval_template/",
                label + " の Approval", {"name": spec["name"], "timeout": spec["timeout"]},
            )
    for link in workflow["links"]:
        label = f"Workflow Link / {link['from']} → {link['to']}"
        api.post(
            f"workflow_job_template_nodes/{node_ids[link['from']]}/success_nodes/",
            label, {"id": node_ids[link["to"]]},
        )
        created.append(label)


def read_local():
    defaults = yaml.safe_load((HERE / "aap-config/demo-vars.yml").read_text())
    definition = yaml.safe_load((ROOT / "aap-config/current-settings.yml").read_text())
    requirements = yaml.safe_load((HERE / "requirements.yml").read_text())
    if requirements != {"collections": []}:
        raise RestoreError("requirements.yml が復元ツールの Collection 定義と一致しません。")
    for playbook in (HERE / "restore_aap.yml", *(ROOT / name for name in PLAYBOOKS)):
        try:
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "--syntax-check", str(playbook)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RestoreError("ansible-playbook を実行できません。README.md のセットアップを確認してください。") from None
        if result.returncode:
            raise RestoreError(f"{playbook.name} — Ansible 構文または組み込みモジュールの解決に失敗しました。")
    expected_templates = {
        "RHEL-Patch-Check": "rhel_patch.yml",
        "RHEL-Patch-Apply": "rhel_patch.yml",
        "RHEL-Slack-Notification": "slack_notify.yml",
    }
    templates = definition["job_templates"]
    if len(templates) != len(expected_templates) or {
        item["name"]: item["playbook"] for item in templates
    } != expected_templates or any(
        item["extra_vars"].get("apply_patch") is not False
        for item in templates if item["playbook"] == "rhel_patch.yml"
    ):
        raise RestoreError("current-settings.yml の Job Template 定義を確認してください。")
    slack_template = next(item for item in templates if item["name"] == "RHEL-Slack-Notification")
    if slack_template["extra_vars"].get("slack_interactive") is not True:
        raise RestoreError("RHEL-Slack-Notification の Extra Variables に slack_interactive: true を設定してください。")
    nodes = definition["workflow"]["nodes"]
    links = definition["workflow"]["links"]
    if [(item["from"], item["to"], item["condition"]) for item in links] != [
        ("patch-check", "patch-approval", "success"), ("patch-approval", "patch-apply", "success"),
    ] or [(item["id"], item["type"], item.get("template")) for item in nodes] != [
        ("patch-check", "job_template", "RHEL-Patch-Check"), ("patch-approval", "approval", None),
        ("patch-apply", "job_template", "RHEL-Patch-Apply"),
    ]:
        raise RestoreError("current-settings.yml の Workflow 定義を確認してください。")
    integrated = definition["slack_workflow"]
    if integrated["name"] != "RHEL-Patch-Slack-Workflow" or [
        (node["id"], node["type"], node.get("template")) for node in integrated["nodes"]
    ] != [("patch-check", "job_template", "RHEL-Patch-Check"), ("patch-apply", "job_template", "RHEL-Patch-Apply")] or integrated["links"] != [
        {"from": "patch-check", "to": "patch-apply", "condition": "success"},
    ]:
        raise RestoreError("Slack 承認後に起動する Workflow の定義を確認してください。")
    print("Ansible の構文と組み込みモジュールを確認しました。追加 Collection は不要です。")
    return defaults, definition


def aap_origin(value):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.path not in ("", "/") or url.query or url.fragment
            or any(char.isspace() for char in value) or "\\" in value):
        raise RestoreError("AAP URL は https:// の Gateway または Controller のベース URL を指定してください。")
    return f"https://{url.netloc}"


def github_location(value):
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", value)
    if not match or match[2] in {".", ".."}:
        raise RestoreError("GitHub URL は https://github.com/OWNER/REPO の形式で指定してください。")
    return match[1], match[2]


def git_blob_sha(content):
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def describe_bytes(content):
    encoding, codec = "UTF-8 (BOM なし)", "utf-8"
    for bom, label, bom_codec in (
        (b"\xff\xfe\x00\x00", "UTF-32 LE BOM", "utf-32"),
        (b"\x00\x00\xfe\xff", "UTF-32 BE BOM", "utf-32"),
        (b"\xff\xfe", "UTF-16 LE BOM", "utf-16"),
        (b"\xfe\xff", "UTF-16 BE BOM", "utf-16"),
        (b"\xef\xbb\xbf", "UTF-8 BOM", "utf-8-sig"),
    ):
        if content.startswith(bom):
            encoding, codec = label, bom_codec
            break
    try:
        text = content.decode(codec)
    except UnicodeDecodeError:
        encoding = "文字コード未判定 (改行はバイト単位)"
        text = content.decode("latin-1")
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    endings = (("\r\n", "CRLF"), ("\n", "LF"), ("\r", "CR"))
    final = next((label for suffix, label in endings if text.endswith(suffix)), "なし")
    return f"{len(content)} bytes、{encoding}、CRLF={crlf}, LF={lf}, CR={cr}、末尾改行={final}"


def github_json(opener, url, label):
    response = request(opener, url, label, headers={
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    })
    try:
        result = json.loads(response)
    except ValueError:
        raise RestoreError(f"{label} — GitHub API の JSON 応答を確認できません。") from None
    if not isinstance(result, dict):
        raise RestoreError(f"{label} — GitHub API の応答形式が異なります。")
    return result


def verify_github(scm_url, branch, *, revision=None, playbooks=PLAYBOOKS):
    owner, repo = github_location(scm_url)
    repo_name = f"{owner}/{repo}"
    api = f"https://api.github.com/repos/{repo_name}"
    branch_label = f"GitHub {repo_name} ブランチ {branch!r}"
    opener = https_opener()
    commit_sha = revision
    if revision is None:
        branch_result = github_json(opener, f"{api}/branches/{quote(branch, safe='')}", branch_label)
        commit = branch_result.get("commit")
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise RestoreError(f"{branch_label} — コミット SHA を確認できません。")

    for name in playbooks:
        local_path = (ROOT / name).resolve()
        label = f"{branch_label}:{name} (commit {commit_sha})"
        url = f"{api}/contents/{quote(name)}?{urlencode({'ref': commit_sha})}"
        result = github_json(opener, url, label)
        remote_sha = result.get("sha")
        encoded = result.get("content")
        if (result.get("type") != "file" or result.get("path") != name
                or result.get("encoding") != "base64" or not isinstance(encoded, str)
                or not isinstance(remote_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", remote_sha)):
            raise RestoreError(f"{label} — Contents API のファイル情報を確認できません。")
        try:
            remote_content = base64.b64decode("".join(encoded.split()), validate=True)
        except ValueError:
            raise RestoreError(f"{label} — Base64 の内容をデコードできません。") from None
        decoded_sha = git_blob_sha(remote_content)
        if decoded_sha != remote_sha or result.get("size") != len(remote_content):
            raise RestoreError(f"{label} — API の Blob SHA {remote_sha} とデコード結果 {decoded_sha}、またはサイズが一致しません。")

        local_content = local_path.read_bytes()
        local_sha = git_blob_sha(local_content)
        if remote_sha != local_sha:
            raise RestoreError(
                f"プレイブックの Git Blob SHA が一致しません。\n"
                f"  GitHub: {label}\n"
                f"    Blob SHA {remote_sha} ({describe_bytes(remote_content)})\n"
                f"  ローカル: {local_path}\n"
                f"    Blob SHA {local_sha} ({describe_bytes(local_content)})"
            )
    print(f"{branch_label} (commit {commit_sha}) のプレイブックの Git Blob SHA が作業ツリーと一致しました。")
    return commit_sha


def prompt(settings):
    def value(label, key):
        return prompt_value(label, settings[key])

    origin = aap_origin(prompt_value("新しい AAP URL", settings.get("aap_url", "")))
    token = prompt_secret("復元用 AAP OAuth アクセストークン", "AAP_RESTORE_OAUTH_TOKEN")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise RestoreError("AAP アクセストークンの形式を確認してください。")
    organization = value("Organization", "demo_organization")
    inventory = value("RHEL Inventory 名", "demo_rhel_inventory")
    host = value("RHEL ホスト名", "demo_rhel_host")
    if host in {"all", "ungrouped", "localhost"} or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
        raise RestoreError("RHEL ホスト名は Inventory 内の単一ホスト名を指定してください。")
    ssh_credential = value("SSH Credential 名", "demo_ssh_credential")
    environment = value("Execution Environment 名", "demo_execution_environment")
    owner, repo = github_location(value("GitHub リポジトリー URL", "demo_scm_url"))
    webhook = prompt_secret("Slack Incoming Webhook URL（既存 Credential を再利用する場合は空欄）")
    if webhook and not WEBHOOK.fullmatch(webhook):
        raise RestoreError("Slack Incoming Webhook URL の形式を確認してください。")
    return Config(origin, organization, inventory, host, ssh_credential, environment,
                  f"https://github.com/{owner}/{repo}.git"), token, webhook


def show_items(label, items):
    print(f"\n{label}")
    for item in items or ["なし"]:
        print(f"  - {item}")


def run(dry_run, api, config, defaults, definition, webhook, confirm=input):
    created = []
    updated = []
    plan = Plan()
    sync_manual = "中断時は Project 同期と作成済みリソースを AAP で確認してください。"
    try:
        api.connect()
        github_revision = verify_github(config.scm_url, definition["project"]["scm_branch"])
        plan = plan_restore(api, config, defaults, definition, bool(webhook))
        if plan.project_sync is not None:
            status, revision = plan.project_sync
            print(f"Project SCM 同期状態 {status}、AAP Revision {revision or '未取得'}、GitHub Commit {github_revision}")
            if revision and revision != github_revision:
                print("AAP と GitHub の Revision が異なります。AAP の Project 同期履歴と通知 Job の Revision を確認してください。")
        missing = plan.missing
        for key in ("workflow", "slack_workflow"):
            workflow = definition[key]
            if f"workflow_job_templates / {workflow['name']}" in missing:
                missing += [f"{workflow['name']} / Node {node['id']}" for node in workflow["nodes"]]
                missing += [f"{workflow['name']} / Link {link['from']} → {link['to']}" for link in workflow["links"]]
        show_items("作成予定", missing)
        show_items("更新予定", [plan.slack_update.describe()] if plan.slack_update else [])
        show_items("再利用予定", plan.reused)
        if plan.errors:
            if dry_run:
                print("\nDry Run 終了。AAP への書き込みは 0 件です。")
            return 1
        if dry_run or not (missing or plan.slack_update):
            print("\nDry Run 完了。AAP への書き込みは 0 件です。" if dry_run else "\n定義が一致しています。")
            return 0
        if confirm("\n上記の作成・更新と、該当する新規 Project の初回 SCM 同期を承認する場合は RESTORE と入力 > ") != "RESTORE":
            print("承認がないため中止しました。")
            return 1
        confirmed = plan_restore(api, config, defaults, definition, bool(webhook))
        if confirmed != plan:
            plan.errors.extend(confirmed.errors)
            raise RestoreError("承認後に事前検証の結果が変わりました。再実行して計画を確認してください。")
        api.write_enabled = True
        apply_plan(api, config, defaults, definition, webhook, plan, created, updated)
        return 0
    except RestoreError as error:
        plan.errors.append(str(error))
        if api.write_enabled:
            plan.manual.append(sync_manual)
        return 1
    except (KeyboardInterrupt, EOFError):
        plan.errors.append("実行を中断しました。")
        if api.write_enabled:
            plan.manual.append(sync_manual)
        return 130
    finally:
        api.write_enabled = False
        show_items("作成したリソース", created)
        show_items("変更した既存リソース", updated)
        show_items("再利用したリソース" if not plan.errors and not dry_run and (created or not plan.missing)
                   else "再利用候補", plan.reused)
        show_items("エラー・競合", plan.errors)
        show_items("手動設定", plan.manual)
        if not dry_run and not plan.errors and (created or not plan.missing):
            show_items("次の動作確認", [
                "AAP で Project の同期結果と各 Job Template の Inventory・Credential・EE を確認。",
                "Workflow Visualizer で Check → Approval → Apply と、両パッチテンプレートの apply_patch=false を確認。",
                "必要に応じて RHEL-Patch-Check と RHEL-Slack-Notification を個別に手動実行。",
            ])


def main():
    parser = argparse.ArgumentParser(prog="restore-demo.sh", description="AAP RHEL パッチデモを対話式で復元します。")
    parser.add_argument("--dry-run", action="store_true", help="接続と差分のみ確認します。")
    parser.add_argument("--save-config", action="store_true", help="確認後に非秘密設定をローカルへ保存します。")
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 10):
            raise RestoreError("Python 3.10 以降が必要です。")
        defaults, definition = read_local()
        local = load_config()
        settings = {**defaults, "aap_url": local.get("aap_url", "")}
        settings.update({key: local[field] for field, key in LOCAL_INPUTS.items() if local.get(field)})
        config, token, webhook = prompt(settings)
        status = run(args.dry_run, Controller(config.origin, token), config, defaults, definition, webhook)
        if status == 0 and args.save_config:
            save_config({
                "aap_url": config.origin,
                "organization": config.organization,
                "inventory": config.inventory,
                "host": config.host,
                "ssh_credential": config.ssh_credential,
                "execution_environment": config.environment,
                "github_url": config.scm_url,
            }, secrets=(token, webhook))
        return status
    except (RestoreError, ConfigError) as error:
        print(f"エラー — {error}", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\n入力を中断しました。", file=sys.stderr)
        return 130
    except (AttributeError, KeyError, TypeError, ValueError, OSError, yaml.YAMLError):
        print("エラー — ローカル設定、URL、または API 応答の形式を確認してください。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
