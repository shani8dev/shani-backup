#!/usr/bin/env python3
"""
Snapshots of people's home folders, kept on the same disk under /home/.snapshots.

The idea comes from NAZY-OS/btrfs-home-user-snapshots (AGPL-3.0; nothing of its
code is used here): one root-owned folder per account, which only that account
may read, holding a dated snapshot a day; any number of "manual" ones besides.
What is different here, and why:

* **Read-only snapshots.** A copy the owner can change is not a copy to go back
  to; files are restored by copying them *out*.
* **Homes that are not subvolumes.** On ShaniOS /home is the `@home` subvolume
  and most homes are plain folders inside it, which btrfs cannot snapshot one by
  one. Those homes are covered by one snapshot of /home itself, kept in
  `/home/.snapshots/@home/`. Inside it every home keeps its own owner and mode,
  so nobody reads anybody else's files there either. (A home that *is* its own
  subvolume - `useradd --btrfs-subvolume-home` - is not in that snapshot at all,
  because a snapshot stops at nested subvolumes; it gets its own.)
* **A timer, not a polling daemon.** `shani-backup-home.timer` runs
  `shani-backup home daily` once a day (and at the next boot when a day was
  missed); a day's snapshot already there is not taken again.
* **Old daily snapshots are removed** beyond `--keep` (14 by default), and
  beyond 2 while less than 15% of the disk is free - a snapshot keeps what
  was deleted since, so on a full disk deleting files would free nothing.
  Manual ones are only removed by hand.

Layout:

    /home/.snapshots/                       root 0711
    /home/.snapshots/<user>/                root 0700 + ACL u:<user>:r-x
    /home/.snapshots/<user>/daily-2026-10-29
    /home/.snapshots/<user>/manual-2026-10-29_14-05-09
    /home/.snapshots/@home/                 root 0755 (the names are dates)
    /home/.snapshots/@home/daily-2026-10-29/<user>/...
"""

import datetime as dt
import os
import pwd
import shutil
import stat
import subprocess
from typing import NamedTuple, Optional

from . import btrfs
from .btrfs import Result

HOME = os.environ.get("SHANI_BACKUP_HOME", "/home")
ALL = "@home"                     # the whole-/home snapshots; "@" is not allowed in a user name


def snapshot_root() -> str:
    return os.path.join(HOME, ".snapshots")


class Snapshot(NamedTuple):
    name: str                     # daily-2026-10-29 | manual-2026-10-29_14-05-09
    path: str                     # where *this person's* home is inside it
    whole: bool                   # part of a snapshot of all of /home


def people() -> list:
    """The accounts whose home is /home/<name>: people, not system users."""
    out = []
    for a in pwd.getpwall():
        if a.pw_uid < 1000 or a.pw_name == "nobody" or a.pw_uid == 65534:
            continue
        home = os.path.join(HOME, a.pw_name)
        if os.path.isdir(home) and not os.path.islink(home):
            out.append(a)
    return out


def _safe_dir(path: str, mode: int, create: bool) -> Optional[str]:
    """A real, root-owned folder that no one else may write: made when asked,
    refused (with the reason) when something else is in its place."""
    if os.path.islink(path):
        return f"{path} is a symbolic link; refusing to use it"
    if not os.path.exists(path):
        if not create:
            return None
        os.mkdir(path, mode)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        return f"{path} is not a folder"
    if st.st_uid != 0:
        if not create:
            return f"{path} is not owned by root"
        os.chown(path, 0, 0)
    if create:
        os.chmod(path, mode)
    elif stat.S_IMODE(st.st_mode) & 0o022:
        return f"{path} can be written by others"
    return None


def _person_dir(account, create: bool):
    """(folder, error) - /home/.snapshots/<user>, readable by that account only."""
    err = _safe_dir(snapshot_root(), 0o711, create)
    if err:
        return None, err
    d = os.path.join(snapshot_root(), account.pw_name)
    err = _safe_dir(d, 0o700, create)
    if err:
        return None, err
    if create:
        if not shutil.which("setfacl"):
            return None, "setfacl is not installed (acl), so the folder cannot be opened to its owner only"
        r = subprocess.run(["setfacl", "-m", f"u:{account.pw_uid}:rx,g::---,o::---", d],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout).strip() or "setfacl failed"
    return d, None


def _name(kind: str, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now()
    return f"daily-{now.date().isoformat()}" if kind == "daily" else "manual-" + now.strftime("%Y-%m-%d_%H-%M-%S")


def snapshot_person(user: str, kind: str = "manual", now=None) -> Result:
    """Snapshot one person's home: its own snapshot when the home is a
    subvolume, otherwise a snapshot of all of /home (which covers it)."""
    if os.geteuid() != 0:
        return Result(False, "taking a snapshot needs root (pkexec shani-backup home snapshot)")
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        return Result(False, f"there is no account named {user}")
    home = os.path.join(HOME, account.pw_name)
    if not os.path.isdir(home) or os.path.islink(home):
        return Result(False, f"{home} is not a folder")
    if not btrfs.is_btrfs_path(home):
        return snapshot_whole(kind, now)
    d, err = _person_dir(account, create=True)
    if err:
        return Result(False, err)
    dest = os.path.join(d, _name(kind, now))
    if os.path.lexists(dest):
        return Result(True, f"{dest} already exists") if kind == "daily" else Result(False, f"{dest} already exists")
    r = btrfs.create_snapshot_detailed(home, dest, readonly=True)
    return Result(True, dest) if r.ok else r


def snapshot_whole(kind: str = "manual", now=None) -> Result:
    """One read-only snapshot of /home itself, for every home that is a plain folder."""
    if os.geteuid() != 0:
        return Result(False, "taking a snapshot needs root")
    if not btrfs.is_btrfs_path(HOME):
        return Result(False, f"{HOME} is not a btrfs subvolume, so its homes cannot be snapshotted")
    # @home readable (0755): a snapshot's name is only a date, and each home
    # inside keeps its own owner and mode - with 0711 an ordinary person could
    # not list it, and their Your Files failed (measured in the slot test)
    for path, mode in ((snapshot_root(), 0o711), (os.path.join(snapshot_root(), ALL), 0o755)):
        err = _safe_dir(path, mode, create=True)
        if err:
            return Result(False, err)
    dest = os.path.join(snapshot_root(), ALL, _name(kind, now))
    if os.path.lexists(dest):
        return Result(True, f"{dest} already exists") if kind == "daily" else Result(False, f"{dest} already exists")
    r = btrfs.create_snapshot_detailed(HOME, dest, readonly=True)
    return Result(True, dest) if r.ok else r


def daily(keep: int = 14, now=None) -> list:
    """Today's snapshot for every person (once), then the oldest daily ones
    beyond `keep` removed. [(who, Result)] - who is a user name or "@home"."""
    out = []
    whole_needed = False
    for a in people():
        home = os.path.join(HOME, a.pw_name)
        if btrfs.is_btrfs_path(home):
            out.append((a.pw_name, snapshot_person(a.pw_name, "daily", now)))
        else:
            whole_needed = True
    if whole_needed:
        out.append((ALL, snapshot_whole("daily", now)))
    # a snapshot holds on to what was deleted since, so on a nearly full disk
    # only the newest few are kept (snapper's FREE_LIMIT, in spirit)
    if keep > 0 and low_on_space():
        keep = min(keep, LOW_SPACE_KEEP)
    if keep > 0:
        for d in _snapshot_dirs():
            for old in sorted(n for n in os.listdir(d) if n.startswith("daily-"))[:-keep]:
                out.append((os.path.basename(d), _delete(os.path.join(d, old))))
    return out


FREE_LIMIT = 0.15                 # below this share of the disk free, keep fewer
LOW_SPACE_KEEP = 2


def low_on_space() -> bool:
    try:
        st = os.statvfs(HOME)
    except OSError:
        return False
    return st.f_blocks > 0 and st.f_bavail / st.f_blocks < FREE_LIMIT


def _snapshot_dirs() -> list:
    root = snapshot_root()
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    return [os.path.join(root, n) for n in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, n)) and not os.path.islink(os.path.join(root, n))]


def _delete(path: str) -> Result:
    r = btrfs.delete_snapshot_detailed(path)
    return Result(True, f"removed {path}") if r.ok else r


def list_for(user: str) -> list:
    """Every snapshot holding this person's home, newest first - their own and
    the whole-/home ones that have their folder in them."""
    out = []
    own = os.path.join(snapshot_root(), user)
    if os.path.isdir(own) and not os.path.islink(own):
        try:
            names = os.listdir(own)
        except PermissionError:
            names = []
        out += [Snapshot(n, os.path.join(own, n), False) for n in names]
    whole = os.path.join(snapshot_root(), ALL)
    # a home that is its own subvolume is not in the whole-/home snapshots at
    # all - a snapshot stops at a nested subvolume, leaving an empty folder
    # there - so only its own snapshots hold it (measured in the slot test:
    # listing that empty folder offered an "earlier version" with no files)
    if _is_subvolume(os.path.join(HOME, user)):
        whole = ""
    if whole and os.path.isdir(whole) and not os.path.islink(whole):
        try:
            names = os.listdir(whole)
        except PermissionError:
            names = []
        for n in names:
            p = os.path.join(whole, n, user)
            if os.path.isdir(p):
                out.append(Snapshot(n, p, True))
    # daily-YYYY-MM-DD and manual-YYYY-MM-DD_… sort by their date part
    return sorted(out, key=lambda s: s.name.split("-", 1)[1], reverse=True)


def _is_subvolume(path: str) -> bool:
    """A btrfs subvolume's root directory has inode 256 - the check that needs
    no privilege (`btrfs subvolume show` may refuse an ordinary user)."""
    try:
        return os.stat(path).st_ino == 256 and os.path.isdir(path)
    except OSError:
        return False


def remove(user: str, name: str) -> Result:
    """Remove one of a person's own snapshots (not a whole-/home one: that
    holds everybody's home)."""
    if "/" in name or name in ("", ".", ".."):
        return Result(False, f"{name!r} is not a snapshot name")
    path = os.path.join(snapshot_root(), user, name)
    if not os.path.isdir(path) or os.path.islink(path):
        return Result(False, f"{path} does not exist")
    return _delete(path)
