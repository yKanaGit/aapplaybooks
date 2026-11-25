"""Run the RHEL Playbook against local command doubles, without AAP or RHEL."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLAYBOOK = Path(__file__).resolve().parents[1] / "rhel_patch.yml"
ADVISORY = "RHSA-2023:7743"
EVENT_ID = "a" * 32


class RhelProbeTests(unittest.TestCase):
    def setUp(self):
        alongside_python = Path(sys.executable).with_name("ansible-playbook")
        self.ansible = str(alongside_python) if alongside_python.is_file() else shutil.which("ansible-playbook")
        if not self.ansible:
            self.skipTest("ansible-playbook is unavailable")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.write_command("yum", f"""#!/bin/sh
if [ "$2" = list ]; then test "$LC_ALL" = C || exit 23; fi
printf '%s\\n' "$*" >> "$PROBE_CALLS"
fixed='7.29.0-59.el7_9.2'
case "$*" in
  "updateinfo info {ADVISORY}") printf '%s\\n' 'Update ID : {ADVISORY}';;
  "updateinfo list all {ADVISORY}")
    printf '%s\\n' "{ADVISORY} Low/Sec. curl-$fixed.x86_64" "{ADVISORY} Low/Sec. libcurl-$fixed.x86_64" "{ADVISORY} Low/Sec. libcurl-$fixed.i686"
    if [ -n "$ADVISORY_EXTRA_ROW" ]; then printf '%s\\n' "$ADVISORY_EXTRA_ROW"; fi;;
  "updateinfo list available {ADVISORY}") printf '%s\\n' "{ADVISORY} Low/Sec. libcurl-$fixed.i686";;
  "updateinfo list updates {ADVISORY}")
    if [ -n "$YUM_UPDATES_RC" ]; then printf '%s\\n' 'hidden yum diagnostic' >&2; exit "$YUM_UPDATES_RC"; fi
    if [ -n "$UPDATES_ROW" ]; then printf '%s\\n' "$UPDATES_ROW"; fi;;
  "updateinfo list installed {ADVISORY}")
    if [ -n "$HIDE_FIXED_VERSIONS" ]; then exit 0; fi
    if [ -z "$OLD_CURL" ]; then printf '%s\\n' "i {ADVISORY} Low/Sec. curl-$fixed.x86_64"; fi
    if [ -z "$OLD_LIBCURL" ]; then printf '%s\\n' "i {ADVISORY} Low/Sec. libcurl-$fixed.x86_64"; fi;;
  *) exit 21;;
esac
""")
        self.write_command("rpm", """#!/bin/sh
test "$#" = 3 && test "$1" = -qa && test "$2" = --queryformat || exit 22
printf '%s\\n' "curl ${CURL_EVR:-0:7.29.0-59.el7_9.2}.x86_64" "libcurl 0:7.29.0-59.el7_9.${OLD_LIBCURL:-2}.x86_64" 'gpg-pubkey 0:fd431d51-4ae0493b.(none)' 'unrelated 0:1-2.el7.noarch'
if [ -n "$INVALID_RPM_ROW" ]; then printf '%s\\n' "$INVALID_RPM_ROW"; fi
exit "${RPM_PROBE_RC:-0}"
""")
        # The macOS test host has no RPM Python bindings. Assert that the probe
        # delegates comparisons and supplies all three EVR components to them.
        (self.root / "rpm.py").write_text("""
def labelCompare(left, right):
    if left == right:
        return 0
    fixed = ('0', '7.29.0', '59.el7_9.2')
    assert right == fixed, (left, right)
    return {
        ('0', '7.29.0', '59.el7_9.1'): -1,
        ('0', '7.29.0', '59.el7_9.10'): 1,
        ('1', '7.28.0', '1.el7'): 1,
    }[left]
""")

    def write_command(self, name, contents):
        path = self.root / name
        path.write_text(contents)
        path.chmod(0o700)

    def run_play(self, stage, **overrides):
        variables = {
            "awx_job_template_name": f"RHEL-Patch-{stage}", "demo_event_id": EVENT_ID,
            "demo_checked_event_id": EVENT_ID, "demo_applied_event_id": EVENT_ID,
            "demo_target_host": "localhost", "demo_target_rhsa": ADVISORY, "apply_patch": False,
            "ansible_become": False, "ansible_python_interpreter": sys.executable,
        }
        result = subprocess.run(
            [self.ansible, "-i", "localhost,", "-c", "local", str(PLAYBOOK), "-e", json.dumps(variables)],
            env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(self.root / "controller"),
                 "ANSIBLE_REMOTE_TMP": str(self.root / "host"), "ANSIBLE_SHOW_CUSTOM_STATS": "true",
                 "ANSIBLE_STDOUT_CALLBACK": "default", "PATH": f"{self.root}:{os.environ['PATH']}",
                 "PYTHONPATH": str(self.root), "PROBE_CALLS": str(self.root / "yum.calls"), **overrides},
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("hidden yum diagnostic", result.stdout + result.stderr)
        line = next(line.strip() for line in result.stdout.splitlines() if line.strip().startswith("RUN: "))
        return json.loads(line.removeprefix("RUN: "))["demo_rhel_state"]

    def test_uninstalled_i686_from_available_does_not_count_as_an_update(self):
        updates = "\n".join(f"{ADVISORY} Low/Sec. {name}-7.29.0-59.el7_9.2.x86_64"
                            for name in ("curl", "libcurl"))
        before = self.run_play("Check", CURL_EVR="0:7.29.0-59.el7_9.1", OLD_CURL="1",
                               OLD_LIBCURL="1", UPDATES_ROW=updates)
        env = {**os.environ, "LC_ALL": "C", "PROBE_CALLS": str(self.root / "yum.calls")}
        available = subprocess.check_output(
            [str(self.root / "yum"), "updateinfo", "list", "available", ADVISORY], env=env, text=True,
        )
        self.assertEqual(available.strip(), f"{ADVISORY} Low/Sec. libcurl-7.29.0-59.el7_9.2.i686")
        (self.root / "yum.calls").write_text("")
        after = self.run_play("Verify")
        self.assertEqual((self.root / "yum.calls").read_text().splitlines(),
                         [f"updateinfo list {mode} {ADVISORY}" for mode in ("all", "updates", "installed")])
        self.assertEqual(before["phase"], "before")
        self.assertEqual(before["verification"]["status"], "pending")
        self.assertEqual(before["installed_rpms"], [
            "curl 0:7.29.0-59.el7_9.1.x86_64", "libcurl 0:7.29.0-59.el7_9.1.x86_64",
        ])
        self.assertEqual(after["phase"], "after")
        self.assertEqual(after["package_names"], ["curl", "libcurl"])
        self.assertEqual(after["installed_rpms"], [
            "curl 0:7.29.0-59.el7_9.2.x86_64", "libcurl 0:7.29.0-59.el7_9.2.x86_64",
        ])
        self.assertIs(after["rpm_complete"], True)
        self.assertEqual(after["rpm_issues"], [])
        self.assertEqual(after["verification"], {
            "status": "fixed", "reason": "fixed_versions_confirmed", "remaining_updates": [],
            "query_rc": {"yum_all": 0, "yum_updates": 0, "yum_installed": 0, "rpm_inventory": 0},
        })

    def test_only_newer_updates_for_installed_name_and_arch_remain(self):
        updates = "\n".join(f"{ADVISORY} Low/Sec. libcurl-7.29.0-59.el7_9.2.{arch}"
                            for arch in ("i686", "x86_64"))
        state = self.run_play("Verify", OLD_LIBCURL="1", UPDATES_ROW=updates)
        verdict = state["verification"]
        self.assertEqual(verdict["status"], "pending")
        self.assertEqual(verdict["reason"], "updates_remain")
        self.assertEqual(verdict["remaining_updates"], [{"name": "libcurl", "arch": "x86_64", "evr": "0:7.29.0-59.el7_9.2"}])

    def test_updates_failure_and_lack_of_fixed_versions_are_unknown(self):
        for overrides, reason, rc in (
            ({"YUM_UPDATES_RC": "23"}, "incomplete_queries", 23),
            ({"HIDE_FIXED_VERSIONS": "1"}, "fixed_version_unconfirmed", 0),
            ({"UPDATES_ROW": f"{ADVISORY} Low/Sec. unrecognized"}, "incomplete_queries", 0),
        ):
            with self.subTest(overrides=overrides):
                verdict = self.run_play("Verify", **overrides)["verification"]
                self.assertEqual(verdict["status"], "unknown")
                self.assertEqual(verdict["reason"], reason)
                self.assertEqual(verdict["query_rc"]["yum_updates"], rc)
                self.assertEqual(verdict["remaining_updates"], [])

    def test_higher_release_or_epoch_can_satisfy_the_fixed_version(self):
        for evr in ("0:7.29.0-59.el7_9.10", "1:7.28.0-1.el7"):
            with self.subTest(evr=evr):
                state = self.run_play("Verify", CURL_EVR=evr)
                self.assertEqual(state["verification"]["status"], "fixed")

    def test_invalid_rows_and_failed_queries_remain_incomplete(self):
        for overrides, reason in (
            ({"INVALID_RPM_ROW": "libcurl 0:7.29.0-59.el7_9.2.(none)"}, "rpm_inventory_parse"),
            ({"RPM_PROBE_RC": "1"}, "rpm_query_failed"),
            ({"ADVISORY_EXTRA_ROW": f"{ADVISORY} Low/Sec. unrecognized"}, "yum_all_parse"),
        ):
            with self.subTest(reason=reason):
                state = self.run_play("Verify", **overrides)
                self.assertEqual(state["verification"]["status"], "unknown")
                self.assertIs(state["rpm_complete"], False)
                self.assertIn(reason, state["rpm_issues"])
