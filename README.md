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
- **Directory backup & restore** — `restic`-backed backup of source
  directories to a repository, with restore of the latest snapshot.
- **Scheduled backups** — enable/disable a daily backup scheduler through
  GSettings.
- **GSettings-backed configuration** — `config.py` talks to the
  `org.shani.backup` schema; no config files to edit by hand.
- **GTK4 UI** — `ui/main_window.py` exposes snapshots, backup jobs, and
  scheduler status in a native window.

## Architecture

```
┌──────────────────────────────────────┐
│         GTK4 UI (main_window.py)     │
└──────────────────┬───────────────────┘
                   │
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
paths the CLI does, so the two always agree. Snapshot operations shell out
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
- **Single `main_window.py`** — the GTK4 UI is one file; a regression in
  signal wiring is only visible in a real GUI run.

## Cross-repo impact

Standalone — no other shani repo imports or depends on `shani-backup`.