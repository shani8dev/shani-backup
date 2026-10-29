#!/usr/bin/env python3
"""
Backups of your home folder to another disk, with restic.

The snapshots in home.py live on the same disk; this is the copy that survives
that disk. Before this module the app called `restic backup` with no repository
ever initialised and no password given, so restic stopped at its password prompt
every time. Now:

* **The password lives in the keyring** (Secret Service: gnome-keyring, KWallet),
  stored with `secret-tool` under {app: shani-backup, repository: <path>}, and
  restic reads it through RESTIC_PASSWORD_COMMAND - never written to a file,
  never on a command line. A caller that already set RESTIC_PASSWORD* (the
  tests, a script) is left alone.
* **What is backed up** is the home folder, without caches (`--exclude-caches`
  honours CACHEDIR.TAG; ~/.cache and the trash are left out by name) and
  without any folder holding a `.nobackup` file.
* **Old backups are thinned** after a scheduled run: 7 daily, 4 weekly, 12
  monthly (`restic forget --prune`).
* **The schedule is a systemd user timer** (shani-backup-restic.timer): it runs
  as the person, so the keyring is theirs and unlocked while they are logged in.
"""

import json
import os
import shlex
import shutil
import subprocess
from typing import Callable, NamedTuple, Optional

from .btrfs import Result

APP = "shani-backup"
TIMER = "shani-backup-restic.timer"
KEEP = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "12"]


def home_dir() -> str:
    return os.path.expanduser("~")


def excludes() -> list:
    h = home_dir()
    return ["--exclude-caches", "--exclude-if-present", ".nobackup",
            "--exclude", os.path.join(h, ".cache"),
            "--exclude", os.path.join(h, ".local/share/Trash"),
            "--exclude", os.path.join(h, ".snapshots")]


def _env(repo: str) -> dict:
    env = dict(os.environ, RESTIC_REPOSITORY=repo)
    if not any(k in env for k in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND")):
        env["RESTIC_PASSWORD_COMMAND"] = shlex.join(["secret-tool", "lookup", "app", APP, "repository", repo])
    return env


def _restic(repo: str, *args, timeout=None, **kw):
    if not shutil.which("restic"):
        return None, "restic is not installed"
    try:
        r = subprocess.run(["restic", *args], env=_env(repo), capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return None, "restic did not answer in time - is the drive asleep or gone?"
    return r, ""


def _why(r) -> str:
    lines = [ln for ln in (r.stderr or r.stdout or "").strip().splitlines() if ln.strip()]
    text = lines[-1].strip() if lines else f"restic exited {r.returncode}"
    if "wrong password" in text or "no key found" in text:
        return "The password does not open this backup"
    if "reading password" in text or "secret-tool" in text or "Resolving password failed" in text:
        return "The backup password is not in your keyring - set the drive up again"
    return text.removeprefix("Fatal: ")


# ---- the place ---------------------------------------------------------------------------
class Place(NamedTuple):
    path: str
    reachable: bool          # the folder is there (the drive is plugged in)
    is_repo: bool            # it already holds a restic backup


def place(repo: str) -> Place:
    if not repo:
        return Place("", False, False)
    there = os.path.isdir(repo)
    return Place(repo, there, there and os.path.isfile(os.path.join(repo, "config")))


def store_password(repo: str, password: str) -> Result:
    if not shutil.which("secret-tool"):
        return Result(False, "secret-tool is not installed (libsecret)")
    r = subprocess.run(["secret-tool", "store", "--label", f"Backup password for {repo}",
                        "app", APP, "repository", repo], input=password, capture_output=True, text=True)
    return Result(r.returncode == 0, (r.stderr or "").strip() or ("" if r.returncode == 0 else "the keyring refused it"))


def set_up(repo: str, password: str) -> Result:
    """Make `repo` a backup: a new one (init) or an existing one (the password
    is checked by reading it). Either way the password goes to the keyring."""
    if not password:
        return Result(False, "A password is needed: without it nobody can read the backup, you included")
    os.makedirs(repo, exist_ok=True)
    existing = place(repo).is_repo
    env_pw = {"RESTIC_PASSWORD": password}
    if not shutil.which("restic"):
        return Result(False, "restic is not installed")
    args = ["cat", "config"] if existing else ["init"]
    r = subprocess.run(["restic", *args], env=dict(os.environ, RESTIC_REPOSITORY=repo, **env_pw),
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return Result(False, _why(r))
    saved = store_password(repo, password)
    if not saved.ok:
        return saved
    return Result(True, "opened" if existing else "created")


# ---- backing up ---------------------------------------------------------------------------
def backup(repo: str, progress: Optional[Callable[[float, str], None]] = None, source: Optional[str] = None) -> Result:
    """Back up the home folder (or `source`). `progress(fraction, current_file)`
    is called as restic reports. Result.detail is the new snapshot's short id."""
    if not shutil.which("restic"):
        return Result(False, "restic is not installed")
    p = place(repo)
    if not p.reachable:
        return Result(False, f"{repo} is not there - is the drive plugged in?")
    if not p.is_repo:
        return Result(False, f"{repo} is not set up for backups yet")
    src = source or home_dir()
    proc = subprocess.Popen(["restic", "backup", "--json", src, *excludes()], env=_env(repo),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    snapshot = ""
    for line in proc.stdout:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("message_type") == "status" and progress:
            progress(float(m.get("percent_done", 0)), (m.get("current_files") or [""])[0])
        elif m.get("message_type") == "summary":
            snapshot = m.get("snapshot_id", "")[:8]
    err = proc.stderr.read()
    proc.wait()
    # restic 3: some files could not be read; the snapshot was still made
    if proc.returncode == 0 or (proc.returncode == 3 and snapshot):
        return Result(True, snapshot)
    return Result(False, _why(subprocess.CompletedProcess([], proc.returncode, "", err)))


def thin(repo: str) -> Result:
    r, why = _restic(repo, "forget", "--prune", *KEEP, timeout=3600)
    if r is None:
        return Result(False, why)
    return Result(r.returncode == 0, "" if r.returncode == 0 else _why(r))


# ---- what is there --------------------------------------------------------------------------
class Snap(NamedTuple):
    id: str
    time: str                # ISO, from restic
    paths: list
    size: int                # bytes added by this backup, when restic says


def snapshots(repo: str):
    """(ok, [Snap] newest first, reason)."""
    r, why = _restic(repo, "snapshots", "--json", timeout=60)
    if r is None:
        return False, [], why
    if r.returncode != 0:
        return False, [], _why(r)
    try:
        data = json.loads(r.stdout or "[]") or []
    except ValueError:
        return False, [], "restic's answer was not JSON"
    out = [Snap(s.get("short_id") or s["id"][:8], s.get("time", ""), s.get("paths", []),
                int((s.get("summary") or {}).get("data_added", 0))) for s in data]
    return True, sorted(out, key=lambda s: s.time, reverse=True), ""


def restore(repo: str, snap_id: str, target: str) -> Result:
    """Everything in one backup, into `target` - a new folder, never over the
    files in your home."""
    if os.path.exists(target) and os.listdir(target):
        return Result(False, f"{target} is not empty")
    r, why = _restic(repo, "restore", snap_id, "--target", target, timeout=None)
    if r is None:
        return Result(False, why)
    return Result(r.returncode == 0, target if r.returncode == 0 else _why(r))


# ---- the schedule (a systemd user timer) --------------------------------------------------------
def _user_systemctl(*args):
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def schedule_on() -> bool:
    r = _user_systemctl("is-enabled", TIMER)
    return bool(r) and r.stdout.strip() == "enabled"


def schedule_time() -> str:
    """HH:MM from this person's drop-in, else the timer's own default."""
    try:
        with open(_dropin()) as f:
            for line in f:
                if line.startswith("OnCalendar=") and line.strip() != "OnCalendar=":
                    return line.strip().rsplit(" ", 1)[-1][:5]
    except OSError:
        pass
    return "12:30"


def _dropin() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home_dir(), ".config")
    return os.path.join(base, "systemd", "user", TIMER + ".d", "time.conf")


def set_schedule(on: bool, hhmm: Optional[str] = None) -> Result:
    if hhmm:
        if not (len(hhmm) == 5 and hhmm[2] == ":" and hhmm[:2].isdigit() and hhmm[3:].isdigit()
                and int(hhmm[:2]) < 24 and int(hhmm[3:]) < 60):
            return Result(False, f"{hhmm!r} is not a time of day (HH:MM)")
        os.makedirs(os.path.dirname(_dropin()), exist_ok=True)
        with open(_dropin(), "w") as f:
            f.write(f"[Timer]\nOnCalendar=\nOnCalendar=*-*-* {hhmm}:00\n")
        _user_systemctl("daemon-reload")
    r = _user_systemctl("enable" if on else "disable", "--now", TIMER)
    if r is None or r.returncode != 0:
        return Result(False, (r.stderr.strip() if r else "") or "systemctl --user did not answer")
    return Result(True, "")
