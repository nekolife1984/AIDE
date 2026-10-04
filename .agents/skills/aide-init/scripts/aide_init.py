#!/usr/bin/env python3
"""Safely initialize a repository's GitHub Project using AIDE conventions."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


STATUS_OPTIONS = [
    {"name": "Backlog", "color": "GRAY", "description": "未着手"},
    {"name": "Ready", "color": "BLUE", "description": "着手可能・依存解決済み"},
    {"name": "In progress", "color": "YELLOW", "description": "作業中"},
    {"name": "In review", "color": "ORANGE", "description": "PR確認中"},
    {"name": "Done", "color": "GREEN", "description": "完了条件達成・マージ済み"},
]
STATUS_OPTION_RENAMES = {
    "todo": "Backlog",
    "in progress": "In progress",
}
PROJECT_QUERY = """
query($id: ID!) {
  node(id: $id) {
    ... on ProjectV2 {
      id
      number
      title
      url
      public
      closed
      owner {
        __typename
        ... on User { login }
        ... on Organization { login }
      }
      repositories(first: 100) {
        nodes { nameWithOwner }
        pageInfo { hasNextPage }
      }
      items(first: 1, archivedStates: [ARCHIVED, NOT_ARCHIVED]) {
        totalCount
      }
      fields(first: 100) {
        nodes {
          __typename
          ... on ProjectV2Field { id name }
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name color description }
          }
        }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""
REPOSITORY_PROJECTS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 100) {
      nodes {
        id
        number
        title
        url
        public
        closed
        owner {
          __typename
          ... on User { login }
          ... on Organization { login }
        }
      }
      pageInfo { hasNextPage }
    }
  }
}
"""
REPOSITORY_METADATA_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    hasIssuesEnabled
    owner {
      __typename
      ... on User { login }
      ... on Organization { login }
    }
  }
}
"""
UPDATE_FIELD_MUTATION = """
mutation($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
  updateProjectV2Field(input: {
    fieldId: $fieldId
    singleSelectOptions: $options
  }) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id
        options { id name color description }
      }
    }
  }
}
"""


class InitError(Exception):
    """An unsafe or unverifiable state that must stop initialization."""


def redact(text: str) -> str:
    token_prefixes = r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)"
    text = re.sub(r"(?i){}[A-Za-z0-9._-]+".format(token_prefixes), "[REDACTED]", text)
    return text


def run_command(args: List[str], input_text: Optional[str] = None) -> str:
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    try:
        result = subprocess.run(
            args,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
        )
    except OSError as error:
        raise InitError("Unable to run {}: {}".format(args[0], error))
    if result.returncode != 0:
        detail = redact(result.stderr.strip())
        if detail:
            raise InitError("Command failed ({}): {}".format(" ".join(args[:3]), detail))
        raise InitError("Command failed ({}), exit status {}".format(" ".join(args[:3]), result.returncode))
    return result.stdout.strip()


def run_json(args: List[str], input_text: Optional[str] = None) -> Dict[str, Any]:
    output = run_command(args, input_text)
    try:
        value = json.loads(output)
    except (TypeError, ValueError) as error:
        raise InitError("Command returned invalid JSON ({}): {}".format(" ".join(args[:3]), error))
    if not isinstance(value, dict):
        raise InitError("Command returned an unexpected JSON value: {}".format(" ".join(args[:3])))
    return value


def github_repo_from_remote(root: Path) -> Tuple[str, str]:
    remote = run_command(["git", "remote", "get-url", "origin"])
    patterns = (
        r"^https://(?:[^/@]+@)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    match = None
    for pattern in patterns:
        match = re.match(pattern, remote)
        if match:
            break
    if not match:
        raise InitError("origin must be a single GitHub.com HTTPS or SSH repository URL")
    owner, name = match.group(1).split("/", 1)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise InitError("origin contains an unsupported GitHub owner or repository name")
    if name.endswith(".git"):
        name = name[:-4]
    return owner, name


def load_config(path: Path, owner: str, name: str) -> Tuple[Dict[str, Any], bool]:
    if not path.exists():
        return {
            "schema_version": 1,
            "repository": {"owner": owner, "name": name},
            "project_defaults": {"title": name, "visibility": "PRIVATE"},
            "project": None,
        }, False
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InitError("Unable to read .agents/project.json: {}".format(error))
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise InitError("Unsupported or malformed project.json schema; reconcile it manually")
    repository = config.get("repository")
    if not isinstance(repository, dict) or repository.get("owner") != owner or repository.get("name") != name:
        raise InitError("project.json repository does not match origin ({}/{})".format(owner, name))
    defaults = config.get("project_defaults")
    if not isinstance(defaults, dict):
        raise InitError("project.json project_defaults must be an object")
    title = defaults.get("title", name)
    visibility = defaults.get("visibility", "PRIVATE")
    if not isinstance(title, str) or not title.strip():
        raise InitError("project_defaults.title must be a non-empty string")
    if visibility not in ("PRIVATE", "PUBLIC"):
        raise InitError("project_defaults.visibility must be PRIVATE or PUBLIC")
    config["project_defaults"] = {"title": title, "visibility": visibility}
    if "project" not in config or (config["project"] is not None and not isinstance(config["project"], dict)):
        raise InitError("project.json project must be null or an object")
    return config, True


def validate_cache_path(root: Path, path: Path) -> None:
    repository_root = root.resolve()
    expected_path = repository_root / ".agents" / "project.json"
    agents_dir = repository_root / ".agents"
    if path.absolute() != expected_path:
        raise InitError("project.json path is outside the expected repository location")
    if agents_dir.is_symlink():
        raise InitError(".agents must not be a symlink for project.json writes")
    if agents_dir.exists() and not agents_dir.is_dir():
        raise InitError(".agents exists but is not a directory")
    if expected_path.is_symlink():
        raise InitError(".agents/project.json must not be a symlink")
    try:
        resolved_parent = expected_path.parent.resolve()
        if os.path.commonpath([str(repository_root), str(resolved_parent)]) != str(repository_root):
            raise InitError("project.json parent resolves outside the repository")
    except (OSError, ValueError) as error:
        raise InitError("Unable to verify project.json path: {}".format(error))


def check_auth() -> None:
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    try:
        result = subprocess.run(
            ["gh", "auth", "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, env=env,
        )
    except OSError as error:
        raise InitError("Unable to run gh auth status: {}".format(error))
    if result.returncode != 0:
        raise InitError("GitHub CLI authentication failed; run `gh auth status`")
    scope_lines = re.findall(r"(?im)^\s*(?:-\s*)?Token scopes:\s*(.+)$", result.stdout + "\n" + result.stderr)
    scopes = set()
    for line in scope_lines:
        scopes.update(part.strip().strip("'\" ") for part in line.split(","))
    if "project" not in scopes:
        raise InitError("GitHub authentication requires the `project` scope; run `gh auth refresh -s project`")


def repo_metadata(owner: str, name: str) -> Dict[str, Any]:
    repo = run_json(["gh", "repo", "view", "{}/{}".format(owner, name), "--json", "nameWithOwner,owner,hasIssuesEnabled"])
    if repo.get("nameWithOwner", "").lower() != "{}/{}".format(owner, name).lower():
        raise InitError("GitHub repository identity does not match origin")
    if not repo.get("hasIssuesEnabled"):
        raise InitError("GitHub Issues are disabled for the target repository")
    repo_owner = repo.get("owner", {})
    data = graphql(REPOSITORY_METADATA_QUERY, {"owner": owner, "name": name})
    actual = data.get("repository")
    if not isinstance(actual, dict) or actual.get("nameWithOwner", "").lower() != "{}/{}".format(owner, name).lower():
        raise InitError("GitHub GraphQL repository identity does not match origin")
    actual_owner = actual.get("owner", {})
    owner_type = actual_owner.get("__typename")
    if repo_owner.get("login", "").lower() != owner.lower() or actual_owner.get("login", "").lower() != owner.lower() or owner_type not in ("User", "Organization"):
        raise InitError("GitHub repository owner does not match origin")
    if not actual.get("hasIssuesEnabled"):
        raise InitError("GitHub Issues are disabled for the target repository")
    return {"owner": owner, "name": name, "owner_type": owner_type}


def graphql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}, ensure_ascii=False)
    response = run_json(["gh", "api", "graphql", "--input", "-"], payload)
    errors = response.get("errors")
    if errors:
        messages = "; ".join(str(error.get("message", "GraphQL error")) for error in errors)
        raise InitError("GitHub GraphQL request failed: {}".format(redact(messages)))
    data = response.get("data")
    if not isinstance(data, dict):
        raise InitError("GitHub GraphQL response is missing data")
    return data


def project_details(node_id: str) -> Dict[str, Any]:
    data = graphql(PROJECT_QUERY, {"id": node_id})
    project = data.get("node")
    if not isinstance(project, dict) or not project.get("id"):
        raise InitError("Project node could not be read; refusing to guess its state")
    repositories = project.get("repositories", {})
    fields = project.get("fields", {})
    if repositories.get("pageInfo", {}).get("hasNextPage"):
        raise InitError("Project has more than 100 linked repositories; refusing incomplete verification")
    if fields.get("pageInfo", {}).get("hasNextPage"):
        raise InitError("Project has more than 100 fields; refusing incomplete verification")
    project["repository_names"] = [item.get("nameWithOwner") for item in repositories.get("nodes", [])]
    project["field_nodes"] = fields.get("nodes", [])
    project["items_count"] = project.get("items", {}).get("totalCount")
    if not isinstance(project["items_count"], int):
        raise InitError("Project item count could not be verified")
    return project


def project_view(number: int, owner: str) -> Dict[str, Any]:
    return run_json(["gh", "project", "view", str(number), "--owner", owner, "--format", "json"])


def project_list(owner: str) -> List[Dict[str, Any]]:
    result = run_json(["gh", "project", "list", "--owner", owner, "--closed", "--limit", "1000", "--format", "json"])
    projects = result.get("projects")
    total = result.get("totalCount")
    if not isinstance(projects, list) or not isinstance(total, int) or total != len(projects):
        raise InitError("Project list is incomplete; refusing candidate discovery")
    return projects


def repository_projects(owner: str, name: str, owner_type: str) -> List[Dict[str, Any]]:
    data = graphql(REPOSITORY_PROJECTS_QUERY, {"owner": owner, "name": name})
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise InitError("GitHub repository could not be resolved for Project discovery")
    connection = repository.get("projectsV2", {})
    if connection.get("pageInfo", {}).get("hasNextPage"):
        raise InitError("Repository has more than 100 linked Projects; refusing incomplete discovery")
    projects = connection.get("nodes")
    if not isinstance(projects, list):
        raise InitError("Repository-linked Projects could not be enumerated")
    for project in projects:
        verify_project_owner(project, owner, owner_type)
    return projects


def owner_login(project: Dict[str, Any]) -> str:
    return project.get("owner", {}).get("login", "")


def visibility(project: Dict[str, Any]) -> str:
    return "PUBLIC" if project.get("public") else "PRIVATE"


def verify_project_owner(project: Dict[str, Any], owner: str, owner_type: str) -> None:
    actual_type = project.get("owner", {}).get("__typename") or project.get("owner", {}).get("type")
    if owner_login(project).lower() != owner.lower() or actual_type != owner_type:
        raise InitError("Project owner does not match the repository owner")


def verify_cached_project(cache: Dict[str, Any], repo: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    saved = cache.get("project")
    if not isinstance(saved, dict):
        raise InitError("Cached project metadata is malformed")
    required = ("owner", "owner_type", "number", "node_id", "title", "url", "visibility", "repository")
    if any(key not in saved for key in required):
        raise InitError("Cached project metadata is incomplete; reconcile it manually")
    if saved["repository"].lower() != "{}/{}".format(repo["owner"], repo["name"]).lower():
        raise InitError("Cached project repository does not match origin")
    if saved["owner"].lower() != repo["owner"].lower() or saved["owner_type"] != repo["owner_type"]:
        raise InitError("Cached project owner does not match the repository owner")
    view = project_view(saved["number"], saved["owner"])
    if view.get("id") != saved["node_id"]:
        raise InitError("Project node ID differs from project.json; reconcile before writing")
    details = project_details(saved["node_id"])
    verify_project_owner(details, saved["owner"], saved["owner_type"])
    actual = cache_project(details, "{}/{}".format(repo["owner"], repo["name"]))
    for key in ("number", "node_id", "title", "url", "visibility", "repository", "owner", "owner_type"):
        if actual[key] != saved[key]:
            raise InitError("Cached Project {} differs from GitHub; reconcile before writing".format(key))
    if details.get("closed"):
        raise InitError("Cached Project is closed; refusing to recreate or modify it")
    target_repo = "{}/{}".format(repo["owner"], repo["name"])
    if target_repo.lower() not in [name.lower() for name in details["repository_names"]]:
        raise InitError("Cached Project is no longer linked to this repository")
    return view, details


def discover_project(owner: str, repo: Dict[str, str], title: str, desired_visibility: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    linked = []
    target_repo = "{}/{}".format(repo["owner"], repo["name"])
    for entry in repository_projects(owner, repo["name"], repo["owner_type"]):
        node_id = entry.get("id")
        if not node_id:
            raise InitError("Repository-linked Project is missing its node ID")
        details = project_details(node_id)
        if details.get("id") != node_id:
            raise InitError("Repository-linked Project resolved to a different node")
        verify_project_owner(details, owner, repo["owner_type"])
        if target_repo.lower() in [name.lower() for name in details["repository_names"]]:
            linked.append(details)
        else:
            raise InitError("Repository.projectsV2 and Project.repositories disagree")
    if len(linked) > 1:
        raise InitError("ambiguous: multiple Projects are linked to this repository")
    if linked:
        candidate = linked[0]
        if candidate.get("closed"):
            raise InitError("The linked Project is closed; choose or reopen it explicitly")
        return candidate, False
    same_title = []
    for entry in project_list(owner):
        if entry.get("title") != title:
            continue
        node_id = entry.get("id")
        if not node_id:
            raise InitError("Same-title Project list entry is missing its node ID")
        details = project_details(node_id)
        if details.get("id") != node_id:
            raise InitError("Same-title Project entry resolved to a different node")
        verify_project_owner(details, owner, repo["owner_type"])
        same_title.append(details)
    if len(same_title) > 1:
        raise InitError("ambiguous: multiple Projects have the configured title")
    if same_title:
        candidate = same_title[0]
        if candidate.get("closed"):
            raise InitError("A closed Project has the configured title; refusing to create a duplicate")
        if candidate["repository_names"] or candidate["items_count"] != 0:
            raise InitError("A same-title Project is linked elsewhere or is not empty; refusing to reuse it")
        if visibility(candidate) != desired_visibility:
            raise InitError("The same-title Project visibility differs from project_defaults")
        return candidate, True
    return None, True


def field_list(project: Dict[str, Any], owner: str) -> List[Dict[str, Any]]:
    result = run_json(["gh", "project", "field-list", str(project["number"]), "--owner", owner, "--limit", "100", "--format", "json"])
    fields = result.get("fields")
    total = result.get("totalCount")
    if not isinstance(fields, list) or not isinstance(total, int) or total != len(fields):
        raise InitError("Project field list is incomplete; refusing to change Status")
    return fields


def status_state(project: Dict[str, Any], owner: str) -> Tuple[Optional[Dict[str, Any]], int]:
    cli_fields = field_list(project, owner)
    cli_status = [field for field in cli_fields if field.get("name") == "Status"]
    graph_status = [field for field in project.get("field_nodes", []) if field.get("name") == "Status"]
    if len(cli_status) > 1 or len(graph_status) > 1:
        raise InitError("Multiple Status fields exist; refusing to choose one")
    if bool(cli_status) != bool(graph_status):
        raise InitError("Project field-list and GraphQL Status results disagree")
    if not cli_status:
        return None, project["items_count"]
    cli_field = cli_status[0]
    graph_field = graph_status[0]
    if cli_field.get("type") != "ProjectV2SingleSelectField" or graph_field.get("__typename") != "ProjectV2SingleSelectField":
        raise InitError("Status exists but is not a single-select field")
    if cli_field.get("id") != graph_field.get("id"):
        raise InitError("Status field IDs differ between CLI and GraphQL")
    cli_options = cli_field.get("options", [])
    graph_options = graph_field.get("options", [])
    if [(item.get("id"), item.get("name")) for item in cli_options] != [(item.get("id"), item.get("name")) for item in graph_options]:
        raise InitError("Status options differ between CLI and GraphQL")
    return graph_field, project["items_count"]


def status_mismatch(field: Optional[Dict[str, Any]]) -> bool:
    if field is None:
        return True
    options = field.get("options", [])
    names = [option.get("name") for option in options]
    canonical_names = [option["name"] for option in STATUS_OPTIONS]
    normalized_names = [STATUS_OPTION_RENAMES.get(name.lower(), name) if isinstance(name, str) else name for name in names]
    if len(normalized_names) != len(set(normalized_names)) or any(name not in canonical_names for name in normalized_names):
        raise InitError("unknown Status option exists; refusing to overwrite it")
    actual = [(item.get("name"), item.get("color"), item.get("description")) for item in options]
    expected = [(item["name"], item["color"], item["description"]) for item in STATUS_OPTIONS]
    return actual != expected


def plan_status_change(field: Optional[Dict[str, Any]], item_count: int, allow_existing: bool) -> bool:
    if not status_mismatch(field):
        return False
    if item_count > 0 and not allow_existing:
        raise InitError("Status is missing or differs and the Project has {} items (archived and active); use --approve-status-update-with-items only after reviewing the impact".format(item_count))
    return True


def update_status_field(project: Dict[str, Any], owner: str, allow_existing: bool) -> None:
    field, item_count = status_state(project, owner)
    plan_status_change(field, item_count, allow_existing)
    if field is None:
        options_arg = ",".join(option["name"] for option in STATUS_OPTIONS)
        try:
            run_json([
                "gh", "project", "field-create", str(project["number"]), "--owner", owner,
                "--name", "Status", "--data-type", "SINGLE_SELECT",
                "--single-select-options", options_arg, "--format", "json",
            ])
        except InitError as original_error:
            project = project_details(project["id"])
            field, item_count = status_state(project, owner)
            if field is None:
                raise original_error
        else:
            project = project_details(project["id"])
            field, item_count = status_state(project, owner)
            if field is None:
                raise InitError("Status field creation was not visible on read-back")
    if not plan_status_change(field, item_count, allow_existing):
        return

    # Re-read immediately before a migration so a newly added item cannot be missed.
    project = project_details(project["id"])
    field, item_count = status_state(project, owner)
    if field is None:
        raise InitError("Status field disappeared before update")
    if not plan_status_change(field, item_count, allow_existing):
        return
    by_name = {}
    for option in field.get("options", []):
        source_name = option.get("name")
        target_name = STATUS_OPTION_RENAMES.get(source_name.lower(), source_name)
        by_name[target_name] = option
    payload = []
    previous_ids = {}
    for desired in STATUS_OPTIONS:
        option = dict(desired)
        previous = by_name.get(desired["name"])
        if previous is not None:
            option["id"] = previous["id"]
            previous_ids[desired["name"]] = previous["id"]
        payload.append(option)
    result = graphql(UPDATE_FIELD_MUTATION, {"fieldId": field["id"], "options": payload})
    mutation = result.get("updateProjectV2Field", {}).get("projectV2Field")
    if not isinstance(mutation, dict) or mutation.get("id") != field["id"]:
        raise InitError("Status update response did not identify the requested field")
    verified = project_details(project["id"])
    updated_field, _ = status_state(verified, owner)
    if updated_field is None or status_mismatch(updated_field):
        raise InitError("Status configuration failed read-back verification")
    updated_ids = {option["name"]: option.get("id") for option in updated_field.get("options", [])}
    for name, option_id in previous_ids.items():
        if updated_ids.get(name) != option_id:
            raise InitError("Status option ID changed during update; refusing to cache success")


def cache_project(project: Dict[str, Any], repository: str) -> Dict[str, Any]:
    owner = project.get("owner", {})
    owner_type = owner.get("__typename") or owner.get("type")
    login = owner.get("login")
    if not login or owner_type not in ("User", "Organization"):
        raise InitError("Project owner could not be verified")
    return {
        "owner": login,
        "owner_type": owner_type,
        "number": project.get("number"),
        "node_id": project.get("id"),
        "title": project.get("title"),
        "url": project.get("url"),
        "visibility": visibility(project),
        "repository": repository,
    }


def verify_final(project: Dict[str, Any], repo: Dict[str, str], title: str, desired_visibility: str, require_exclusive_link: bool) -> Dict[str, Any]:
    if project.get("closed"):
        raise InitError("Project is closed")
    verify_project_owner(project, repo["owner"], repo["owner_type"])
    if require_exclusive_link and (project.get("title") != title or visibility(project) != desired_visibility):
        raise InitError("Project title or visibility failed read-back verification")
    expected_repo = "{}/{}".format(repo["owner"], repo["name"])
    if expected_repo.lower() not in [name.lower() for name in project["repository_names"]]:
        raise InitError("Repository link failed read-back verification")
    if require_exclusive_link and (len(project["repository_names"]) != 1 or project["repository_names"][0].lower() != expected_repo.lower()):
        raise InitError("Project is linked to unexpected repositories")
    field, _ = status_state(project, repo["owner"])
    if field is None or status_mismatch(field):
        raise InitError("Standard Status field failed read-back verification")
    return cache_project(project, expected_repo)


def save_cache(root: Path, path: Path, config: Dict[str, Any]) -> None:
    validate_cache_path(root, path)
    encoded = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    previous_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(str(temp_path), previous_mode)
        os.replace(str(temp_path), str(path))
    except OSError as error:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        raise InitError("Unable to atomically update project.json: {}".format(error))


def confirm(plan: List[str], yes: bool) -> None:
    if not plan:
        return
    print("Planned changes:")
    for item in plan:
        print("  - " + item)
    if yes:
        return
    if not sys.stdin.isatty():
        raise InitError("Use --yes to approve these changes in a non-interactive session")
    answer = input("Apply these changes? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise InitError("Cancelled; no changes applied")


def create_project(owner: str, owner_type: str, title: str, desired_visibility: str, repository: str) -> Dict[str, Any]:
    created = run_json(["gh", "project", "create", "--owner", owner, "--title", title, "--format", "json"])
    number = created.get("number")
    node_id = created.get("id")
    if not isinstance(number, int) or not node_id:
        raise InitError("Project creation response lacks a stable project number or node ID")
    description = "リポジトリのIssue・PRの進捗管理"
    visibility_flag = "PUBLIC" if desired_visibility == "PUBLIC" else "PRIVATE"
    run_command([
        "gh", "project", "edit", str(number), "--owner", owner,
        "--description", description, "--visibility", visibility_flag,
    ])
    view = project_view(number, owner)
    if view.get("id") != node_id or view.get("title") != title or visibility(view) != desired_visibility:
        raise InitError("New Project metadata failed read-back verification before linking")
    details = project_details(node_id)
    verify_project_owner(details, owner, owner_type)
    if details.get("closed"):
        raise InitError("New Project is unexpectedly closed")
    run_command(["gh", "project", "link", str(number), "--owner", owner, "--repo", repository.split("/", 1)[1]])
    return {"number": number, "id": node_id}


def run(root: Path, dry_run: bool, yes: bool, approve_existing: bool, approve_public: bool) -> int:
    owner, name = github_repo_from_remote(root)
    config_path = root / ".agents" / "project.json"
    validate_cache_path(root, config_path)
    config, config_exists = load_config(config_path, owner, name)
    check_auth()
    repo = repo_metadata(owner, name)
    defaults = config["project_defaults"]
    title = defaults["title"]
    desired_visibility = defaults["visibility"]
    print("Repository: {}/{}".format(owner, name))

    cached = config.get("project")
    selected = None
    needs_link = False
    if cached is not None:
        view, details = verify_cached_project(config, repo)
        selected = {"number": view["number"], "id": view["id"]}
        project = details
        print("Using cached Project #{}: {}".format(view["number"], view["title"]))
    else:
        candidate, needs_link = discover_project(owner, repo, title, desired_visibility)
        if candidate is None:
            project = None
            print("No linked Project found; a new Project will be created if approved.")
        else:
            selected = {"number": candidate["number"], "id": candidate["id"]}
            project = candidate
            print("Selected Project #{}: {}".format(candidate["number"], candidate["title"]))

    plan = []
    if project is None:
        plan.extend([
            "Create a {} Project named {!r}".format(desired_visibility, title),
            "Link it to {}/{}".format(owner, name),
            "Ensure the standard Status field and options",
        ])
    else:
        if needs_link:
            plan.append("Link Project #{} to {}/{}".format(project["number"], owner, name))
        current_status, item_count = status_state(project, owner)
        if current_status is None:
            plan_status_change(current_status, item_count, approve_existing)
            plan.append("Create the missing Status field")
        elif plan_status_change(current_status, item_count, approve_existing):
            plan.append("Update Status options{}".format(" (explicit approval for existing items)" if item_count else ""))
    expected_cache = cache_project(project, "{}/{}".format(owner, name)) if project is not None else None
    if expected_cache is None or config.get("project") != expected_cache:
        plan.append("Write verified Project metadata to .agents/project.json")

    if dry_run:
        print("Dry run: no remote or local writes will be performed.")
        if plan:
            print("Planned changes:")
            for item in plan:
                print("  - " + item)
        else:
            print("No changes required.")
        return 0

    if project is None and desired_visibility == "PUBLIC" and not approve_public:
        raise InitError("Creating a PUBLIC Project requires separate approval; rerun with --approve-public-project after reviewing the plan")

    confirm(plan, yes)
    if project is None:
        created = create_project(owner, repo["owner_type"], title, desired_visibility, "{}/{}".format(owner, name))
        selected = created
        project_view(created["number"], owner)
        project = project_details(created["id"])
    elif needs_link:
        run_command(["gh", "project", "link", str(project["number"]), "--owner", owner, "--repo", name])
        project = project_details(project["id"])

    if project is None:
        raise InitError("Project state could not be established")
    update_status_field(project, owner, approve_existing)
    project = project_details(project["id"])
    fresh_view = project_view(project["number"], owner)
    if fresh_view.get("id") != project.get("id"):
        raise InitError("Project identity changed during setup")
    project["owner"] = project.get("owner", {})
    verified_cache = verify_final(project, repo, title, desired_visibility, project is not None and needs_link)
    if config.get("project") != verified_cache or not config_exists:
        config["project"] = verified_cache
        save_cache(root, config_path, config)
    print("Verified Project #{} ({}); project.json is up to date.".format(project["number"], project["url"]))
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely initialize the repository's GitHub Project.")
    parser.add_argument("--dry-run", action="store_true", help="inspect state and show changes without writing")
    parser.add_argument("--yes", action="store_true", help="approve the displayed safe setup plan")
    parser.add_argument(
        "--approve-status-update-with-items", action="store_true",
        help="explicitly approve updating known Status options when the Project contains items",
    )
    parser.add_argument(
        "--approve-public-project", action="store_true",
        help="explicitly approve creating a PUBLIC GitHub Project",
    )
    args = parser.parse_args(argv)
    if args.dry_run and args.yes:
        parser.error("--dry-run cannot be combined with --yes")
    return args


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        return run(Path.cwd(), args.dry_run, args.yes, args.approve_status_update_with_items, args.approve_public_project)
    except InitError as error:
        print("aide-init: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
