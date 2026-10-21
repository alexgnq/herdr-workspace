# Security

## Reporting a vulnerability

Use [GitHub's private vulnerability reporting](https://github.com/alexgnq/herdr-workspaces/security/advisories/new)
to report a security issue. Include the affected version, steps to reproduce it and the likely impact.
Keep exploit details and credentials out of public issues.

## Supported versions

Security fixes target the latest release. Update to the latest version before reporting an issue.

Use Python 3.11 or newer and keep it updated to the latest patch release. CI tests the oldest
supported Python series and the latest stable series.

## Trust boundaries and code scanning

This is a local plugin that runs as the current user and inherits Herdr's environment.
Herdr supplies `HERDR_BIN_PATH`, `HERDR_PLUGIN_CONFIG_DIR` and `HERDR_PLUGIN_STATE_DIR`.
The local user can also select the config file with `HERDR_CONFIG_PATH` and storage locations
with the standard XDG variables. These are trusted host settings, not fields in shared workspace files.
See [Herdr's plugin environment](https://herdr.dev/docs/plugins/#commands-and-environment).

CodeQL alerts 1 through 4 flag those settings as untrusted input. They are false positives for
this plugin's trust model:

- Alert 1: `HERDR_BIN_PATH` selects the host's executable. Commands use an argument list without
  a shell. Shell syntax in workspace names or repository paths remains literal.
- Alerts 2 and 3: the host or local user selects the config and state directories.
- Alert 4: the local user selects the Herdr config file for the explicit `setup-sidebar` action.

Importing a shared workspace never changes these settings. It matches repository identities to
local checkouts and creates a new workspace ID. It does not load shell configuration or repository
`.env` files. Keep CodeQL enabled; reassess these dismissals if any untrusted input can set the
executable, storage directories or config file in a future change.
