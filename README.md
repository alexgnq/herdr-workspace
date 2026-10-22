# Herdr Workspaces

Group Git repositories into saved Herdr workspaces and launch coding agents with access to all of them.
Switch between Codex, Claude Code, Copilot and Pi, or run them side by side in the same workspace.

[Install](#install) · [Usage](#usage) · [Shortcuts and sidebar](#shortcuts-and-sidebar) · [How it works](#directory-access)

![Save repository folders, create a workspace and choose an agent in the terminal picker.](assets/demo.gif)

<sub>Recorded with temporary repos. Agent launches are simulated.</sub>

## Install

Requires macOS or Linux, Herdr 0.9.1+, Python 3.11+, Git and a supported agent CLI.
Sign in to the agent using its normal command before launching it through the plugin.
Use the latest patch release of a supported Python version.

```sh
herdr plugin install alexgnq/herdr-workspaces
```

Open the picker **from a Herdr pane**:

```sh
herdr plugin action invoke open --plugin herdr-workspaces
```

Press `n` to create a workspace. Name it, select the repositories, choose an agent and review the
checkouts before opening. The workspace stays saved for the next session.

For a different checkout location, open **Repository folders** from the home screen once. The saved
folders become the default search locations for all future workspaces on that machine.

### Update

```sh
herdr plugin update herdr-workspaces
```

<details>
<summary>Install from a local checkout or release ZIP</summary>

Keep the extracted source in a permanent directory, then link it:

```sh
herdr plugin link /path/to/herdr-workspaces --enabled
```

Python's standard library is the only runtime dependency. There is no build step or package installation.

</details>

## Usage

| Key | What it does |
| :--- | :--- |
| `n` | Create a workspace with guided setup. |
| `Enter` | Open a saved workspace. Choose an agent, then focus it if running or start it in the same space. |
| `a` | Choose an agent and start another session in the workspace. |
| `r` | Edit the workspace name or repositories. |
| `f` | Set the default repository folders. |
| `e` / `i` | Export a workspace / import a shared file. |
| `d` | Delete a saved workspace after confirmation. |
| `Esc` / `q` | Go back / close the picker. |

In setup, use `Tab` or arrows to move between fields and `Enter` to edit or activate an action.
In the repo picker, type to filter, use arrows to move, `Space` to select and `Enter` to finish.
`Ctrl+A` adds a single checkout. `Ctrl+F` opens Repository folders and refreshes the list when settings
change. Paths and current branches distinguish different checkouts.

Deleting a saved workspace keeps the repos, generated links and running agents. To close the running
space separately, use Herdr's Close workspace action, or Ctrl+B then Shift+D with default keybindings.
Closing a space also stops its terminals.

### Repository folders

Choose **Repository folders** on the home screen or press `f` to add, change or remove search locations.
Changes are saved automatically and apply to every workspace. This is a one-time preference;
normal workspace setup uses the saved folders without asking again. Removing a search folder keeps
its repositories and any saved workspace definitions.

The picker searches two folder levels beneath each saved location. Before any folders are configured,
it looks under `~/code`, `~/Projects`, `~/projects` and `~/src`, or the home folder if none exist.
**Restore default folders** returns to those locations. Git worktrees are supported, and `Ctrl+A` can
still add an individual checkout outside the search folders.

### Agent sessions

Each saved workspace keeps one Herdr space. Opening it focuses an existing agent of the chosen kind
or starts a new one. The last chosen agent is preselected next time. Press `a` to start another session.
Sessions opened with the same repository set share checkouts, branches and notes.

Changing a workspace's repositories affects new sessions. Running agents keep the repository set
and instructions they started with. Once no pane in any Herdr session uses an earlier repository
set's folder, opening the workspace removes it. Folders containing extra files or notes absent from
the current folder are kept.

### Import and export

Press `e` to export the selected workspace or `i` to import a workspace file.
Exports contain repository names and Git remotes, without local paths or URL credentials.
During import, select a folder containing the local checkouts. Repositories are matched by remote
across SSH and HTTPS, including renamed checkout folders. Missing or ambiguous matches produce an
error so a different checkout folder can be selected. Importing does not clone repositories.

## Shortcuts and sidebar

### Use a short command

Add this alias to the shell configuration and reload it:

```sh
alias herdrws='herdr plugin action invoke open --plugin herdr-workspaces'
```

Then run `herdrws` from a Herdr pane. The source also includes `bin/herdrws` as a standalone
shortcut that can be copied to a directory on `PATH`.

### Open it with a keybinding

Add this to Herdr's `config.toml` and reload its configuration from Herdr:

```toml
[[keys.command]]
key = "prefix+shift+o"
type = "plugin_action"
command = "herdr-workspaces.open"
description = "Open Herdr Workspaces"
```

With the default prefix, press Ctrl+B, then Shift+O. Choose a different binding if that one is in use.

### Show repositories in the sidebar

Run this once inside Herdr to show the repos beneath each workspace in the **Spaces** sidebar:

```sh
herdr plugin action invoke setup-sidebar --plugin herdr-workspaces
```

```text
Platform
├─ service-api
├─ web-app
└─ infrastructure
```

This preserves the theme and existing sidebar rows, saves a config backup with the suffix
`.before-herdr-workspaces`, and reloads the sidebar configuration. Repo labels refresh when the workspace
opens and when Herdr restarts. Other spaces retain their usual rows. Up to eight repos are shown,
followed by a count when there are more.

## Directory access

Each agent starts in a generated workspace directory containing repo links and a short repo index.
Agents that limit file access to their folder receive every selected checkout in the form they accept:
`--add-dir` for Codex, Claude Code and Copilot, with Codex in `workspace-write` sandboxing. Pi has no
folder limit and needs nothing extra.
Agents retain their normal command approval rules and global settings. This removes the extra directory
boundary for the chosen repos; it does not disable command, network or first-run trust prompts.

Before working in a repo, the generated instructions tell the agent to read that repo's instructions.
Repo-local agent settings are not merged into a single workspace configuration. Each repo retains its
own Git checkout and history. The extension does not create branches, worktrees or commits.

The folders under `~/.local/state/herdr/plugins/herdr-workspaces/roots/` contain **symlinks to the selected
checkouts**, not local copies. `workspace.json` records the exact target of each link. A small
`AGENTS.md` describes the repo set to agents; `CLAUDE.md` links to it. The hash identifies a repo set and
stays stable while its selected checkout paths stay the same.

Notes written below the marker line in `AGENTS.md` are kept when the workspace reopens. They carry
over to the new folder when the repository set changes. Everything above the marker is regenerated.

Two workspaces selecting the same checkout share its files and branch. For separate branches, add
existing Git worktrees as separate checkout paths. The picker shows their full paths to distinguish
them. Checkouts with the same folder name get distinct link names, such as `api` and `api-2`.
Existing saved names stay the same. A repo link that has been retargeted or replaced with a copy
is rejected on open rather than used silently.

## Command line

The picker can also run with `python3 herdr_workspaces.py ui`. Configuration and sharing work outside
Herdr; launching agents requires a Herdr pane. Run `--help` for the command-line equivalents.
The following commands run from a source checkout:

```sh
python3 herdr_workspaces.py create Platform ~/code/service-a ~/code/service-b --agent codex
python3 herdr_workspaces.py export Platform platform.herdr-workspace.json
python3 herdr_workspaces.py import platform.herdr-workspace.json --base ~/development
python3 herdr_workspaces.py open Platform --agent claude --new-agent
```

## Develop

`agents.py` contains the supported agents and their directory-access options.

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

## License

[MIT](LICENSE).
