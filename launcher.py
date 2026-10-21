"""Launch agents through Herdr without changing their normal approval policy."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

from agents import ADAPTERS, AGENTS, UNSUPPORTED
from core import Repository, Store, UserError, Workspace, atomic_write, exclusive, make_repository


# Generated files and their names are reserved; compare case-insensitively for macOS.
RESERVED_LINKS = {"agents.md", "claude.md", "workspace.json", ".github"}
NOTES_MARKER = "<!-- herdr-workspaces: notes below this line are kept when the workspace reopens -->"


@dataclass
class LaunchResult:
    message: str
    workspace_id: str


class Herdr:
    def __init__(self, executable: Optional[str] = None):
        self.executable = executable or os.environ.get("HERDR_BIN_PATH", "herdr")

    def call(self, *arguments: str) -> dict:
        try:
            result = subprocess.run(
                [self.executable, *arguments], capture_output=True, text=True,
                timeout=40, check=False,
            )
        except FileNotFoundError:
            raise UserError("Herdr is not installed or is missing from PATH.") from None
        except subprocess.TimeoutExpired:
            raise UserError("Herdr did not respond. Check its terminal before retrying.") from None
        # Lifecycle/metadata reporters are intentionally quiet on success in Herdr.
        if result.returncode == 0 and not result.stdout.strip() and arguments[:2] in {
            ("workspace", "report-metadata"), ("server", "reload-config"),
        }:
            return {}
        try:
            response = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        except (ValueError, TypeError):
            detail = (result.stderr or result.stdout).strip()
            raise UserError("Herdr: " + (detail[:800] or "No response received.")) from None
        if not isinstance(response, dict):
            raise UserError("Herdr returned an unsupported response. Check the installed version.")
        if result.returncode or "error" in response:
            error = response.get("error", {})
            raise UserError("Herdr: " + str(error.get("message", "Command failed.")))
        body = response.get("result")
        if not isinstance(body, dict):
            raise UserError("Herdr returned an unsupported response. Check the installed version.")
        return body


def agent_arguments(kind: str, repositories: list[Repository]) -> list[str]:
    if kind not in ADAPTERS:
        raise UserError(UNSUPPORTED)
    agent = ADAPTERS[kind]
    args = list(agent.arguments)
    if agent.directory_flag:
        for repo in repositories:
            args.extend([agent.directory_flag, str(Path(repo.path).resolve())])
    return args


def workspace_directory(store: Store, workspace: Workspace) -> Path:
    # A changed repo set gets a new root so old agents keep their original context.
    identity = json.dumps([workspace.id, sorted((r.name, str(Path(r.path).resolve())) for r in workspace.repos)])
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return store.state_dir / "roots" / key


def prepare_directory(store: Store, workspace: Workspace) -> Path:
    root = workspace_directory(store, workspace)
    if root.is_symlink():
        raise UserError("The generated workspace folder must not be a symlink: " + str(root))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    aliases = set()
    bindings = []
    lines = [
        "# " + workspace.name + " workspace", "",
        "This workspace contains separate Git repositories:", "",
    ]
    for repo in sorted(workspace.repos, key=lambda repo: (repo.name, repo.path)):
        stem = re.sub(r"[^A-Za-z0-9._-]", "-", repo.name).strip(".-") or "repo"
        alias = stem
        suffix = 2
        while alias.casefold() in aliases or alias.casefold() in RESERVED_LINKS:
            alias = stem + "-" + str(suffix)
            suffix += 1
        aliases.add(alias.casefold())
        link = root / alias
        if link.is_symlink():
            if link.resolve() != Path(repo.path).resolve():
                raise UserError("The workspace link changed outside the extension: " + str(link))
        elif link.exists():
            raise UserError("A file already occupies a workspace repo link: " + str(link))
        else:
            link.symlink_to(Path(repo.path).resolve(), target_is_directory=True)
        lines.append("- `" + alias + "` -> " + json.dumps(repo.path, ensure_ascii=False))
        bindings.append({"name": repo.name, "link": alias, "checkout": str(Path(repo.path).resolve())})
    lines.extend([
        "", "Each repo entry is a symlink to the original checkout, not a separate copy.",
        "File changes and branch switches affect that checkout wherever it is opened.",
        "", "Use the absolute repository paths for shell commands and Git operations.",
        "Before working in a repository, read its AGENTS.md or CLAUDE.md and applicable nested instructions.",
        "Run each repository's checks from that repository. Each repository has its own Git history.", "",
        "Record how these repositories work together below the marker line. Everything above it is regenerated.", "",
        NOTES_MARKER, "",
    ])
    agents = root / "AGENTS.md"
    if agents.is_symlink():
        raise UserError("Refusing to overwrite a workspace instruction symlink: " + str(agents))
    notes = _notes(agents)
    if notes is None:
        notes = _previous_notes(store, workspace.id, root) or ""
    atomic_write(agents, "\n".join(lines) + notes + ("\n" if notes else ""))
    # One instruction file serves every agent; the link stays inside the workspace folder.
    claude = root / "CLAUDE.md"
    if claude.is_symlink() and os.readlink(claude) != "AGENTS.md":
        raise UserError("Refusing to overwrite a workspace instruction symlink: " + str(claude))
    if not claude.is_symlink():
        if claude.exists():
            claude.unlink()
        claude.symlink_to("AGENTS.md")
    summary = root / "workspace.json"
    if summary.is_symlink():
        raise UserError("Refusing to overwrite a workspace summary symlink: " + str(summary))
    atomic_write(summary, json.dumps({
        "version": 1, "workspace_id": workspace.id, "name": workspace.name,
        "repo_access": "symlinks-to-existing-checkouts", "repositories": bindings,
    }, indent=2, ensure_ascii=False) + "\n")
    return root


def _notes(path: Path) -> Optional[str]:
    """Text written below the marker, or None when the file has no marker yet."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeError):
        return None
    if NOTES_MARKER not in text:
        return None
    return text.split(NOTES_MARKER, 1)[1].strip("\n")


def sibling_roots(store: Store, workspace_id: str, root: Path) -> list[Path]:
    """Folders made for other repo sets of the same saved workspace, newest first."""
    found = []
    for summary in (store.state_dir / "roots").glob("*/workspace.json"):
        if summary.parent == root or summary.parent.is_symlink() or summary.is_symlink():
            continue
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("workspace_id") == workspace_id:
            found.append(summary.parent)
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def _previous_notes(store: Store, workspace_id: str, root: Path) -> Optional[str]:
    """Carry notes into the new folder created when the repo set changes."""
    for sibling in sibling_roots(store, workspace_id, root):
        agents = sibling / "AGENTS.md"
        if agents.is_symlink() or not agents.is_file():
            continue
        notes = _notes(agents)
        if notes is not None:
            return notes
    return None


def _remove_generated(root: Path) -> bool:
    """Remove a folder made only of repo links and generated files; never follow a link."""
    entries = list(root.iterdir())
    if any(not entry.is_symlink() and entry.name not in ("AGENTS.md", "workspace.json") for entry in entries):
        return False
    for entry in entries:
        entry.unlink()
    root.rmdir()
    return True


class Launcher:
    def __init__(self, store: Store, herdr: Optional[Herdr] = None,
                 which: Callable = shutil.which):
        self.store = store
        self.herdr = herdr or Herdr()
        self.which = which

    def open(self, workspace: Workspace, new_agent: bool = False) -> LaunchResult:
        if os.environ.get("HERDR_ENV") != "1":
            raise UserError("Open the picker from a Herdr pane to start agents. You can still save and share workspaces here.")
        if workspace.agent not in AGENTS or not self.which(workspace.agent):
            raise UserError("Install " + AGENTS.get(workspace.agent, workspace.agent) + " and sign in before opening it.")
        if not workspace.name.strip() or any(not c.isprintable() for c in workspace.name):
            raise UserError("Give the workspace a printable name.")
        if not workspace.repos:
            raise UserError("Select at least one repository.")
        # Revalidate before granting directory access; saved checkouts may have moved.
        repositories = []
        for saved in workspace.repos:
            actual = make_repository(saved.path)
            repositories.append(Repository(actual.path, saved.name, actual.remote))
        checked = Workspace(workspace.id, workspace.name, repositories, workspace.agent)
        root = prepare_directory(self.store, checked)
        workspaces = self.herdr.call("workspace", "list").get("workspaces", [])
        panes = self.herdr.call("pane", "list").get("panes", [])
        workspace_id = self._find_space(workspace, root, workspaces, panes)
        try:
            self._remove_unused_roots(panes, keep=root)
        except OSError:
            pass  # Leftover folders are harmless; never block opening over them.
        record = self._registry().get(root.name, {})
        matches = [pane for pane in panes if pane.get("workspace_id") == workspace_id
                   and pane.get("terminal_id") in record.get("terminals", [])] if workspace_id else []
        if matches and not new_agent:
            existing = next((p for p in matches if p.get("agent") == workspace.agent), None)
            if existing:
                self._publish(existing["workspace_id"], checked)
                self.herdr.call("agent", "focus", existing["pane_id"])
                return LaunchResult("Opened " + workspace.name + ".", existing["workspace_id"])
        environment = [item for value in ADAPTERS[workspace.agent].environment for item in ("--env", value)]
        if workspace_id:
            created = self.herdr.call(
                "tab", "create", "--workspace", workspace_id, "--cwd", str(root),
                "--label", AGENTS[workspace.agent], *environment, "--no-focus",
            )
        else:
            created = self.herdr.call(
                "workspace", "create", "--cwd", str(root), "--label", workspace.name,
                *environment, "--no-focus",
            )
            workspace_id = created["workspace"]["workspace_id"]
        live_label = next((w.get("label", workspace.name) for w in workspaces if w.get("workspace_id") == workspace_id), workspace.name)
        self._remember(root, workspace_id, live_label, created["root_pane"].get("terminal_id"), created["root_pane"].get("pane_id"))
        self._publish(workspace_id, checked)
        pane_id = created["root_pane"]["pane_id"]
        name = "ws-" + workspace.agent + "-" + uuid4().hex[:12]
        try:
            self.herdr.call(
                "agent", "start", name, "--kind", workspace.agent, "--pane", pane_id,
                "--timeout", "15000", "--", *agent_arguments(workspace.agent, repositories),
            )
        except UserError as error:
            # A trust/login dialog is normal on first launch. Keep the terminal visible
            # and let the person answer it; a failure must never launch a second agent.
            self.herdr.call("workspace", "focus", workspace_id)
            self.herdr.call("tab", "focus", created["tab"]["tab_id"])
            return LaunchResult(
                "Workspace opened. " + AGENTS[workspace.agent] + " needs attention in its terminal: " + str(error),
                workspace_id,
            )
        self.herdr.call("agent", "focus", pane_id)
        return LaunchResult("Opened " + workspace.name + " with " + AGENTS[workspace.agent] + ".", workspace_id)

    def refresh(self) -> int:
        """Restore repo labels after a restart without changing focus or launching agents."""
        if os.environ.get("HERDR_ENV") != "1":
            raise UserError("Refresh the repo tree through the Herdr plugin action.")
        workspaces = self.herdr.call("workspace", "list").get("workspaces", [])
        panes = self.herdr.call("pane", "list").get("panes", [])
        count = 0
        failures = []
        for workspace in self.store.load():
            # One broken workspace must not hide the repo trees of the others.
            try:
                root = workspace_directory(self.store, workspace)
                workspace_id = self._find_space(workspace, root, workspaces, panes,
                                                restored=os.environ.get("HERDR_PLUGIN_EVENT") == "startup")
                if workspace_id:
                    prepare_directory(self.store, workspace)
                    self._publish(workspace_id, workspace)
                    count += 1
            except (UserError, OSError) as error:
                failures.append(workspace.name + " (" + str(error) + ")")
        if failures:
            raise UserError("Refreshed " + str(count) + " workspace(s). Could not refresh " + "; ".join(failures))
        return count

    def _remove_unused_roots(self, panes, keep):
        """Delete folders of earlier repo sets once nothing uses them.

        Runs only when opening, when Herdr's pane list is complete. Only folders of
        saved workspaces are considered. A folder is kept while a pane uses it, while
        another Herdr session has a record of it (its panes cannot be checked from
        here), while it holds notes its workspace's current folder lacks, or when it
        contains anything this plugin did not create.
        """
        current = {saved.id: workspace_directory(self.store, saved) for saved in self.store.load()}
        registry = self._registry()
        live = {pane.get("terminal_id") for pane in panes if pane.get("terminal_id")}
        elsewhere = set()
        for other in self.store.state_dir.glob("runtime-*.json"):
            if other == self._registry_path():
                continue
            try:
                data = json.loads(other.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return  # Ownership is uncertain; keep every folder.
            if isinstance(data, dict):
                elsewhere.update(data)
        removed = []
        for summary in (self.store.state_dir / "roots").glob("*/workspace.json"):
            root = summary.parent
            try:
                data = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            owner = data.get("workspace_id") if isinstance(data, dict) else None
            if root.is_symlink() or root == keep or owner not in current or root == current[owner] or root.name in elsewhere:
                continue
            record = registry.get(root.name)
            if isinstance(record, dict) and live & set(record.get("terminals", [])):
                continue
            if any(self._same_directory(pane.get("cwd"), root) for pane in panes):
                continue
            notes = (_notes(root / "AGENTS.md") or "").strip()
            if notes and notes not in (_notes(current[owner] / "AGENTS.md") or ""):
                continue
            if _remove_generated(root):
                removed.append(root.name)
        if removed:
            with exclusive(self.store.state_dir / ".runtime.lock"):
                data = self._registry()
                for name in removed:
                    data.pop(name, None)
                atomic_write(self._registry_path(), json.dumps(data))

    def _publish(self, workspace_id, workspace):
        from sidebar import publish_workspace
        publish_workspace(self.herdr, workspace_id, workspace)

    def _registry_path(self) -> Path:
        namespace = os.environ.get("HERDR_SOCKET_PATH", "default")
        key = hashlib.sha256(namespace.encode()).hexdigest()[:16]
        return self.store.state_dir / ("runtime-" + key + ".json")

    def _registry(self) -> dict:
        try:
            data = json.loads(self._registry_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _find_space(self, workspace, root, workspaces, panes, restored=False):
        """A saved workspace keeps one Herdr space, even after its repo set changes.

        Agents started with an earlier repo set keep running in their own folder;
        the space is found through any of those folders.
        """
        found = None
        for candidate in [root] + sibling_roots(self.store, workspace.id, root):
            space = self._existing_workspace(candidate, workspaces, panes, workspace.name, restored)
            found = found or space
            # At startup, keep checking so every folder's restored agents are rebound.
            if found and not restored:
                break
        return found

    def _existing_workspace(self, root, workspaces, panes, label, restored=False):
        record = self._registry().get(root.name, {})
        if isinstance(record, dict):
            # Terminal IDs also distinguish a fresh server after its saved state is reset.
            match = next((w for w in workspaces if w.get("workspace_id") == record.get("id")), None)
            terminals = record.get("terminals", [])
            pane_ids = record.get("panes", [])
            if match:
                found = False
                for pane in panes:
                    if pane.get("workspace_id") != match["workspace_id"]:
                        continue
                    if pane.get("terminal_id") in terminals or (restored and match.get("label") == record.get("label")
                                                               and pane.get("pane_id") in pane_ids):
                        # Herdr preserves public pane IDs on restore but allocates new
                        # terminal IDs. Rebind only in its startup hook, before normal use.
                        self._remember(root, match["workspace_id"], match.get("label", label), pane.get("terminal_id"), pane.get("pane_id"))
                        found = True
                if found:
                    return match["workspace_id"]
        for pane in panes:
            if self._same_directory(pane.get("cwd"), root):
                live_label = next((w.get("label", label) for w in workspaces if w.get("workspace_id") == pane["workspace_id"]), label)
                # The directory identifies the space, but a manually started agent
                # there may not have received this workspace's directory permissions.
                self._remember(root, pane["workspace_id"], live_label)
                return pane["workspace_id"]
        return None

    def _remember(self, root, workspace_id, label, terminal_id=None, pane_id=None):
        with exclusive(self.store.state_dir / ".runtime.lock"):
            data = self._registry()
            prior = data.get(root.name, {})
            keep_sessions = isinstance(prior, dict) and prior.get("id") == workspace_id and (terminal_id or pane_id)
            terminals = prior.get("terminals", []) if keep_sessions else []
            terminals = [item for item in terminals if isinstance(item, str)] if isinstance(terminals, list) else []
            if terminal_id and terminal_id not in terminals:
                terminals.append(terminal_id)
            pane_ids = prior.get("panes", []) if keep_sessions else []
            pane_ids = [item for item in pane_ids if isinstance(item, str)] if isinstance(pane_ids, list) else []
            if pane_id and pane_id not in pane_ids:
                pane_ids.append(pane_id)
            data[root.name] = {"id": workspace_id, "label": label, "terminals": terminals[-64:], "panes": pane_ids}
            atomic_write(self._registry_path(), json.dumps(data))

    @staticmethod
    def _same_directory(value, expected: Path) -> bool:
        return isinstance(value, str) and Path(value).resolve() == expected.resolve()
