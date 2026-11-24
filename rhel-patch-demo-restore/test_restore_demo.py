"""Offline tests that exercise Controller HTTP requests through a fake opener."""

import base64
import copy
import io
import json
import sys
import unittest
from collections import defaultdict
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlsplit

import yaml

import restore_demo as restore


class ControllerFixture:
    def __init__(self):
        self.calls = []
        self.data = defaultdict(list, {
            "organizations": [{"id": 1, "name": "Default"}],
            "inventories": [{"id": 2, "name": "Workshop Inventory", "organization": 1}],
            "hosts": [{"id": 3, "name": "node1.example.com", "inventory": 2, "enabled": True}],
            "credentials": [{"id": 4, "name": "Workshop Credential", "organization": 1, "credential_type__kind": "ssh"}],
            "execution_environments": [{"id": 5, "name": "Default execution environment", "organization": None}],
        })
        self.next_id = 10
        self.associations = defaultdict(list)
        self.fail_path = None
        self.version = "4.7.18"
        self.api_root = restore.GATEWAY_API_ROOT
        self.me_status = 200
        self.github_branch = "main"
        self.github_commit = "ab" * 20
        self.github_files = {}
        self.github_overrides = {}
        self.patches = []

    def open(self, request, timeout):
        method = request.get_method()
        url = urlsplit(request.full_url)
        self.calls.append((method, url.path))
        if url.netloc == "api.github.com":
            if method != "GET" or request.get_header("Authorization"):
                raise AssertionError("GitHub must receive an unauthenticated GET")
            return io.BytesIO(json.dumps(self.github_response(url)).encode())
        if url.netloc != "controller.example.test":
            raise AssertionError("Unexpected destination")
        authorization = request.get_header("Authorization")
        if method == "GET" and url.path in {
            restore.GATEWAY_API_ROOT + "ping/", restore.LEGACY_API_ROOT + "ping/",
        }:
            if authorization is not None:
                raise AssertionError("Public API discovery does not need an OAuth token")
            if url.path != self.api_root + "ping/":
                raise HTTPError(request.full_url, 404, "missing", {}, None)
            return io.BytesIO(json.dumps({"version": self.version}).encode())
        if not url.path.startswith(self.api_root):
            raise AssertionError("Request used the wrong Controller API root")
        if authorization != "Bearer synthetic-oauth-value":
            raise AssertionError("Controller requires its OAuth token")
        path = url.path.removeprefix(self.api_root).strip("/")
        if path == "me" and self.me_status != 200:
            raise HTTPError(request.full_url, self.me_status, "sensitive server message", {}, None)
        parts = path.split("/")
        if method == "POST":
            if path == self.fail_path:
                raise HTTPError(request.full_url, 400, "sensitive server message", {}, io.BytesIO(request.data))
            result = self.create(parts, json.loads(request.data))
        elif method == "PATCH":
            if len(parts) != 2 or parts[0] != "job_templates":
                raise AssertionError("PATCH outside job template detail")
            data = json.loads(request.data)
            result = next(row for row in self.data["job_templates"] if row["id"] == int(parts[1]))
            if result["name"] != "RHEL-Slack-Notification" or set(data) != {"extra_vars"}:
                raise AssertionError("PATCH outside Slack Extra Variables")
            self.patches.append((url.path, data))
            result.update(data)
        elif method == "GET":
            result = self.read(path, parts, parse_qs(url.query))
        else:
            raise AssertionError(f"Unexpected HTTP method {method}")
        return io.BytesIO(json.dumps(result).encode())

    def github_response(self, url):
        repo = "/repos/example/demo"
        if url.path == f"{repo}/branches/{quote(self.github_branch, safe='')}":
            return {"commit": {"sha": self.github_commit}}
        refs = {self.github_commit} | {row["scm_revision"] for row in self.data.get("projects", [])}
        if not url.path.startswith(repo + "/contents/") or parse_qs(url.query).get("ref", [None])[0] not in refs:
            raise AssertionError("Both GitHub files must be read from the resolved commit")
        name = url.path.rsplit("/", 1)[-1]
        revision = parse_qs(url.query)["ref"][0]
        content = self.github_files.get((revision, name), self.github_files.get(name, (restore.ROOT / name).read_bytes()))
        return {
            "type": "file", "path": name, "encoding": "base64", "size": len(content),
            "sha": restore.git_blob_sha(content), "content": base64.encodebytes(content).decode("ascii"),
            **self.github_overrides.get(name, {}),
        }

    def read(self, path, parts, query):
        if path == "me":
            return self.page([{"id": 1}])
        if len(parts) == 1:
            scope = query.pop("or__organization", None)
            query.pop("or__organization__isnull", None)
            query.pop("page")
            query.pop("page_size")
            return self.page([
                item for item in self.data.get(path, [])
                if all(str(item.get(key)) == values[0] for key, values in query.items())
                and (scope is None or item.get("organization") in (None, int(scope[0])))
            ])
        collection, identifier = parts[:2]
        row = next(row for row in self.data[collection] if row["id"] == int(identifier))
        if len(parts) == 2:
            return row
        relation = parts[2]
        if relation == "playbooks":
            return list(restore.PLAYBOOKS)
        if relation == "workflow_nodes":
            return self.page([node for node in self.data["workflow_job_template_nodes"] if node["workflow_job_template"] == row["id"]])
        if relation == "hosts":
            return self.page([host for host in self.data["hosts"] if host["inventory"] == row["id"]])
        if relation == "credentials":
            return self.page([{"id": cred} for cred in self.associations.get(path, [])])
        if relation in {"schedules", "groups", "inventory_sources"}:
            return self.page([])
        raise AssertionError(f"Unexpected GET relation {relation}")

    @staticmethod
    def page(results):
        return {"results": results, "next": None}

    def create(self, parts, payload):
        collection = parts[0]
        if len(parts) == 1:
            row = {"id": self.next_id, **payload}
            if collection == "projects":
                row.update(status="successful", scm_revision=self.github_commit)
            elif collection == "credentials":
                row["inputs"] = {"slack_webhook_url": "$encrypted$"}
            elif collection == "job_templates":
                project = next(item for item in self.data["projects"] if item["id"] == row["project"])
                row.update(organization=project["organization"], diff_mode=False, force_handlers=False, start_at_task="")
            elif collection == "workflow_job_templates":
                row.setdefault("extra_vars", "")
                row.update(inventory=None, limit=None)
            elif collection == "workflow_job_template_nodes":
                row.update(success_nodes=[], always_nodes=[], failure_nodes=[], extra_data={})
            if collection in {"job_templates", "workflow_job_templates"}:
                row.update(survey_enabled=False, webhook_service="", scm_branch="", job_tags="", skip_tags="")
            self.next_id += 1
            self.data[collection].append(row)
            return row
        if parts[2] == "credentials":
            self.associations["/".join(parts)].append(payload["id"])
        elif parts[2] == "success_nodes":
            node = next(node for node in self.data[collection] if node["id"] == int(parts[1]))
            node["success_nodes"].append(payload["id"])
        elif parts[2] == "create_approval_template":
            approval = self.create(["workflow_approval_templates"], payload)
            node = next(node for node in self.data[collection] if node["id"] == int(parts[1]))
            node["unified_job_template"] = approval["id"]
            node["related"] = {"unified_job_template": f"{self.api_root}workflow_approval_templates/{approval['id']}/"}
            return approval
        else:
            raise AssertionError(f"Unexpected POST path {parts}")
        return {}


class RestoreTests(unittest.TestCase):

    def setUp(self):
        self.fake = ControllerFixture()
        opener = patch.object(restore, "https_opener", return_value=self.fake)
        opener.start()
        self.addCleanup(opener.stop)
        self.api = restore.Controller("https://controller.example.test", "synthetic-oauth-value")
        self.defaults = yaml.safe_load((restore.HERE / "aap-config/demo-vars.yml").read_text())
        self.definition = yaml.safe_load((restore.ROOT / "aap-config/current-settings.yml").read_text())
        self.config = restore.Config(
            self.api.origin, "Default", "Workshop Inventory", "node1.example.com",
            "Workshop Credential", "Default execution environment", "https://github.com/example/demo.git",
        )

    def execute(self, dry_run=False, confirm=lambda _: "RESTORE"):
        output = io.StringIO()
        with redirect_stdout(output):
            status = restore.run(dry_run, self.api, self.config, self.defaults, self.definition, confirm)
        report = output.getvalue()
        self.assertNotIn("synthetic-oauth-value", report)
        return status, report

    def no_approval(self, _):
        self.fail("Approval should not be requested")

    def assert_read_only(self):
        self.assertTrue(self.fake.calls)
        self.assertEqual({"GET"}, {method for method, _ in self.fake.calls})

    def test_dry_run_is_read_only_even_when_everything_is_missing(self):
        self.fake.version = "4.6.31"
        before = copy.deepcopy(self.fake.data)
        status, output = self.execute(dry_run=True, confirm=self.no_approval)
        self.assertEqual(status, 0)
        self.assertIn("書き込みは 0 件", output)
        self.assertEqual(self.fake.data, before)
        self.assert_read_only()
        with self.assertRaises(restore.RestoreError):
            self.api.post("projects/", "test", {"name": "should never be sent"})

    def test_cli_dry_run_cannot_prompt_for_approval(self):
        self.fake.api_root = restore.LEGACY_API_ROOT
        self.fake.version = "4.5.30"
        with (
            patch.object(sys, "argv", ["restore-demo.sh", "--dry-run"]),
            patch.object(restore, "read_local", return_value=(self.defaults, self.definition)),
            patch.object(restore, "load_config", return_value={}),
            patch.object(restore, "prompt", return_value=(self.config, "synthetic-oauth-value")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(restore.main(), 0)
        self.assert_read_only()
        self.assertEqual(self.fake.calls[:3], [
            ("GET", restore.GATEWAY_API_ROOT + "ping/"),
            ("GET", restore.LEGACY_API_ROOT + "ping/"),
            ("GET", restore.LEGACY_API_ROOT + "me/"),
        ])

    def test_dry_run_supplies_saved_defaults_and_sends_only_nonsecret_updates_to_save(self):
        with (
            patch.object(sys, "argv", ["restore-demo.sh", "--dry-run", "--save-config"]),
            patch.object(restore, "read_local", return_value=(self.defaults, self.definition)),
            patch.object(restore, "load_config", return_value={
                "aap_url": self.config.origin, "inventory": self.config.inventory, "slack_team_id": "TDEMO",
            }),
            patch.object(restore, "prompt", return_value=(self.config, "synthetic-oauth-value")) as prompt,
            patch.object(restore, "save_config") as save,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(restore.main(), 0)
        self.assertEqual(prompt.call_args.args[0]["aap_url"], self.config.origin)
        self.assertEqual(prompt.call_args.args[0]["demo_rhel_inventory"], self.config.inventory)
        save.assert_called_once()
        updates = save.call_args.args[0]
        self.assertEqual(set(updates), {"aap_url", *restore.LOCAL_INPUTS})
        self.assertNotIn("synthetic-oauth-value", updates.values())
        self.assert_read_only()

    def test_github_mismatch_reports_paths_shas_and_text_format_without_writes(self):
        branch = "release/demo"
        name = "slack_notify.yml"
        self.definition["project"]["scm_branch"] = self.fake.github_branch = branch
        local = (restore.ROOT / name).resolve()
        remote = b"\xef\xbb\xbf" + local.read_bytes().replace(b"\n", b"\r\n").rstrip(b"\r\n")
        self.fake.github_files[name] = remote
        status, output = self.execute(dry_run=True, confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn(f"example/demo ブランチ {branch!r}:{name} (commit {self.fake.github_commit})", output)
        self.assertIn(str(local), output)
        self.assertIn(restore.git_blob_sha(remote), output)
        self.assertIn(restore.git_blob_sha(local.read_bytes()), output)
        self.assertIn("UTF-8 BOM", output)
        self.assertIn("末尾改行=なし", output)
        self.assertIn("末尾改行=LF", output)
        self.assert_read_only()

    def test_github_response_sha_must_match_decoded_contents(self):
        self.fake.github_overrides["slack_notify.yml"] = {"sha": "12" * 20}
        status, output = self.execute(dry_run=True, confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("API の Blob SHA", output)
        self.assert_read_only()

    def test_gateway_authentication_failure_does_not_fall_back(self):
        self.fake.me_status = 401
        status, output = self.execute(dry_run=True, confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("HTTP 401", output)
        self.assertNotIn("sensitive server message", output)
        self.assertEqual(self.fake.calls, [
            ("GET", restore.GATEWAY_API_ROOT + "ping/"),
            ("GET", restore.GATEWAY_API_ROOT + "me/"),
        ])

    def test_conflicting_change_during_approval_is_read_only(self):
        def confirm(_):
            self.fake.data["projects"].append({
                "id": 9, "organization": 1, "name": "RHEL-Patch-Demo",
                "status": "failed", "scm_url": "sensitive-existing-url",
            })
            return "RESTORE"

        status, output = self.execute(confirm=confirm)
        self.assertEqual(status, 1)
        self.assertIn("承認後", output)
        self.assertNotIn("sensitive-existing-url", output)
        self.assert_read_only()

    def test_declined_restore_is_read_only(self):
        self.assertEqual(self.execute(confirm=lambda _: "no")[0], 1)
        self.assert_read_only()

    def test_approved_restore_and_idempotent_rerun(self):
        self.assert_restore_and_rerun()

    def test_approved_restore_and_idempotent_rerun_on_controller_4_5(self):
        self.fake.api_root = restore.LEGACY_API_ROOT
        self.fake.version = "4.5.30"
        self.assert_restore_and_rerun()

    def assert_restore_and_rerun(self):
        def confirm(_):
            self.assert_read_only()
            return "RESTORE"

        status, output = self.execute(confirm=confirm)
        self.assertEqual(status, 0, output)
        writes = [path for method, path in self.fake.calls if method == "POST"]
        self.assertEqual(len(writes), 19)
        self.assertTrue(all(restore.WRITE_PATH.fullmatch(path.removeprefix(self.fake.api_root)) for path in writes))
        for job in self.fake.data["job_templates"][:2]:
            self.assertIs(json.loads(job["extra_vars"])["apply_patch"], False)
        notification = next(job for job in self.fake.data["job_templates"] if job["name"] == "RHEL-Slack-Notification")
        self.assertEqual(json.loads(notification["extra_vars"]), {"demo_event": self.definition["target"]})
        self.assertEqual(self.fake.associations.get(f"job_templates/{notification['id']}/credentials", []), [])
        self.assertFalse(any(path.startswith(self.fake.api_root + "credential") for path in writes))
        nodes = self.fake.data["workflow_job_template_nodes"]
        self.assertEqual(nodes[0]["success_nodes"], [nodes[1]["id"]])
        self.assertEqual(nodes[1]["success_nodes"], [nodes[2]["id"]])
        workflow = next(item for item in self.fake.data["workflow_job_templates"] if item["name"] == "RHEL-Patch-Slack-Workflow")
        integrated = [node for node in nodes if node["workflow_job_template"] == workflow["id"]]
        self.assertEqual([node["identifier"] for node in integrated], ["patch-check", "patch-apply"])
        self.assertEqual(integrated[0]["success_nodes"], [integrated[1]["id"]])
        self.assertIs(workflow["ask_variables_on_launch"], True)
        before = copy.deepcopy(self.fake.data)
        self.fake.calls.clear()
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 0, output)
        self.assertEqual(self.fake.data, before)
        self.assert_read_only()

    def test_existing_apply_patch_conflict_reports_the_flag(self):
        self.assertEqual(self.execute()[0], 0)
        self.fake.calls.clear()
        patch_apply = self.fake.data["job_templates"][1]
        patch_apply["extra_vars"] = json.dumps({"apply_patch": True, "password": "sensitive-existing-var"})
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("'apply_patch': True", output)
        self.assertIn("'apply_patch': False", output)
        self.assertNotIn("sensitive-existing-var", output)
        self.assert_read_only()

    def test_dry_run_reports_slack_override_and_project_revision_without_writes(self):
        self.assertEqual(self.execute()[0], 0)
        self.fake.calls.clear()
        project_revision = "23" * 20
        self.fake.data["projects"][0]["scm_revision"] = project_revision
        notification = next(job for job in self.fake.data["job_templates"] if job["name"] == "RHEL-Slack-Notification")
        extra_vars = json.loads(notification["extra_vars"])
        extra_vars.update(slack_interactive=False, password="sensitive-notification-var")
        notification["extra_vars"] = json.dumps(extra_vars)
        before = copy.deepcopy(self.fake.data)

        status, output = self.execute(dry_run=True, confirm=self.no_approval)

        self.assertEqual(status, 0, output)
        self.assertIn("更新予定", output)
        self.assertIn("Project SCM 同期状態 successful", output)
        self.assertIn(f"AAP Revision {project_revision}", output)
        self.assertIn(f"GitHub Commit {self.fake.github_commit}", output)
        self.assertIn("定義にない変数 2 件を削除", output)
        self.assertNotIn("sensitive-notification-var", output)
        self.assertEqual(self.fake.data, before)
        self.assert_read_only()

    def test_old_webhook_playbook_revision_blocks_migration(self):
        self.assertEqual(self.execute()[0], 0)
        self.fake.calls.clear()
        old = "45" * 20
        self.fake.data["projects"][0]["scm_revision"] = old
        self.fake.github_files[(old, "slack_notify.yml")] = b"old webhook playbook"
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("成果物出力版", output)
        self.assert_read_only()

    def test_approved_update_only_and_idempotent_rerun(self):
        self.assertEqual(self.execute()[0], 0)
        notification = next(job for job in self.fake.data["job_templates"] if job["name"] == "RHEL-Slack-Notification")
        expected = notification["extra_vars"]
        notification["extra_vars"] = 'slack_interactive: "true"'
        self.fake.data["credentials"].append({
            "id": 93, "name": self.defaults["demo_slack_credential"], "organization": 1,
            "inputs": {"slack_webhook_url": "$encrypted$"},
        })
        self.fake.associations[f"job_templates/{notification['id']}/credentials"] = [93]
        before = copy.deepcopy(self.fake.data)
        path = f"{self.fake.api_root}job_templates/{notification['id']}/"

        self.fake.calls.clear()
        status, output = self.execute(dry_run=True, confirm=self.no_approval)
        self.assertEqual(status, 0, output)
        self.assertIn("demo_event: <未設定>", output)
        self.assert_read_only()

        def confirm(prompt):
            self.assertIn("更新", prompt)
            self.assert_read_only()
            return "RESTORE"

        status, output = self.execute(confirm=confirm)
        self.assertEqual(status, 0, output)
        self.assertIn("変更した既存リソース\n  - job_templates / RHEL-Slack-Notification / extra_vars", output)
        self.assertEqual(self.fake.patches, [(path, {"extra_vars": expected})])
        self.assertEqual([call for call in self.fake.calls if call[0] != "GET"], [("PATCH", path)])
        original = next(job for job in before["job_templates"] if job["id"] == notification["id"])
        original["extra_vars"] = expected
        self.assertEqual(self.fake.data, before)

        self.fake.calls.clear()
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 0, output)
        self.assertIn("定義が一致しています", output)
        self.assert_read_only()

    def test_slack_update_coexists_with_creating_only_the_missing_slack_workflow(self):
        self.assertEqual(self.execute()[0], 0)
        templates = self.fake.data["workflow_job_templates"]
        legacy = copy.deepcopy(next(row for row in templates if row["name"] == "RHEL-Patch-Workflow"))
        missing = next(row for row in templates if row["name"] == "RHEL-Patch-Slack-Workflow")
        templates.remove(missing)
        notification = next(job for job in self.fake.data["job_templates"] if job["name"] == "RHEL-Slack-Notification")
        notification["extra_vars"] = ""
        self.fake.calls.clear()
        status, output = self.execute()
        self.assertEqual(status, 0, output)
        self.assertEqual(len(self.fake.patches), 1)
        self.assertEqual(sum(method == "POST" for method, _ in self.fake.calls), 4)
        self.assertIn(legacy, templates)
        self.assertTrue(any(row["name"] == "RHEL-Patch-Slack-Workflow" for row in templates))

    def test_known_slack_update_is_blocked_by_an_unrelated_conflict(self):
        self.assertEqual(self.execute()[0], 0)
        for job in self.fake.data["job_templates"]:
            if job["name"] == "RHEL-Slack-Notification":
                job["extra_vars"] = ""
            if job["name"] == "RHEL-Patch-Apply":
                job["extra_vars"] = '{"apply_patch": true}'
        self.fake.calls.clear()
        self.assertEqual(self.execute(confirm=self.no_approval)[0], 1)
        self.assert_read_only()

    def test_slack_variables_changed_during_confirmation_are_not_overwritten(self):
        self.assertEqual(self.execute()[0], 0)
        notification = next(job for job in self.fake.data["job_templates"] if job["name"] == "RHEL-Slack-Notification")
        notification["extra_vars"] = ""
        self.fake.calls.clear()

        def confirm(_):
            notification["extra_vars"] = '{"slack_interactive": false, "password": "sensitive-concurrent-value"}'
            return "RESTORE"

        status, output = self.execute(confirm=confirm)
        self.assertEqual(status, 1)
        self.assertIn("承認後に事前検証の結果が変わりました", output)
        self.assertNotIn("sensitive-concurrent-value", output)
        self.assert_read_only()

    def test_missing_host_requires_manual_setup(self):
        self.fake.data["hosts"].clear()
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("RHEL Host", output)
        self.assert_read_only()

    def test_existing_slack_inventory_is_not_modified(self):
        self.fake.data["inventories"].append(
            {"id": 6, "name": "Slack-Notification-Inventory", "organization": 1, "kind": "", "variables": ""}
        )
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("Slack Inventory / hosts", output)
        self.assert_read_only()

    def test_unsupported_version_is_read_only(self):
        self.fake.version = "4.4.0"
        status, output = self.execute(confirm=self.no_approval)
        self.assertEqual(status, 1)
        self.assertIn("未対応", output)
        self.assert_read_only()

    def test_server_failure_hides_response_and_does_not_retry_post(self):
        self.fake.fail_path = "projects"
        status, output = self.execute()
        self.assertEqual(status, 1)
        self.assertIn("HTTP 400", output)
        self.assertNotIn("sensitive server message", output)
        self.assertEqual(self.fake.calls.count(("POST", self.fake.api_root + "projects/")), 1)
        self.assertFalse(self.api.write_enabled)

    def test_write_allowlist_rejects_launch_even_when_enabled(self):
        with redirect_stdout(io.StringIO()):
            self.api.connect()
        self.fake.calls.clear()
        self.api.write_enabled = True
        with self.assertRaises(restore.RestoreError):
            self.api.post("job_templates/1/launch/", "test", {})
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
