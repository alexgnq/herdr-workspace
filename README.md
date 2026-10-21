# Herdr Workspaces

Open your coding agent across several repos in one Herdr workspace.

Codex, Claude Code, Copilot and Pi. macOS and Linux. Python 3.9+ and Herdr 0.9.1+.

[Get started](#get-started) · [Shortcuts](#shortcuts-and-sidebar) · [Share with your team](#share-with-your-team)

![Create a workspace, select repositories and choose an agent in the terminal picker.](assets/demo.gif)

<sub>Recorded with temporary repos. Agent launches are simulated.</sub>

Save a set of repositories, then open whichever agent you want in the same Herdr space.
Run different agents side by side, show your repos in the sidebar, and share workspace definitions
with your team. Repo links point to your existing checkouts.

The picker runs in a terminal overlay, uses your terminal's colours and supports small windows.
Python's standard library is the only runtime dependency. There is no build step.

## Get started

You'll need Git and one of the supported agent CLIs alongside Herdr and Python.
Sign in to the agent once using its normal command, then install the plugin:

```sh
herdr plugin install alexgnq/herdr-workspaces
```

Add the short command to your shell configuration:

```sh
alias herdrws='herdr plugin action invoke open --plugin herdr-workspaces'
```

Reload your shell configuration, then run `herdrws` **inside a Herdr pane**. Press `n` to name a workspace, select repositories,
choose an agent, and review the checkouts before opening.

<details>
<summary>Full command and updates</summary>

Open the picker without an alias:

```sh
herdr plugin action invoke open --plugin herdr-workspaces
```

Update an existing GitHub installation:

```sh
herdr plugin update herdr-workspaces
```

</details>

The source also includes `bin/herdrws`, which you can copy to a directory on your PATH.

## Shortcuts and sidebar

### Open it with a keybinding

Add this to your Herdr `config.toml` and reload its configuration from Herdr:

```toml
[[keys.command]]
key = "prefix+shift+o"
type = "plugin_action"
command = "herdr-workspaces.open"
description = "Open Herdr Workspaces"
```

With the default prefix, press Ctrl+B, then Shift+O. Choose a different binding if that one is in use.

### Put your repos in the sidebar

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

This preserves your theme and existing sidebar rows, saves a config backup with the suffix
`.before-herdr-workspaces`, and reloads the sidebar configuration. Repo labels refresh when the workspace
opens and when Herdr restarts. Other spaces retain their usual rows. Up to eight repos are shown,
followed by a count when there are more.

## Use

| Key | What it does |
| :--- | :--- |
| `n` | Create a workspace with guided setup. |
| `Enter` | Open a saved workspace. Choose an agent, then focus it if running or start it in the same space. |
| `a` | Choose an agent and start another session in the workspace. |
| `r` | Edit the workspace name or repositories. |
| `e` / `i` | Export a workspace / import a shared file. |
| `d` | Delete a saved workspace after confirmation. |
| `Esc` / `q` | Go back / close the picker. |

In setup, use `Tab` or arrows to move between fields and `Enter` to edit or activate an action.
In the repo picker, type to filter, use arrows to move, `Space` to select and `Enter` to finish.
`Ctrl+A` adds a checkout folder. Paths and current branches distinguish different checkouts.

Deleting a saved workspace keeps the repos, generated links and running agents. To close the running
space separately, use Herdr's Close workspace action, or Ctrl+B then Shift+D with default keybindings.
Closing a space also stops its terminals.

The picker looks two folder levels under `~/code`, `~/Projects`, `~/projects` and `~/src`.
Use **Add folder** for other locations. Git worktrees are supported.

Opening an existing workspace focuses an agent of the chosen kind when one exists. The last chosen
agent is preselected next time. **New agent** creates another tab. Each saved workspace keeps one Herdr space. After you change its repos, opening
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

## License

[MIT](LICENSE).
