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

## Test harness: shani-testbed (use it - and improve it, never invent around it)

The ecosystem's real test harness is the sibling repo **`../shani-testbed`**
(read its `README.md` and `AGENTS.md`). It installs a real ShaniOS image with
the real installer, boots its slots (`systemd-nspawn`, and UEFI + TPM VMs),
runs real deploys and rollbacks, drives GUI apps through their accessibility
tree, and checks web pages in a real headless browser. Every command runs from
`../shani-install-media`, which provides the builder container:

```bash
cd ../shani-install-media
./run_in_container.sh build.sh test <command> ...   # `... test help` lists them all
```

**If the check you need does not exist, add it to shani-testbed - do not invent
around it.** A one-off script in this repo, a scratchpad, or a heredoc piped
into a container is lost when the session ends, and the next agent re-derives
it. Extend the harness instead (see "Extend the harness" in its AGENTS.md):

- an in-slot check -> `shani-testbed/slot-tests/<name>.sh` (`# slot-test-mode: boot`,
  prints `RESULT <name> PASS|FAIL|SKIP` lines), run by `slot-test <slot> <name>`;
- a GUI interaction or assertion -> an `app` action in `lib/app.sh`, or a walk
  through a real app as `app-scripts/<app>.actions`;
- a web check -> `lib/web_client.py`;
- a new way to boot, drive or observe -> a command or option in `lib/`;

each with a negative control (a check that cannot fail is not a check), its
self-test (`tests/run-app-actions.sh`, `tests/run-web-client.sh`, ...), and the
`usage` + README updated. One harness run at a time: disk-touching commands
take `disk/.testbed.lock` and a second run is refused. Plain nspawn boots see
the image's whole `/var`; real boots have an empty tmpfs `/var`
(`systemd.volatile=state`) - use `slot-test --volatile`, or a real UEFI boot
with `iso-install --boot-only --console-exec=CMD`, for anything touching `/var`.

### What to run for this repo

- `slot-test <slot> repo-pytest` runs this repo's own test suite on the
  image's Python, PyGObject, GTK4 and libadwaita (not the CI runner's).
- `app <slot> --run=shani-backup --strict --wait-window=Backup:60 --a11y-lint
  --expect-clean-log --monkey=30` drives the real GUI in the image.
- An unpublished build: add `--local-pkg=<built .pkg.tar.zst>`.

## Verification for any change

`tests/test_backup.py` runs the checks below as a suite (CI:
`.github/workflows/ci.yml` - GTK under Xvfb, the btrfs tests as root on a
throwaway loopback fs): `python3 -m pytest tests/ -v`, plus
`sudo python3 -m pytest tests/ -v -k btrfs` for the snapshot tests. Before calling a change
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
#    (GSETTINGS_SCHEMA_DIR - GLib ignores XDG_SCHEMA_DIRS, verified)
mkdir -p /tmp/shani-schema && cp data/*.xml /tmp/shani-schema/ &&
  glib-compile-schemas /tmp/shani-schema/
GSETTINGS_SCHEMA_DIR=/tmp/shani-schema PYTHONPATH=src python3 -c "
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
test suite's btrfs tests do exactly that (root only).

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
- **No `setup.py`/`pyproject.toml` — the app is not pip-installable.** The only install path is the `shani-backup` PKGBUILD wrapper (`/usr/bin/shani-backup` → `sys.path.insert(0, "/usr/share/shani-backup/src")` → `shani_backup.cli.main`). The README's `pip install -e .` instructions were removed (the README no longer claims it). `PYTHONPATH=src python3 -m shani_backup.cli ...` is the real local invocation.
- **Schema compiles but isn't installed in a bare checkout.** `glib-compile-schemas` on `data/org.shani.backup.gschema.xml` produces `gschemas.compiled` cleanly (verified), so the schema itself is valid — but the file ships under `data/`, not `/usr/share/glib-2.0/schemas/`, so running the app from source (`PYTHONPATH=src`) fails with `GLib-GIO-ERROR **: Settings schema 'org.shani.backup' is not installed` (exit 133) unless `GSETTINGS_SCHEMA_DIR` is pointed at a compiled copy. The PKGBUILD's `post_install` does the real compile at package time; this is expected for a source checkout, but don't read a clean `app.run()` from source as a pass signal.
- **`list_snapshots` returned paths that did not exist, on every real
  filesystem — found 2026-10-14 by `slot-tests/backup-snapshots.sh` in
  shani-testbed, on a real slot, and FIXED.** Shanios root is btrfs, so this is
  the main path and not an edge case. `btrfs subvolume list <path>` names every
  subvolume of the filesystem **relative to the subvolume that contains**
  `<path>` — not relative to `<path>`, and not relative to the filesystem root.
  The implementation joined those names onto `findmnt -T`'s mount point, which
  is wrong in both directions, and both were measured rather than argued:

  | asked about | old answer |
  |---|---|
  | a mount root | **every subvolume in the filesystem, at paths that do not exist** — 27 of them on the probe slot, including `@blue`, `@green` and `@home` |
  | a subvolume inside that mount | **nothing at all** — every name failed the prefix filter |

  The consequence is not cosmetic and it is a safety problem: the Snapshots tab
  hands a returned path straight to `btrfs subvolume delete`, so the list was
  offering deletions of paths that do not exist, while presenting the slot's own
  system subvolumes as if they were the user's backups. The base is now the
  queried path itself (the names are relative to the containing subvolume, so
  that is where they belong) and every candidate is kept only if it **exists** —
  a `stat`, not a subprocess, so it costs nothing on the GTK main thread. The
  rejected entries are counted and reported as `elsewhere` in the JSON, because
  they are real subvolumes of the filesystem that simply are not under the path
  asked about; a zero count beside an empty list would otherwise mean "and the
  other 26 are somewhere you did not ask about".

  **Two limits are deliberate, not oversights.** A path that is neither a
  subvolume nor a mount root cannot have its subvolumes named at all — the names
  are relative to a subvolume whose absolute location this process cannot know
  without mounting subvolume 5 — so the listing says so in its `note` instead of
  returning a bare empty list. And on a slot the query for `/` answers
  `ERROR: not a btrfs filesystem: /` (nspawn's root is not a subvolume), which
  is reported as a refusal rather than as an empty machine.

  **The probe measures six things and every one of them changed the fix**, which
  is why it is a harness check and not a note: it proved that `create_snapshot`
  must check `is_btrfs_path` (a plain directory inside a subvolume is refused by
  btrfs, and most of `/home` is one); that a snapshot cannot cross a filesystem
  (measured against a tmpfs); that the tab's sibling-of-source default is fine
  for an ordinary subvolume but has nowhere to go for a top-level one; and — the
  one that was **not** a defect — that deleting a `--readonly` snapshot works
  fine on btrfs-progs 7.1, so the Delete button can remove what Create makes.
  It also found the slot has **no loop devices at all** (`/dev/loop-control`
  absent), so `mount -o loop` answers ENOENT and the probe falls back to a
  scratch subvolume under the slot's own root, which it removes on exit. Two
  probe bugs of its own were caught by running it: a fixture that created
  `data/inner` *before* `subvolume create data` (so claims 2 and 3 were answered
  about a path that was not a subvolume), and `printf %q` emitting bare paths
  into a generated Python string, which reported three SyntaxErrors as findings
  about this code. **A probe whose fixture is wrong is worse than no probe.**
- **No LICENSE file (documented gap, needs maintainer decision).** `shani-pkgbuilds/shani-backup/PKGBUILD:9` declares `license=('GPL-3.0-only')`, but this repo ships no `LICENSE` file. The PKGBUILD's claim can't be verified against the tree. Add the actual file or correct the identifier — don't guess.

## Known gaps (needs maintainer decision)
- **No LICENSE** — this repo (like `shani-website`, `shani-docs`)
  currently ships no license file. The web content repos
  carry MIT only in `shani-blog`; OS-side repos carry GPL-3.0. The
  PKGBUILD already claims `GPL-3.0-only`, so the likely answer is to add
  that file rather than change the identifier — but confirm with the
  maintainer before committing either way.
- **Still not pip-installable** — there is no `setup.py`/`pyproject.toml`,
  so the only install path is the PKGBUILD wrapper. (This bullet used to
  also claim the README's `pip install -e .` instructions were impossible to
  follow; the README no longer contains them, so that half was stale.)
- **Single `main_window.py`** — the GTK4 UI is one file (no templates);
  a regression in signal wiring is only visible in a real GUI run.

### Closed since this list was written (2026-10-14)

- **"No CI / no tests" is no longer true, and the entry above was the stale
  artefact.** `tests/test_backup.py` runs CLI dispatch, the GSettings schema,
  real GTK4 window construction and a real btrfs snapshot
  create/list/delete against a throwaway loopback filesystem, under
  `.github/workflows/ci.yml` (lint, test on Xvfb, secrets scan) via
  `shani-ci-commons`. The btrfs tests skip without root, which is why CI runs
  them a second time as root. What remains uncovered is noted where it remains
  uncovered — see the btrfs probe entry below.
- **The five GUI/CLI defects found on 2026-10-14 are fixed, each with a test
  and a control that was run:** the header's status indicator could never leave
  "Ready" (its name was assigned a second time by the Backup tab, so every
  update went to the card's dot instead); the Restore tab opened empty and
  greyed until Refresh was pressed; two restore checkboxes were read by nothing
  and are gone; the version string was hardcoded twice; and **`_delete_snapshot`
  destroyed a subvolume on a single click with no confirmation**, which on a
  btrfs OS means one mis-click removes a system subvolume. It now confirms with
  the full path, and refuses to delete the path the list is showing.

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
