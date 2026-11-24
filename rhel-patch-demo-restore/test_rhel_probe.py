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
case "$*" in
  "updateinfo info {ADVISORY}") printf '%s\\n' 'Update ID : {ADVISORY}';;
  "updateinfo list all {ADVISORY}") printf '%s\\n' 'i {ADVISORY} Low/Sec. example-libs-1.2-3.el7.i686' '  {ADVISORY} Low/Sec. 2:example-libs-1.3-4.el7.x86_64' "${{ADVISORY_EXTRA_ROW:-}}";;
  "updateinfo list available {ADVISORY}") printf '%s\\n' '{ADVISORY} Low/Sec. example-libs-2:1.3-4.el7.x86_64';;
  *) exit 21;;
esac
""")
        self.write_command("rpm", """#!/bin/sh
test "$#" = 3 && test "$1" = -qa && test "$2" = --queryformat || exit 22
printf '%s\\n' 'example-libs 2:1.2-3.el7.x86_64' 'example-libs 2:1.2-3.el7.i686' 'gpg-pubkey 0:fd431d51-4ae0493b.(none)' 'unrelated 0:1-2.el7.noarch'
if [ -n "$INVALID_RPM_ROW" ]; then printf '%s\\n' "$INVALID_RPM_ROW"; fi
exit "${RPM_PROBE_RC:-0}"
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
                 "ANSIBLE_STDOUT_CALLBACK": "default", "PATH": f"{self.root}:{os.environ['PATH']}", **overrides},
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(line.strip() for line in result.stdout.splitlines() if line.strip().startswith("RUN: "))
        return json.loads(line.removeprefix("RUN: "))["demo_rhel_state"]

    def test_rhel7_public_key_entry_does_not_invalidate_before_and_after(self):
        before = self.run_play("Check")
        after = self.run_play("Verify")
        self.assertEqual(before["phase"], "before")
        self.assertEqual(after["phase"], "after")
        for state in (before, after):
            self.assertEqual(state["package_names"], ["example-libs"])
            self.assertEqual(state["availability"], "available")
            self.assertEqual(state["installed_rpms"], ["example-libs 2:1.2-3.el7.i686", "example-libs 2:1.2-3.el7.x86_64"])
            self.assertIs(state["rpm_complete"], True)
            self.assertEqual(state["rpm_issues"], [])

    def test_invalid_rows_and_failed_queries_remain_incomplete(self):
        for overrides, reason in (
            ({"INVALID_RPM_ROW": "example-libs 2:1.2-3.el7.(none)"}, "rpm_inventory_parse"),
            ({"RPM_PROBE_RC": "1"}, "rpm_query_failed"),
            ({"ADVISORY_EXTRA_ROW": f"{ADVISORY} Low/Sec. unrecognized"}, "yum_all_parse"),
        ):
            with self.subTest(reason=reason):
                state = self.run_play("Verify", **overrides)
                self.assertEqual(state["availability"], "available")
                self.assertIs(state["rpm_complete"], False)
                self.assertIn(reason, state["rpm_issues"])
