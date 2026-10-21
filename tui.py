"""Small terminal picker for saved Herdr workspaces."""

import copy
import curses
import json
import os
import re
import subprocess
import tempfile
import textwrap
import unicodedata
import uuid
from pathlib import Path

from core import (
    AGENTS,
    UserError,
    Workspace,
    default_roots,
    discover_repositories,
    import_definition,
    make_repository,
    name_repositories,
    portable_definition,
)


def plain(value):
    """Keep paths and names from emitting terminal control characters."""
    return "".join(char if char.isprintable() else " " for char in str(value))


def clipped(value, width):
    text = plain(value)
    result = []
    used = 0
    for char in text:
        size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + size > max(0, width):
            break
        result.append(char)
        used += size
    return "".join(result)


def short_path(value):
    home = str(Path.home())
    text = str(value)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def display_width(value):
    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in str(value))


def selected_text(value, width):
    text = clipped(value, width)
    return text + " " * max(0, width - display_width(text))


def wrapped_path(value, width):
    text = plain(value)
    result = []
    while text:
        part = clipped(text, max(2, width))
        result.append(part)
        text = text[len(part):]
    return result


def checkout_branch(repository):
    """A local snapshot of the selected checkout, never a remote lookup."""
    try:
        result = subprocess.run(["git", "-C", repository.path, "branch", "--show-current"], capture_output=True, text=True, timeout=2)
        branch = result.stdout.strip() if result.returncode == 0 else ""
        if not branch:
            result = subprocess.run(["git", "-C", repository.path, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
            branch = "detached " + result.stdout.strip() if result.returncode == 0 else "no commits"
        return plain(branch) + (" · worktree" if (Path(repository.path) / ".git").is_file() else "")
    except (OSError, subprocess.TimeoutExpired):
        return "branch unavailable"


def export_file(workspace, destination):
    """Write a complete definition without exposing a partial JSON file."""
    path = Path(destination).expanduser()
    data = json.dumps(portable_definition(workspace), indent=2, ensure_ascii=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
            temporary = handle.name
            handle.write(data)
        os.replace(temporary, path)
    except OSError as error:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise UserError("Could not export workspace: " + str(error)) from error
    return path


class Screen:
    def __init__(self, window):
        self.window = window
        window.keypad(True)
        self.accent = curses.A_BOLD
        self.selection = curses.A_REVERSE
        self.unicode = "UTF" in getattr(window, "encoding", "UTF-8").upper()
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                self.accent = curses.color_pair(1) | curses.A_BOLD
                self.selection = curses.color_pair(1) | curses.A_REVERSE
        except curses.error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass

    def size(self):
        return self.window.getmaxyx()

    def write(self, row, text, attr=0, col=2):
        height, width = self.size()
        if row < 0 or row >= height or col < 0 or col >= width - 1:
            return
        try:
            self.window.addstr(row, col, clipped(text, width - col - 1), attr)
        except curses.error:
            pass

    def start(self, title, subtitle=""):
        self.window.erase()
        self.write(0, "herdr  /  Workspaces", self.accent)
        self.write(1, ("─" if self.unicode else "-") * max(0, self.size()[1] - 4), curses.A_DIM)
        self.write(2, title, self.accent)
        if subtitle:
            self.write(3, subtitle, curses.A_DIM)

    def footer(self, help_text, error=""):
        height, width = self.size()
        self.write(height - 4, ("─" if self.unicode else "-") * max(0, width - 4), curses.A_DIM)
        if error:
            self.write(height - 3, error, curses.A_BOLD)
        lines = textwrap.wrap(plain(help_text), max(1, width - 3))[:2]
        for index, line in enumerate(lines):
            self.write(height - len(lines) + index, line, curses.A_DIM)

    def key(self):
        self.window.refresh()
        try:
            return self.window.get_wch()
        except curses.error:
            return curses.KEY_RESIZE

    def usable(self):
        height, width = self.size()
        if height >= 14 and width >= 38:
            return True
        self.window.erase()
        self.write(0, "Make the terminal at least 38 x 14.", col=0)
        self.write(1, "Esc: back", col=0)
        return False

    def prompt(self, title, value="", hint="", allow_empty=False):
        text = str(value)
        cursor = len(text)
        error = ""
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                if not self.usable():
                    if self.key() in ("\x1b", "\x03"):
                        return None
                    continue
                self.start(title, hint)
                _, width = self.size()
                visible = clipped(text, width - 6)
                if len(visible) < cursor:
                    visible = clipped(text[max(0, cursor - (width - 7)):], width - 6)
                self.write(6, "> " + visible)
                self.footer("Enter: accept   Esc: cancel", error)
                try:
                    self.window.move(6, min(width - 2, 4 + display_width(clipped(text[:cursor], width - 6))))
                except curses.error:
                    pass
                key = self.key()
                if key in ("\x1b", "\x03"):
                    return None
                if key in ("\n", "\r", curses.KEY_ENTER):
                    if text.strip() or allow_empty:
                        return text.strip()
                    error = "Enter a value, or Esc to cancel."
                elif key in (curses.KEY_BACKSPACE, "\x7f", "\b") and cursor:
                    text = text[:cursor - 1] + text[cursor:]
                    cursor -= 1
                elif key == curses.KEY_DC:
                    text = text[:cursor] + text[cursor + 1:]
                elif key == curses.KEY_LEFT:
                    cursor = max(0, cursor - 1)
                elif key == curses.KEY_RIGHT:
                    cursor = min(len(text), cursor + 1)
                elif key == curses.KEY_HOME:
                    cursor = 0
                elif key == curses.KEY_END:
                    cursor = len(text)
                elif key == "\x15":
                    text, cursor = text[cursor:], 0
                elif isinstance(key, str) and key.isprintable() and len(text) < 4096:
                    text = text[:cursor] + key + text[cursor:]
                    cursor += len(key)
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def choose(self, title, options, selected=0, subtitle=""):
        selected = min(max(selected, 0), max(0, len(options) - 1))
        while True:
            if not self.usable():
                if self.key() in ("\x1b", "\x03"):
                    return None
                continue
            self.start(title, subtitle)
            height, _ = self.size()
            count = max(1, height - 9)
            start = max(0, selected - count + 1)
            for index in range(start, min(len(options), start + count)):
                text = ("> " if index == selected else "  ") + options[index]
                self.write(5 + index - start, selected_text(text, self.size()[1] - 4) if index == selected else text, self.selection if index == selected else 0)
            self.footer("Up/Down: move   Enter: select   Esc: back")
            key = self.key()
            if key in ("\x1b", "\x03"):
                return None
            if key in (curses.KEY_DOWN, "j", "\t"):
                selected = (selected + 1) % len(options)
            elif key in (curses.KEY_UP, "k", curses.KEY_BTAB):
                selected = (selected - 1) % len(options)
            elif key in ("\n", "\r", curses.KEY_ENTER, " "):
                return selected


class Picker:
    def __init__(self, screen, store, launcher):
        self.screen = screen
        self.store = store
        self.launcher = launcher
        self.message = ""
        self.branches = {}

    def branch(self, repository):
        if repository.path not in self.branches:
            self.branches[repository.path] = checkout_branch(repository)
        return self.branches[repository.path]

    def tree(self, repositories, row, end, col=2, width=None, paths=True):
        """Render only complete repo entries; all paths are their real checkouts."""
        screen = self.screen
        width = width or screen.size()[1] - col - 2
        remaining = list(repositories)
        for index, repo in enumerate(repositories):
            prefix = ("└─ " if index == len(repositories) - 1 else "├─ ") if getattr(screen, "unicode", True) else ("`- " if index == len(repositories) - 1 else "+- ")
            heading = prefix + repo.name + "  [" + self.branch(repo) + "]"
            path_lines = wrapped_path(short_path(str(Path(repo.path).resolve())), width - 3) if paths else []
            needed = 1 + len(path_lines)
            if row + needed + (1 if index < len(repositories) - 1 else 0) > end:
                if row < end:
                    screen.write(row, "+ {} more · open Repositories to inspect".format(len(remaining)), curses.A_DIM, col=col)
                return row
            screen.write(row, clipped(heading, width), getattr(screen, "accent", curses.A_BOLD), col=col)
            for offset, line in enumerate(path_lines):
                screen.write(row + 1 + offset, "   " + line, curses.A_DIM, col=col)
            row += needed
            remaining.pop(0)
        return row

    def agent(self, selected, title="Choose an agent", subtitle="Every selected repo will be available to this agent."):
        kinds = list(AGENTS)
        index = self.screen.choose(title, list(AGENTS.values()), kinds.index(selected) if selected in kinds else 0, subtitle=subtitle)
        return kinds[index] if index is not None else None

    def open_workspace(self, original, new_agent=False):
        choice = self.agent(
            original.agent,
            title=("New agent in " if new_agent else "Open ") + original.name,
            subtitle="Always starts another session." if new_agent else "Use running agent, or start one.",
        )
        if choice is None:
            return None
        workspace = copy.deepcopy(original)
        workspace.agent = choice
        self.screen.start("Opening " + workspace.name, "Opening " + AGENTS[choice] + " with every selected repo.")
        self.screen.window.refresh()
        opened = self.launcher.open(workspace, new_agent=new_agent)
        self.store.set_agent(workspace.id, choice)
        return opened.message

    def repositories(self, initial, title="Choose repositories"):
        screen = self.screen
        chosen = {repo.path: repo for repo in initial}
        candidates = {repo.path: repo for repo in discover_repositories(default_roots())}
        candidates.update(chosen)
        selected = 0
        query = ""
        error = ""
        while True:
            if not screen.usable():
                if screen.key() in ("\x1b", "\x03"):
                    return None
                continue
            visible = sorted((repo for repo in candidates.values() if query.casefold() in (repo.name + " " + repo.path).casefold()), key=lambda repo: (repo.name.casefold(), repo.path))
            selected = min(selected, max(0, len(visible) - 1))
            screen.start(title, "{} selected · links to original checkouts".format(len(chosen)))
            height, width = screen.size()
            screen.write(5, "Find repo > " + (query or "type a name or path"), curses.A_DIM if not query else getattr(screen, "accent", curses.A_BOLD))
            count = max(1, (height - 12) // 2)
            start = max(0, selected - count + 1)
            for index in range(start, min(len(visible), start + count)):
                repo = visible[index]
                row = 7 + (index - start) * 2
                mark = "[x]" if repo.path in chosen else "[ ]"
                text = "{} {}  [{}]".format(mark, repo.name, self.branch(repo))
                screen.write(row, selected_text(text, width - 4) if index == selected else text, getattr(screen, "selection", curses.A_REVERSE) if index == selected else 0)
                screen.write(row + 1, "    " + short_path(str(Path(repo.path).resolve())), curses.A_DIM)
            if not visible:
                screen.write(7, "No matches. Ctrl+A adds a checkout folder.")
            screen.footer("Type: find  Space: select  ↑↓: move  ^A: add  Enter: done  Esc: back", error)
            key = screen.key()
            if key in ("\x1b", "\x03"):
                return None
            if key in (curses.KEY_DOWN, "\t") and visible:
                selected = (selected + 1) % len(visible)
            elif key in (curses.KEY_UP, curses.KEY_BTAB) and visible:
                selected = (selected - 1) % len(visible)
            elif key == " " and visible:
                repo = visible[selected]
                if repo.path in chosen:
                    del chosen[repo.path]
                else:
                    chosen[repo.path] = repo
                error = ""
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                query, selected = query[:-1], 0
            elif key == "\x15":
                query, selected = "", 0
            elif key == "\x01":
                path = screen.prompt("Add a repository folder", hint="Paste the local checkout path.")
                if path:
                    try:
                        repo = make_repository(path)
                        candidates[repo.path] = chosen[repo.path] = repo
                        query, error = "", ""
                        selected = 0
                    except UserError as exc:
                        error = str(exc)
            elif key in ("\n", "\r", curses.KEY_ENTER):
                if chosen:
                    return name_repositories(list(chosen.values()), initial)
                error = "Choose at least one repository."
            elif isinstance(key, str) and key.isprintable() and len(query) < 200:
                query += key
                selected = 0

    def new_workspace(self):
        workspace = Workspace(id=uuid.uuid4().hex, name="", repos=[], agent="codex")
        step = 0
        while True:
            if step == 0:
                value = self.screen.prompt("1/3  Name your workspace", workspace.name, "A name for this set of existing checkouts.")
                if value is None:
                    return None
                workspace.name, step = value, 1
            elif step == 1:
                value = self.repositories(workspace.repos, title="2/3  Choose repositories")
                if value is None:
                    step = 0
                else:
                    workspace.repos, step = value, 2
            else:
                value = self.agent(workspace.agent, title="3/3  Choose an agent")
                if value is None:
                    step = 1
                else:
                    workspace.agent = value
                    return self.edit(workspace, review=True)

    def edit(self, original, new_agent=False, review=False):
        workspace = copy.deepcopy(original)
        selected = 0 if not workspace.name else 3
        error = ""
        while True:
            screen = self.screen
            if not screen.usable():
                if screen.key() in ("\x1b", "\x03"):
                    return None
                continue
            screen.start(("Ready to open · " if review else "") + (workspace.name or "New workspace"), "Links to existing checkouts · files and branches are shared")
            height, _ = screen.size()
            compact = height < 23
            fields = [
                "Name           " + (workspace.name or "Enter a workspace name"),
                "Repositories   {} linked checkouts".format(len(workspace.repos)),
                "Agent          " + AGENTS.get(workspace.agent, workspace.agent),
                "[ Open new agent ]" if new_agent else "[ Open workspace ]",
                "[ Save workspace ]",
                "[ Back ]",
            ]
            rows = [4, 5, 6, 7, 8, 9] if compact else [5, 7, height - 9, height - 7, height - 6, height - 5]
            if not compact:
                self.tree(workspace.repos, 8, rows[2] - 1, col=4)
                screen.write(rows[2] - 1, "Changes and branch switches are shared wherever this checkout is used.", curses.A_DIM)
            for index, text in enumerate(fields):
                text = ("> " if index == selected else "  ") + text
                screen.write(rows[index], selected_text(text, screen.size()[1] - 4) if index == selected else text, getattr(screen, "selection", curses.A_REVERSE) if index == selected else 0)
            screen.footer("Tab/Up/Down: move   Enter: choose   Esc: back", error)
            key = screen.key()
            if key in ("\x1b", "\x03"):
                return None
            if key in (curses.KEY_DOWN, "j", "\t"):
                selected = (selected + 1) % len(fields)
            elif key in (curses.KEY_UP, "k", curses.KEY_BTAB):
                selected = (selected - 1) % len(fields)
            elif key in ("\n", "\r", curses.KEY_ENTER, " "):
                if selected == 0:
                    value = screen.prompt("Workspace name", workspace.name)
                    if value is not None:
                        workspace.name = value
                elif selected == 1:
                    try:
                        value = self.repositories(workspace.repos)
                        if value is not None:
                            workspace.repos = value
                    except UserError as exc:
                        error = str(exc)
                elif selected == 2:
                    value = self.agent(workspace.agent)
                    if value is not None:
                        workspace.agent = value
                elif selected in (3, 4):
                    if not workspace.name.strip():
                        error, selected = "Enter a workspace name first.", 0
                        continue
                    if not workspace.repos:
                        error, selected = "Choose at least one repository.", 1
                        continue
                    try:
                        self.store.save(workspace)
                        if selected == 4:
                            self.message = "Saved " + workspace.name
                            return None
                        screen.start("Opening " + workspace.name, "Starting " + AGENTS[workspace.agent] + " with every selected repo.")
                        screen.window.refresh()
                        result = self.launcher.open(workspace, new_agent=new_agent)
                        return result.message
                    except UserError as exc:
                        error = str(exc)
                else:
                    return None

    def remove_workspace(self, workspace):
        choice = self.screen.choose(
            "Remove " + workspace.name + "?",
            ["Keep workspace", "Remove saved definition"],
            selected=0, subtitle="Keeps repos and running spaces.",
        )
        if choice == 1:
            self.store.delete(workspace.id)
            self.message = "Removed " + workspace.name + " from saved workspaces."

    def export(self, workspace):
        slug = re.sub(r"[^a-z0-9]+", "-", workspace.name.lower()).strip("-") or "workspace"
        destination = self.screen.prompt("Share " + workspace.name, str(Path.home() / (slug + ".herdr-workspace.json")), "Export repo names and Git remotes. Local paths stay private.")
        if destination is None:
            return
        path = Path(destination).expanduser()
        if path.exists():
            overwrite = self.screen.choose("Replace the existing file?", ["Keep existing file", "Replace file"], subtitle=short_path(path))
            if overwrite != 1:
                return
        path = export_file(workspace, path)
        self.message = "Shared workspace saved to " + short_path(path)

    def import_workspace(self):
        path = self.screen.prompt("Open shared workspace", hint="Path to a .herdr-workspace.json file")
        if path is None:
            return None
        try:
            source = Path(path).expanduser()
            if source.stat().st_size > 1024 * 1024:
                raise UserError("Workspace file is too large.")
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UserError("Could not read workspace file: " + str(exc)) from exc
        roots = default_roots()
        base = self.screen.prompt("Where do you keep these repos?", roots[0] if roots else "~/code", "Repos are matched by their Git remote when available.")
        if base is None:
            return None
        workspace = import_definition(data, base)
        return self.edit(workspace)

    def run(self):
        selected = 0
        while True:
            screen = self.screen
            if not screen.usable():
                if screen.key() in ("q", "\x1b", "\x03"):
                    return None
                continue
            workspaces = self.store.load()
            options = ["{}   {} repos".format(workspace.name, len(workspace.repos)) for workspace in workspaces]
            options += ["+ New workspace", "Open shared workspace"]
            selected = min(selected, len(options) - 1)
            screen.start("Your workspaces", "Choose a workspace, then an agent")
            height, width = screen.size()
            split = width >= 76 and height >= 18
            left_width = max(28, min(38, width // 3)) if split else width - 4
            count = max(1, height - 9)
            if not split and height >= 22 and selected < len(workspaces):
                count = min(count, max(3, (height - 10) // 2))
            start = max(0, selected - count + 1)
            for index in range(start, min(len(options), start + count)):
                text = ("> " if index == selected else "  ") + options[index]
                screen.write(5 + index - start, selected_text(text, left_width) if index == selected else clipped(text, left_width), getattr(screen, "selection", curses.A_REVERSE) if index == selected else 0)
            if selected < len(workspaces):
                if split:
                    divider = left_width + 3
                    for row in range(5, height - 5):
                        screen.write(row, "│" if getattr(screen, "unicode", True) else "|", curses.A_DIM, col=divider)
                    column = divider + 3
                    screen.write(5, workspaces[selected].name, getattr(screen, "accent", curses.A_BOLD), col=column)
                    screen.write(6, "Existing checkouts", curses.A_DIM, col=column)
                    self.tree(workspaces[selected].repos, 8, height - 5, col=column)
                elif height >= 22:
                    row = 6 + min(len(options), count)
                    screen.write(row, "Existing checkout links", getattr(screen, "accent", curses.A_BOLD))
                    self.tree(workspaces[selected].repos, row + 1, height - 5)
            screen.footer("Enter:open a:+agent r:edit n:new e:share i:import d:remove q:quit", self.message)
            key = screen.key()
            if key in ("q", "\x1b", "\x03"):
                return None
            if key in (curses.KEY_DOWN, "j", "\t"):
                selected = (selected + 1) % len(options)
                continue
            if key in (curses.KEY_UP, "k", curses.KEY_BTAB):
                selected = (selected - 1) % len(options)
                continue
            try:
                result = None
                if key == "n" or (key in ("\n", "\r", curses.KEY_ENTER) and selected == len(workspaces)):
                    result = self.new_workspace()
                elif key == "i" or (key in ("\n", "\r", curses.KEY_ENTER) and selected == len(workspaces) + 1):
                    result = self.import_workspace()
                elif selected < len(workspaces):
                    workspace = workspaces[selected]
                    if key in ("\n", "\r", curses.KEY_ENTER):
                        result = self.open_workspace(workspace)
                    elif key == "r":
                        result = self.edit(workspace)
                    elif key == "e":
                        self.export(workspace)
                    elif key == "d":
                        self.remove_workspace(workspace)
                    elif key == "a":
                        result = self.open_workspace(workspace, new_agent=True)
                if result:
                    return result
            except UserError as exc:
                self.message = str(exc)


def run(store, launcher):
    """Return the opened-workspace message, or None when the picker closes."""
    try:
        return curses.wrapper(lambda window: Picker(Screen(window), store, launcher).run())
    except curses.error as exc:
        raise UserError("The terminal picker needs an interactive terminal. Run it inside Herdr.") from exc
