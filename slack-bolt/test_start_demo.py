"""Verify launcher selection, secret-free config and read-only discovery."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from controller import Controller, WORKFLOW
from demo_config import ConfigError, load_config, save_config
from start_demo import launch
from test_slack_app import FakeController, Response


class DiscoveryTests(unittest.TestCase):
    def test_paginated_discovery_and_unavailable_reasons(self):
        fake = FakeController()
        requests = []

        def open_mock(request, timeout):
            path = urlsplit(request.full_url)
            self.assertEqual(request.get_method(), "GET")
            requests.append(request)
            if path.path.endswith("/ping/"):
                if path.path.startswith("/api/controller/"):
                    self.assertIsNone(request.get_header("Authorization"))
                    return Response({"version": "4.6.31"})
                self.fail("Gateway must be preferred")
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-oauth-token")
            prefix = "/api/controller/v2/"
            resource = path.path.removeprefix(prefix)
            self.assertNotEqual(resource, path.path)
            if resource == "workflow_jobs/":
                query = parse_qs(path.query)
                self.assertEqual(query["name"], [WORKFLOW])
                self.assertEqual(query["status"], ["running"])
                page = int(query["page"][0])
                ids = [11, 12] if page == 1 else [13, 15]
                return Response({
                    "results": [{"id": job_id, "name": WORKFLOW, "status": "running"} for job_id in ids],
                    "next": "https://untrusted.example.test/path" if page == 1 else None,
                })
            if resource.endswith("/workflow_nodes/"):
                job_id = int(resource.split("/")[1])
                return Response({"results": [{
                    "identifier": "patch-approval", "workflow_job": job_id,
                    "job": {11: 14, 12: None, 13: 18, 15: 20}[job_id],
                }], "next": None})
            approval_id = int(resource.split("/")[1])
            details = {**fake.approval, "id": approval_id, "timed_out": approval_id == 18}
            return Response(details)

        controller = Controller("https://controller.example.test", "synthetic-oauth-token",
                                opener=Mock(open=open_mock))
        self.assertEqual(controller.check_version(), "4.6.31")
        candidates, reasons = controller.discover_pending()
        self.assertEqual(candidates, [(11, 14), (15, 20)])
        self.assertTrue(any("到達していません" in reason for reason in reasons))
        self.assertTrue(any("期限" in reason for reason in reasons))
        self.assertEqual(controller.for_approval(11, 14).api_root, "/api/controller/v2/")
        self.assertTrue(all(urlsplit(request.full_url).hostname == "controller.example.test" for request in requests))

    def test_no_running_demo_workflow(self):
        opener = Mock()
        opener.open.return_value = Response({"results": [], "next": None})
        controller = Controller("https://controller.example.test", "synthetic-oauth-token", opener=opener)
        candidates, reasons = controller.discover_pending()
        self.assertEqual(candidates, [])
        self.assertIn("参照権限", reasons[0])
        self.assertEqual(opener.open.call_args.args[0].get_method(), "GET")


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock(origin="https://controller.example.test", api_root="/api/v2/")
        self.controller.check_version.return_value = "4.5.30"
        self.controller.discover_pending.return_value = ([(11, 14), (15, 20)], [])
        self.target = Mock(workflow_job=15, approval=20)
        self.controller.for_approval.return_value = self.target
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        patch("controller.Controller", return_value=self.controller).start()
        patch("start_demo.load_config", return_value={
            "aap_url": self.controller.origin, "slack_team_id": "TDEMO", "slack_channel_id": "CDEMO",
            "slack_allowed_user_ids": "UALLOWED",
        }).start()
        patch("start_demo.prompt_value", side_effect=lambda label, default="": default).start()
        self.secrets = patch("start_demo.prompt_secret", side_effect=[
            "synthetic-oauth-token", "xoxb-synthetic", "xapp-synthetic",
        ]).start()
        self.serve = patch("slack_app.serve").start()
        self.save = patch("start_demo.save_config").start()

    def test_dry_run_lists_every_candidate_and_never_starts_slack(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launch(SimpleNamespace(dry_run=True, save_config=False)), 0)
        self.assertIn("Workflow Job ID 11 / Approval ID 14", output.getvalue())
        self.assertIn("Workflow Job ID 15 / Approval ID 20", output.getvalue())
        self.controller.for_approval.assert_not_called()
        self.serve.assert_not_called()
        self.save.assert_not_called()
        self.assertEqual(self.secrets.call_count, 1)

    def test_multiple_candidates_require_choice_and_selection_confirmation(self):
        with (
            patch("builtins.input", side_effect=["2", "START"]),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(launch(SimpleNamespace(dry_run=False, save_config=True)), 0)
        self.controller.for_approval.assert_called_once_with(15, 20)
        self.target.pending.assert_called_once()
        self.serve.assert_called_once()
        self.assertIs(self.serve.call_args.args[0], self.target)
        saved = self.save.call_args.args[0]
        self.assertEqual(saved["slack_allowed_user_ids"], "UALLOWED")
        self.assertTrue(all("synthetic" not in value for value in saved.values()))
        self.assertFalse({"workflow_job_id", "approval_id"} & saved.keys())

    def test_no_candidate_explains_why_and_never_starts_slack(self):
        self.controller.discover_pending.return_value = ([], ["AAP の承認期限が切れています。"])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launch(SimpleNamespace(dry_run=False, save_config=False)), 1)
        self.assertIn("期限", output.getvalue())
        self.serve.assert_not_called()


class LocalConfigTests(unittest.TestCase):
    def test_save_requires_confirmation_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "config.json"
            self.assertFalse(save_config({"host": "node.example.test"}, confirm=lambda _: "", path=path))
            self.assertFalse(path.exists())
            self.assertTrue(save_config({"host": "node.example.test"}, confirm=lambda _: "SAVE", path=path))
            save_config({"aap_url": "https://controller.example.test"}, confirm=lambda _: "SAVE", path=path)
            self.assertEqual(load_config(path), {"host": "node.example.test", "aap_url": "https://controller.example.test"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_secret_fields_and_fixed_ids_without_printing(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output:
            path = Path(directory) / "config.json"
            for setting in (
                {"aap_token": "synthetic-oauth-value"},
                {"approval_id": "14"},
                {"ssh_credential": "synthetic-oauth-value"},
                {"github_url": "https://synthetic-user:synthetic-password@github.com/example/repo"},
            ):
                with self.assertRaises(ConfigError):
                    save_config(setting, secrets=("synthetic-oauth-value",), path=path)
            self.assertEqual(output.getvalue(), "")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
