"""Saved repo sets and portable workspace definitions. No third-party dependencies."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


AGENTS = {"codex": "Codex", "claude": "Claude Code", "copilot": "Copilot"}
PLUGIN_ID = "herdr-multirepo"


class UserError(Exception):
    """An actionable error that can be shown without a traceback."""


@dataclass
class Repository:
    path: str
    name: str
    remote: Optional[str] = None


@dataclass
class Workspace:
    id: str
    name: str
    repos: list[Repository]
    agent: str = "codex"


def _text(value, label: str, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise UserError(f"{label} must be nonempty text under {limit} characters.")
    if not all(character.isprintable() for character in value):
        raise UserError(f"{label} contains unsupported control characters.")
    return value


def _repo_name(value) -> str:
    name = _text(value, "Repository name", 200)
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise UserError("Repository names must be folder names, without path separators.")
    return name


def _remote(value: Optional[str]) -> Optional[str]:
    """Keep network repo identity while dropping credentials and local paths."""
    if value is None or value == "":
        return None
    value = _text(value, "Git remote").strip()
    if "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.hostname:
                return None
            host = parsed.hostname.lower()
            if ":" in host:
                host = f"[{host}]"
            if parsed.port:
                host += f":{parsed.port}"
            if not parsed.path.strip("/"):
                return None
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError as error:
            raise UserError("The Git remote is not a valid URL.") from error
    # Git's common scp spelling, e.g. git@github.com:team/repo.git.
    match = re.fullmatch(r"(?:[^/@:\s]+@)?([^/:\s]+):([^\s]+)", value)
    if match and not re.match(r"^[A-Za-z]:[\\/]", value):
        host, path = match.groups()
        if path.startswith("/") or "?" in path or "#" in path:
            return None
        return f"{host.lower()}:{path}"
    return None


def _remote_identity(value: Optional[str]):
    value = _remote(value)
    if not value:
        return None
    if "://" in value:
        parsed = urlsplit(value)
        host = parsed.hostname.lower()
        # SSH/HTTPS clones of the same repository are the same identity.
        if parsed.port and parsed.port not in {22, 80, 443}:
            host += f":{parsed.port}"
        path = parsed.path
    else:
        host, path = value.split(":", 1)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return host, path


def _origin(path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None
    return _remote(result.stdout.strip()) if result.returncode == 0 else None


def make_repository(path) -> Repository:
    raw = _text(os.fspath(path), "Repository path")
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise UserError(f"Repository folder not found: {raw}") from error
    if not resolved.is_dir() or not (resolved / ".git").exists():
        raise UserError(f"Choose a Git repository or worktree: {resolved}")
    name = _repo_name(resolved.name)
    _text(str(resolved), "Repository path")
    return Repository(str(resolved), name, _origin(resolved))


def default_roots() -> list[str]:
    candidates = [Path.home() / name for name in ("code", "Projects", "projects", "src")]
    result = []
    for path in candidates:
        if path.is_dir() and str(path.resolve()) not in result:
            result.append(str(path.resolve()))
    return result or [str(Path.home())]


def discover_repositories(roots: list[str]) -> list[Repository]:
    """Look at each root and two folder levels; never walk inside a repo."""
    found = {}
    visited = set()
    remaining = 5000

    def visit(path: Path, depth: int) -> None:
        nonlocal remaining
        if remaining <= 0:
            return
        remaining -= 1
        try:
            path = path.resolve(strict=True)
            if path in visited or not path.is_dir():
                return
            visited.add(path)
            if (path / ".git").exists():
                name = _repo_name(path.name)
                _text(str(path), "Repository path")
                found[str(path)] = Repository(str(path), name)
                return
            if depth >= 2:
                return
            with os.scandir(path) as entries:
                children = sorted(
                    (entry for entry in entries if not entry.name.startswith(".")
                     and entry.name not in {"node_modules", "vendor", "dist", "build"}
                     and entry.is_dir(follow_symlinks=True)),
                    key=lambda entry: entry.name.casefold(),
                )
            for child in children:
                visit(Path(child.path), depth + 1)
        except (OSError, RuntimeError, UserError):
            return

    for root in roots:
        visit(Path(root).expanduser(), 0)
    return sorted(found.values(), key=lambda repo: (repo.name.casefold(), repo.path))


def _validate_workspace(workspace: Workspace) -> None:
    if not isinstance(workspace, Workspace):
        raise UserError("Invalid saved workspace.")
    if not isinstance(workspace.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", workspace.id):
        raise UserError("Invalid workspace ID.")
    _text(workspace.name, "Workspace name", 120)
    if workspace.agent not in AGENTS:
        raise UserError("Choose Codex, Claude Code or Copilot.")
    if not isinstance(workspace.repos, list) or not workspace.repos or len(workspace.repos) > 100:
        raise UserError("Choose between 1 and 100 repositories.")
    seen_paths, seen_names = set(), set()
    for repo in workspace.repos:
        if not isinstance(repo, Repository):
            raise UserError("Invalid repository in workspace.")
        _repo_name(repo.name)
        _text(repo.path, "Repository path")
        if not Path(repo.path).is_absolute():
            raise UserError("Saved repository paths must be absolute.")
        if repo.path in seen_paths or repo.name in seen_names:
            raise UserError("Each repository needs a unique path and name in the workspace.")
        seen_paths.add(repo.path)
        seen_names.add(repo.name)
        _remote(repo.remote)


class Store:
    def __init__(self, config_dir=None, state_dir=None):
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        self.config_dir = Path(config_dir or os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or config_home / "herdr" / "plugins" / "config" / PLUGIN_ID).expanduser()
        self.state_dir = Path(state_dir or os.environ.get("HERDR_PLUGIN_STATE_DIR") or state_home / "herdr" / "plugins" / PLUGIN_ID).expanduser()
        self.path = self.config_dir / "workspaces.json"

    def load(self) -> list[Workspace]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as error:
            raise UserError(f"Cannot read saved workspaces: {error}") from error
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1 or not isinstance(data.get("workspaces"), list):
                raise ValueError("unsupported file format")
            result = []
            ids = set()
            for item in data["workspaces"]:
                if not isinstance(item, dict) or not isinstance(item.get("repos"), list):
                    raise ValueError("invalid workspace")
                workspace = Workspace(
                    id=item["id"], name=item["name"], agent=item.get("agent", "codex"),
                    repos=[Repository(**repo) for repo in item["repos"]],
                )
                _validate_workspace(workspace)
                if workspace.id in ids:
                    raise ValueError("duplicate workspace ID")
                ids.add(workspace.id)
                result.append(workspace)
            return result
        except (ValueError, KeyError, TypeError, UserError) as error:
            raise UserError(f"Cannot read {self.path}: {error}. The file has been left unchanged.") from error

    def _write(self, workspaces: list[Workspace]) -> None:
        temporary = None
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.config_dir, prefix=".workspaces-", delete=False) as file:
                temporary = file.name
                json.dump({"version": 1, "workspaces": [asdict(workspace) for workspace in workspaces]}, file, indent=2, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise UserError(f"Cannot save workspaces: {error}") from error
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, workspace: Workspace) -> None:
        _validate_workspace(workspace)
        workspaces = self.load()
        # Persist only the credential-free remote identity.
        clean = Workspace(workspace.id, workspace.name.strip(), [Repository(repo.path, repo.name, _remote(repo.remote)) for repo in workspace.repos], workspace.agent)
        for index, saved in enumerate(workspaces):
            if saved.id == workspace.id:
                workspaces[index] = clean
                break
        else:
            workspaces.append(clean)
        self._write(workspaces)

    def delete(self, workspace_id: str) -> None:
        workspaces = self.load()
        remaining = [workspace for workspace in workspaces if workspace.id != workspace_id]
        if len(remaining) != len(workspaces):
            self._write(remaining)


def portable_definition(workspace: Workspace) -> dict:
    _validate_workspace(workspace)
    repos = []
    for repo in workspace.repos:
        remote = _remote(repo.remote) if repo.remote else _origin(Path(repo.path))
        item = {"name": repo.name}
        if remote:
            item["remote"] = remote
        repos.append(item)
    return {"version": 1, "name": workspace.name, "repositories": repos}


def import_definition(data: dict, base: str) -> Workspace:
    if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1:
        raise UserError("Choose a workspace definition with version 1.")
    name = _text(data.get("name"), "Workspace name", 120).strip()
    entries = data.get("repositories")
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise UserError("The workspace definition must contain 1 to 100 repositories.")
    folder = Path(_text(os.fspath(base), "Checkout folder")).expanduser().resolve()
    if not folder.is_dir():
        raise UserError(f"Checkout folder not found: {folder}")
    requested = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise UserError("Invalid repository in workspace definition.")
        repo_name = _repo_name(entry.get("name"))
        if repo_name in seen:
            raise UserError(f"Repeated repository name: {repo_name}")
        seen.add(repo_name)
        raw_remote = entry.get("remote")
        remote = _remote(raw_remote)
        if raw_remote and not remote:
            raise UserError(f"Repository {repo_name} needs a network Git remote, or no remote field.")
        requested.append((repo_name, remote))

    discovered = None
    origin_cache = {}

    def origin(path: str) -> Optional[str]:
        if path not in origin_cache:
            origin_cache[path] = _origin(Path(path))
        return origin_cache[path]

    resolved = []
    for repo_name, expected_remote in requested:
        candidate = folder / repo_name
        actual = None
        if candidate.is_dir() and (candidate / ".git").exists():
            direct = make_repository(str(candidate))
            if not expected_remote or _remote_identity(direct.remote) == _remote_identity(expected_remote):
                actual = direct
        if actual is None and expected_remote:
            if discovered is None:
                discovered = discover_repositories([str(folder)])
            matches = [repo for repo in discovered if _remote_identity(origin(repo.path)) == _remote_identity(expected_remote)]
            if len(matches) > 1:
                raise UserError(f"Several checkouts match {repo_name}. Use a checkout folder containing the intended repository.")
            if matches:
                actual = Repository(matches[0].path, repo_name, origin(matches[0].path))
        if actual is None:
            if expected_remote and candidate.is_dir() and (candidate / ".git").exists():
                raise UserError(f"{repo_name} has a different Git remote. Choose the correct checkout folder.")
            raise UserError(f"Repository {repo_name} was not found under {folder}. Clone it there or choose another checkout folder.")
        resolved.append(Repository(actual.path, repo_name, actual.remote))
    workspace = Workspace(uuid.uuid4().hex, name, resolved)
    _validate_workspace(workspace)
    return workspace
