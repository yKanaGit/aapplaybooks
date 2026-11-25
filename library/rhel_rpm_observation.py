#!/usr/bin/python
"""Collect advisory evidence on the managed RHEL host."""

import json
import re
from collections import defaultdict

from ansible.module_utils.basic import AnsibleModule


NAME = r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}"
COMPONENT = r"[A-Za-z0-9._+~^]+"
ARCH = r"[A-Za-z0-9_]+"
NEVRA = re.compile(
    r"^(?:(?P<leading_epoch>[0-9]+):)?(?P<name>" + NAME
    + r")-(?:(?P<epoch>[0-9]+):)?(?P<version>" + COMPONENT
    + r")-(?P<release>" + COMPONENT + r")\.(?P<arch>" + ARCH + r")$"
)
INSTALLED = re.compile(
    r"^(?P<name>" + NAME + r") (?P<epoch>[0-9]+):(?P<version>" + COMPONENT
    + r")-(?P<release>" + COMPONENT + r")\.(?P<arch>" + ARCH + r")$"
)
RPM_KEY = re.compile(r"^gpg-pubkey [0-9]+:[0-9a-fA-F]{8}-[0-9a-fA-F]{8}\.(?:\(none\)|)$")

# RHEL 7's system RPM bindings can be installed for Python 2 only.
SYSTEM_PYTHONS = ("/usr/libexec/platform-python", "/usr/bin/python3", "/usr/bin/python")
COMPARE_SCRIPT = (
    "import json, rpm, sys\n"
    "print(json.dumps([rpm.labelCompare(tuple(map(str, a)), tuple(map(str, b)))"
    " for a, b in json.load(sys.stdin)]))"
)


def package(match):
    groups = match.groupdict()
    epoch = str(int(groups.get("leading_epoch") or groups["epoch"] or "0"))
    return groups["name"], groups["arch"], (epoch, groups["version"], groups["release"])


def advisory_packages(stdout, advisory, mode):
    rows = set()
    malformed = False
    for line in stdout.splitlines():
        fields = line.split()
        marked_installed = bool(fields and fields[0] in ("i", "I"))
        if marked_installed:
            fields = fields[1:]
        if not fields or fields[0] != advisory:
            continue
        match = NEVRA.match(fields[2]) if len(fields) == 3 else None
        if (not match or (match.group("leading_epoch") and match.group("epoch"))
                or (mode == "updates" and marked_installed)):
            malformed = True
        else:
            rows.add(package(match))
    return rows, malformed


def installed_packages(stdout):
    rows = set()
    malformed = False
    for line in stdout.splitlines():
        if RPM_KEY.match(line):
            continue
        match = INSTALLED.match(line)
        if match:
            rows.add(package(match))
        else:
            malformed = True
    return rows, malformed


def compare_versions(run, pairs):
    if not pairs:
        return {}
    try:
        from rpm import labelCompare
        return {pair: labelCompare(*pair) for pair in pairs}
    except (ImportError, TypeError, ValueError):
        pass
    for interpreter in SYSTEM_PYTHONS:
        rc, stdout, _ = run([interpreter, "-c", COMPARE_SCRIPT], data=json.dumps(pairs))
        if rc != 0:
            continue
        try:
            results = json.loads(stdout)
        except ValueError:
            continue
        if (isinstance(results, list) and len(results) == len(pairs)
                and all(type(value) is int and value in (-1, 0, 1) for value in results)):
            return dict(zip(pairs, results))
    return None


def evr_text(evr):
    return "{0}:{1}-{2}".format(*evr)


def package_versions(packages):
    versions = defaultdict(list)
    for name, arch, evr in packages:
        versions[name, arch].append(evr)
    return versions


def assess(run, installed, updates, fixes, issues):
    if any(issue.startswith(("yum_updates_", "rpm_query_", "rpm_inventory_")) for issue in issues):
        return "unknown", "incomplete_queries", []
    if not installed:
        return "unknown", "incomplete_queries" if issues else "no_installed_targets", []

    # Keep every installed version, including parallel RPMs of the same name and arch.
    versions_by_package = package_versions(installed)
    fixes_by_package = package_versions(fixes)
    update_pairs = {
        row: [(evr, row[2]) for evr in versions_by_package.get(row[:2], [])]
        for row in updates
    }
    fix_pairs = {
        row: [(row[2], evr) for evr in fixes_by_package.get(row[:2], [])]
        for row in installed
    }
    pairs = sorted({pair for comparisons in (update_pairs, fix_pairs)
                    for package_pairs in comparisons.values() for pair in package_pairs})
    comparisons = compare_versions(run, pairs)
    if comparisons is None:
        return "unknown", "rpm_compare_unavailable", []

    remaining = [
        {"name": name, "arch": arch, "evr": evr_text(evr)}
        for (name, arch, evr), package_pairs in sorted(update_pairs.items())
        if any(comparisons[pair] < 0 for pair in package_pairs)
    ]
    if remaining:
        return "pending", "updates_remain", remaining
    if issues:
        return "unknown", "incomplete_queries", []
    if all(any(comparisons[pair] >= 0 for pair in package_pairs) for package_pairs in fix_pairs.values()):
        return "fixed", "fixed_versions_confirmed", []
    return "unknown", "fixed_version_unconfirmed", []


def observe(run, advisory):
    codes = {}
    issues = []
    packages = {}
    for mode in ("all", "updates", "installed"):
        rc, stdout, _ = run(["yum", "updateinfo", "list", mode, advisory])
        key = "yum_" + mode
        codes[key] = rc
        packages[mode], malformed = advisory_packages(stdout if rc == 0 else "", advisory, mode)
        if rc != 0:
            issues.append(key + "_failed")
        if malformed:
            issues.append(key + "_parse")

    names = {row[0] for rows in packages.values() for row in rows}
    if not names:
        issues.append("rpm_scope_empty")
    rc, stdout, stderr = run(["rpm", "-qa", "--queryformat", "%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\\n"])
    codes["rpm_inventory"] = rc
    inventory, malformed = installed_packages(stdout if rc == 0 else "")
    if rc != 0:
        issues.append("rpm_query_failed")
    if stderr.strip():
        issues.append("rpm_query_stderr")
    if rc == 0 and not inventory:
        issues.append("rpm_inventory_empty")
    if malformed:
        issues.append("rpm_inventory_parse")

    # The all listing determines scope only. Installed advisory records provide fixed EVRs.
    installed = {row for row in inventory if row[0] in names}
    status, reason, remaining = assess(run, installed, packages["updates"], packages["installed"], issues)
    return {
        "package_names": sorted(names),
        "installed_rpms": sorted("{0} {1}.{2}".format(name, evr_text(evr), arch) for name, arch, evr in installed),
        "rpm_complete": not issues,
        "rpm_issues": issues,
        "verification": {"status": status, "reason": reason, "query_rc": codes, "remaining_updates": remaining},
    }


def main():
    module = AnsibleModule(argument_spec={"advisory": {"type": "str", "required": True}},
                           supports_check_mode=True)
    if not re.match(r"^RHSA-[0-9]{4}:[0-9]+\Z", module.params["advisory"]):
        module.fail_json(msg="Invalid advisory ID")
    def run(argv, **kwargs):
        try:
            return module.run_command(argv, handle_exceptions=False, **kwargs)
        except OSError as error:
            return error.errno or 257, "", ""

    module.exit_json(changed=False, observation=observe(run, module.params["advisory"]))


if __name__ == "__main__":
    main()
