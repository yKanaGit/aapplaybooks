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
LEGACY_AVAILABILITY = {"available", "not_listed", "unknown"}
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
RPM_EVR = r"[0-9]+:[A-Za-z0-9._+~^]+-[A-Za-z0-9._+~^]+"
RPM_ARCH = r"[A-Za-z0-9_]+"
INSTALLED_VERSION = re.compile(RPM_EVR + r"\." + RPM_ARCH)
VERDICT = {"fixed": "修正RPM適用済み", "pending": "未適用の更新あり", "unknown": "判定不能"}
VERIFICATION_REASONS = {
    "fixed_versions_confirmed": ("fixed", "インストール済みの対象 RPM は修正版以上"),
    "updates_remain": ("pending", "インストール済み RPM に対応する更新候補"),
    "incomplete_queries": ("unknown", "照会または出力の解析が不完全"),
    "no_installed_targets": ("unknown", "対象のインストール済み RPM を確認できません"),
    "fixed_version_unconfirmed": ("unknown", "修正版以上であることを確認できない RPM があります"),
    "rpm_compare_unavailable": ("unknown", "RPM のバージョン比較を実行できません"),
}
QUERY_KEYS = {"yum_all", "yum_updates", "yum_installed", "rpm_inventory"}
RPM_ISSUES = {
    "yum_all_failed": "RHSA 全件照会に失敗",
    "yum_available_failed": "RHSA 適用候補の照会に失敗",
    "yum_all_parse": "RHSA 全件出力に解析できない対象行あり",
    "yum_available_parse": "RHSA 適用候補の出力に解析できない対象行あり",
    "yum_updates_failed": "RHSA 更新候補の照会に失敗",
    "yum_updates_parse": "RHSA 更新候補に解析できない対象行あり",
    "yum_installed_failed": "適用済み RHSA の照会に失敗",
    "yum_installed_parse": "適用済み RHSA に解析できない対象行あり",
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
    "Compare installed RPMs with the advisory metadata": "RHSA と RPM の照会を実行できません",
    "Save the event-bound Check or Verify observation": "RHEL 状態の成果物を保存できません",
}


def observation(job, event, phase):
    artifacts = job.get("artifacts")
    state = artifacts.get("demo_rhel_state") if isinstance(artifacts, dict) else None
    if (not isinstance(state, dict) or state.get("event_id") != event["id"]
            or state.get("hostname") != event["hostname"] or state.get("advisory") != event["advisory"]
            or state.get("phase") != phase or type(state.get("rpm_complete")) is not bool):
        return None
    if "verification" not in state:
        availability = state.get("availability")
        if not isinstance(availability, str) or availability not in LEGACY_AVAILABILITY:
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
    if "verification" in state and not valid_verification(state):
        return None
    return state


def valid_verification(state):
    verification = state["verification"]
    if not isinstance(verification, dict):
        return False
    reason = verification.get("reason")
    if not isinstance(reason, str) or reason not in VERIFICATION_REASONS:
        return False
    status, _ = VERIFICATION_REASONS[reason]
    if verification.get("status") != status:
        return False
    codes = verification.get("query_rc")
    if not isinstance(codes, dict) or set(codes) != QUERY_KEYS or any(type(rc) is not int for rc in codes.values()):
        return False
    candidates = verification.get("remaining_updates")
    if not isinstance(candidates, list) or bool(candidates) != (status == "pending"):
        return False
    if status == "fixed" and (not state["rpm_complete"] or not state["installed_rpms"] or any(codes.values())):
        return False
    if candidates and (codes["yum_updates"] != 0 or codes["rpm_inventory"] != 0):
        return False
    installed = {(row.split(" ", 1)[0], row.rsplit(".", 1)[1]) for row in state["installed_rpms"]}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return False
        name, arch, evr = (candidate.get(key) for key in ("name", "arch", "evr"))
        if (not isinstance(name, str) or not isinstance(arch, str) or (name, arch) not in installed
                or not isinstance(evr, str) or len(evr) > 250 or not re.fullmatch(RPM_EVR, evr)):
            return False
    return True


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
    return f"関連 RPM {rpm_text}"


def describe_verification(state):
    if state is None:
        return "判定不能（Verify の成果物なし、または照合不可）"
    verification = state.get("verification")
    if verification is None:
        return "判定不能（旧形式の Verify 成果物に判定根拠なし）"
    _, reason = VERIFICATION_REASONS[verification["reason"]]
    candidates = verification["remaining_updates"]
    if candidates:
        rows = [f"{item['name']}.{item['arch']} {item['evr']}" for item in candidates[:6]]
        reason += " " + "、".join(rows)
        if len(candidates) > 6:
            reason += f"（ほか {len(candidates) - 6} 件）"
    return f"{VERDICT[verification['status']]}（{reason}）"


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
        fields = ("package_names", "installed_rpms", "rpm_complete")
        report = {key: state[key] for key in fields if key in state}
        if "verification" in state:
            verification = state["verification"]
            report["verification"] = {
                **{key: verification[key] for key in ("status", "reason", "query_rc")},
                "remaining_updates": [{key: candidate[key] for key in ("name", "arch", "evr")}
                                      for candidate in verification["remaining_updates"]],
            }
        else:
            report["availability"] = state["availability"]
        print(json.dumps(report, ensure_ascii=False))
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
    return "\n".join([
        f"RHEL 対応結果【デモ】 | イベントID：{event['id']}",
        f"対象ホスト：{event['hostname']} | CVE：{event['cve']} | RHSA：{event['advisory']}",
        f"AAP 実行結果：Workflow Job #{workflow_id} | {status}",
        *stage_statuses,
        f"適用前状態：{describe_observation(before)}",
        f"適用後状態：{describe_observation(after)}",
        f"更新有無（関連 RPM の観測差分）：{describe_rpm_change(before, after)}",
        f"パッチ適用：{patch_status}",
        f"RHSA 判定：{describe_verification(after)}",
    ])
