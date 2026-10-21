"""How each agent kind is given the workspace's checkouts. Add an agent here and nowhere else."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    label: str
    # Repeated once per checkout; None when the agent does not limit file access to its folder.
    directory_flag: str | None = None
    arguments: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()


# Keys are Herdr `agent start --kind` values, which are also the executable names.
# The order is the order shown in the picker.
ADAPTERS = {
    "codex": Agent("Codex", "--add-dir", arguments=("--sandbox", "workspace-write")),
    "claude": Agent("Claude Code", "--add-dir",
                    environment=("CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1",)),
    "copilot": Agent("Copilot", "--add-dir"),
    "pi": Agent("Pi"),
}

AGENTS = {kind: agent.label for kind, agent in ADAPTERS.items()}
UNSUPPORTED = "Choose one of: " + ", ".join(AGENTS.values()) + "."
