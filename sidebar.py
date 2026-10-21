"""Show selected repositories beneath a workspace in Herdr's Spaces sidebar."""

import copy
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from core import UserError


SOURCE = "herdr-multirepo"
MAX_REPOSITORIES = 8
MAX_LAYOUT_ROWS = 16
DEFAULT_ROWS = [["state_icon", "workspace"], ["branch", "git_status"]]
REPOSITORY_KEYS = ["mr_repo" + str(index) for index in range(1, MAX_REPOSITORIES + 1)]
TOKEN_KEYS = REPOSITORY_KEYS + ["mr_more"]
_OWN_TOKENS = {"$" + key for key in TOKEN_KEYS}
_SECTION = re.compile(r"^\s*\[\s*(?:ui|'ui'|\"ui\")\s*\.\s*(?:sidebar|'sidebar'|\"sidebar\")\s*\.\s*(?:spaces|'spaces'|\"spaces\")\s*\]\s*(?:#.*)?$", re.M)
_HEADER = re.compile(r"^\s*\[", re.M)


def repository_tokens(workspace) -> dict:
    """Produce bounded presentation values, never filesystem instructions."""
    repositories = workspace.repos
    more = max(0, len(repositories) - MAX_REPOSITORIES)
    values = {}
    for index, repo in enumerate(repositories[:MAX_REPOSITORIES]):
        last = index == min(len(repositories), MAX_REPOSITORIES) - 1 and not more
        name = "".join(char for char in str(repo.name) if char.isprintable()).strip()
        if len(name) > 77:
            name = name[:76] + "…"
        values[REPOSITORY_KEYS[index]] = ("└─ " if last else "├─ ") + name
    if more:
        values["mr_more"] = "└─ + " + str(more) + " more repositories"
    return values


def publish_workspace(herdr, workspace_id, workspace) -> None:
    """One metadata patch also clears rows left by an older repo selection."""
    values = repository_tokens(workspace)
    arguments = ["workspace", "report-metadata", workspace_id, "--source", SOURCE]
    for key in TOKEN_KEYS:
        if key in values:
            arguments.extend(["--token", key + "=" + values[key]])
        else:
            arguments.extend(["--clear-token", key])
    herdr.call(*arguments)


def _row_tree_tokens(row) -> set:
    return {token if isinstance(token, str) else token.get("token") for token in row
            if (isinstance(token, str) and token in _OWN_TOKENS)
            or (isinstance(token, dict) and token.get("token") in _OWN_TOKENS)}


def sidebar_rows(existing_rows=None) -> list:
    """Retain built-in/custom rows and append rows hidden until values exist."""
    rows = copy.deepcopy(DEFAULT_ROWS if existing_rows is None else existing_rows)
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise UserError("Herdr sidebar rows must be an array of row arrays.")
    present = set().union(*(_row_tree_tokens(row) for row in rows)) if rows else set()
    missing = [["$" + key] for key in TOKEN_KEYS if "$" + key not in present]
    if len(rows) + len(missing) > MAX_LAYOUT_ROWS:
        raise UserError("The Spaces sidebar supports 16 rows. Keep at most 7 existing rows before adding the repository tree.")
    return rows + missing


def _array_rows(text, start):
    """Find a single TOML row array without interpreting unrelated TOML."""
    depth = 0
    quote = None
    escaped = False
    comment = False
    row_start = None
    rows = []
    for index in range(start, len(text)):
        char = text[index]
        if comment:
            if char == "\n":
                comment = False
            continue
        if quote:
            if escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char == "#":
            comment = True
        elif char in "\"'":
            if text[index:index + 3] == char * 3:
                raise UserError("Multiline strings in sidebar rows are unsupported. Configure the repo rows manually.")
            quote = char
        elif char == "[":
            depth += 1
            if depth == 2:
                row_start = index
        elif char == "]":
            if depth == 2:
                row = text[row_start:index + 1]
                content = _without_comments(row)[1:].lstrip()
                if not content or content[0] not in "\"'{]":
                    raise UserError("Cannot read the Spaces sidebar token rows. Configure the repo rows manually.")
                rows.append(row)
                row_start = None
            depth -= 1
            if depth == 0:
                return index + 1, rows
        elif depth == 1 and not char.isspace() and char != ",":
            raise UserError("Cannot read the Spaces sidebar row arrays. Configure the repo rows manually.")
    raise UserError("Cannot read the Spaces sidebar rows: the array is incomplete.")


def _without_comments(text):
    output = []
    quote = None
    escaped = False
    comment = False
    for char in text:
        if comment:
            if char == "\n":
                comment = False
                output.append(char)
            continue
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "#":
            comment = True
        else:
            output.append(char)
            if char in "\"'":
                quote = char
    return "".join(output)


def _raw_tree_tokens(row):
    tokens = re.findall(r'''["'](\$mr_repo[1-8]|\$mr_more)["']''', _without_comments(row))
    return set(tokens)


def _format_rows(rows):
    return "[\n" + "\n".join("  " + row.strip() + "," for row in rows) + "\n]"


def _with_repo_rows(text):
    matches = list(_SECTION.finditer(text))
    if len(matches) > 1:
        raise UserError("The Herdr config contains repeated Spaces sidebar sections. Fix it before adding the tree.")
    if not matches:
        # Defining the section again would invalidate inline/dotted TOML layouts.
        if re.search(r"^\s*(?:ui\.)?sidebar\.spaces\b|^\s*spaces\s*=", text, re.M):
            raise UserError("This config defines Spaces using dotted or inline keys. Add the repo rows manually.")
        rows = _format_rows([json.dumps(row) for row in sidebar_rows()])
        return text.rstrip() + ("\n\n" if text.strip() else "") + "[ui.sidebar.spaces]\nrows = " + rows + "\n"
    section = matches[0]
    next_header = _HEADER.search(text, section.end())
    end = next_header.start() if next_header else len(text)
    body = text[section.end():end]
    settings = list(re.finditer(r"^\s*rows\s*=\s*", body, re.M))
    if len(settings) > 1:
        raise UserError("The Spaces sidebar has repeated rows settings. Fix it before adding the tree.")
    if not settings:
        rows = _format_rows([json.dumps(row) for row in sidebar_rows()])
        insertion = section.end()
        return text[:insertion] + "\nrows = " + rows + text[insertion:]
    start = section.end() + settings[0].end()
    if start >= len(text) or text[start] != "[":
        raise UserError("Cannot read the Spaces sidebar rows. Configure the repo rows manually.")
    finish, raw_rows = _array_rows(text, start)
    present = set().union(*(_raw_tree_tokens(row) for row in raw_rows)) if raw_rows else set()
    missing = [json.dumps(["$" + key]) for key in TOKEN_KEYS if "$" + key not in present]
    if len(raw_rows) + len(missing) > MAX_LAYOUT_ROWS:
        raise UserError("The Spaces sidebar supports 16 rows. Keep at most 7 existing rows before adding the repository tree.")
    updated = _format_rows(raw_rows + missing)
    return text[:start] + updated + text[finish:]


def install_sidebar(config_path: Path) -> bool:
    """Add repo rows and keep a one-time backup of the original configuration."""
    path = Path(config_path).expanduser()
    if path.is_symlink():
        path = path.resolve()
    try:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _with_repo_rows(original)
        if updated == original:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        backup = path.with_name(path.name + ".before-herdr-multirepo")
        if path.exists() and not backup.exists():
            with backup.open("x", encoding="utf-8") as stream:
                stream.write(original)
            backup.chmod(mode)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".sidebar-", delete=False) as stream:
                temporary = stream.name
                stream.write(updated)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as error:
        raise UserError("Cannot configure the Spaces sidebar: " + str(error)) from error
    return True
