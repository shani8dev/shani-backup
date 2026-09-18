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
done:

```bash
# 1. Compile-check every module you touched (catches syntax/NameError
#    import-time issues pyflakes would miss without a flake config here).
python3 -m py_compile src/shani_backup/*.py src/shani_backup/ui/*.py

# 2. Smoke the CLI entry path end-to-end — this is a backup utility, so
#    prefer a dry/dry-run path or a `--help` argument over touching real
#    btrfs subvolumes. A regression that breaks command dispatch is the
#    most likely regression here.
python3 -m shani_backup.cli --help
```

For an end-to-end check of snapshot logic, point the CLI at a throwaway
btrfs subvolume on loopback — never against the user's real data. If the
repo gains a test suite or CI, prefer those and update this section.

## Boundaries

- ✅ **Always**: `py_compile` + the CLI `--help` smoke test before calling a
  change done (see above).
- ⚠️ **Ask first**: adding a `LICENSE` file — needs the maintainer's actual
  license choice (see "Known gaps" below), not a default guess.
- 🚫 **Never**: point this CLI or its snapshot/restore logic at a real user's
  btrfs subvolumes to test a change — use a throwaway loopback subvolume
  only. This is a backup tool; a bug exercised against real data is exactly
  the failure mode it exists to prevent, not a place to find one.

## Audit status

**NOT YET AUDITED.** This repo has no audit-verified known-issues inventory
yet (unlike its siblings). Do not treat this file's absence earlier as an
"everything is fine" signal — run your own check before relying on this
utility for real backups. If you audit it, add a "## Audit-verified known
issues" section and a `## Cross-repo impact" section here, matching the
sibling repos' shape.

## Known gaps (needs maintainer decision)
- **No LICENSE** — this repo (like `shani-website`, `shani-docs`,
  `shani-chronoa`) currently ships no license file. The web content repos
  carry MIT only in `shani-blog`; OS-side repos carry GPL-3.0. Decide and
  add a `LICENSE`.
- **No CI / no tests** — `py_compile` + a CLI smoke test are the only
  automated gates. Adding a tiny snapshot-logic test (against a throwaway
  loopback btrfs subvolume) would catch the regressions this utility is
  most prone to.
- **Single `main_window.py`** — the GTK4 UI is one file (no templates);
  a regression in signal wiring is only visible in a real GUI run.

## Cross-repo impact

Standalone — no other shani repo imports or depends on `shani_backup`.
