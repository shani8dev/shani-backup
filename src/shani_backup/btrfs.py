#!/usr/bin/env python3
"""
Btrfs subvolume/snapshot operations for Shani Backup.
"""

import os
import shutil
import subprocess
from typing import NamedTuple

# Reads are instantaneous when the filesystem is healthy, and `list_snapshots`
# runs on the GTK main thread, so an unbounded read is a frozen window.
_READ_TIMEOUT = 10

# Writes scale with subvolume size; this only stops a stall, it does not police
# how long a large snapshot may legitimately take.
_WRITE_TIMEOUT = 600


class Result(NamedTuple):
    """The outcome of one operation, with the reason when it failed.

    A bare bool cannot tell a caller *why* a snapshot did not happen, and on this
    operating system the reason is usually the useful part: `/` is a btrfs
    subvolume, a snapshot cannot cross a filesystem, and most of `/home` is not a
    subvolume at all. Every caller used to invent its own message for the `False`
    case, which is how "Failed to create snapshot" ended up on screen for all
    three.

    `detail` is the tool's own stderr where there is one - btrfs names the real
    reason ("not a btrfs subvolume", "not a btrfs filesystem") - and a synthesized
    sentence only where there is nothing to quote: the binary missing, or a call
    that outran its timeout. Those two produce no output at all, so silently
    returning False for them would be the same indistinguishable bool this type
    exists to remove.
    """

    ok: bool
    detail: str = ""


def _run(cmd: list, timeout: int):
    """Return the completed process, or None if the tool is absent or too slow.

    None is deliberately ambiguous: callers already read "no answer" as "nothing
    to report", so a timeout degrades exactly as a missing btrfs binary does
    instead of raising through a GTK callback. `subprocess.run` kills the child
    on timeout, so nothing is left running.

    It is ambiguous *on purpose*, so nothing that reports a reason may use it -
    see `_run_detailed`, which keeps the two apart. The first version of the
    detailed paths used this and reported "did not answer within 600s" for a
    machine with no btrfs binary at all: a message that contradicts its own
    zero-second runtime, and that sends the reader after a stuck filesystem that
    does not exist.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _run_detailed(cmd: list, timeout: int):
    """(process_or_None, reason_it_is_None).

    The same call, keeping the two reasons apart: a missing binary and a stalled
    filesystem are different problems with different remedies, and only one of
    them is worth waiting about.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout), ""
    except FileNotFoundError:
        return None, "btrfs is not installed (btrfs-progs)"
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed the child, so nothing is left running.
        return None, (f"btrfs did not answer within {timeout}s - the filesystem "
                      "may be stuck, or the operation may be larger than the "
                      "timeout allows")


def _why(result, what: str, timeout: int) -> str:
    """The reason an operation did not happen, in btrfs's own words where it gave
    any. See Result.detail for why the synthesized sentences are not invented."""
    if result is None:
        return f"btrfs did not answer within {timeout}s (the filesystem may be stuck)"
    text = (result.stderr or result.stdout or "").strip()
    if text:
        return text.splitlines()[-1].strip()
    return f"{what} failed with exit status {result.returncode} and said nothing"


def is_btrfs_path(path: str) -> bool:
    """
    Check whether path is itself a Btrfs subvolume (not just a directory
    that happens to live on a Btrfs filesystem).

    Args:
        path: Path to check

    Returns:
        bool: True if path is a Btrfs subvolume, False otherwise
    """
    result = _run(["btrfs", "subvolume", "show", path], _READ_TIMEOUT)
    return result is not None and result.returncode == 0


def create_snapshot(source: str, destination: str, readonly: bool = False) -> bool:
    """
    Create a Btrfs snapshot of source at destination.

    Args:
        source: Source subvolume to snapshot
        destination: Destination path for the snapshot
        readonly: Create a read-only snapshot

    Returns:
        bool: True if the snapshot was created successfully, False otherwise
    """
    return create_snapshot_detailed(source, destination, readonly).ok


def create_snapshot_detailed(
    source: str, destination: str, readonly: bool = False
) -> Result:
    """create_snapshot, with the reason.

    Two preflights, both answered from btrfs itself rather than guessed, because
    Shanios root *is* btrfs and both preconditions fail for ordinary inputs:

    * **the source must be a subvolume.** `os.path.exists()` is not the question
      btrfs asks. Most of `/home` is a plain directory inside the `@home`
      subvolume, and snapshotting one of those fails inside btrfs with nothing
      the caller had checked.
    * **the destination must be on the same filesystem.** btrfs cannot snapshot
      across filesystems, and the Snapshots tab's default - a sibling of the
      source - puts the destination beside the *top-level* subvolume when the
      source is `/`, which is the one place a snapshot may not be created.
    """
    if not _have_btrfs():
        return Result(False, "btrfs is not installed (btrfs-progs)")
    if not is_btrfs_path(source):
        return Result(False, f"{source} is not a btrfs subvolume, and only a "
                             "subvolume can be snapshotted")
    source_fs = _mount_point_for(source)
    dest_fs = _mount_point_for(destination.rsplit("/", 1)[0] or "/")
    if not source_fs or not dest_fs:
        # No answer from findmnt: say so rather than claiming the two agree.
        return Result(False, "could not read where either path lives, so the "
                             "same-filesystem check could not be made")
    if source_fs != dest_fs:
        return Result(False, f"a snapshot cannot cross a filesystem: {source} is on "
                             f"{source_fs} but {destination} would be on {dest_fs}")

    cmd = ["btrfs", "subvolume", "snapshot"]
    if readonly:
        cmd.append("-r")
    cmd += [source, destination]
    result, absent = _run_detailed(cmd, _WRITE_TIMEOUT)
    if result is None:
        return Result(False, absent)
    if result.returncode == 0:
        return Result(True, "")
    return Result(False, _why(result, "btrfs subvolume snapshot", _WRITE_TIMEOUT))


def list_snapshots(path: str) -> list:
    """
    List Btrfs subvolumes/snapshots at or below path.

    Args:
        path: Path to list snapshots from

    Returns:
        list: Absolute paths of subvolumes found under path, or an empty
              list if path isn't on a Btrfs filesystem or none exist.
    """
    return list_snapshots_detailed(path).paths


class Listing(NamedTuple):
    """A listing with the reason it might be empty.

    An empty list is the answer to two completely different questions - "there
    are no snapshots here" and "nothing answered" - and only one of them is safe
    to show somebody. The GUI's plain-text path keeps reading them the same way
    (see list_snapshots above); this is for the callers that have to branch.

    `dropped` counts the subvolumes the tool listed that do not live under the
    path asked about, and which therefore have no absolute path this process can
    name. It is the difference between "this directory has no snapshots" and "the
    filesystem has 27 subvolumes and 26 of them are somewhere else".
    """
    ok: bool
    paths: list
    detail: str = ""
    dropped: int = 0
    note: str = ""


def list_snapshots_detailed(path: str) -> Listing:
    """list_snapshots, able to say "nothing answered" apart from "nothing there".

    **How the absolute path of each subvolume is worked out**, which is the part
    that was wrong and is now measured rather than argued. `btrfs subvolume list
    <path>` names every subvolume of the filesystem relative to *the subvolume
    that contains* `<path>` - not relative to `<path>` and not relative to the
    filesystem root. So the old implementation, which joined those names onto
    `findmnt -T`'s mount point, was wrong in both directions, and both were
    measured on a real slot (`slot-tests/backup-snapshots.sh` in shani-testbed):

    * asked about a mount root, it returned **every subvolume in the filesystem
      at paths that do not exist** - 27 of them, including the slot's own
      `@blue`, `@green` and `@home`, all reported under the queried directory.
    * asked about a subvolume *inside* that mount, it returned **nothing at
      all**, because every name it had been given failed the prefix test.

    So the base is now the queried path itself, resolved to an absolute path -
    the names are relative to the containing subvolume, so the containing path
    is where they belong - and every candidate is kept only if it actually
    exists. That existence test is a `stat`, not a subprocess, so it costs
    nothing on the GTK main thread; the entries it rejects are counted rather
    than passed on, because they are real subvolumes of the filesystem that
    simply are not under the path that was asked about.

    Three ways this can come back with no paths, and only the last is an empty
    answer rather than a refusal: btrfs is not installed; `btrfs subvolume list`
    refused the path (it is not a btrfs filesystem, or does not exist); or
    findmnt could not say where the path lives. Each is a different thing for the
    reader to go and fix, and reporting all three as "No snapshots found."
    tells them the machine is empty when the machine never answered.
    """
    if not _have_btrfs():
        return Listing(False, [], "btrfs is not installed (btrfs-progs)")
    result, absent = _run_detailed(["btrfs", "subvolume", "list", path], _READ_TIMEOUT)
    if result is None:
        return Listing(False, [], absent)
    if result.returncode != 0:
        return Listing(False, [], _why(result, "btrfs subvolume list", _READ_TIMEOUT))

    base = os.path.abspath(path.rstrip("/") or "/")
    prefix = base.rstrip("/") + "/"

    snapshots = []
    dropped = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if "path" not in parts:
            continue
        rel_path = " ".join(parts[parts.index("path") + 1:])
        abs_path = os.path.normpath(f"{base}/{rel_path}")
        if abs_path != base and not abs_path.startswith(prefix):
            dropped += 1
            continue
        if not os.path.exists(abs_path):
            # The tool named a subvolume that is not reachable at that path - the
            # mount-root case above. Reporting it would hand the Snapshots tab a
            # path that does not exist, and that tab passes a path straight to
            # `btrfs subvolume delete`.
            dropped += 1
            continue
        snapshots.append(abs_path)

    # A path that is neither a subvolume nor a mount root cannot have its
    # subvolumes named by path: the names are relative to the *containing*
    # subvolume, whose absolute location this process cannot know without mounting
    # subvolume 5. Measured on a real slot - asking about a plain directory inside
    # a subvolume returns nothing, every name failing the existence test. So it is
    # said out loud, rather than left as an empty list that reads as "there are no
    # snapshots here".
    note = ""
    if not is_btrfs_path(path) and _mount_point_for(path) != os.path.abspath(
            path.rstrip("/") or "/"):
        note = (f"{path} is not a btrfs subvolume, so the subvolumes of its "
                "filesystem cannot be named by path from it - point at the "
                "subvolume itself, or at its mount point")
    return Listing(True, snapshots, "", dropped, note)


def delete_snapshot(snapshot: str) -> bool:
    """
    Delete a Btrfs snapshot.

    Args:
        snapshot: Snapshot path to delete

    Returns:
        bool: True if deleted successfully, False otherwise
    """
    return delete_snapshot_detailed(snapshot).ok


def delete_snapshot_detailed(snapshot: str) -> Result:
    """delete_snapshot, with the reason - for a delete, the reason is the whole
    point, because this is the one operation here that destroys something."""
    if not _have_btrfs():
        return Result(False, "btrfs is not installed (btrfs-progs)")
    result, absent = _run_detailed(
        ["btrfs", "subvolume", "delete", snapshot], _WRITE_TIMEOUT)
    if result is None:
        return Result(False, absent)
    if result.returncode == 0:
        return Result(True, "")
    return Result(False, _why(result, "btrfs subvolume delete", _WRITE_TIMEOUT))


def _have_btrfs() -> bool:
    """Whether the tool exists at all.

    Checked before anything that would otherwise report the *path* as the
    problem. `is_btrfs_path` answers False for a missing binary for exactly the
    same reason it answers False for a plain directory, and "is not a btrfs
    subvolume" is the wrong thing to tell somebody whose machine has no btrfs on
    it.
    """
    return shutil.which("btrfs") is not None


def _mount_point_for(path: str) -> str:
    """Resolve the mount point of the Btrfs filesystem containing path."""
    result = _run(["findmnt", "-n", "-o", "TARGET", "-T", path], _READ_TIMEOUT)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()