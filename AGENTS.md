# Agent instructions — shani-backup

This file applies to any AI coding assistant working in this repository
(Claude Code, opencode, Kilo Code, Cursor, Aider, or similar). Read this
before editing, and follow the verification steps before calling any change
done.

## What this repo is

A Python backup utility for Shanios: a CLI and GTK4 UI (`shani_backup`)
that manages **btrfs** snapshots (see `btrfs.py`), with a `config.py`
(GSettings-backed) and `ui/main_window.py`. It is **not** a web site and
has no build step — it is a desktop app consumed by the OS shell. The
repo uses conventional commits (`commitizen`/`.cz.toml` present) and Renovate
(`.renovate.json`, `renovate.json` present) for dependency updates.

## Empirical verification (mandatory)

**Reading code is analysis; running code is verification.** A change is not
verified by reading the diff, running `bash -n`, or confirming it "looks
correct." It is verified by observing the actual behavior of the real
thing in the real environment — built, served, deployed, signed, running.
If you haven't seen it work (or fail) for real, it isn't verified.

## Verification for any change

There is no `tests/` directory and no `.github/workflows/` today, so the
only automated gate available is the interpreter. Before calling a change
done, be able to state the specific observable pass condition ("`--help`
exits 0 and lists the `snapshot`/`restore` subcommands", not "should
work") and which command below actually proves it:

```bash
# 1. Compile-check every module you touched (catches syntax/NameError
#    import-time issues pyflakes would miss without a flake config here).
python3 -m py_compile src/shani_backup/*.py src/shani_backup/ui/*.py

# 2. Smoke the CLI entry path end-to-end — this is a backup utility, so
#    prefer a dry/dry-run path or a `--help` argument over touching real
#    btrfs subvolumes. A regression that breaks command dispatch is the
#    most likely regression here.
PYTHONPATH=src python3 -m shani_backup.cli --help

# 3. Construct the real GTK4 objects — catches API-removed/renamed bugs
#    that py_compile can't see (this repo already shipped an Adwaita API
#    regression that a source read missed). The schema must be compiled
#    first or the window fails at runtime, not import:
mkdir -p /tmp/shani-schema && cp data/*.xml /tmp/shani-schema/ &&
  glib-compile-schemas /tmp/shani-schema/
XDG_SCHEMA_DIRS=/tmp/shani-schema PYTHONPATH=src python3 -c "
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from shani_backup.ui.main_window import ShaniBackupWindow, ShaniBackupApp
from gi.repository import Gtk
app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', lambda a: (ShaniBackupWindow(), a.quit()))
app.run([])
"
```

The window class is `ShaniBackupWindow` (an `Adw.Window`), not `MainWindow`
— there is no `MainWindow` symbol in this repo, and importing one is an
`ImportError`. The GUI entry point is `ShaniBackupApp.do_activate()` in
`ui/main_window.py`; `cli.py::main()` is CLI dispatch only and never
builds a window.

For an end-to-end check of snapshot logic, point the CLI at a throwaway
btrfs subvolume on loopback — never against the user's real data. If the
repo gains a test suite or CI, prefer those and update this section.

## Commit discipline

Before composing a commit message, run `git log --oneline -20` (and `git
log -5 -- <touched paths>` for the files you changed) and match the
existing style — subject shape, scope prefixes, body detail level —
rather than writing in a generic format.

## Boundaries

- ✅ **Always**: `py_compile` + the CLI `--help` smoke test before calling a
  change done (see above).
- ⚠️ **Ask first**: adding a `LICENSE` file — needs the maintainer's actual
  license choice (see "Known gaps" below), not a default guess.
- 🚫 **Never**: point this CLI or its snapshot/restore logic at a real user's
  btrfs subvolumes to test a change — use a throwaway loopback subvolume
  only. This is a backup tool; a bug exercised against real data is exactly
  the failure mode it exists to prevent, not a place to find one.

## Audit-verified known issues (confirmed present 2026-09-22)

- **`rclone` declared dependency with no usage anywhere — FIXED in packaging (2026-09-23, `shani-pkgbuilds/shani-backup` pkgrel 2, not yet committed there).** Removed from `depends=()` and `pkgdesc`; verified by a real `./make_pkg.sh shani-backup` build (`.PKGINFO` no longer lists it) and a real `pacman -U` into a clean `archlinux:latest` container (rclone not pulled in; `pacman -R` also clean). `data/shani-backup.desktop:9`'s `Keywords=` no longer lists `rclone` either (2026-09-23, `desktop-file-validate` clean). Note the PKGBUILD pins `#commit=`, so the keyword change only ships after that pin is bumped. Original finding: `shani-pkgbuilds/shani-backup/PKGBUILD:10` lists `rclone` in `depends=()`, but `grep -rn rclone src/ data/` returns zero matches — this repo shells out to `btrfs` (snapshots) and `restic` (directory backup) only, never `rclone`. Every install pulls in a ~30MB binary nothing uses. Confirmed by grep, not by assumption. Fix: remove `rclone` from `depends=()` and bump `pkgrel`.
- **`shani-backup-scheduler.service` referenced by the `.install` hook but doesn't exist — FIXED (2026-09-23, same pkgrel 2).** `pre_remove()` dropped entirely (it only held those two lines); the real remove in the same container ran the remaining hooks with rc=0. Original finding: `shani-pkgbuilds/shani-backup/shani-backup.install:14-15` runs `systemctl stop/disable shani-backup-scheduler.service` in `pre_remove`, but no such unit ships anywhere — `grep -rn shani-backup-scheduler src/ data/` is empty, and there's no `systemd/` tree in this repo. Harmless (`|| true` on both), but it's dead weight in the install script and misleads anyone reading it into thinking a scheduler service exists. Fix: drop those two lines.
- **README + AGENTS.md both name the window class `MainWindow` — it's `ShaniBackupWindow`.** `src/shani_backup/ui/main_window.py:45` defines `class ShaniBackupWindow(Adw.Window)`; there is no `MainWindow` symbol anywhere (`grep -rn MainWindow .` excluding `.git` matches only prose). The GTK4 app class is `ShaniBackupApp(Adw.Application)` at `main_window.py:1544`, with its own `main()` entry point there — `cli.py:main()` is the CLI dispatch only and never constructs a window. Anyone following the docs to subclass or test the window will hit `ImportError`. Both files corrected to the real names.
- **No `setup.py`/`pyproject.toml` — the app is not pip-installable.** The only install path is the `shani-backup` PKGBUILD wrapper (`/usr/bin/shani-backup` → `sys.path.insert(0, "/usr/share/shani-backup/src")` → `shani_backup.cli.main`). The README's `pip install -e .` instructions are impossible to follow as written. `PYTHONPATH=src python3 -m shani_backup.cli ...` is the real local invocation.
- **Schema compiles but isn't installed in a bare checkout.** `glib-compile-schemas` on `data/org.shani.backup.gschema.xml` produces `gschemas.compiled` cleanly (verified), so the schema itself is valid — but the file ships under `data/`, not `/usr/share/glib-2.0/schemas/`, so running the app from source (`PYTHONPATH=src`) fails with `GLib-GIO-ERROR **: Settings schema 'org.shani.backup' is not installed` (exit 133) unless `XDG_SCHEMA_DIRS` is pointed at a compiled copy. The PKGBUILD's `post_install` does the real compile at package time; this is expected for a source checkout, but don't read a clean `app.run()` from source as a pass signal.
- **No LICENSE file (documented gap, needs maintainer decision).** `shani-pkgbuilds/shani-backup/PKGBUILD:9` declares `license=('GPL-3.0-only')`, but this repo ships no `LICENSE` file. The PKGBUILD's claim can't be verified against the tree. Add the actual file or correct the identifier — don't guess.

## Known gaps (needs maintainer decision)
- **No LICENSE** — this repo (like `shani-website`, `shani-docs`)
  currently ships no license file. The web content repos
  carry MIT only in `shani-blog`; OS-side repos carry GPL-3.0. The
  PKGBUILD already claims `GPL-3.0-only`, so the likely answer is to add
  that file rather than change the identifier — but confirm with the
  maintainer before committing either way.
- **No CI / no tests** — `py_compile` + a CLI smoke test are the only
  automated gates. Adding a tiny snapshot-logic test (against a throwaway
  loopback btrfs subvolume) would catch the regressions this utility is
  most prone to. Note: the GTK4 object-construction check below needs the
  schema compiled first (`XDG_SCHEMA_DIRS`), or it errors at runtime
  rather than import.
- **Single `main_window.py`** — the GTK4 UI is one file (no templates);
  a regression in signal wiring is only visible in a real GUI run.

## Cross-repo impact
`shani-pkgbuilds/shani-backup/PKGBUILD` packages this repo's `src/` and
`data/` trees verbatim (with a mode normalization pass — see the PKGBUILD's
`find ... -exec chmod` lines; the working tree has world-writable files the
package deliberately fixes). Any change here needs that PKGBUILD's `_commit`
pinned in `source=` and its `pkgrel` bumped, or the packaged artifact silently
ships stale content — there is no automated checksum-sync check between the
two repos (unlike `shani-settings`/`shani-keyring`, which have one).

Standalone at the application level — no other shani repo imports or depends
on `shani_backup`. The `.install` script's scheduler hook is the only
cross-cutting reference and it points at a nonexistent unit (see above).
