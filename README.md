# Herdr Workspaces

Pick repositories, choose an agent, and open it with access to all of them.
Save the repo set as a workspace and reuse it for your next agent.

The picker runs in a Herdr terminal overlay. Python's standard library is the only runtime dependency.
Setup guides you through the name, repositories and agent, then shows a checkout review before opening.
The terminal UI uses your terminal's colours with a cyan accent and supports small windows.

## Install

Requires macOS or Linux, Herdr 0.9.1+, Python 3.9+, Git, and a supported agent CLI:
Codex, Claude Code, Copilot or Pi.
Sign in to the agent once using its normal command before launching it here.

```sh
herdr plugin install alexgnq/herdr-workspaces
```

To update a GitHub installation:

```sh
herdr plugin update herdr-workspaces
```

Then run this **inside a Herdr pane**:

```sh
herdr plugin action invoke open --plugin herdr-workspaces
```

For a short command, add this to your shell configuration:

```sh
alias herdrws='herdr plugin action invoke open --plugin herdr-workspaces'
```

The source also includes `bin/herdrws`, which you can copy to a directory on your PATH.

For a keybinding, add this to your Herdr `config.toml` and reload its configuration from Herdr:

```toml
[[keys.command]]
key = "prefix+shift+o"
type = "plugin_action"
command = "herdr-workspaces.open"
description = "Open Herdr Workspaces"
```

With the default prefix, press Ctrl+B, then Shift+O. Choose a different binding if that one is in use.

To show the repos beneath each workspace in Herdr's **Spaces** sidebar, run this once inside Herdr:

```sh
herdr plugin action invoke setup-sidebar --plugin herdr-workspaces
```

```text
Platform
├─ service-api
├─ service-config
└─ infrastructure
```

This preserves your theme and existing sidebar rows, saves a config backup with the suffix
`.before-herdr-workspaces`, and reloads the sidebar configuration. Repo labels refresh when the workspace
opens and when Herdr restarts. Other spaces retain their usual rows. Up to eight repos are shown,
followed by a count when there are more.

## Use

- `n` starts guided setup. Name the workspace, select repositories, choose an agent, then review and open.
- `Tab` or arrows move between fields. `Enter` edits a field or activates the selected action.
- In the repo picker, type to filter, use arrows to move, `Space` to select and `Enter` to finish.
  `Ctrl+A` adds a checkout folder. Paths and current branches distinguish different checkouts.
- `Enter` on a saved workspace lets you choose an agent. It focuses a running
  agent of that kind, or starts one in the same space. The last chosen agent is preselected next time.
- `a` chooses an agent and always starts another session in that workspace. `r` edits its name or repos.
- `e` exports the selected workspace. `i` imports a shared file. `Esc` goes back; `q` closes the picker.
- `d` removes the selected saved workspace after confirmation. It keeps the repos, generated links
  and running agents. To close the running space separately, use Herdr's Close workspace action,
  or Ctrl+B then Shift+D with default keybindings. Closing a space also stops its terminals.

The picker looks two folder levels under `~/code`, `~/Projects`, `~/projects` and `~/src`.
Use **Add folder** for other locations. Git worktrees are supported.

Opening an existing workspace focuses an agent of the chosen kind when one exists. **New agent**
creates another tab. Each saved workspace keeps one Herdr space. After you change its repos, opening
it adds a tab with the new repo set to that space. Agents already running keep the repos and
instructions they started with, and are not reused for the new set. Once no pane in any Herdr session uses an earlier
repo set's folder, opening the workspace removes it. A folder is kept while it holds notes the current
folder lacks or files the extension did not create.

You can run different agents side by side in one workspace, for example Claude Code and Pi: open the
workspace with each, or press `a` to add another. They share the same checkouts, branches and notes.

## Share with your team

Share this source folder or the release ZIP. Each teammate extracts it somewhere permanent, runs
`herdr plugin link /path/to/herdr-workspaces --enabled`, and uses the same action or shortcut.
There is no build or package installation step.

Teammates can also install directly from GitHub:

```sh
herdr plugin install alexgnq/herdr-workspaces
```

To share a **workspace**, select it and press `e`. The JSON file contains repo names and Git remotes,
without your machine paths or URL credentials. A teammate imports it and selects their local checkout
folder. Imports match Git remotes across SSH and HTTPS, including renamed checkout folders. Missing
or ambiguous repos produce an error so the teammate can choose the correct folder. Imports do not clone.

## Directory access

Each agent starts in a generated workspace directory containing repo links and a short repo index.
Agents that limit file access to their folder receive every selected checkout in the form they accept:
`--add-dir` for Codex, Claude Code and Copilot, with Codex in `workspace-write` sandboxing. Pi has no
folder limit and needs nothing extra. `agents.py` holds this per-agent detail; adding an agent is one entry.
Agents retain their normal command approval rules and global settings. This removes the extra directory
boundary for the chosen repos; it does not disable command, network or first-run trust prompts.

Before working in a repo, the generated instructions tell the agent to read that repo's instructions.
Repo-local agent settings are not merged into a single workspace configuration. Each repo retains its
own Git checkout and history. The extension does not create branches, worktrees or commits.

The folders under `~/.local/state/herdr/plugins/herdr-workspaces/roots/` contain **symlinks to the selected
checkouts**, not local copies. `workspace.json` records the exact target of each link. A small
`AGENTS.md` describes the repo set to agents; `CLAUDE.md` links to it. The hash identifies a repo set and
stays stable while its selected checkout paths stay the same.

Notes written below the marker line in `AGENTS.md`, by you or an agent, are kept when the workspace
reopens. They carry over to the new folder when you change the repo set. Everything above the marker
is regenerated.

Two workspaces selecting the same checkout share its files and branch. For separate branches, add
existing Git worktrees as separate checkout paths. The picker shows their full paths so you can tell
them apart. Checkouts with the same folder name get distinct link names, such as `api` and `api-2`.
Existing saved names stay the same. A repo link that has been retargeted or replaced with a copy
is rejected on open rather than used silently.

## Command line

The picker can also run with `python3 herdr_workspaces.py ui`. Configuration and sharing work outside
Herdr; launching agents requires a Herdr pane. Run `--help` for the command-line equivalents.

```sh
python3 herdr_workspaces.py create Platform ~/code/service-a ~/code/service-b --agent codex
python3 herdr_workspaces.py export Platform platform.herdr-workspace.json
python3 herdr_workspaces.py import platform.herdr-workspace.json --base ~/development
python3 herdr_workspaces.py open Platform --agent claude --new-agent
```

## Develop

From a source checkout, link the plugin locally:

```sh
herdr plugin link "$PWD" --enabled
```

Run the tests:

```sh
python3 -B -m unittest discover -s tests -v
```

Tests use temporary repos and a simulated Herdr transport. Terminal smoke tests can use a temporary
Store and an injected launcher; they do not need to open agents or change a running Herdr session.
