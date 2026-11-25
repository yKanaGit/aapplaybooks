"""End-to-end local doubles for the integrated demo runner."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from controller import ApprovalError, Controller, ControllerHTTPError, SLACK_WORKFLOW
from demo_config import ConfigError
from run_demo import NOTIFICATION_TEMPLATE, RunJournal, demo_definition, run
from rhel_result import workflow_result_text
from slack_app import APPROVE, HOLD
from test_slack_app import FakeController, Response


class DemoAPI(FakeController):
    def __init__(self):
        super().__init__()
        self.repo = "https://github.com/yKanaGit/aapplaybooks.git"
        self.workflow.update(workflow_job_template=4, name=SLACK_WORKFLOW)
        self.template = {
            "id": 4, "name": SLACK_WORKFLOW, "organization": 1, "extra_vars": {"demo_requires_event": True},
            "ask_variables_on_launch": True,
            "inventory": None, "allow_simultaneous": False, "survey_enabled": False,
        }
        self.nodes["results"].pop(1)
        self.nodes["results"][0]["success_nodes"] = [23]
        self.nodes["results"][1]["success_nodes"] = [24]
        self.nodes["results"].append({"id": 24, "identifier": "patch-verify", "workflow_job": 11,
                                      "job": None, "unified_job_template": 9, "success_nodes": [],
                                      "failure_nodes": [], "always_nodes": []})
        self.template_nodes = {"next": None, "results": [
            {**node, "workflow_job_template": 4} for node in self.nodes["results"]
        ]}
        self.apply.update(job_type="run", limit="node1.example.com", survey_enabled=False,
                          ask_variables_on_launch=False, job_slice_count=1, inventory=2)
        self.check_template = {**self.apply, "id": 5, "name": "RHEL-Patch-Check"}
        self.verify_template = {**self.apply, "id": 9, "name": "RHEL-Patch-Verify"}
        self.project = {
            "id": 3, "name": "RHEL-Patch-Demo", "status": "successful", "scm_type": "git",
            "scm_url": self.repo, "scm_branch": "main", "allow_override": False,
            "scm_update_on_launch": False, "scm_revision": "ab" * 20,
        }
        event, *_ = demo_definition({})
        self.notification = {
            "id": 8, "name": NOTIFICATION_TEMPLATE, "organization": 1, "playbook": "slack_notify.yml",
            "job_type": "run", "project": 3, "job_slice_count": 1, "limit": "localhost",
            "ask_variables_on_launch": False, "survey_enabled": False, "extra_vars": {"demo_event": event},
        }
        self.notification_jobs = {}
        self.launched = False
        self.workflow_runs = {}
        self.external_active = False
        self.launch_timeout = False
        self.finish_without_approval = False
        self.apply_job = {"id": 17, "status": "successful", "name": "RHEL-Patch-Apply",
                          "playbook": "rhel_patch.yml", "extra_vars": '{"apply_patch": false}'}
        self.verify_job = {"id": 18, "status": "successful", "name": "RHEL-Patch-Verify", "playbook": "rhel_patch.yml"}

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        method = request.get_method()
        path = url.path.removeprefix("/api/v2/")
        if path == "workflow_job_templates/4/launch/":
            self.calls.append((method, url.path))
            assert method == "POST"
            self.launch_vars = json.loads(request.data)["extra_vars"]
            assert request.get_header("Authorization") == "Bearer synthetic-oauth-token"
            if self.launched:
                self.workflow = {**self.workflow, "id": self.workflow["id"] + 1, "status": "running"}
            self.launched = True
            self.workflow["extra_vars"] = self.launch_vars
            self.workflow_runs[self.workflow["id"]] = self.workflow
            if self.launch_timeout:
                raise URLError("synthetic hidden body")
            if self.finish_without_approval:
                self.workflow["status"] = "failed"
            return Response({"workflow_job": self.workflow["id"], "id": self.workflow["id"], "workflow_job_template": 4,
                             "name": SLACK_WORKFLOW}, 201)
        if path == "workflow_jobs/":
            body = {"results": [self.workflow] if self.launched or self.external_active else [], "next": None}
        elif path == "job_templates/8/jobs/":
            query = parse_qs(url.query)
            rows = [{"id": job_id} for job_id in sorted(self.notification_jobs) if job_id > int(query.get("id__gt", [0])[0])]
            body = {"next": None, "results": rows[-1:] if query["order_by"] == ["-id"] else rows}
        else:
            body = {
                "organizations/": {"results": [{"id": 1, "name": "Default"}], "next": None},
                "workflow_job_templates/": {"results": [self.template], "next": None},
                "workflow_job_templates/4/": self.template,
                "workflow_job_templates/4/workflow_nodes/": self.template_nodes,
                "workflow_approval_templates/6/": {"id": 6, "name": "Approve RHEL Security Patch", "timeout": 3600},
                "job_templates/": {"results": [self.notification], "next": None},
                "job_templates/5/": self.check_template,
                "job_templates/8/": self.notification,
                "job_templates/9/": self.verify_template,
                **{f"jobs/{key}/": value for key, value in self.notification_jobs.items()},
                **{f"workflow_jobs/{key}/": value for key, value in self.workflow_runs.items()},
                **{f"workflow_jobs/{key}/workflow_nodes/": {
                    "next": None, "results": [{**node, "workflow_job": key} for node in self.nodes["results"]],
                } for key in self.workflow_runs},
                "projects/3/": self.project,
                "inventories/2/hosts/": {"results": [{"name": "node1.example.com", "enabled": True}], "next": None},
                "jobs/17/": self.apply_job,
                "jobs/18/": self.verify_job,
            }.get(path)
        if body is not None:
            assert method == "GET" and request.get_header("Authorization") == "Bearer synthetic-oauth-token"
            self.calls.append((method, url.path))
            return Response(body)
        return super().open(request, timeout)


class DemoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "bindings.sqlite3"
        self.api = DemoAPI()
        self.controller = Controller("https://controller.example.test", "synthetic-oauth-token", opener=self.api)
        self.journal = RunJournal(self.state, self.controller.origin)

    def execute(self, *, dry_run=False, status=False, new=False, service=False, serve=None, inspect=None, inspect_rhel=None):
        args = SimpleNamespace(dry_run=dry_run, status=status, new=new, serve=service, approval_wait=10,
                               result_wait=10, source=None, inspect_notification_job=inspect, inspect_rhel_job=inspect_rhel)
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"AAP_SLACK_STATE_PATH": str(self.state)}, clear=True),
            patch("run_demo.load_config", return_value={"aap_url": self.controller.origin}),
            patch("run_demo.prompt_value", side_effect=lambda _, default: default),
            patch("run_demo.prompt_secret", side_effect=["synthetic-oauth-token", "xoxb-synthetic", "xapp-synthetic"]),
            patch("run_demo.slack_preferences", return_value=("TDEMO", "CDEMO", {"UALLOWED"}, "hold")),
            patch("builtins.input", return_value="RUN"),
            patch("controller.Controller", wraps=Controller, return_value=self.controller),
            patch("restore_demo.verify_github", return_value=self.api.project["scm_revision"]) as verify,
            patch("slack_app.serve", side_effect=serve) as slack,
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            try:
                result = run(args)
            except ApprovalError as error:
                result = 1
                print(error)
        return result, output.getvalue() + errors.getvalue(), slack, verify

    def writes(self):
        return [path for method, path in self.api.calls if method == "POST"]

    def callback(self, relay, action=APPROVE, actor="UALLOWED", value=None):
        event_id = self.journal.read()["event"]["id"]
        return relay.press({
            "team": {"id": "TDEMO"}, "user": {"id": actor},
            "container": {"type": "message", "channel_id": "CDEMO", "message_ts": "1.2"},
        }, {"action_id": action, "value": event_id if value is None else value})

    def slack_client(self):
        client = Mock()
        client.chat_postMessage.return_value = {"channel": "CDEMO", "ts": "1.2"}
        return client

    def test_slack_message_precedes_launch_and_click_passes_validated_fields(self):
        def slack(**kwargs):
            client = self.slack_client()
            relay, context = kwargs["event_session"](client)
            self.assertEqual(self.writes(), [])
            text = client.chat_postMessage.call_args.kwargs["text"]
            record = self.journal.read()
            for field in ("id", "hostname", "advisory", "cve", "severity"):
                self.assertIn(record["event"][field], text)
            self.assertIn("検知元：Satellite / Lightspeed（模擬）", text)
            self.assertIn("許可されていません", self.callback(relay, actor="UOTHER"))
            self.assertIn("一致しません", self.callback(relay, value="forged host/RHSA"))
            self.assertEqual(self.writes(), [])
            self.assertIn("Workflow Job #11", self.callback(relay))
            self.assertIn("処理済み", self.callback(relay))
            self.assertEqual(self.api.launch_vars, {
                "demo_event_id": record["event"]["id"], "demo_target_host": "node1.example.com",
                "demo_target_rhsa": "RHSA-2023:7743",
            })
            self.assertEqual(self.journal.read()["actor"], "UALLOWED")
            self.api.workflow["status"] = "successful"
            self.api.nodes["results"][1]["job"] = 17
            result = kwargs["monitor"](context)
            self.assertEqual(client.chat_postMessage.call_count, 2)
            self.assertEqual(client.chat_postMessage.call_args.kwargs["thread_ts"], "1.2")
            return result

        status, report, slack_spy, verify = self.execute(serve=slack)
        self.assertEqual(status, 0, report)
        self.assertEqual(self.writes(), ["/api/v2/workflow_job_templates/4/launch/"])
        self.assertIn("RHEL-Patch-Apply #17：successful", report)
        self.assertIn("パッチ適用：スキップ（apply_patch=false）", report)
        self.assertEqual(verify.call_args.kwargs["revision"], self.api.project["scm_revision"])
        slack_spy.assert_called_once()
        self.assertEqual(self.journal.read()["status"], "finished")
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual(len(self.writes()), 1)

    def test_dry_run_is_read_only(self):
        status, report, slack, verify = self.execute(dry_run=True)
        self.assertEqual(status, 0, report)
        self.assertTrue(self.api.calls)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.journal.path.exists())
        slack.assert_not_called()
        self.assertEqual(verify.call_count, 2)

    def test_non_boolean_apply_is_blocked_before_notification_and_at_click(self):
        self.api.apply["extra_vars"] = '{"apply_patch": "true", "target_rhsa": "RHSA-2023:7743"}'
        status, report, slack, _ = self.execute()
        self.assertEqual(status, 1)
        self.assertIn("apply_patch: true または false", report)
        self.assertEqual(self.writes(), [])
        slack.assert_not_called()
        self.api.apply["extra_vars"] = self.api.check_template["extra_vars"]

        def slack(**kwargs):
            relay, _ = kwargs["event_session"](self.slack_client())
            self.api.apply["extra_vars"] = '{"apply_patch": "true", "target_rhsa": "RHSA-2023:7743"}'
            self.assertIn("apply_patch: true または false", self.callback(relay))
            return 1

        self.assertEqual(self.execute(serve=slack)[0], 1)
        self.assertEqual(self.writes(), [])

    def test_hold_and_expiry_never_launch(self):
        def slack(**kwargs):
            relay, context = kwargs["event_session"](self.slack_client())
            with patch("run_demo.time.time", return_value=self.journal.read()["expires"]):
                self.assertIn("期限", self.callback(relay))
            self.assertIn("保留しました", self.callback(relay, HOLD))
            self.assertIn("処理済み", self.callback(relay))
            return kwargs["monitor"](context)

        self.assertEqual(self.execute(serve=slack)[0], 0)
        self.assertEqual(self.journal.read()["status"], "held")
        self.assertEqual(self.writes(), [])

    def test_slack_post_failure_never_launches_or_reposts_ambiguous_message(self):
        def slack(**kwargs):
            client = self.slack_client()
            client.chat_postMessage.side_effect = TimeoutError("synthetic Slack data")
            kwargs["event_session"](client)

        status, report, _, _ = self.execute(serve=slack)
        self.assertEqual(status, 1)
        self.assertIn("Slack 脆弱性通知", report)
        self.assertEqual(self.journal.read()["status"], "posting")
        status, _, slack, _ = self.execute()
        self.assertEqual(status, 1)
        slack.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_socket_start_failure_reuses_original_pending_message(self):
        def first(**kwargs):
            kwargs["event_session"](self.slack_client())
            raise ApprovalError("Socket Mode failure")

        self.assertEqual(self.execute(serve=first)[0], 1)
        previous = self.journal.read()

        def second(**kwargs):
            client = self.slack_client()
            kwargs["event_session"](client)
            client.chat_postMessage.assert_not_called()
            self.assertEqual(self.journal.read(), previous)
            return 0

        self.assertEqual(self.execute(serve=second)[0], 0)
        self.assertEqual(self.writes(), [])

    def test_ambiguous_launch_is_never_retried(self):
        self.api.launch_timeout = True

        def slack(**kwargs):
            relay, context = kwargs["event_session"](self.slack_client())
            self.assertIn("未確定", self.callback(relay))
            self.assertIn("処理済み", self.callback(relay))
            return kwargs["monitor"](context)

        self.assertEqual(self.execute(serve=slack)[0], 1)
        self.assertEqual(self.journal.read()["status"], "uncertain")
        self.assertEqual(self.execute(new=True)[0], 1)
        self.assertEqual(self.writes(), ["/api/v2/workflow_job_templates/4/launch/"])
        self.assertEqual(self.execute(status=True)[0], 0)

    def test_existing_active_workflow_blocks_launch(self):
        self.api.external_active = True

        def slack(**kwargs):
            relay, _ = kwargs["event_session"](self.slack_client())
            self.assertIn("実行中", self.callback(relay))
            return 1

        self.assertEqual(self.execute(serve=slack)[0], 1)
        self.assertEqual(self.writes(), [])

    def notification_job(self, job_id, status="successful"):
        event = {**self.api.notification["extra_vars"]["demo_event"], "id": f"{job_id:032x}"}
        job = {
            "id": job_id, "status": status, "launch_type": "manual", "job_template": 8,
            "project": 3, "playbook": "slack_notify.yml", "scm_revision": self.api.project["scm_revision"],
            "artifacts": {"demo_vulnerability_event": {"schema": 1, "notification_job_id": job_id, "event": event}},
        }
        self.api.notification_jobs[job_id] = job
        return job

    def press_notification(self, relay, job_id, action=APPROVE, actor="UALLOWED", timestamp=None):
        saved = self.journal.notification(job_id).read()
        return relay.press({
            "team": {"id": "TDEMO"}, "user": {"id": actor},
            "container": {"type": "message", "channel_id": "CDEMO", "message_ts": timestamp or saved["timestamp"]},
        }, {"action_id": action, "value": saved["event"]["id"]})

    def service_client(self):
        client = Mock()
        client.chat_postMessage.side_effect = lambda **kwargs: {
            "channel": kwargs["channel"], "ts": f"1.{client.chat_postMessage.call_count}",
        }
        return client

    def test_service_repeated_ui_runs_independent_approval_and_thread_results(self):
        old = self.notification_job(90)

        def slack(**kwargs):
            client = self.service_client()
            relay, context = kwargs["event_session"](client)
            stage = 0

            def poll_sleep(_seconds):
                nonlocal stage
                stage += 1
                if stage == 1:
                    client.chat_postMessage.assert_not_called()
                    self.notification_job(91, "running")
                    self.notification_job(92)
                elif stage == 2:
                    self.assertEqual(client.chat_postMessage.call_count, 1)
                    self.assertIsNone(self.journal.notification(old["id"]).read())
                    self.assertEqual(self.journal.notification(91).read()["status"], "observed")
                    record = self.journal.notification(92).read()
                    event_id = record["event"]["id"]
                    expires = datetime.fromtimestamp(record["expires"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    message = client.chat_postMessage.call_args.kwargs
                    self.assertEqual(message["text"], (
                        "RHEL脆弱性検知通知【デモ】\n\n"
                        "Red Hat Satellite / Red Hat Lightspeedによる脆弱性検知を模擬しています。"
                        "対象RHELで脆弱性が検出され、対応するセキュリティ修正情報が特定された想定です。\n\n"
                        "CVE：CVE-2022-43552\n"
                        "RHSA：RHSA-2023:7743\n"
                        "重要度：Low\n"
                        "対象ホスト：node1.example.com\n"
                        "検知元：Satellite / Lightspeed（模擬）\n"
                        f"イベントID：{event_id}\n"
                        f"承認期限：{expires}\n\n"
                        "承認後、Ansible Automation Platformが対象ホストのRHSAを再確認し、対応処理を実行します。"
                        "承認時に RHEL-Patch-Apply が apply_patch: true に設定されている場合、対象 RHSA を実際に適用します。"
                    ))
                    self.assertEqual(message["blocks"][0]["text"]["text"], message["text"])
                    buttons = message["blocks"][1]["elements"]
                    self.assertEqual([(button["action_id"], button["text"]["text"], button["value"]) for button in buttons],
                                     [(APPROVE, "パッチ適用を承認", event_id), (HOLD, "保留", event_id)])
                    self.assertEqual(self.writes(), [])
                    self.api.notification_jobs[91]["status"] = "successful"
                elif stage == 3:
                    self.assertEqual(client.chat_postMessage.call_count, 2)
                    self.assertIn("許可されていません", self.press_notification(relay, 91, actor="UOTHER"))
                    self.assertIn("一致しません", self.press_notification(relay, 91, timestamp="1.1"))
                    self.assertIn("Workflow Job #11", self.press_notification(relay, 91))
                    self.assertIn("処理済み", self.press_notification(relay, 91))
                    self.assertIn("実行中", self.press_notification(relay, 92))
                    self.api.workflow["status"] = "successful"
                    event = self.journal.notification(91).read()["event"]
                    self.api.nodes["results"][1]["job"] = 17
                    self.api.nodes["results"][2]["job"] = 18
                    self.api.check["artifacts"] = self.rhel_artifact(event, "before")
                    self.api.verify_job["artifacts"] = self.rhel_artifact(event, "after")
                elif stage == 4:
                    self.assertEqual(client.chat_postMessage.call_count, 3)
                    self.assertEqual(client.chat_postMessage.call_args.kwargs["thread_ts"], "1.2")
                    result = client.chat_postMessage.call_args.kwargs["text"]
                    self.assertIn("更新有無（関連 RPM の観測差分）：変更なし", result)
                    self.assertIn("脆弱性解消：未確認", result)
                    self.assertIn("Workflow Job #12", self.press_notification(relay, 92))
                    self.api.workflow["status"] = "failed"
                else:
                    self.assertEqual(client.chat_postMessage.call_count, 4)
                    self.assertEqual(client.chat_postMessage.call_args.kwargs["thread_ts"], "1.1")
                    self.assertIn("failed", client.chat_postMessage.call_args.kwargs["text"])
                    raise KeyboardInterrupt

            with patch("run_demo.time.sleep", side_effect=poll_sleep), self.assertRaises(KeyboardInterrupt):
                kwargs["monitor"](context)
            return 0

        status, report, _, _ = self.execute(service=True, serve=slack)
        self.assertEqual(status, 0, report)
        self.assertEqual(len(self.writes()), 2)
        first = self.journal.notification(91).read()
        second = self.journal.notification(92).read()
        self.assertNotEqual(first["event"]["id"], second["event"]["id"])
        self.assertEqual((first["outcome"], second["outcome"]), ("successful", "failed"))
        self.assertEqual(first["actor"], "UALLOWED")
        self.assertEqual(first["result_delivery"], "sent")
        _, report, _, _ = self.execute(status=True)
        self.assertIn("通知 Job #91", report)
        self.assertIn("通知 Job #92", report)

    @staticmethod
    def rhel_artifact(event, phase, version="0:7.1-2.el7.x86_64"):
        return {"demo_rhel_state": {
            "event_id": event["id"], "hostname": event["hostname"], "advisory": event["advisory"],
            "phase": phase, "availability": "available", "rpm_complete": True,
            "package_names": ["demo-rpm"], "installed_rpms": [f"demo-rpm {version}"],
        }}

    def test_result_distinguishes_observed_change_from_workflow_success(self):
        event = self.notification_job(59)["artifacts"]["demo_vulnerability_event"]["event"]
        controller = Mock()
        jobs = {
            13: {**self.api.check, "artifacts": self.rhel_artifact(event, "before")},
            17: self.api.apply_job,
            18: {**self.api.verify_job, "artifacts": self.rhel_artifact(event, "after", "0:7.2-1.el7.x86_64")},
        }
        controller.rows.return_value = [{**node, "job": job_id} for node, job_id in
                                        zip(self.api.nodes["results"], jobs)]
        def get(path):
            job_id = int(path.split("/")[1])
            if "job_events" in path:
                return {"results": [{
                    "job": job_id, "event": "runner_on_failed",
                    "event_data": {"task": "Show target advisory", "res": {"msg": "sensitive diagnostic"}},
                }]}
            return jobs[job_id]

        controller.get.side_effect = get
        message = workflow_result_text(controller, 11, "successful", event)
        for value in (event["id"], event["hostname"], event["advisory"], event["cve"], "0:7.1-2.el7.x86_64",
                      "0:7.2-1.el7.x86_64", "変更あり", "脆弱性解消：未確認"):
            self.assertIn(value, message)

        for job_id, stage in ((13, "Check"), (17, "Apply"), (18, "Verify")):
            with self.subTest(stage=stage):
                jobs[job_id]["status"] = "failed"
                message = workflow_result_text(controller, 11, "failed", event)
                self.assertIn(f"RHEL-Patch-{stage} #{job_id}：failed", message)
                self.assertIn("RHSA 情報の照会に失敗", message)
                self.assertNotIn("sensitive diagnostic", message)
                self.assertIn("脆弱性解消：未確認", message)
                jobs[job_id]["status"] = "successful"

        jobs[18]["artifacts"] = self.rhel_artifact({**event, "id": "f" * 32}, "after")
        message = workflow_result_text(controller, 11, "successful", event)
        self.assertIn("適用後状態：未確認", message)
        self.assertIn("更新有無（関連 RPM の観測差分）：未確認", message)
        controller.rows.side_effect = ControllerHTTPError(403)
        message = workflow_result_text(controller, 11, "failed", event)
        self.assertIn("ノード一覧：未確認 (HTTP 403)", message)
        self.assertIn("適用前状態：未確認", message)

    def test_read_only_rhel_inspection_compares_complete_matching_scopes(self):
        event = self.notification_job(59)["artifacts"]["demo_vulnerability_event"]["event"]
        self.api.notification_jobs[75] = {**self.api.check, "id": 75, "artifacts": self.rhel_artifact(event, "before")}
        self.api.notification_jobs[77] = {**self.api.verify_job, "id": 77, "artifacts": self.rhel_artifact(event, "after")}
        status, output, slack, _ = self.execute(inspect_rhel=[75, 77])
        self.assertEqual(status, 0, output)
        self.assertIn("比較結果：変更なし", output)
        for field in ("package_names", "installed_rpms", "rpm_complete", "availability"):
            self.assertEqual(output.count(f'"{field}"'), 2)
        self.assertTrue(all(method == "GET" and path.endswith(("/ping/", "/jobs/75/", "/jobs/77/"))
                            for method, path in self.api.calls))
        self.assertFalse(self.journal.notification_path.exists())
        slack.assert_not_called()

        state = self.api.notification_jobs[77]["artifacts"]["demo_rhel_state"]
        state.update(rpm_complete=False, rpm_issues=["rpm_inventory_parse"])
        status, output, _, _ = self.execute(inspect_rhel=[75, 77])
        self.assertEqual(status, 1, output)
        self.assertIn("rpm_inventory_parse", output)
        self.assertIn("比較結果：未確認（適用前後の RPM 情報が揃っていません）", output)

        state.update(package_names=["demo-rpm", "another-rpm"], rpm_complete=True, rpm_issues=[])
        status, output, _, _ = self.execute(inspect_rhel=[75, 77])
        self.assertEqual(status, 1, output)
        self.assertIn("比較結果：未確認（対象 RPM 名の範囲が異なります）", output)

        state.update(package_names=["demo-rpm"], event_id="b" * 32)
        status, output, _, _ = self.execute(inspect_rhel=[75, 77])
        self.assertEqual(status, 1, output)
        self.assertIn("比較結果：未確認（前後のイベント ID が異なります）", output)

    def test_service_does_not_retry_an_ambiguous_result_post(self):
        def slack(**kwargs):
            client = self.service_client()
            relay, _ = kwargs["event_session"](client)
            self.journal.notification_path.mkdir()
            self.notification_job(117)
            relay.tick(client, 116)
            self.assertIn("Workflow Job #11", self.press_notification(relay, 117))
            self.api.workflow["status"] = "failed"
            client.chat_postMessage.side_effect = TimeoutError("sensitive response")
            relay.tick(client, 116)
            relay.tick(client, 116)
            self.assertEqual(client.chat_postMessage.call_count, 2)
            self.assertEqual(self.journal.notification(117).read()["result_delivery"], "uncertain")
            return 0

        status, output, _, _ = self.execute(service=True, serve=slack)
        self.assertEqual(status, 0, output)
        self.assertNotIn("sensitive response", output)

    def test_service_restart_retains_pending_and_skips_unseen_history(self):
        client = self.service_client()

        def before(**kwargs):
            _, context = kwargs["event_session"](client)
            stage = 0

            def poll_sleep(_seconds):
                nonlocal stage
                stage += 1
                if stage == 1:
                    self.notification_job(101)
                else:
                    raise KeyboardInterrupt

            with patch("run_demo.time.sleep", side_effect=poll_sleep), self.assertRaises(KeyboardInterrupt):
                kwargs["monitor"](context)
            return 0

        self.assertEqual(self.execute(service=True, serve=before)[0], 0)
        previous = self.journal.notification(101).read()
        self.notification_job(102)

        def after(**kwargs):
            relay, context = kwargs["event_session"](client)
            stage = 0

            def poll_sleep(_seconds):
                nonlocal stage
                stage += 1
                if stage == 1:
                    self.assertEqual(client.chat_postMessage.call_count, 1)
                    self.assertEqual(self.journal.notification(101).read(), previous)
                    self.assertIsNone(self.journal.notification(102).read())
                    self.assertIn("保留しました", self.press_notification(relay, 101, HOLD))
                    self.notification_job(103)
                else:
                    self.assertEqual(client.chat_postMessage.call_count, 2)
                    expires = self.journal.notification(103).read()["expires"]
                    with patch("run_demo.time.time", return_value=expires):
                        self.assertIn("期限", self.press_notification(relay, 103))
                        relay.tick(client, 102)
                    raise KeyboardInterrupt

            with patch("run_demo.time.sleep", side_effect=poll_sleep), self.assertRaises(KeyboardInterrupt):
                kwargs["monitor"](context)
            return 0

        self.assertEqual(self.execute(service=True, serve=after)[0], 0)
        self.assertEqual(self.journal.notification(101).read()["status"], "held")
        self.assertEqual(self.journal.notification(103).read()["status"], "expired")
        self.assertEqual(self.writes(), [])

    def test_service_ignores_forged_events_failed_jobs_and_ambiguous_post(self):
        def slack(**kwargs):
            client = self.service_client()
            client.chat_postMessage.side_effect = TimeoutError("synthetic secret body")
            relay, _ = kwargs["event_session"](client)
            self.journal.notification_path.mkdir()
            self.notification_job(107)["artifacts"]["demo_vulnerability_event"]["event"]["hostname"] = "other.example.test"
            self.notification_job(108)["artifacts"]["demo_vulnerability_event"]["notification_job_id"] = 107
            self.notification_job(109, status="failed")
            self.notification_job(110)["launch_type"] = "workflow"
            self.notification_job(111)
            relay.tick(client, 106)
            relay.tick(client, 106)
            client.chat_postMessage.assert_called_once()
            self.assertEqual(self.journal.notification(111).read()["status"], "posting")
            for job_id in range(107, 111):
                self.assertEqual(self.journal.notification(job_id).read()["status"], "ignored")
            return 0

        status, report, _, _ = self.execute(service=True, serve=slack)
        self.assertEqual(status, 0, report)
        self.assertNotIn("synthetic secret body", report)
        self.assertEqual(self.writes(), [])

    def test_service_approval_accepts_true_only_for_apply_with_matching_rhsa(self):
        def slack(**kwargs):
            client = self.service_client()
            relay, _ = kwargs["event_session"](client)
            self.journal.notification_path.mkdir()
            self.notification_job(113)
            relay.tick(client, 112)
            self.api.apply["extra_vars"] = '{"apply_patch": true, "target_rhsa": "RHSA-2023:9999"}'
            self.assertIn("RHSA", self.press_notification(relay, 113))
            self.assertEqual(self.writes(), [])
            self.api.apply["extra_vars"] = '{"apply_patch": true, "target_rhsa": "RHSA-2023:7743"}'
            for template in (self.api.check_template, self.api.verify_template):
                original = template["extra_vars"]
                template["extra_vars"] = self.api.apply["extra_vars"]
                self.assertIn("apply_patch: false", self.press_notification(relay, 113))
                template["extra_vars"] = original
            self.assertEqual(self.writes(), [])
            self.assertIn("Workflow Job #11", self.press_notification(relay, 113))
            self.api.apply_job["extra_vars"] = self.api.apply["extra_vars"]
            self.api.nodes["results"][1]["job"] = 17
            result = workflow_result_text(relay.controller, 11, "successful", self.journal.notification(113).read()["event"])
            self.assertIn("パッチ適用：適用処理完了（apply_patch=true）", result)
            return 0

        self.assertEqual(self.execute(service=True, serve=slack)[0], 0)
        self.assertEqual(self.writes(), ["/api/v2/workflow_job_templates/4/launch/"])

    def test_service_rejects_stringified_job_id_from_older_ansible_and_accepts_integer(self):
        def slack(**kwargs):
            client = self.service_client()
            relay, _ = kwargs["event_session"](client)
            self.journal.notification_path.mkdir()
            old = self.notification_job(59)
            old["artifacts"]["demo_vulnerability_event"]["notification_job_id"] = "59"
            self.notification_job(60)
            relay.tick(client, 58)
            relay.tick(client, 58)
            client.chat_postMessage.assert_called_once()
            self.assertEqual(self.journal.notification(59).read()["status"], "ignored")
            self.assertEqual(self.journal.notification(60).read()["status"], "pending")
            return 0

        status, output, _, _ = self.execute(service=True, serve=slack)
        self.assertEqual(status, 0)
        self.assertIn("notification_job_id: 型または構造不一致（期待 int、実際 str）", output)
        self.assertEqual(self.writes(), [])

    def test_inspect_job_is_read_only_and_reports_field_types_without_raw_values(self):
        job = self.notification_job(59)
        artifact = job["artifacts"]["demo_vulnerability_event"]
        artifact["notification_job_id"] = "59"
        artifact["event"]["hostname"] = "synthetic-unexpected-value"
        artifact["event"]["advisory"] = 7743
        status, output, slack, _ = self.execute(inspect=59)
        self.assertEqual(status, 1)
        self.assertIn("GET /api/v2/jobs/59/", output)
        self.assertIn("notification_job_id: 型または構造不一致（期待 int、実際 str）", output)
        self.assertIn("期待する整数と同じ数字の文字列", output)
        self.assertIn("event.hostname: 値が不一致（型 str）", output)
        self.assertIn("event.advisory: 型または構造不一致（期待 str、実際 int）", output)
        for key in ("id", "cve", "severity", "source"):
            self.assertIn(f"event.{key}: 一致（型 str）", output)
        self.assertNotIn("synthetic-unexpected-value", output)
        self.assertFalse(self.journal.notification_path.exists())
        self.assertTrue(all(method == "GET" and path.endswith(("/ping/", "/jobs/59/"))
                            for method, path in self.api.calls))
        slack.assert_not_called()

    def test_inspect_job_distinguishes_empty_missing_and_stringified_artifacts(self):
        job = self.notification_job(59)
        for artifacts, diagnostic in (
            ({}, "artifacts: 空のオブジェクト"),
            ({"other": True}, "demo_vulnerability_event: 型または構造不一致（期待 dict、実際 未設定）"),
            ({"demo_vulnerability_event": "sensitive-string"}, "demo_vulnerability_event: 型または構造不一致（期待 dict、実際 str）"),
            ({"demo_vulnerability_event": {"schema": True, "notification_job_id": 59, "event": "{}"}},
             "event: 型または構造不一致（期待 dict、実際 str）"),
        ):
            with self.subTest(diagnostic=diagnostic):
                job["artifacts"] = artifacts
                status, output, _, _ = self.execute(inspect=59)
                self.assertEqual(status, 1)
                self.assertIn(diagnostic, output)
                self.assertNotIn("sensitive-string", output)

        self.notification_job(59)
        self.assertEqual(self.execute(inspect=59)[0], 0)

    def test_service_does_not_launch_another_event_after_uncertain_launch(self):
        self.api.launch_timeout = True

        def slack(**kwargs):
            client = self.service_client()
            relay, _ = kwargs["event_session"](client)
            self.journal.notification_path.mkdir()
            self.notification_job(114)
            self.notification_job(115)
            relay.tick(client, 113)
            self.assertIn("未確定", self.press_notification(relay, 114))
            self.assertIn("未確定", self.press_notification(relay, 115))
            self.assertEqual(self.journal.notification(114).read()["status"], "uncertain")
            self.assertEqual(self.journal.notification(115).read()["status"], "pending")
            return 0

        self.assertEqual(self.execute(service=True, serve=slack)[0], 0)
        self.assertEqual(self.writes(), ["/api/v2/workflow_job_templates/4/launch/"])

    def test_local_lock_rejects_a_concurrent_run(self):
        with self.journal.locked():
            with self.assertRaisesRegex(ConfigError, "実行中"), self.journal.locked():
                self.fail("concurrent runner obtained the same lock")


if __name__ == "__main__":
    unittest.main()
