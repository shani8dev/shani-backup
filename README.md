# shani-backup

Backup utility for Shanios: a CLI and GTK4 UI that manages **btrfs**
snapshots and directory backups.

`shani-backup` is a Python desktop app consumed by the Shanios OS shell.
It is **not** a web site and has no build step — install the package and
run the CLI or launch the GTK4 window. The repo uses conventional commits
(`commitizen` / `.cz.toml`) and Renovate for dependency updates.

## Features

- **btrfs snapshot lifecycle** — create, list, and delete snapshots of any
  subvolume (read-only or writable), backed by `btrfs.py`.
- **Daily home snapshots** — `home.py` and `shani-backup-home.timer`: once a
  day, a read-only snapshot covering every home, kept in `/home/.snapshots`
  (14 days, fewer when the disk is nearly full). On ShaniOS a home is a plain
  folder inside the `@home` subvolume, so those are covered by one snapshot of
  `/home` in which each home keeps its own owner and mode; a home that is its
  own subvolume (`useradd --btrfs-subvolume-home`) gets its own, in a folder
  only its owner can open. Same disk: an undo for deleted files, not a
  backup. The idea is NAZY-OS/btrfs-home-user-snapshots'; the code is new.
- **Pre-deploy home snapshots** — `home predeploy` is called by
  `shani-deploy` immediately before it writes to the standby slot, so an
  update always has a same-minute undo instead of yesterday's `daily`.
  Separate from `daily` because that one is idempotent per day: an afternoon
  update would be told the morning's snapshot already exists. The newest two
  are kept (fewer on a nearly full disk); daily and manual snapshots are not
  touched.
- **Directory backup & restore** — `restic`-backed backup of source
  directories to a repository, with restore of the latest snapshot.
- **Scheduled backups** — enable/disable a daily backup scheduler through
  GSettings.
- **GSettings-backed configuration** — `config.py` talks to the
  `org.shani.backup` schema; no config files to edit by hand.
- **GTK4 UI** — `ui/main_window.py`'s `ShaniBackupWindow` (an
  `Adw.Window`) exposes snapshots, backup jobs, and scheduler status in a
  native window, driven by the `ShaniBackupApp(Adw.Application)` entry point
  in the same file.

## Architecture

```
┌──────────────────────────────────────┐
│  GTK4 UI (ui/main_window.py)         │
│  ShaniBackupApp → ShaniBackupWindow  │
└──────────────────┬───────────────────┘
                   │  calls the same code paths as the CLI
┌──────────────────▼───────────────────┐
│        shani_backup.cli              │
│   (argparse dispatch, subcommands)   │
└──────┬──────────────────┬────────────┘
       │                  │
       ▼                  ▼
  btrfs.py          config.py (GSettings)
  create/list/      org.shani.backup
  delete snapshots  backup-location
                   enable-scheduler
                   schedule-time
```

`cli.py` is the single dispatch point. The GTK4 UI calls the same code
paths the CLI does, so the two always agree — the UI is not a separate
`MainWindow` class; it's `ShaniBackupWindow` constructed by
`ShaniBackupApp.do_activate()`. Snapshot operations shell out
to the `btrfs` tool; directory backups shell out to `restic`.

## CLI

```bash
# Create a btrfs snapshot of a subvolume (read-only by default)
shani-backup snapshot create / /mnt/snapshots/root@2026-09-20
shani-backup snapshot create / /mnt/snapshots/root@2026-09-20 --readonly

# List snapshots under a path
shani-backup snapshot list /

# Delete a snapshot
shani-backup snapshot delete /mnt/snapshots/root@2026-09-20

# Home snapshots (see home.py)
shani-backup home list                 # yours, newest first (--json for Cassini)
sudo shani-backup home snapshot        # one now
sudo shani-backup home daily --keep 14 # what the timer runs
sudo shani-backup home predeploy --keep 2 # what shani-deploy runs before an update
sudo shani-backup home remove manual-2026-10-29_14-05-09

# Directory backup via restic
shani-backup backup /home/user --destination /mnt/backups/repo

# Restore the latest restic snapshot
shani-backup restore /mnt/backups/repo /tmp/restore

# Scheduler management
shani-backup schedule --enable
shani-backup schedule --disable
shani-backup schedule            # show status + config
```

## Configuration

All settings live in the `org.shani.backup` GSettings schema:

```bash
# Inspect every key
dconf dump /org/shani/backup/

# Backup repository location
gsettings set org.shani.backup backup-location /mnt/backups/repo

# Daily scheduler
gsettings set org.shani.backup enable-scheduler true
gsettings set org.shani.backup schedule-time '02:00'
```

## Development

```bash
# Compile-check every module
python3 -m py_compile src/shani_backup/*.py src/shani_backup/ui/*.py

# Smoke the CLI dispatch path (safe — no btrfs touched)
PYTHONPATH=src python3 -m shani_backup.cli --help
```

## Verification

Per the Shanios contribution guide, changes are verified by running the
real thing, not by reading the diff:

```bash
# 1. Compile-check every module you touched
python3 -m py_compile src/shani_backup/*.py src/shani_backup/ui/*.py

# 2. Smoke the CLI entry path end-to-end
PYTHONPATH=src python3 -m shani_backup.cli --help
```

For an end-to-end snapshot check, point the CLI at a throwaway btrfs
subvolume on loopback — **never** against a real user's data. This is a
backup tool; a bug exercised against real data is exactly the failure
mode it exists to prevent.

## Known gaps

- **No LICENSE** — needs the maintainer's actual license choice.
- **No CI / no tests** — `py_compile` + a CLI smoke test are the only
  automated gates.
- **Single `main_window.py`** — the GTK4 UI is one file (no templates);
  a regression in signal wiring is only visible in a real GUI run. The
  window class is `ShaniBackupWindow`, not `MainWindow` — there is no
  `MainWindow` symbol in this repo.

## Cross-repo impact

Standalone — no other shani repo imports or depends on `shani-backup`.