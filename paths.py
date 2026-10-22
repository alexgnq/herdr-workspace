"""Directory suggestions for terminal path prompts."""

import os


def directory_completions(value: str) -> list[str]:
    """Complete one directory component while keeping the path's spelling."""
    if not isinstance(value, str) or not all(char.isprintable() for char in value):
        return []
    if value in ("", "~"):
        return ["~/"]
    if value.startswith("~") and not value.startswith("~/"):
        return []
    parent, prefix = os.path.split(value)
    display_parent = value[:-len(prefix)] if prefix else value
    matches = []
    try:
        with os.scandir(os.path.expanduser(parent or ".")) as entries:
            for entry in entries:
                if (not entry.name.casefold().startswith(prefix.casefold())
                        or (entry.name.startswith(".") and not prefix.startswith("."))
                        or not all(char.isprintable() for char in entry.name)):
                    continue
                try:
                    if entry.is_dir():
                        matches.append(display_parent + entry.name + "/")
                except OSError:
                    continue
    except (OSError, ValueError, RuntimeError):
        return []
    return sorted(matches, key=lambda path: (path.casefold(), path))
