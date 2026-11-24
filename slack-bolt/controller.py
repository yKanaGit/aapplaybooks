"""Read the running demo graph and decide one specific Controller approval."""

import json
import re
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import yaml


API = "/api/v2/"
GATEWAY_API = "/api/controller/v2/"
WORKFLOW = "RHEL-Patch-Workflow"
SLACK_WORKFLOW = "RHEL-Patch-Slack-Workflow"
PLAYBOOK = "rhel_patch.yml"
APPROVAL_NAME = "Approve RHEL Security Patch"


class ApprovalError(Exception):
    """A message safe to show in Slack or on the terminal."""


class ControllerHTTPError(ApprovalError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"AAP API が HTTP {status} を返しました。")


class ControllerTransportError(ApprovalError):
    def __init__(self, error):
        reason = error.reason if isinstance(error, URLError) and isinstance(error.reason, OSError) else error
        self.source_type = type(reason).__name__
        super().__init__("AAP API の通信または応答の確認に失敗しました。")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def positive_id(value):
    return type(value) is int and value > 0


def variables(value):
    try:
        parsed = yaml.safe_load(value) if isinstance(value, str) else value
    except yaml.YAMLError:
        raise ApprovalError("AAP の変数定義を安全に解釈できません。") from None
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ApprovalError("AAP の変数定義がマッピングではありません。")
    return parsed


class Controller:
    def __init__(self, origin, token, workflow_job=None, approval=None, *, opener=None):
        try:
            url = urlsplit(origin)
            port = url.port
        except ValueError:
            raise ApprovalError("AAP_URL の形式を確認してください。") from None
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.path not in ("", "/") or url.query or url.fragment
                or any(char.isspace() for char in origin) or "\\" in origin):
            raise ApprovalError("AAP_URL に HTTPS の Controller ベース URL を指定してください。")
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
            raise ApprovalError("AAP_OAUTH_TOKEN を設定してください。")
        if (workflow_job is not None or approval is not None) and not all(map(positive_id, (workflow_job, approval))):
            raise ApprovalError("Workflow Job ID と Approval ID には正の整数が必要です。")
        netloc = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        if port not in (None, 443):
            netloc += f":{port}"
        self.origin = f"https://{netloc}"
        self.token = token
        self.workflow_job = workflow_job
        self.approval = approval
        self.api_root = API
        self.opener = opener if opener is not None else build_opener(
            NoRedirect(), HTTPSHandler(context=ssl.create_default_context())
        )

    def get(self, endpoint, *, authenticate=True):
        return self._request(endpoint, authenticate=authenticate)

    def _request(self, endpoint, *, authenticate=True, post=False, api_root=None, expected_status=204, payload=None):
        headers = {"Accept": "application/json"}
        if authenticate:
            headers["Authorization"] = f"Bearer {self.token}"
        if post:
            headers["Content-Type"] = "application/json"
        request = Request(self.origin + (api_root or self.api_root) + endpoint, headers=headers,
                          data=json.dumps(payload or {}).encode() if post else None)
        try:
            with self.opener.open(request, timeout=8) as response:
                if post:
                    if response.status != expected_status:
                        raise ApprovalError("AAP の書き込み結果を確定できません。")
                    if expected_status == 204:
                        return
                result = json.loads(response.read(2 * 1024 * 1024))
        except HTTPError as error:
            error.close()
            raise ControllerHTTPError(error.code) from None
        except (OSError, URLError, ValueError) as error:
            raise ControllerTransportError(error) from None
        if not isinstance(result, dict):
            raise ApprovalError("AAP API の応答形式が想定と異なります。")
        return result

    def check_version(self):
        for root, minor in ((GATEWAY_API, "[67]"), (API, "5")):
            try:
                ping = self._request("ping/", authenticate=False, api_root=root)
            except ControllerHTTPError as error:
                if error.status == 404:
                    continue
                raise
            version = ping.get("version")
            if not isinstance(version, str) or not re.fullmatch(rf"4\.{minor}\.\d+(?:[-.+][A-Za-z0-9.-]+)?", version):
                raise ApprovalError("AAP 2.4 の Controller 4.5.x、または AAP 2.5／2.6 の Gateway が必要です。")
            self.api_root = root
            return version
        raise ApprovalError("Gateway と従来型 Controller の ping が両方とも HTTP 404 でした。AAP のベース URL を確認してください。")

    def for_approval(self, workflow_job, approval):
        selected = Controller(self.origin, self.token, workflow_job, approval, opener=self.opener)
        selected.api_root = self.api_root
        return selected

    def launch_demo_workflow(self, template_id, event):
        if not positive_id(template_id):
            raise ApprovalError("Workflow Template ID が不正です。")
        extra = {"demo_event_id": event["id"], "demo_target_host": event["hostname"], "demo_target_rhsa": event["advisory"]}
        result = self._request(f"workflow_job_templates/{template_id}/launch/", post=True, expected_status=201,
                               payload={"extra_vars": extra})
        job_id = result.get("workflow_job")
        if (not positive_id(job_id) or result.get("id") != job_id
                or result.get("workflow_job_template") != template_id or result.get("name") != SLACK_WORKFLOW):
            raise ApprovalError("AAP の起動応答から Workflow Job ID を確定できません。")
        return job_id

    def rows(self, endpoint, **filters):
        for page in range(1, 101):
            query = urlencode({**filters, "page": page, "page_size": 100})
            data = self.get(f"{endpoint}?{query}")
            rows = data.get("results")
            if not isinstance(rows, list) or "next" not in data or any(not isinstance(row, dict) for row in rows):
                raise ApprovalError("AAP API の一覧形式が想定と異なります。")
            yield from rows
            if not data["next"]:
                return
        raise ApprovalError("AAP の検索結果がページ上限を超えました。")

    def discover_pending(self):
        """Discover only readable approvals attached to active demo workflows."""
        candidates = set()
        reasons = set()
        running = False
        for workflow in self.rows("workflow_jobs/", name=WORKFLOW, status="running", order_by="id"):
            job_id = workflow.get("id")
            if workflow.get("name") != WORKFLOW or workflow.get("status") != "running" or not positive_id(job_id):
                continue
            running = True
            try:
                nodes = list(self.rows(f"workflow_jobs/{job_id}/workflow_nodes/", identifier="patch-approval"))
                matches = [node for node in nodes if node.get("identifier") == "patch-approval" and node.get("workflow_job") == job_id]
                if len(matches) != 1:
                    reasons.add("実行中の Workflow にデモ用承認ノードを一意に確認できません。")
                    continue
                approval_id = matches[0].get("job")
                if not positive_id(approval_id):
                    reasons.add("Check の実行中などで、Workflow が Approval に到達していません。")
                    continue
                self._require_pending_approval(approval_id)
                candidates.add((job_id, approval_id))
            except ControllerHTTPError as error:
                if error.status not in (403, 404):
                    raise
                reasons.add(f"承認ノードまたは Approval を参照できません (HTTP {error.status})。")
            except ControllerTransportError:
                raise
            except ApprovalError as error:
                reasons.add(str(error))
        if not running:
            reasons.add("参照できる実行中の RHEL-Patch-Workflow がありません。未起動・終了済み・参照権限を確認してください。")
        return sorted(candidates), sorted(reasons)

    def pending(self):
        """Confirm both the pending approval and the running workflow snapshot."""
        self._require_pending_approval()
        workflow = self.get(f"workflow_jobs/{self.workflow_job}/")
        if (workflow.get("id") != self.workflow_job or workflow.get("name") != WORKFLOW
                or workflow.get("status") != "running" or not positive_id(workflow.get("workflow_job_template"))):
            raise ApprovalError("指定した Workflow Job が稼働中のデモ Workflow と一致しません。")
        if variables(workflow.get("extra_vars")) or workflow.get("scm_branch"):
            raise ApprovalError("Workflow Job に Extra Variables または SCM ブランチの上書きがあります。")

        page = self.get(f"workflow_jobs/{self.workflow_job}/workflow_nodes/?page_size=10")
        rows = page.get("results")
        if page.get("next") or not isinstance(rows, list) or len(rows) != 3 or any(not isinstance(row, dict) for row in rows):
            raise ApprovalError("Workflow のノード構成がデモ定義と一致しません。")
        nodes = {row.get("identifier"): row for row in rows if isinstance(row.get("identifier"), str)}
        if set(nodes) != {"patch-check", "patch-approval", "patch-apply"}:
            raise ApprovalError("Workflow のノード識別子がデモ定義と一致しません。")
        check, approval, apply = (nodes[key] for key in ("patch-check", "patch-approval", "patch-apply"))
        if approval.get("job") != self.approval:
            raise ApprovalError("Approval ID が指定した Workflow Job の承認ノードと一致しません。")
        if any(
            row.get("workflow_job") != self.workflow_job or not positive_id(row.get("id"))
            or row.get("failure_nodes") != [] or row.get("always_nodes") != []
            for row in rows
        ) or (check.get("success_nodes"), approval.get("success_nodes"), apply.get("success_nodes")) != (
            [approval["id"]], [apply["id"]], [],
        ):
            raise ApprovalError("Workflow の遷移が Check → Approval → Apply の成功経路と一致しません。")
        if apply.get("job") is not None or variables(apply.get("extra_data")) or apply.get("scm_branch"):
            raise ApprovalError("Apply ノードが実行済みか、変数または SCM ブランチが上書きされています。")

        check_job_id, check_template_id = check.get("job"), check.get("unified_job_template")
        apply_template_id = apply.get("unified_job_template")
        if not all(positive_id(item) for item in (check_job_id, check_template_id, apply_template_id)):
            raise ApprovalError("Check Job または Apply Template の ID を確認できません。")
        check_job = self.get(f"jobs/{check_job_id}/")
        if (check_job.get("id") != check_job_id or check_job.get("job_template") != check_template_id
                or check_job.get("status") != "successful" or check_job.get("name") != "RHEL-Patch-Check"
                or check_job.get("playbook") != PLAYBOOK or check_job.get("artifacts") != {}
                or variables(check_job.get("extra_vars")).get("apply_patch") is not False):
            raise ApprovalError("デモ Check Job の正常終了または安全な変数設定を確認できません。")

        template = self.get(f"job_templates/{apply_template_id}/")
        extras = variables(template.get("extra_vars"))
        if (template.get("id") != apply_template_id or template.get("name") != "RHEL-Patch-Apply"
                or template.get("playbook") != PLAYBOOK or not positive_id(template.get("project"))
                or template["project"] != check_job.get("project") or template.get("scm_branch")
                or extras.get("apply_patch") is not False or set(extras) - {"apply_patch", "target_rhsa"}):
            raise ApprovalError("デモ Apply Template の apply_patch: false と構成を確認できません。")

    def _require_pending_approval(self, approval_id=None):
        approval_id = self.approval if approval_id is None else approval_id
        if not positive_id(approval_id):
            raise ApprovalError("Approval ID を指定してください。")
        approval = self.get(f"workflow_approvals/{approval_id}/")
        if (approval.get("id") != approval_id or approval.get("type") != "workflow_approval"
                or approval.get("name") != APPROVAL_NAME):
            raise ApprovalError("指定した ID の Workflow Approval がデモ定義と一致しません。")
        if approval.get("timed_out") is True:
            raise ApprovalError("AAP の承認期限が切れています。")
        if approval.get("status") != "pending":
            raise ApprovalError("AAP の承認は処理済み、または承認待ちではありません。")
        if approval.get("timed_out") is not False or "approval_expiration" not in approval:
            raise ApprovalError("AAP の承認期限を確認できません。")
        expiration = approval["approval_expiration"]
        if expiration is not None:
            try:
                deadline = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
                expired = deadline <= datetime.now(timezone.utc)
            except (AttributeError, TypeError, ValueError):
                raise ApprovalError("AAP の承認期限を確認できません。") from None
            if expired:
                raise ApprovalError("AAP の承認期限が切れています。")
        if approval.get("can_approve_or_deny") is not True:
            raise ApprovalError("AAP OAuth のユーザーに対象 Workflow の承認権限がありません。")

    def decide(self, action):
        if action not in ("approve", "deny"):
            raise ApprovalError("未対応の承認操作です。")
        self._require_pending_approval()
        self._request(f"workflow_approvals/{self.approval}/{action}/", post=True)
