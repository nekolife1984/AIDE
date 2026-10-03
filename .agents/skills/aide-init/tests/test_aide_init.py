import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "aide_init.py"
MOCK_GH = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state_path = Path(os.environ["AIDE_MOCK_STATE"])
log_path = Path(os.environ["AIDE_MOCK_LOG"])
state = json.loads(state_path.read_text())
with log_path.open("a") as stream:
    stream.write(json.dumps(args) + "\n")


def emit(value):
    print(json.dumps(value))


def save():
    state_path.write_text(json.dumps(state))


def option_list():
    return state["project"].get("options", [])


if args[:2] == ["auth", "status"]:
    print("Token scopes: project, repo", file=sys.stderr)
elif args[:2] == ["repo", "view"]:
    emit(state["repository"])
elif args[:2] == ["project", "list"]:
    projects = state.get("projects", [])
    emit({"projects": projects, "totalCount": len(projects)})
elif args[:2] == ["project", "view"]:
    emit(state["project"])
elif args[:2] == ["project", "field-list"]:
    fields = state["project"].get("fields", [])
    emit({"fields": fields, "totalCount": len(fields)})
elif args[:2] == ["project", "create"]:
    project = state["project"]
    project.update({"id": "PVT_CREATED", "number": 7, "url": "https://github.com/users/acme/projects/7", "title": args[args.index("--title") + 1], "public": False, "closed": False})
    state["project"]["owner"] = {"login": "acme", "type": "User"}
    save()
    emit(project)
elif args[:2] == ["project", "edit"]:
    project = state["project"]
    project["title"] = args[args.index("--title") + 1] if "--title" in args else project["title"]
    project["public"] = args[args.index("--visibility") + 1] == "PUBLIC" if "--visibility" in args else project["public"]
    save()
    emit(project)
elif args[:2] == ["project", "link"]:
    project = state["project"]
    project["repositories"] = ["acme/Widget"]
    save()
elif args[:2] == ["project", "field-create"]:
    project = state["project"]
    project["fields"].append({"id": "STATUS_FIELD", "name": "Status", "type": "ProjectV2SingleSelectField", "options": []})
    project["options"] = []
    save()
    emit(project["fields"][-1])
elif args[:2] == ["api", "graphql"]:
    request = json.loads(sys.stdin.read())
    query = request.get("query", "")
    variables = request.get("variables", {})
    with log_path.open("a") as stream:
        stream.write(json.dumps(args + [{"query": query}]) + chr(10))
    if "updateProjectV2Field" in query:
        with log_path.open("a") as stream:
            stream.write(json.dumps(args + ["updateProjectV2Field"]) + "\n")
        state["project"]["options"] = variables["options"]
        state["mutation_count"] = state.get("mutation_count", 0) + 1
        for field in state["project"].get("fields", []):
            if field["name"] == "Status":
                field["options"] = variables["options"]
        save()
        emit({"data": {"updateProjectV2Field": {"projectV2Field": {"id": variables["fieldId"], "options": variables["options"]}}}})
    elif "projectsV2(first: 100)" in query:
        candidates = state.get("projects") or [state["project"]]
        repository_name = variables["owner"] + "/" + variables["name"]
        linked = []
        for item in candidates:
            repositories = item.get("repositories", state["project"].get("repositories", []))
            if repository_name.lower() in [value.lower() for value in repositories]:
                linked.append({
                    "id": item["id"], "number": item["number"], "title": item["title"],
                    "url": item["url"], "public": item["public"], "closed": item["closed"],
                    "owner": {"__typename": item.get("owner", {}).get("type", "User"), "login": "acme"},
                })
        emit({"data": {"repository": {"projectsV2": {"nodes": linked, "pageInfo": {"hasNextPage": False}}}}})
    elif "hasIssuesEnabled" in query:
        repository = state["repository"]
        owner = repository["owner"]
        emit({"data": {"repository": {
            "nameWithOwner": repository["nameWithOwner"],
            "hasIssuesEnabled": repository["hasIssuesEnabled"],
            "owner": {"__typename": owner["type"], "login": owner["login"]},
        }}})
    else:
        project = dict(state["project"])
        candidate = next((item for item in state.get("projects", []) if item.get("id") == variables.get("id")), None)
        if candidate is not None:
            project.update(candidate)
        fields = []
        for field in project.get("fields", []):
            field_value = {"__typename": field.get("type", "ProjectV2Field"), "id": field["id"], "name": field["name"], "options": field.get("options", [])}
            if os.environ.get("AIDE_MOCK_CORRUPT_READBACK") == "1" and state.get("mutation_count", 0) > 0 and field_value["name"] == "Status":
                field_value["options"][0]["color"] = "RED"
            fields.append(field_value)
        emit({"data": {"node": {
            "id": project["id"], "number": project["number"], "title": project["title"], "url": project["url"],
            "public": project["public"], "closed": project["closed"], "owner": {"__typename": "User", "login": "acme"},
            "repositories": {"nodes": [{"nameWithOwner": repo} for repo in project.get("repositories", [])], "pageInfo": {"hasNextPage": False}},
            "items": {"totalCount": (
                project.get("itemsArchived", 0) + project.get("itemsActive", project.get("itemsCount", 0))
                if "archivedStates: [ARCHIVED, NOT_ARCHIVED]" in query
                else project.get("itemsActive", project.get("itemsCount", 0))
            )},
            "fields": {"nodes": fields, "pageInfo": {"hasNextPage": False}}
        }}})
else:
    print("unexpected mock gh call: " + json.dumps(args), file=sys.stderr)
    sys.exit(2)
'''


STATUS_OPTIONS = [
    {"name": "Backlog", "color": "GRAY", "description": "未着手"},
    {"name": "Ready", "color": "BLUE", "description": "着手可能・依存解決済み"},
    {"name": "In progress", "color": "YELLOW", "description": "作業中"},
    {"name": "In review", "color": "ORANGE", "description": "PR確認中"},
    {"name": "Done", "color": "GREEN", "description": "完了条件達成・マージ済み"},
]
GITHUB_STATUS_OPTIONS = [
    {"name": "Todo", "color": "GRAY", "description": "Items to be worked on"},
    {"name": "In Progress", "color": "BLUE", "description": "Items currently being worked on"},
    {"name": "Done", "color": "GREEN", "description": "Completed items"},
]


def make_options(wrong_color=False):
    result = []
    for index, option in enumerate(STATUS_OPTIONS):
        item = dict(option)
        item["id"] = "OPTION_{}".format(index + 1)
        if wrong_color and index == 0:
            item["color"] = "RED"
        result.append(item)
    return result


def project_state(options=None, repositories=None, items_count=0, items_archived=0, items_active=None):
    chosen_options = make_options() if options is None else options
    return {
        "id": "PVT_1",
        "number": 1,
        "url": "https://github.com/users/acme/projects/1",
        "title": "Widget",
        "public": False,
        "closed": False,
        "owner": {"login": "acme", "type": "User"},
        "repositories": ["acme/Widget"] if repositories is None else repositories,
        "itemsCount": items_count,
        "itemsArchived": items_archived,
        "itemsActive": items_count if items_active is None else items_active,
        "fields": [
            {"id": "TITLE_FIELD", "name": "Title", "type": "ProjectV2Field"},
            {"id": "STATUS_FIELD", "name": "Status", "type": "ProjectV2SingleSelectField", "options": chosen_options},
        ],
        "options": chosen_options,
    }


def project_cache():
    return {
        "owner": "acme",
        "owner_type": "User",
        "number": 1,
        "node_id": "PVT_1",
        "title": "Widget",
        "url": "https://github.com/users/acme/projects/1",
        "visibility": "PRIVATE",
        "repository": "acme/Widget",
    }


class AideInitScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".agents").mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", "https://github.com/acme/Widget.git"], check=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(MOCK_GH)
        gh.chmod(0o755)
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "calls.jsonl"
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["AIDE_MOCK_STATE"] = str(self.state_path)
        self.env["AIDE_MOCK_LOG"] = str(self.log_path)

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, project=None, projects=None):
        state = {
            "repository": {"nameWithOwner": "acme/Widget", "owner": {"login": "acme", "type": "User"}, "hasIssuesEnabled": True},
            "project": project or project_state(),
            "projects": projects or [],
            "mutation_count": 0,
        }
        self.state_path.write_text(json.dumps(state))

    def write_cache(self, project=None, repository=None, visibility="PRIVATE"):
        value = {
            "schema_version": 1,
            "repository": repository or {"owner": "acme", "name": "Widget"},
            "project_defaults": {"title": "Widget", "visibility": visibility},
            "project": project,
        }
        path = self.repo / ".agents" / "project.json"
        path.write_text(json.dumps(value))
        return path

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.repo, env=self.env,
            capture_output=True, text=True,
        )

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def mutations(self):
        write_prefixes = {("project", "create"), ("project", "edit"), ("project", "link"), ("project", "field-create")}
        return [call for call in self.calls() if tuple(call[:2]) in write_prefixes or (call[:2] == ["api", "graphql"] and "updateProjectV2Field" in call)]

    def test_cached_dry_run_is_read_only_and_does_not_enumerate_projects(self):
        self.write_state()
        cache_path = self.write_cache(project_cache())
        before = cache_path.read_text()

        result = self.run_script("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cache_path.read_text(), before)
        self.assertEqual(self.mutations(), [])
        self.assertFalse(any(call[:2] == ["project", "list"] for call in self.calls()))
        self.assertIn("dry run", result.stdout.lower())

    def test_first_run_reuses_one_exact_linked_project_without_writes_in_dry_run(self):
        state_project = project_state()
        self.write_state(project=state_project, projects=[state_project])
        self.write_cache(project=None)

        result = self.run_script("--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertFalse(any(call[:2] == ["project", "list"] for call in self.calls()))

    def test_one_linked_project_is_reused_without_changing_its_title_or_visibility(self):
        linked_project = project_state()
        linked_project["title"] = "Existing shared roadmap"
        linked_project["public"] = True
        self.write_state(project=linked_project, projects=[linked_project])
        cache_path = self.write_cache(project=None)

        result = self.run_script("--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(cache_path.read_text())["project"]
        self.assertEqual(saved["title"], "Existing shared roadmap")
        self.assertEqual(saved["visibility"], "PUBLIC")
        self.assertEqual(self.mutations(), [])

    def test_cached_project_rerun_is_idempotent(self):
        self.write_state()
        cache_path = self.write_cache(project=None)

        first = self.run_script("--yes")
        self.assertEqual(first.returncode, 0, first.stderr)
        after_first_run = cache_path.read_text()
        self.log_path.unlink()

        second = self.run_script("--yes")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(cache_path.read_text(), after_first_run)
        self.assertEqual(self.mutations(), [])

    def test_github_default_status_options_are_migrated_on_empty_project(self):
        github_options = [dict(option, id="GITHUB_OPTION_{}".format(index + 1)) for index, option in enumerate(GITHUB_STATUS_OPTIONS)]
        self.write_state(project=project_state(options=github_options))
        self.write_cache(project_cache())

        result = self.run_script("--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        saved_options = json.loads(self.state_path.read_text())["project"]["options"]
        self.assertEqual([item["name"] for item in saved_options], [item["name"] for item in STATUS_OPTIONS])
        self.assertEqual(saved_options[-1]["id"], "GITHUB_OPTION_3")

    def test_archived_only_item_blocks_status_migration_without_approval(self):
        self.write_state(project=project_state(
            options=make_options(wrong_color=True), items_archived=1, items_active=0,
        ))
        self.write_cache(project_cache())

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        graphql_queries = [
            call[-1]["query"] for call in self.calls()
            if call[:3] == ["api", "graphql", "--input"] and isinstance(call[-1], dict)
        ]
        self.assertTrue(any("archivedStates: [ARCHIVED, NOT_ARCHIVED]" in query for query in graphql_queries))

    def test_symlinked_agents_directory_is_rejected_without_writing_outside_repository(self):
        self.write_state()
        outside = self.root / "outside"
        outside.mkdir()
        cache_path = outside / "project.json"
        cache_path.write_text(json.dumps({
            "schema_version": 1,
            "repository": {"owner": "acme", "name": "Widget"},
            "project_defaults": {"title": "Widget", "visibility": "PRIVATE"},
            "project": None,
        }))
        before = cache_path.read_text()
        (self.repo / ".agents").rmdir()
        (self.repo / ".agents").symlink_to(outside, target_is_directory=True)

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(cache_path.read_text(), before)
        self.assertEqual(self.mutations(), [])

    def test_symlinked_project_json_is_rejected_without_writing_outside_repository(self):
        self.write_state()
        outside = self.root / "outside.json"
        outside.write_text(json.dumps({
            "schema_version": 1,
            "repository": {"owner": "acme", "name": "Widget"},
            "project_defaults": {"title": "Widget", "visibility": "PRIVATE"},
            "project": None,
        }))
        before = outside.read_text()
        (self.repo / ".agents" / "project.json").symlink_to(outside)

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_text(), before)
        self.assertEqual(self.mutations(), [])

    def test_multiple_linked_candidates_fail_closed(self):
        first = project_state()
        second = dict(first, id="PVT_2", number=2, url="https://github.com/users/acme/projects/2")
        self.write_state(project=first, projects=[first, second])
        self.write_cache(project=None)

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("ambiguous", (result.stdout + result.stderr).lower())

    def test_multiple_unlinked_same_title_projects_fail_closed(self):
        first = project_state(repositories=[])
        second = dict(first, id="PVT_2", number=2, url="https://github.com/users/acme/projects/2")
        self.write_state(project=first, projects=[first, second])
        self.write_cache(project=None)

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("ambiguous", (result.stdout + result.stderr).lower())

    def test_unlinked_same_title_project_with_items_is_not_reused(self):
        candidate = project_state(repositories=[], items_count=1)
        self.write_state(project=candidate, projects=[candidate])
        self.write_cache(project=None)

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("not empty", (result.stdout + result.stderr).lower())

    def test_unlinked_same_title_project_linked_to_another_repository_is_not_reused(self):
        candidate = project_state(repositories=["acme/Other"])
        self.write_state(project=candidate, projects=[candidate])
        self.write_cache(project=None)

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("linked elsewhere", (result.stdout + result.stderr).lower())

    def test_repository_cache_mismatch_stops_before_remote_writes(self):
        self.write_state()
        self.write_cache(project_cache(), repository={"owner": "acme", "name": "Other"})

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("repository", (result.stdout + result.stderr).lower())

    def test_empty_project_status_mismatch_updates_options_preserving_existing_ids(self):
        self.write_state(project=project_state(options=make_options(wrong_color=True)))
        cache_path = self.write_cache(project_cache())

        result = self.run_script("--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state_path.read_text())["project"]["options"], [dict(option, id="OPTION_{}".format(i + 1)) for i, option in enumerate(STATUS_OPTIONS)])
        mutation_calls = [call for call in self.calls() if call[:2] == ["api", "graphql"]]
        self.assertTrue(any("updateProjectV2Field" in call for call in mutation_calls))
        self.assertEqual(json.loads(cache_path.read_text())["project"], project_cache())

    def test_nonempty_project_status_mismatch_stops_without_mutation(self):
        self.write_state(project=project_state(options=make_options(wrong_color=True), items_count=1))
        self.write_cache(project_cache())

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("item", (result.stdout + result.stderr).lower())

    def test_missing_status_on_project_with_items_stops_before_field_creation(self):
        project = project_state(items_count=1)
        project["fields"] = [field for field in project["fields"] if field["name"] != "Status"]
        self.write_state(project=project)
        self.write_cache(project_cache())

        dry_run = self.run_script("--dry-run")
        self.assertNotEqual(dry_run.returncode, 0)
        self.assertEqual(self.mutations(), [])

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("items", (result.stdout + result.stderr).lower())

    def test_missing_status_on_project_with_items_can_be_explicitly_approved(self):
        project = project_state(items_count=1)
        project["fields"] = [field for field in project["fields"] if field["name"] != "Status"]
        self.write_state(project=project)
        self.write_cache(project_cache())

        result = self.run_script("--yes", "--approve-status-update-with-items")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[:2] == ["project", "field-create"] for call in self.calls()))
        self.assertTrue(any("updateProjectV2Field" in call for call in self.calls()))

    def test_new_private_project_is_read_back_before_cache_write(self):
        empty_project = project_state(options=[], repositories=[], items_count=0)
        empty_project["fields"] = [{"id": "TITLE_FIELD", "name": "Title", "type": "ProjectV2Field"}]
        self.write_state(project=empty_project, projects=[])
        cache_path = self.write_cache(project=None)

        result = self.run_script("--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(cache_path.read_text())
        self.assertEqual(saved["project"]["number"], 7)
        self.assertEqual(saved["project"]["visibility"], "PRIVATE")
        self.assertEqual(saved["project"]["repository"], "acme/Widget")
        self.assertTrue(any(call[:2] == ["project", "create"] for call in self.calls()))
        self.assertTrue(any(call[:2] == ["project", "link"] for call in self.calls()))

    def test_public_project_creation_requires_separate_approval(self):
        empty_project = project_state(options=[], repositories=[], items_count=0)
        empty_project["fields"] = [{"id": "TITLE_FIELD", "name": "Title", "type": "ProjectV2Field"}]
        self.write_state(project=empty_project, projects=[])
        cache_path = self.write_cache(project=None, visibility="PUBLIC")
        before = cache_path.read_text()

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--approve-public-project", (result.stdout + result.stderr))
        self.assertEqual(self.mutations(), [])
        self.assertEqual(cache_path.read_text(), before)

    def test_public_project_creation_succeeds_with_separate_approval(self):
        empty_project = project_state(options=[], repositories=[], items_count=0)
        empty_project["fields"] = [{"id": "TITLE_FIELD", "name": "Title", "type": "ProjectV2Field"}]
        self.write_state(project=empty_project, projects=[])
        cache_path = self.write_cache(project=None, visibility="PUBLIC")

        result = self.run_script("--yes", "--approve-public-project")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(self.state_path.read_text())["project"]["public"])
        self.assertEqual(json.loads(cache_path.read_text())["project"]["visibility"], "PUBLIC")

    def test_unknown_status_option_fails_without_mutation(self):
        options = make_options()
        options.append({"id": "OPTION_X", "name": "Unexpected", "color": "PURPLE", "description": ""})
        self.write_state(project=project_state(options=options))
        self.write_cache(project_cache())

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn("unknown", (result.stdout + result.stderr).lower())

    def test_status_readback_failure_does_not_update_cache(self):
        empty_project = project_state(options=[], repositories=[], items_count=0)
        empty_project["fields"] = [{"id": "TITLE_FIELD", "name": "Title", "type": "ProjectV2Field"}]
        self.write_state(project=empty_project, projects=[])
        cache_path = self.write_cache(project=None)
        before = cache_path.read_text()
        self.env["AIDE_MOCK_CORRUPT_READBACK"] = "1"

        result = self.run_script("--yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.mutations(), [])
        self.assertEqual(cache_path.read_text(), before)
        self.assertIsNone(json.loads(cache_path.read_text())["project"])


if __name__ == "__main__":
    unittest.main()
