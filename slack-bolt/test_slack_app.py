"""Local tests for Slack action binding and Controller 4.5 API behavior."""

import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from controller import ApprovalError, Controller
from slack_app import APPROVE, HOLD, SELECT_HOSTS, Binding, Relay, StageError, Store, check_slack, main, message_blocks, serve


class Response(io.BytesIO):
    def __init__(self, body=None, status=200):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status


class FakeController:
    def __init__(self):
        self.calls = []
        self.approval = {
            "id": 14, "type": "workflow_approval", "name": "Approve RHEL Security Patch",
            "status": "pending", "timed_out": False, "can_approve_or_deny": True,
            "approval_expiration": "2999-01-01T00:00:00Z",
        }
        self.workflow = {
            "id": 11, "name": "RHEL-Patch-Workflow", "status": "running",
            "workflow_job_template": 4, "extra_vars": "", "scm_branch": None,
        }
        self.nodes = {
            "next": None, "results": [
                {"id": 21, "workflow_job": 11, "identifier": "patch-check", "job": 13,
                 "unified_job_template": 5, "success_nodes": [22], "failure_nodes": [], "always_nodes": []},
                {"id": 22, "workflow_job": 11, "identifier": "patch-approval", "job": 14,
                 "unified_job_template": 6, "success_nodes": [23], "failure_nodes": [], "always_nodes": []},
                {"id": 23, "workflow_job": 11, "identifier": "patch-apply", "job": None,
                 "unified_job_template": 7, "success_nodes": [], "failure_nodes": [], "always_nodes": [],
                 "extra_data": {}, "scm_branch": None},
            ],
        }
        self.check = {
            "id": 13, "job_template": 5, "status": "successful", "name": "RHEL-Patch-Check",
            "playbook": "rhel_patch.yml", "artifacts": {}, "project": 3, "extra_vars": '{"apply_patch": false}',
        }
        self.apply = {
            "id": 7, "name": "RHEL-Patch-Apply", "playbook": "rhel_patch.yml", "project": 3,
            "extra_vars": 'apply_patch: false\ntarget_rhsa: "RHSA-2023:7743"\n',
        }
        self.post_error = None

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        method = request.get_method()
        if url.netloc != "controller.example.test":
            raise AssertionError("Wrong destination")
        self.calls.append((method, url.path))
        authorization = request.get_header("Authorization")
        if url.path == "/api/controller/v2/ping/":
            self.assert_discovery_request(method, authorization)
            raise HTTPError(request.full_url, 404, "legacy Controller", {}, None)
        if url.path == "/api/v2/ping/":
            self.assert_discovery_request(method, authorization)
            return Response({"version": "4.5.30"})
        if authorization != "Bearer synthetic-oauth-token":
            raise AssertionError("Controller request must use its OAuth token")
        if method == "GET":
            return Response({
                "/api/v2/workflow_approvals/14/": self.approval,
                "/api/v2/workflow_jobs/11/": self.workflow,
                "/api/v2/workflow_jobs/11/workflow_nodes/": self.nodes,
                "/api/v2/jobs/13/": self.check,
                "/api/v2/job_templates/7/": self.apply,
            }[url.path])
        if method == "POST" and url.path in (
            "/api/v2/workflow_approvals/14/approve/", "/api/v2/workflow_approvals/14/deny/",
        ):
            if self.post_error:
                raise self.post_error
            self.approval["status"] = "successful" if url.path.endswith("/approve/") else "failed"
            return Response(status=204)
        raise AssertionError(f"Unexpected HTTP {method} {url.path}")

    @staticmethod
    def assert_discovery_request(method, authorization):
        if authorization is not None or method != "GET":
            raise AssertionError("Version discovery must not send credentials")


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fake = FakeController()
        self.controller = Controller("https://controller.example.test", "synthetic-oauth-token", 11, 14, opener=self.fake)
        self.store = Store(Path(temp.name) / "approvals.sqlite", self.controller)
        self.binding = Binding("TDEMO", "CDEMO", "1234567890.1234", "aap:11:14:synthetic-nonce")
        self.assertEqual(self.store.bind("TDEMO", "CDEMO", lambda: self.binding), self.binding)
        self.relay = Relay(self.controller, self.store, self.binding, {"UALLOWED"}, "hold")
        self.body = {
            "team": {"id": self.binding.team},
            "user": {"id": "UALLOWED"},
            "container": {"type": "message", "channel_id": self.binding.channel,
                          "message_ts": self.binding.timestamp},
        }

    def press(self, action=APPROVE, value=None):
        return self.relay.press(
            self.body, {"action_id": action, "value": self.binding.value if value is None else value},
        )

    @property
    def writes(self):
        return [path for method, path in self.fake.calls if method == "POST"]

    def test_check_cli_is_read_only(self):
        output = io.StringIO()
        with (
            patch("slack_app.Controller", return_value=self.controller),
            patch("slack_app.serve") as serve,
            patch("sys.argv", ["slack_app.py", "--check", "--workflow-job-id", "11", "--approval-id", "14"]),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertIn("Workflow Job #11 / Approval #14", output.getvalue())
        serve.assert_not_called()
        self.assertTrue(self.fake.calls)
        self.assertEqual(self.writes, [])

    def test_check_reports_aap_http_status_without_response_body(self):
        with (
            patch.object(self.fake, "open", side_effect=HTTPError(
                "https://controller.example.test", 403, "sensitive-aap-value", {}, None,
            )),
            patch("slack_app.Controller", return_value=self.controller),
            patch("sys.argv", ["slack_app.py", "--check", "--workflow-job-id", "11", "--approval-id", "14"]),
            redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(main(), 1)
        self.assertIn("[AAP API 接続・バージョン確認] ControllerHTTPError", output.getvalue())
        self.assertIn("HTTP 403", output.getvalue())
        self.assertNotIn("sensitive-aap-value", output.getvalue())

    def test_diagnose_checks_slack_without_posting_or_starting_socket(self):
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
            from slack_sdk import WebClient
        except ImportError:
            self.skipTest("Install slack-bolt/requirements.txt for Slack startup tests")
        auth = self.auth_response("chat:write,channels:read,users:read")
        absent = self.store.path.with_name("missing.sqlite")
        with (
            patch.dict(os.environ, {
                "SLACK_BOT_TOKEN": "xoxb-synthetic", "SLACK_APP_TOKEN": "xapp-synthetic",
                "SLACK_TEAM_ID": "TDEMO", "SLACK_CHANNEL_ID": "CDEMO", "SLACK_ALLOWED_USER_IDS": "UALLOWED",
                "AAP_SLACK_STATE_PATH": str(absent),
            }),
            patch("slack_app.Controller", return_value=self.controller),
            patch("sys.argv", ["slack_app.py", "--diagnose", "--workflow-job-id", "11", "--approval-id", "14"]),
            patch.object(WebClient, "auth_test", return_value=auth),
            patch.object(WebClient, "conversations_info", return_value={"channel": {"id": "CDEMO", "is_member": True}}),
            patch.object(WebClient, "users_info", return_value={"user": {"id": "UALLOWED", "team_id": "TDEMO"}}),
            patch.object(WebClient, "apps_connections_open", return_value={"url": "wss://example.test/sensitive-slack-value"}),
            patch.object(WebClient, "chat_postMessage") as post,
            patch.object(SocketModeHandler, "start") as socket,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(), 0)
        self.assertIn("Bot 参加を確認", output.getvalue())
        self.assertIn("許可ユーザーが対象 Workspace", output.getvalue())
        self.assertNotIn("sensitive-slack-value", output.getvalue())
        post.assert_not_called()
        socket.assert_not_called()
        self.assertFalse(absent.exists())
        self.assertEqual(self.writes, [])

    def auth_response(self, scopes="chat:write"):
        from slack_sdk.web.slack_response import SlackResponse

        return SlackResponse(
            client=None, http_verb="POST", api_url="https://slack.com/api/auth.test", req_args={},
            data={"ok": True, "team_id": self.binding.team, "user_id": "UBOT", "bot_id": "BDEMO"},
            headers={"X-OAuth-Scopes": scopes}, status_code=200,
        )

    def test_slack_checks_flag_wrong_workspace_scope_and_membership(self):
        try:
            client = Mock(auth_test=Mock(return_value=self.auth_response("chat:write,channels:read")))
        except ImportError:
            self.skipTest("Install slack-bolt/requirements.txt for Slack startup tests")
        client.conversations_info.return_value = {"channel": {"id": "CDEMO", "is_member": False}}
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(StageError, "Workspace"):
                check_slack(client, "TOTHER", "CDEMO", {"UALLOWED"})
            with self.assertRaisesRegex(StageError, "参加していません"):
                check_slack(client, "TDEMO", "CDEMO", {"UALLOWED"})
            client.auth_test.return_value = self.auth_response("channels:read")
            with self.assertRaisesRegex(StageError, "chat:write がありません"):
                check_slack(client, "TDEMO", "CDEMO", {"UALLOWED"})
        client.chat_postMessage.assert_not_called()

    def test_failed_socket_start_reuses_an_already_posted_message(self):
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError
        except ImportError:
            self.skipTest("Install slack-bolt/requirements.txt for Slack startup tests")
        socket_failure = SlackApiError("sensitive socket message", {"error": "invalid_app_token"})
        state = self.store.path.with_name("socket-retry.sqlite")
        with (
            patch.dict(os.environ, {
                "SLACK_BOT_TOKEN": "xoxb-synthetic", "SLACK_APP_TOKEN": "xapp-synthetic",
                "AAP_SLACK_STATE_PATH": str(state),
            }),
            patch.object(WebClient, "auth_test", return_value=self.auth_response()),
            patch.object(WebClient, "chat_postMessage", return_value={
                "channel": self.binding.channel, "ts": self.binding.timestamp,
            }) as post,
            patch.object(SocketModeHandler, "start", side_effect=[socket_failure, None]) as socket,
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(StageError, r"Socket Mode 開始.*SlackApiError.*invalid_app_token") as error:
                serve(self.controller, team=self.binding.team, channel=self.binding.channel, users={"UALLOWED"})
            self.assertNotIn("sensitive socket message", str(error.exception))
            serve(self.controller, team=self.binding.team, channel=self.binding.channel, users={"UALLOWED"})
        self.assertEqual(socket.call_count, 2)
        post.assert_called_once()
        self.assertEqual(self.writes, [])

    def test_unknown_slack_post_result_blocks_retry_and_channel_change(self):
        store = Store(self.store.path.with_name("uncertain.sqlite"), self.controller)
        post = Mock(side_effect=StageError("Slack 投稿 chat.postMessage", TimeoutError("sensitive transport value")))
        with self.assertRaisesRegex(StageError, "Slack 投稿 chat.postMessage.*TimeoutError") as error:
            store.bind("TDEMO", "CDEMO", post)
        self.assertNotIn("sensitive transport value", str(error.exception))
        with self.assertRaisesRegex(ApprovalError, "投稿結果が未確定"):
            store.bind("TDEMO", "CDEMO", post)
        with self.assertRaisesRegex(ApprovalError, "別の Workflow、Workspace、または通知先"):
            store.bind("TDEMO", "COTHER", post)
        post.assert_called_once()

    def test_explicit_slack_post_rejection_allows_corrected_retry(self):
        try:
            from slack_sdk.errors import SlackApiError
        except ImportError:
            self.skipTest("Install slack-bolt/requirements.txt for Slack startup tests")
        rejected = self.auth_response()
        rejected.data = {"ok": False, "error": "not_in_channel", "secret": "sensitive Slack response"}
        failure = StageError("Slack 投稿 chat.postMessage", SlackApiError("sensitive Slack message", rejected))
        store = Store(self.store.path.with_name("rejected.sqlite"), self.controller)
        with self.assertRaisesRegex(StageError, r"Slack 投稿 chat.postMessage.*not_in_channel.*HTTP 200") as error:
            store.bind("TDEMO", "CDEMO", Mock(side_effect=failure))
        self.assertNotIn("sensitive Slack", str(error.exception))
        self.assertEqual(store.status(), "missing")
        post = Mock(return_value=self.binding)
        self.assertEqual(store.bind("TDEMO", "CDEMO", post), self.binding)
        post.assert_called_once()

    def test_notification_carries_both_ids_and_bound_value(self):
        summary, blocks = message_blocks(11, 14, self.binding.value)
        self.assertIn("Workflow Job #11 | Approval #14", summary)
        self.assertEqual([item["value"] for item in blocks[1]["elements"]], [self.binding.value] * 2)

    def test_bolt_dispatches_the_bound_message_with_mocked_services(self):
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
            from slack_bolt.request import BoltRequest
            from slack_sdk import WebClient
            from slack_sdk.web.slack_response import SlackResponse
            from slack_sdk.webhook import WebhookClient
        except ImportError:
            self.skipTest("Install slack-bolt/requirements.txt for the Bolt dispatch test")

        posted = {}
        response = {}
        sent = threading.Event()

        def post_message(client, **kwargs):
            posted.update(kwargs)
            return {"channel": self.binding.channel, "ts": self.binding.timestamp}

        def respond(client, payload):
            response.update(payload)
            sent.set()

        def dispatch_click(handler):
            body = {
                **self.body, "type": "block_actions", "channel": {"id": self.binding.channel},
                "actions": [posted["blocks"][1]["elements"][0]],
                "response_url": "https://hooks.slack.com/actions/synthetic",
            }
            ack = handler.app.dispatch(BoltRequest(body=json.dumps(body), mode="socket_mode"))
            self.assertEqual(ack.status, 200)
            self.assertTrue(sent.wait(4), "Bolt did not dispatch the registered action")

        auth = SlackResponse(
            client=None, http_verb="POST", api_url="https://slack.com/api/auth.test", req_args={},
            data={"ok": True, "team_id": self.binding.team, "user_id": "UBOT", "bot_id": "BDEMO"},
            headers={}, status_code=200,
        )
        with (
            patch.dict(os.environ, {
                "SLACK_BOT_TOKEN": "xoxb-synthetic", "SLACK_APP_TOKEN": "xapp-synthetic",
                "AAP_SLACK_STATE_PATH": str(self.store.path.with_name("new-binding.sqlite")),
            }),
            patch.object(WebClient, "auth_test", return_value=auth),
            patch.object(WebClient, "chat_postMessage", post_message),
            patch.object(SocketModeHandler, "start", dispatch_click),
            patch.object(WebhookClient, "send_dict", respond),
            redirect_stdout(io.StringIO()),
        ):
            serve(self.controller, team=self.binding.team, channel=self.binding.channel, users={"UALLOWED"})
        self.assertEqual(response["response_type"], "ephemeral")
        self.assertEqual(response["text"], "AAP の承認を反映しました。")
        self.assertEqual(self.writes, ["/api/v2/workflow_approvals/14/approve/"])

    def test_bolt_confirms_host_selection_without_calling_the_approval_relay(self):
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
            from slack_bolt.request import BoltRequest
            from slack_sdk import WebClient
        except ImportError:
            self.skipTest("Install slack-bolt/requirements.txt for the Bolt dispatch test")

        relay = Mock()
        opened = {}
        modal_opened = threading.Event()
        message_updated = threading.Event()
        updates = []
        text = "CVE：CVE-2022-43552\nRHSA：RHSA-2023:7743"
        blocks = [
            {"type": "section", "text": {"type": "plain_text", "text": text}},
            {"type": "actions", "elements": [
                {"type": "button", "action_id": action, "text": {"type": "plain_text", "text": label},
                 "value": "synthetic-event-id"}
                for action, label in (
                    (APPROVE, "パッチ適用を承認"), (HOLD, "保留"), (SELECT_HOSTS, "適用対象を選ぶ"),
                )
            ]},
        ]

        def open_view(client, **kwargs):
            opened.update(kwargs)
            modal_opened.set()

        def update_message(client, **kwargs):
            updates.append(kwargs)
            message_updated.set()

        def dispatch_selection(handler):
            def dispatch(body):
                result = handler.app.dispatch(BoltRequest(body=json.dumps(body), mode="socket_mode"))
                self.assertEqual(result.status, 200)
                return json.loads(result.body) if result.body else None

            def open_modal(message):
                modal_opened.clear()
                dispatch({
                    **self.body, "type": "block_actions", "trigger_id": "synthetic-trigger",
                    "message": {"ts": self.binding.timestamp, **message},
                    "actions": [blocks[1]["elements"][-1]],
                })
                self.assertTrue(modal_opened.wait(4), "Bolt did not dispatch the host selection action")
                self.assertEqual(opened["trigger_id"], "synthetic-trigger")
                return opened["view"]

            original = {"text": text, "blocks": blocks}
            modal = open_modal(original)
            self.assertEqual(modal["title"]["text"], "適用対象ホスト")
            self.assertEqual(modal["submit"]["text"], "選択を確認")
            self.assertFalse(modal.get("send_close_notification", False))
            self.assertEqual(updates, [])
            field = modal["blocks"][0]
            select = field["element"]
            self.assertEqual(select["type"], "multi_static_select")
            self.assertEqual([option["value"] for option in select["options"]], ["node1.example.com"])
            self.assertNotIn("initial_options", select)

            def submit(view, values):
                return dispatch({
                    "type": "view_submission", "team": self.body["team"], "user": self.body["user"],
                    "view": {**view, "state": {"values": values}},
                })

            def select_hosts(view, options):
                return submit(view, {field["block_id"]: {
                    select["action_id"]: {"type": select["type"], "selected_options": options},
                }})

            for options in ([], [{"value": "other.example.com"}]):
                with self.subTest(options=options):
                    error = select_hosts(modal, options)
                    self.assertEqual(error["response_action"], "errors")
                    self.assertIn(field["block_id"], error["errors"])
            confirmed = select_hosts(modal, select["options"])
            self.assertEqual(confirmed["response_action"], "update")
            preview = confirmed["view"]
            self.assertEqual(preview["blocks"][0]["text"]["text"], "node1.example.com を選択しました")
            self.assertEqual(preview["submit"]["text"], "OK")
            self.assertFalse(preview.get("send_close_notification", False))
            self.assertEqual(updates, [])

            def accept(view):
                message_updated.clear()
                self.assertIsNone(submit(view, {}))
                self.assertTrue(message_updated.wait(4), "Bolt did not update the notification")

            # Dismiss the preview and reopen it before confirming.
            modal = open_modal(original)
            preview = select_hosts(modal, select["options"])["view"]
            self.assertEqual(updates, [])
            accept(preview)

            selection_text = "選択済みホスト：node1.example.com"
            first_update = updates[0]
            self.assertEqual(first_update["channel"], self.binding.channel)
            self.assertEqual(first_update["ts"], self.binding.timestamp)
            self.assertEqual(first_update["text"], f"{text}\n{selection_text}")
            self.assertEqual(first_update["blocks"][:-1], blocks)
            self.assertEqual(first_update["blocks"][-1]["text"]["text"], selection_text)

            modal = open_modal({"text": first_update["text"], "blocks": first_update["blocks"]})
            accept(select_hosts(modal, select["options"])["view"])
            self.assertEqual(updates, [first_update, first_update])

        with (
            patch.object(WebClient, "auth_test", return_value=self.auth_response()),
            patch.object(WebClient, "views_open", open_view),
            patch.object(WebClient, "chat_update", update_message),
            patch.object(SocketModeHandler, "start", dispatch_selection),
            redirect_stdout(io.StringIO()),
        ):
            serve(team=self.binding.team, channel=self.binding.channel, users={"UALLOWED"},
                  bot_token="xoxb-synthetic", app_token="xapp-synthetic", event_session=lambda client: (relay, client))
        relay.press.assert_not_called()
        self.assertEqual(self.fake.calls, [])

    def test_approve_once_across_repeated_or_concurrent_clicks(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            messages = list(executor.map(lambda _: self.press(), range(2)))
        self.assertEqual(sum("承認を反映" in message for message in messages), 1)
        self.assertEqual(self.writes, ["/api/v2/workflow_approvals/14/approve/"])

    def test_default_hold_is_terminal_locally_and_never_posts(self):
        self.assertIn("承認待ちのまま", self.press(HOLD))
        store = Store(self.store.path, self.controller)
        binding = store.bind("TDEMO", "CDEMO", lambda: self.fail("reposted an existing notification"))
        relay = Relay(self.controller, store, binding, {"UALLOWED"}, "hold")
        self.assertIn("処理済み", relay.press(self.body, {"action_id": APPROVE, "value": binding.value}))
        self.assertEqual(store.status(), "held")
        self.assertEqual(self.writes, [])

    def test_opt_in_hold_posts_only_deny(self):
        self.relay.hold_action = "deny"
        self.assertIn("拒否として反映", self.press(HOLD))
        self.press()
        self.assertEqual(self.writes, ["/api/v2/workflow_approvals/14/deny/"])

    def test_other_workspace_and_user_are_rejected_without_aap_requests(self):
        self.body["team"]["id"] = "TOTHER"
        self.assertIn("許可されていません", self.press())
        self.body["team"]["id"] = "TDEMO"
        self.body["user"]["id"] = "UOTHER"
        self.assertIn("許可されていません", self.press())
        self.assertEqual(self.fake.calls, [])

    def test_wrong_message_and_legacy_or_wrong_id_fail_before_api(self):
        self.body["container"]["message_ts"] = "1234567890.9999"
        self.assertIn("一致しません", self.press())
        self.body["container"]["message_ts"] = self.binding.timestamp
        self.assertIn("一致しません", self.press(value="RHSA-2023:7743"))
        self.assertIn("一致しません", self.press(value="aap:11:15:synthetic-nonce"))
        self.assertEqual(self.fake.calls, [])

    def test_approval_must_belong_to_selected_job(self):
        self.fake.nodes["results"][1]["job"] = 999
        self.assertIn("Approval ID", self.press())
        self.assertEqual(self.writes, [])

    def test_expired_or_decided_approval_is_read_only(self):
        self.fake.approval["approval_expiration"] = "2000-01-01T00:00:00Z"
        self.assertIn("期限", self.press())
        self.fake.approval["status"] = "successful"
        self.assertIn("処理済み", self.press())
        self.assertEqual(self.writes, [])

    def test_approve_permission_is_required(self):
        self.fake.approval["can_approve_or_deny"] = False
        self.assertIn("承認権限", self.press())
        self.assertEqual(self.writes, [])

    def test_variable_overrides_prevent_approval(self):
        self.fake.apply["extra_vars"] = "apply_patch: true"
        self.assertIn("apply_patch: false", self.press())
        self.fake.apply["extra_vars"] = "apply_patch: false"
        self.fake.workflow["extra_vars"] = "apply_patch: true"
        self.assertIn("上書き", self.press())
        self.fake.workflow["extra_vars"] = ""
        self.fake.nodes["results"][2]["extra_data"] = {"apply_patch": True}
        self.assertIn("上書き", self.press())
        self.fake.nodes["results"][2]["extra_data"] = {}
        self.fake.check["artifacts"] = {"apply_patch": True}
        self.assertIn("安全な変数設定", self.press())
        self.assertEqual(self.writes, [])

    def test_deny_cannot_trigger_failure_edges(self):
        self.fake.nodes["results"][1]["failure_nodes"] = [23]
        self.relay.hold_action = "deny"
        self.assertIn("遷移", self.press(HOLD))
        self.assertEqual(self.writes, [])

    def test_ambiguous_post_and_conflict_never_retry_or_reveal_body(self):
        self.fake.post_error = URLError("remote data including synthetic secret")
        self.assertNotIn("synthetic secret", self.press())
        self.assertEqual(self.store.status(), "unconfirmed")
        self.press()
        self.assertEqual(self.writes, ["/api/v2/workflow_approvals/14/approve/"])

    def test_controller_race_conflict_is_terminal_and_redacted(self):
        self.fake.post_error = HTTPError("https://controller.example.test", 400, "synthetic secret", {}, None)
        message = self.press()
        self.assertIn("承認済み・拒否・期限切れ", message)
        self.assertNotIn("synthetic secret", message)
        self.press()
        self.assertEqual(self.writes, ["/api/v2/workflow_approvals/14/approve/"])

    def test_https_is_required_and_cannot_redirect_posts(self):
        with self.assertRaises(ApprovalError):
            Controller("http://controller.example.test", "synthetic-oauth-token", 11, 14)
        with self.assertRaises(ApprovalError):
            self.controller.decide("launch")
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
