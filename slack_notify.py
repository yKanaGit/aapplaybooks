#!/usr/bin/env python3
"""Send vulnerability JSON report to a Slack Incoming Webhook.

This script works with both HCC (cloud) and Satellite (on-premises) vulnerability
reports that follow the unified JSON schema. Supports both single CVE mode and
multiple CVE (top) mode.
"""

import argparse
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


# Utility functions and classes
class NoRedirect(HTTPRedirectHandler):
    """HTTP handler that prevents automatic redirects."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def obj(value):
    """Return value if it's a dict, otherwise return empty dict."""
    return value if isinstance(value, dict) else {}


def count(value):
    """Return value if it's a non-negative integer, otherwise return None."""
    return value if type(value) is int and value >= 0 else None


def display(value):
    """Convert value to a printable string, replacing non-printable characters."""
    if not isinstance(value, (str, int, float)):
        return "不明"
    return " ".join("".join(char if char.isprintable() else " " for char in str(value)).split())


def rhsa_ids(advisories):
    """Extract RHSA IDs from a list of advisories."""
    if not isinstance(advisories, list):
        return None
    rhsa_pattern = re.compile(r"RHSA-[0-9]{4}:[0-9]+")
    return list(dict.fromkeys(
        item for item in advisories if isinstance(item, str) and rhsa_pattern.fullmatch(item)
    ))


def utc_to_jst(utc_string):
    """Convert UTC ISO 8601 datetime string to JST display string."""
    if not isinstance(utc_string, str):
        return "不明"
    try:
        # Parse ISO 8601 datetime
        dt = datetime.fromisoformat(utc_string.replace('Z', '+00:00'))
        # Convert to JST (UTC+9)
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime("%Y-%m-%d %H:%M:%S JST")
    except (ValueError, TypeError):
        return "不明"


def date_only(iso_string):
    """Extract date part from ISO 8601 datetime string."""
    if not isinstance(iso_string, str):
        return "不明"
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "不明"


def extract_satellite_tags(tags):
    """Extract Satellite-specific tags from host tags list.

    Returns dict with content_view, lifecycle_environment, organization, location.
    """
    if not isinstance(tags, list):
        return {}

    result = {}
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        if tag.get("namespace") != "satellite":
            continue

        key = tag.get("key")
        value = tag.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
            continue

        if key in ("content_view", "lifecycle_environment", "organization", "location"):
            result[key] = value

    return result


def aggregate_rhsa_from_hosts(hosts):
    """Aggregate RHSA IDs from all hosts, removing duplicates.

    Returns list of unique RHSA IDs, or None if hosts is None.
    """
    if hosts is None:
        return None
    if not isinstance(hosts, list):
        return []

    all_rhsa = []
    for host in hosts:
        if not isinstance(host, dict):
            continue
        # Collect from both rhsa_ids and advisories_list
        for field in ("rhsa_ids", "advisories_list"):
            ids = rhsa_ids(host.get(field))
            if ids:
                all_rhsa.extend(ids)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(all_rhsa))


# Constants
CVE_ID = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")


DEFAULT_INPUT = Path("output/vulnerability_report.json")
MAX_HOSTS = 5
TIMEOUT = 20
WEBHOOK_PATTERN = re.compile(
    r"https://hooks\.slack(?:-gov)?\.com/(?:services/)?[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+"
)


class NotificationError(Exception):
    """An error with a message safe to print."""


def load_report(path):
    try:
        with path.open(encoding="utf-8") as report_file:
            return json.load(report_file)
    except (OSError, ValueError, RecursionError):
        raise NotificationError("JSON レポートを読み込めませんでした。") from None


def limited_text(value, limit=250):
    rendered = display(value) if type(value) in (str, int, float) else "不明"
    if len(rendered) > limit:
        return rendered[:limit - 1].rstrip() + "…"
    return rendered or "不明"


def advisory_text(values):
    ids = rhsa_ids(values)
    if ids is None:
        return "不明"
    if not ids:
        return "該当なし（API 応答）"
    shown = []
    length = 0
    for advisory in ids:
        length += len(advisory) + (2 if shown else 0)
        if length > 900:
            return ", ".join([*shown, "…"])
        shown.append(advisory)
    return ", ".join(shown)


def plain(text):
    return {"type": "plain_text", "text": text}


def mrkdwn(text):
    return {"type": "mrkdwn", "text": text}


def section(text):
    return {"type": "section", "text": plain(text)}


def mrkdwn_section(text):
    return {"type": "section", "text": mrkdwn(text)}


def count_text(value):
    number = count(value)
    return "不明" if number is None else f"{limited_text(number)} 台"


def host_blocks(hosts, filtered_count, source=None):
    """Build Slack blocks for affected hosts.

    Args:
        hosts: List of affected hosts
        filtered_count: Count after advisory filter
        source: Data source ("satellite", "hcc", or None)
    """
    if hosts is None:
        return [section("アドバイザリありの影響ホスト一覧は未取得")]
    if not isinstance(hosts, list) or any(not isinstance(host, dict) for host in hosts):
        raise NotificationError("影響ホスト一覧の形式が不正です。")
    if not hosts:
        message = "該当ホストなし" if count(filtered_count) == 0 else "表示できる取得済みホストなし"
        return [section(message)]

    blocks = []
    for host in hosts[:MAX_HOSTS]:
        # Basic host info
        host_text = f"ホスト名　{limited_text(host.get('display_name'))}\n"
        host_text += f"OS　{limited_text(host.get('os'), 80)}\n"

        # RHSA info
        host_text += f"ホストの RHSA　{advisory_text(host.get('rhsa_ids'))}\n"

        # Satellite-specific tags (if source is satellite)
        if source == "satellite":
            sat_tags = extract_satellite_tags(host.get("tags"))
            if sat_tags.get("content_view"):
                host_text += f"Content View　{limited_text(sat_tags['content_view'], 80)}\n"
            if sat_tags.get("lifecycle_environment"):
                host_text += f"Lifecycle Environment　{limited_text(sat_tags['lifecycle_environment'], 80)}\n"
            if sat_tags.get("organization"):
                host_text += f"Organization　{limited_text(sat_tags['organization'], 80)}\n"
            if sat_tags.get("location"):
                host_text += f"Location　{limited_text(sat_tags['location'], 80)}\n"

        # Timestamps (convert to JST)
        host_text += f"初回検知日時　{utc_to_jst(host.get('first_reported'))}\n"
        host_text += f"最終評価日時　{utc_to_jst(host.get('last_evaluation'))}"

        blocks.append(section(host_text))

    omitted = len(hosts) - MAX_HOSTS
    if omitted > 0:
        blocks.append(section(f"ほか {omitted} 台を省略（取得済みホストのうち）"))
    return blocks


def build_payload_single(report):
    """Build Slack payload for single CVE mode.

    Args:
        report: Vulnerability report dict

    Returns:
        dict ready for Slack Webhook
    """
    cves = obj(report).get("cves")
    if not isinstance(cves, list) or len(cves) != 1 or not isinstance(cves[0], dict):
        raise NotificationError("単一 CVE レポートの形式が不正です。")

    cve = cves[0]
    cve_id = cve.get("cve_id")
    if not isinstance(cve_id, str) or len(cve_id) > 100 or not CVE_ID.fullmatch(cve_id):
        raise NotificationError("CVE ID の形式が不正です。")

    # Determine data source
    source = report.get("source")
    if source == "satellite":
        source_title = "Lightspeed on Satellite 脆弱性レポート"
        source_label = "Lightspeed on Satellite"
    elif source == "hcc":
        source_title = "HCC 脆弱性レポート"
        source_label = "HCC"
    else:
        source_title = "脆弱性レポート"
        source_label = "不明"

    title = f"{source_title} | {cve_id}"
    blocks = [{"type": "header", "text": plain(title)}]

    # Data source info
    generated_at_jst = utc_to_jst(report.get("generated_at"))
    blocks.append(section(f"データソース　{source_label}\n生成日時　{generated_at_jst}"))

    if report.get("complete") is False or cve.get("complete") is False:
        warning = "データ取得が不完全です。表示内容は取得済みの情報に限られます。"
        blocks.append(section(warning))
        title = f"{title} | {warning}"

    exploit = cve.get("known_exploit")
    exploit_text = "不明"
    if exploit is True:
        exploit_text = "true（API 報告値）"
    elif exploit is False:
        exploit_text = "false（API 報告値。悪用が存在しないことを保証しません）"

    # Slack caps section text at 3,000 characters and each field at 2,000.
    blocks.append(section(f"脆弱性の概要\n{limited_text(cve.get('description'), 1400)}"))
    counts = obj(cve.get("affected_host_counts"))
    filtered_count = counts.get("after_advisory_filter")
    fields = [
        plain(f"Red Hat 重要度\n{limited_text(cve.get('impact'))}"),
        plain(f"CVSS v3 スコア\n{limited_text(cve.get('cvss3_score'), 80)}"),
        plain(f"Known Exploit\n{exploit_text}"),
        plain(f"CVE 公開日\n{date_only(cve.get('public_date'))}"),
        plain(f"影響ホスト数\n{count_text(counts.get('before_advisory_filter'))}"),
        plain(f"アドバイザリありの影響ホスト数\n{count_text(filtered_count)}"),
    ]
    fetched_count = counts.get("fetched_after_advisory_filter")
    if count(filtered_count) is None and count(fetched_count) is not None:
        fields.append(plain(f"アドバイザリありの取得済みホスト数\n{count_text(fetched_count)}"))
    blocks.append({"type": "section", "fields": fields})

    # CVE-level RHSA (from API)
    cve_rhsa = cve.get('cve_rhsa_ids')
    blocks.append(section(f"CVE 全体の RHSA\n{advisory_text(cve_rhsa)}"))

    # Aggregated RHSA from affected hosts (Satellite特有)
    if source == "satellite":
        aggregated_rhsa = aggregate_rhsa_from_hosts(cve.get("affected_hosts"))
        if aggregated_rhsa:
            rhsa_note = "対応 RHSA（影響ホストから集約）\n"
            rhsa_note += advisory_text(aggregated_rhsa)
            rhsa_note += "\n\n注：RHSA が存在することと、現在の Content View でパッチを適用できることは別です。適用可否については未確認です。"
            blocks.append(section(rhsa_note))

    blocks.append({"type": "divider"})
    blocks.extend(host_blocks(cve.get("affected_hosts"), filtered_count, source=source))
    link = f"https://access.redhat.com/security/cve/{cve_id.lower()}"
    blocks.append({"type": "section", "text": {
        "type": "mrkdwn", "text": f"<{link}|Red Hat CVE 詳細ページ>",
    }})
    return {"text": title, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}


def build_payload_multi(report):
    """Build Slack payload for multiple CVE mode (top).

    Args:
        report: Vulnerability report dict

    Returns:
        dict ready for Slack Webhook
    """
    cves = obj(report).get("cves")
    if not isinstance(cves, list):
        raise NotificationError("CVE 一覧の形式が不正です。")

    # Determine data source
    source = report.get("source")
    if source == "satellite":
        source_title = "Lightspeed on Satellite 脆弱性レポート"
        source_label = "Lightspeed on Satellite"
    elif source == "hcc":
        source_title = "HCC 脆弱性レポート"
        source_label = "HCC"
    else:
        source_title = "脆弱性レポート"
        source_label = "不明"

    # Report metadata
    cve_list = obj(report.get("cve_list"))
    total_cves = count(cve_list.get("total_items"))
    system_count = count(cve_list.get("system_count"))
    cve_count = len(cves)
    date_filter = obj(report.get("date_filter"))
    date_filter_mode = date_filter.get("mode") if date_filter else None

    # Build title
    if date_filter_mode and date_filter_mode != "none":
        title = f"{source_title} | 期間指定 {cve_count} 件"
    else:
        title = f"{source_title} | 上位 {cve_count} 件"
    if report.get("complete") is False:
        title = f"{title} | データ取得が不完全です"

    blocks = [{"type": "header", "text": plain(title)}]

    # Overall information
    generated_at_jst = utc_to_jst(report.get("generated_at"))
    overview = f"データソース　{source_label}\n"
    overview += f"生成日時　{generated_at_jst}\n"

    # Date filter information
    if date_filter_mode and date_filter_mode != "none":
        filter_label = "初回検知日時" if date_filter_mode == "first_detected" else "CVE公開日"
        cutoff_date_jst = utc_to_jst(date_filter.get("cutoff_date"))
        overview += f"抽出条件　{filter_label}\n"
        overview += f"対象期間　{cutoff_date_jst}以降\n"
        overview += f"Satellite 上の全 CVE 件数　{limited_text(total_cves) if total_cves is not None else '不明'}\n"
        before_filter = count(date_filter.get("before_date_filter"))
        after_filter = count(date_filter.get("after_date_filter"))
        if before_filter is not None and after_filter is not None:
            overview += f"期間条件に一致した CVE 件数　{after_filter} / {before_filter}\n"
        overview += f"通知対象 CVE 件数　{cve_count}\n"
        overview += f"管理対象システム数　{limited_text(system_count) if system_count is not None else '不明'}"
    else:
        overview += f"Satellite 上の全 CVE 件数　{limited_text(total_cves) if total_cves is not None else '不明'}\n"
        overview += f"管理対象システム数　{limited_text(system_count) if system_count is not None else '不明'}\n"
        overview += f"通知対象 CVE 件数　{cve_count}"

    blocks.append(section(overview))

    # Filter information
    cv_filter = report.get("content_view_filter")
    le_filter = report.get("lifecycle_environment_filter")
    if cv_filter or le_filter:
        filter_info = "フィルター条件\n"
        if cv_filter:
            filter_info += f"Content View: {limited_text(cv_filter, 80)}\n"
        if le_filter:
            filter_info += f"Lifecycle Environment: {limited_text(le_filter, 80)}"
        blocks.append(section(filter_info))

    # Incompleteness warning
    if report.get("complete") is False:
        blocks.append(section("⚠️ データ取得が不完全です。表示内容は取得済みの情報に限られます。"))

    # CVE list
    blocks.append({"type": "divider"})

    # Slack Block Kit limit: 50 blocks
    # Header (1) + Overview (1) + Filter (0-1) + Warning (0-1) + Divider (1) = 3-4 blocks
    # Each CVE: 1 divider + 1 section = 2 blocks
    # Footer link: 1 block per CVE
    # Reserve 5 blocks for header/footer, so ~15 CVEs max (3 blocks each)
    max_cves_to_show = 15

    for idx, cve in enumerate(cves[:max_cves_to_show]):
        cve_id = cve.get("cve_id")
        if not isinstance(cve_id, str) or not CVE_ID.fullmatch(cve_id):
            continue

        # CVE basic info
        cve_text = f"*{cve_id}*\n"

        # Impact and CVSS
        impact = limited_text(cve.get("impact"), 80)
        cvss = limited_text(cve.get("cvss3_score"), 80)
        cve_text += f"重要度: {impact}　CVSS: {cvss}\n"

        # Known Exploit
        exploit = cve.get("known_exploit")
        if exploit is True:
            cve_text += "Known Exploit: true（API 報告値）\n"
        elif exploit is False:
            cve_text += "Known Exploit: false\n"

        # Public date
        cve_text += f"公開日: {date_only(cve.get('public_date'))}\n"

        # Affected hosts count
        counts = obj(cve.get("affected_host_counts"))

        # Date filter mode: show newly detected hosts
        if date_filter_mode == "first_detected":
            # In first_detected mode, affected_hosts are newly detected hosts
            before_filter = counts.get("before_date_filter")
            after_filter = counts.get("after_date_filter")
            if before_filter is not None and after_filter is not None:
                cve_text += f"影響ホスト総数: {count_text(before_filter)}\n"
                cve_text += f"期間内新規検知: {count_text(after_filter)}\n"
            else:
                cve_text += f"影響ホスト数: {count_text(counts.get('before_advisory_filter'))}\n"
        else:
            # Normal mode or public_date mode
            host_count = counts.get("before_advisory_filter")
            cve_text += f"影響ホスト数: {count_text(host_count)}\n"

        # Affected hosts (up to 5)
        affected_hosts = cve.get("affected_hosts")
        if isinstance(affected_hosts, list) and affected_hosts:
            host_names = []
            for host in affected_hosts[:MAX_HOSTS]:
                if isinstance(host, dict):
                    name = host.get("display_name")
                    if isinstance(name, str) and name:
                        # In first_detected mode, show first_reported date
                        if date_filter_mode == "first_detected":
                            first_reported = host.get("first_reported")
                            first_reported_jst = utc_to_jst(first_reported) if first_reported else "不明"
                            host_names.append(f"{name} ({first_reported_jst})")
                        else:
                            host_names.append(name)
            if host_names:
                if date_filter_mode == "first_detected":
                    cve_text += f"新規検知ホスト: {', '.join(host_names)}"
                else:
                    cve_text += f"影響ホスト: {', '.join(host_names)}"
                omitted = len(affected_hosts) - len(host_names)
                if omitted > 0:
                    cve_text += f" ほか{omitted}台"
                cve_text += "\n"
        elif affected_hosts is None:
            if date_filter_mode == "first_detected":
                cve_text += "新規検知ホスト: 未取得\n"
            else:
                cve_text += "影響ホスト: 未取得\n"

        # RHSA
        cve_rhsa = cve.get('cve_rhsa_ids')
        aggregated_rhsa = aggregate_rhsa_from_hosts(cve.get("affected_hosts")) if source == "satellite" else None

        # Combine RHSA from both sources
        all_rhsa = []
        if rhsa_ids(cve_rhsa):
            all_rhsa.extend(rhsa_ids(cve_rhsa))
        if aggregated_rhsa:
            all_rhsa.extend(aggregated_rhsa)
        all_rhsa = list(dict.fromkeys(all_rhsa))  # Remove duplicates

        cve_text += f"対応 RHSA: {advisory_text(all_rhsa) if all_rhsa else advisory_text(cve_rhsa)}\n"

        # CVE link
        link = f"https://access.redhat.com/security/cve/{cve_id.lower()}"
        cve_text += f"<{link}|詳細ページ>"

        blocks.append(mrkdwn_section(cve_text))

        # Add divider between CVEs (except after the last one)
        if idx < min(len(cves), max_cves_to_show) - 1:
            blocks.append({"type": "divider"})

    # Omitted CVEs
    omitted_cves = len(cves) - max_cves_to_show
    if omitted_cves > 0:
        blocks.append({"type": "divider"})
        blocks.append(section(f"ほか {omitted_cves} 件の CVE を省略"))

    return {"text": title, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}


def build_payload(report):
    """Build Slack payload based on report mode.

    Args:
        report: Vulnerability report dict

    Returns:
        dict ready for Slack Webhook
    """
    if not isinstance(report, dict):
        raise NotificationError("不明なレポートモード: None")

    mode = report.get("mode")
    if mode == "cve":
        return build_payload_single(report)
    elif mode == "top":
        return build_payload_multi(report)
    else:
        raise NotificationError(f"不明なレポートモード: {mode}")


def send_notification(payload):
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        raise NotificationError("SLACK_WEBHOOK_URL が設定されていません。")
    if not WEBHOOK_PATTERN.fullmatch(url):
        raise NotificationError("SLACK_WEBHOOK_URL に Slack Incoming Webhook の HTTPS URL を設定してください。")

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"},
                      method="POST")
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            if response.status == 200 and response.read(32).strip() == b"ok":
                return
    except HTTPError as error:
        error.close()
        raise NotificationError(f"Slack Webhook の送信に失敗しました（HTTP {error.code}）。") from None
    except (HTTPException, OSError, ValueError):
        raise NotificationError("Slack Webhook との通信に失敗しました。") from None
    raise NotificationError("Slack Webhook から送信成功の応答を確認できませんでした。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="脆弱性 JSON レポートを Slack に通知")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help=f"入力 JSON（既定値 {DEFAULT_INPUT}）")
    parser.add_argument("--dry-run", action="store_true", help="送信せず通知の JSON を標準出力に表示")
    args = parser.parse_args(argv)
    try:
        payload = build_payload(load_report(args.input))
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            send_notification(payload)
            print("Slack 通知を送信しました。")
    except NotificationError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
