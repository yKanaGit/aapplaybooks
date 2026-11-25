"""Read and report the event-bound RHSA and RPM observations."""

import json
import re
from collections import defaultdict


STAGES = {
    "patch-check": "RHEL-Patch-Check",
    "patch-apply": "RHEL-Patch-Apply",
    "patch-verify": "RHEL-Patch-Verify",
}
JOB_STATUSES = {"new", "pending", "waiting", "running", "successful", "failed", "error", "canceled"}
AVAILABILITY = {"available": "RHSA 適用候補あり", "not_listed": "RHSA 適用候補の表示なし", "unknown": "未確認"}
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
INSTALLED_VERSION = re.compile(r"[0-9]+:[A-Za-z0-9._+~^]+-[A-Za-z0-9._+~^]+\.[A-Za-z0-9_]+")
RPM_ISSUES = {
    "yum_all_failed": "RHSA 全件照会に失敗",
    "yum_available_failed": "RHSA 適用候補の照会に失敗",
    "yum_all_parse": "RHSA 全件出力に解析できない対象行あり",
    "yum_available_parse": "RHSA 適用候補の出力に解析できない対象行あり",
    "rpm_scope_empty": "RHSA の関連 RPM 名を特定できません",
    "rpm_query_failed": "RPM データベースの照会に失敗",
    "rpm_query_stderr": "RPM 照会が標準エラーを出力",
    "rpm_inventory_empty": "インストール済み RPM 一覧が空",
    "rpm_inventory_parse": "RPM 一覧に解析できない行あり",
}
FAILURE_TASKS = {
    "Require the demo to stay read-only": "apply_patch=false の安全条件を満たしていません",
    "Require explicit patch mode on the approved Apply job": "apply_patch の設定または承認イベントを確認できません",
    "Revalidate the approved Slack event against the job template and hosts": "承認イベントと実行対象の照合に失敗",
    "Require the approved event for Verify": "Verify に必要な承認イベントを確認できません",
    "Show target advisory": "RHSA 情報の照会に失敗",
    "Confirm the approved advisory was found": "対象ホストで RHSA を確認できません",
    "Apply the approved RHSA": "対象 RHSA の適用コマンドが失敗しました",
    "Save the event-bound Check or Verify observation": "RHEL 状態の成果物を保存できません",
}


def observation(job, event, phase):
    artifacts = job.get("artifacts")
    state = artifacts.get("demo_rhel_state") if isinstance(artifacts, dict) else None
    if (not isinstance(state, dict) or state.get("event_id") != event["id"]
            or state.get("hostname") != event["hostname"] or state.get("advisory") != event["advisory"]
            or state.get("phase") != phase or not isinstance(state.get("availability"), str)
            or state["availability"] not in AVAILABILITY or type(state.get("rpm_complete")) is not bool):
        return None
    names, rpms = state.get("package_names"), state.get("installed_rpms")
    if (not isinstance(names, list) or not isinstance(rpms, list)
            or any(not isinstance(name, str) or not PACKAGE_NAME.fullmatch(name) for name in names)
            or (state["rpm_complete"] and not names)):
        return None
    for row in rpms:
        if not isinstance(row, str) or len(row) > 250:
            return None
        name, _, version = row.partition(" ")
        if name not in names or not INSTALLED_VERSION.fullmatch(version):
            return None
    if "rpm_issues" in state:
        issues = state["rpm_issues"]
        if (not isinstance(issues, list) or any(not isinstance(issue, str) or issue not in RPM_ISSUES for issue in issues)
                or state["rpm_complete"] != (not issues)):
            return None
    return state


def describe_observation(state):
    if state is None:
        return "未確認（成果物なし、または照合不可）"
    if not state["rpm_complete"]:
        rpm_text = "未確認"
        issues = state.get("rpm_issues", [])
        if issues:
            rpm_text += "（" + "、".join(RPM_ISSUES[issue] for issue in issues) + "）"
    else:
        rpms = sorted(set(state["installed_rpms"]))
        rpm_text = "、".join(rpms[:6]) or "該当するインストール済み RPM なし"
        if len(rpms) > 6:
            rpm_text += f"（ほか {len(rpms) - 6} 件）"
    return f"{AVAILABILITY[state['availability']]} / 関連 RPM {rpm_text}"


def describe_rpm_change(before, after):
    if not before or not after or not before["rpm_complete"] or not after["rpm_complete"]:
        return "未確認（適用前後の RPM 情報が揃っていません）"
    if set(before["package_names"]) != set(after["package_names"]):
        return "未確認（対象 RPM 名の範囲が異なります）"
    old, new = set(before["installed_rpms"]), set(after["installed_rpms"])
    if old == new:
        return "変更なし"
    return f"変更あり（追加 {len(new - old)} 件、比較対象から消えたバージョン {len(old - new)} 件）"


def inspect_rhel_jobs(controller, job_ids, expected):
    """Compare recorded observations without running jobs or opening local state."""
    from controller import ApprovalError, PLAYBOOK

    snapshots = {}
    for job_id in job_ids:
        endpoint = f"jobs/{job_id}/"
        print(f"GET {controller.api_root}{endpoint}")
        try:
            job = controller.get(endpoint)
        except ApprovalError as error:
            print(f"Job #{job_id}：{error}")
            continue
        phase = {"RHEL-Patch-Check": "before", "RHEL-Patch-Verify": "after"}.get(job.get("name"))
        artifacts = job.get("artifacts")
        raw = artifacts.get("demo_rhel_state") if isinstance(artifacts, dict) else None
        event_id = raw.get("event_id") if isinstance(raw, dict) else None
        if (job.get("id") != job_id or job.get("playbook") != PLAYBOOK or job.get("status") != "successful"
                or not phase or not isinstance(event_id, str) or not re.fullmatch(r"[0-9a-f]{32}", event_id)):
            print(f"Job #{job_id}：ジョブまたは成果物を確認できません。")
            continue
        state = observation(job, {**expected, "id": event_id}, phase)
        if state is None:
            print(f"Job #{job_id}：成果物が対象ホスト・RHSA・形式と一致しません。")
            continue
        print(json.dumps({key: state[key] for key in
                          ("package_names", "installed_rpms", "rpm_complete", "availability")}, ensure_ascii=False))
        if "rpm_issues" in state:
            print("rpm_issues: " + json.dumps(state["rpm_issues"]))
        elif not state["rpm_complete"]:
            print("rpm_issues: 旧形式の成果物に未確認の理由は保存されていません。")
        snapshots[phase] = state

    before, after = snapshots.get("before"), snapshots.get("after")
    if before and after and before["event_id"] != after["event_id"]:
        print("比較結果：未確認（前後のイベント ID が異なります）")
        return 1
    print("比較結果：" + describe_rpm_change(before, after))
    complete_pair = (before and after and before["rpm_complete"] and after["rpm_complete"]
                     and set(before["package_names"]) == set(after["package_names"]))
    return 0 if complete_pair else 1


def job_failure_reason(controller, job_id):
    from controller import ApprovalError

    try:
        result = controller.get(f"jobs/{job_id}/job_events/?event__in=runner_on_failed,runner_on_unreachable&page_size=10")
    except ApprovalError:
        return None
    events = result.get("results")
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict) or event.get("job") != job_id:
            continue
        if event.get("event") == "runner_on_unreachable":
            return "対象ホストに接続できません"
        details = event.get("event_data")
        task = details.get("task") if isinstance(details, dict) else None
        if event.get("event") == "runner_on_failed" and isinstance(task, str) and task in FAILURE_TASKS:
            return FAILURE_TASKS[task]
    return None


def workflow_result_text(controller, workflow_id, status, event):
    from controller import ApprovalError, ControllerHTTPError, PLAYBOOK, positive_id, variables

    snapshots = {}
    nodes_by_stage = defaultdict(list)
    stage_statuses = []
    patch_status = "未確認"
    try:
        for node in controller.rows(f"workflow_jobs/{workflow_id}/workflow_nodes/"):
            stage = node.get("identifier")
            if isinstance(stage, str) and stage in STAGES and node.get("workflow_job") == workflow_id:
                nodes_by_stage[stage].append(node)
    except ApprovalError as error:
        suffix = f" (HTTP {error.status})" if isinstance(error, ControllerHTTPError) else ""
        stage_statuses.append(f"ノード一覧：未確認{suffix}")
    for stage, name in STAGES.items():
        nodes = nodes_by_stage[stage]
        if len(nodes) != 1:
            stage_statuses.append(f"{name}：未確認")
            continue
        job_id = nodes[0].get("job")
        if not positive_id(job_id):
            stage_statuses.append(f"{name}：未実行")
            continue
        try:
            job = controller.get(f"jobs/{job_id}/")
        except ApprovalError as error:
            suffix = f" (HTTP {error.status})" if isinstance(error, ControllerHTTPError) else ""
            stage_statuses.append(f"{name} #{job_id}：未確認{suffix}")
            continue
        stage_status = job.get("status")
        if (job.get("id") != job_id or job.get("name") != name or job.get("playbook") != PLAYBOOK
                or not isinstance(stage_status, str) or stage_status not in JOB_STATUSES):
            stage_statuses.append(f"{name} #{job_id}：未確認（ジョブ照合不可）")
            continue
        reason = job_failure_reason(controller, job_id) if stage_status in {"failed", "error"} else None
        explanation = f"（{reason}）" if reason else ""
        stage_statuses.append(f"{name} #{job_id}：{stage_status}{explanation}")
        if stage == "patch-apply" and stage_status == "successful":
            try:
                apply_patch = variables(job.get("extra_vars")).get("apply_patch")
            except ApprovalError:
                apply_patch = None
            if apply_patch is False:
                patch_status = "スキップ（apply_patch=false）"
            elif apply_patch is True:
                patch_status = "適用処理完了（apply_patch=true）"
        if stage_status == "successful" and stage in ("patch-check", "patch-verify"):
            phase = "before" if stage == "patch-check" else "after"
            snapshots[phase] = observation(job, event, phase)

    before, after = (snapshots.get(phase) for phase in ("before", "after"))
    resolution = "未確認"
    if after and after["availability"] == "available":
        resolution += "（対象 RHSA が引き続き適用候補に表示されています）"
    return "\n".join([
        f"RHEL 対応結果【デモ】 | イベントID：{event['id']}",
        f"対象ホスト：{event['hostname']} | CVE：{event['cve']} | RHSA：{event['advisory']}",
        f"AAP 実行結果：Workflow Job #{workflow_id} | {status}",
        *stage_statuses,
        f"適用前状態：{describe_observation(before)}",
        f"適用後状態：{describe_observation(after)}",
        f"更新有無（関連 RPM の観測差分）：{describe_rpm_change(before, after)}",
        f"パッチ適用：{patch_status}",
        f"脆弱性解消：{resolution}",
    ])
