#!/usr/bin/env python3
"""
Btrfs subvolume/snapshot operations for Shani Backup.
"""

import subprocess

# Reads are instantaneous when the filesystem is healthy, and `list_snapshots`
# runs on the GTK main thread, so an unbounded read is a frozen window.
_READ_TIMEOUT = 10

# Writes scale with subvolume size; this only stops a stall, it does not police
# how long a large snapshot may legitimately take.
_WRITE_TIMEOUT = 600


def _run(cmd: list, timeout: int):
    """Return the completed process, or None if the tool is absent or too slow.

    None is deliberately ambiguous: callers already read "no answer" as "nothing
    to report", so a timeout degrades exactly as a missing btrfs binary does
    instead of raising through a GTK callback. `subprocess.run` kills the child
    on timeout, so nothing is left running.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


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
    cmd = ["btrfs", "subvolume", "snapshot"]
    if readonly:
        cmd.append("-r")
    cmd += [source, destination]
    result = _run(cmd, _WRITE_TIMEOUT)
    return result is not None and result.returncode == 0


def list_snapshots(path: str) -> list:
    """
    List Btrfs subvolumes/snapshots at or below path.

    Args:
        path: Path to list snapshots from

    Returns:
        list: Absolute paths of subvolumes found under path, or an empty
              list if path isn't on a Btrfs filesystem or none exist.
    """
    # `-o` filters by the nearest enclosing *subvolume* ancestor of
    # `path`, not by literal directory nesting - passing a plain
    # subdirectory (not itself a subvolume) returns every subvolume in
    # the whole filesystem instead of just the ones under `path`
    # (confirmed empirically). Get everything and filter by resolved
    # absolute path prefix in Python instead, which behaves the way a
    # caller actually expects "snapshots under this path" to mean.
    result = _run(["btrfs", "subvolume", "list", path], _READ_TIMEOUT)
    if result is None or result.returncode != 0:
        return []

    mount_point = _mount_point_for(path)
    if not mount_point:
        return []
    prefix = path.rstrip("/") + "/"

    snapshots = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if "path" not in parts:
            continue
        rel_path = " ".join(parts[parts.index("path") + 1:])
        abs_path = f"{mount_point.rstrip('/')}/{rel_path}"
        if abs_path == path.rstrip("/") or abs_path.startswith(prefix):
            snapshots.append(abs_path)
    return snapshots


def delete_snapshot(snapshot: str) -> bool:
    """
    Delete a Btrfs snapshot.

    Args:
        snapshot: Snapshot path to delete

    Returns:
        bool: True if deleted successfully, False otherwise
    """
    result = _run(["btrfs", "subvolume", "delete", snapshot], _WRITE_TIMEOUT)
    return result is not None and result.returncode == 0


def _mount_point_for(path: str) -> str:
    """Resolve the mount point of the Btrfs filesystem containing path."""
    result = _run(["findmnt", "-n", "-o", "TARGET", "-T", path], _READ_TIMEOUT)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()
