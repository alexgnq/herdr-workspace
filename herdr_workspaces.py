#!/usr/bin/env python3
"""Saved multi-repository workspaces for Herdr."""

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from core import AGENTS, PLUGIN_ID, Store, UserError, Workspace, import_definition, make_repository, name_repositories, portable_definition
from launcher import Herdr, Launcher

VERSION = "0.2.5"


def find_workspace(store, name):
    matches = [item for item in store.load() if item.id == name or item.name == name]
    if len(matches) != 1:
        raise UserError("Workspace not found or name is ambiguous: " + name + ". Run 'list' to see workspace IDs.")
    return matches[0]


def parser():
    result = argparse.ArgumentParser(description="Open an agent with a saved set of repositories in Herdr.")
    result.add_argument("--version", action="version", version=VERSION)
    commands = result.add_subparsers(dest="command")
    commands.add_parser("ui", help="Open the terminal workspace picker")
    commands.add_parser("open-picker", help="Open the picker as a Herdr overlay")
    commands.add_parser("refresh", help="Refresh repo trees for this plugin's open spaces")
    commands.add_parser("setup-sidebar", help="Show workspace repo trees in the Herdr Spaces sidebar")
    commands.add_parser("list", help="List saved workspaces")
    create = commands.add_parser("create", help="Save a workspace")
    create.add_argument("name")
    create.add_argument("repos", nargs="+")
    create.add_argument("--agent", choices=AGENTS, default="codex")
    launch = commands.add_parser("open", help="Open a saved workspace")
    launch.add_argument("name")
    launch.add_argument("--agent", choices=AGENTS)
    launch.add_argument("--new-agent", action="store_true")
    export = commands.add_parser("export", help="Export a portable workspace file")
    export.add_argument("name")
    export.add_argument("output")
    incoming = commands.add_parser("import", help="Import a shared workspace using your local checkout folder")
    incoming.add_argument("file")
    incoming.add_argument("--base", required=True)
    incoming.add_argument("--agent", choices=AGENTS, default="codex")
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    store = Store()
    command = arguments.command or "ui"
    try:
        if command == "open-picker":
            if os.environ.get("HERDR_ENV") != "1":
                raise UserError("Run this action from a Herdr pane.")
            Herdr().call("plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "picker", "--focus")
        elif command == "refresh":
            count = Launcher(store).refresh()
            print("Refreshed repo trees for " + str(count) + " workspace(s).")
        elif command == "setup-sidebar":
            from sidebar import install_sidebar
            config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            config = Path(os.environ.get("HERDR_CONFIG_PATH", config_home / "herdr" / "config.toml")).expanduser()
            changed = install_sidebar(config)
            if os.environ.get("HERDR_ENV") == "1":
                Herdr().call("server", "reload-config")
                Launcher(store).refresh()
                print("Repo trees are enabled in Spaces.")
            else:
                print(("Updated " if changed else "Already configured: ") + str(config))
                print("Reload Herdr's configuration to show the repo trees.")
        elif command == "ui":
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise UserError("The picker needs an interactive terminal. Use --help for command-line options.")
            try:
                from tui import run
            except ImportError:
                raise UserError("Your Python installation needs the standard curses module for the terminal picker.") from None
            message = run(store, Launcher(store))
            if message:
                print(message)
        elif command == "list":
            for item in store.load():
                print(item.id + "  " + item.name + "  " + str(len(item.repos)) + " repos  " + AGENTS[item.agent])
        elif command == "create":
            if any(item.name == arguments.name for item in store.load()):
                raise UserError("That workspace name already exists. Edit it in the picker.")
            item = Workspace(uuid4().hex, arguments.name, name_repositories([make_repository(p) for p in arguments.repos]), arguments.agent)
            store.save(item)
            print("Saved " + item.name + ".")
        elif command == "open":
            item = find_workspace(store, arguments.name)
            if arguments.agent:
                item.agent = arguments.agent
            launched = Launcher(store).open(item, new_agent=arguments.new_agent)
            store.save(item)
            print(launched.message)
        elif command == "export":
            item = find_workspace(store, arguments.name)
            output = Path(arguments.output).expanduser()
            data = json.dumps(portable_definition(item), indent=2, ensure_ascii=False) + "\n"
            with output.open("x", encoding="utf-8") as stream:
                stream.write(data)
            print("Exported " + str(output) + ".")
        elif command == "import":
            with Path(arguments.file).expanduser().open(encoding="utf-8") as stream:
                data = json.load(stream)
            item = import_definition(data, arguments.base)
            if any(saved.name == item.name for saved in store.load()):
                raise UserError("That workspace name already exists. Edit the name in the shared file before importing.")
            item.agent = arguments.agent
            store.save(item)
            print("Imported " + item.name + ".")
    except (UserError, OSError, ValueError) as error:
        print("Workspaces: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
