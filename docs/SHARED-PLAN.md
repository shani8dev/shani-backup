# shani-backup: its part of the shared architecture

The plan for all repos is `../shani-shell/docs/SHARED-ARCHITECTURE.md` (https://github.com/shani8dev/shani-shell/blob/master/docs/SHARED-ARCHITECTURE.md). Read it first; its rules apply
here. This file is only this repo's part; change the plan there, not here.

**Role:** backups: the `shani-backup` command and library, plus a GTK app that becomes `shani-backup-gtk` (phase 4).

**Do as much of this as your task allows** (never breaking a consumer), and
add a line to the plan's Status section when you finish something:

- Keep all logic in the command/library (`cli.py` and the GTK-free modules); the GTK files only show it.
- Every `--json` mode prints JSON even when it fails (today `backups --json` aborts when its GSettings schema is missing).
- Make the GTK part separable so niri installs the command without GTK.
