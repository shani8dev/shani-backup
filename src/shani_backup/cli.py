#!/usr/bin/env python3
"""
Command-line interface for Shani Backup.
"""

import argparse
import json
import subprocess
import sys
import os
from . import __version__
from . import btrfs
from .config import get_config, apply_config_defaults


def _json_out(payload, ok: bool) -> int:
    """Emit one JSON object on stdout and return the exit code.

    Every subcommand accepts `--json`, because the GUI in Cassini that now drives
    these operations needs an answer it can branch on rather than English prose:
    the page that used to live here was deleted for parsing `snapshot list`
    output that was never JSON. So the shape is fixed and never varied:

      ok: true   - "error" is absent
      ok: false  - "error" is present, in this tool's own words, and is never
                   invented at the call site (see btrfs.Result.detail)

    stdout carries the object and nothing else, so a caller can parse it without
    having to strip progress chatter; the human-readable text is only printed
    when `--json` was not asked for.
    """
    print(json.dumps(payload))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Shani Backup utility")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Create a backup")
    backup_parser.add_argument(
        "source", help="Source directory to backup"
    )
    backup_parser.add_argument(
        "--destination", help="Destination for the backup"
    )
    backup_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from a backup")
    restore_parser.add_argument(
        "backup", help="Backup to restore from"
    )
    restore_parser.add_argument(
        "target", help="Target directory to restore to"
    )
    restore_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # Snapshot command
    snapshot_parser = subparsers.add_parser("snapshot", help="Manage Btrfs snapshots")
    snapshot_subparsers = snapshot_parser.add_subparsers(dest="snapshot_command", help="Snapshot commands")

    # Snapshot create
    create_parser = snapshot_subparsers.add_parser("create", help="Create a snapshot")
    create_parser.add_argument("source", help="Source subvolume to snapshot")
    create_parser.add_argument("destination", help="Destination path for the snapshot")
    create_parser.add_argument(
        "--readonly", action="store_true", help="Create a read-only snapshot"
    )
    create_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # Snapshot list
    list_parser = snapshot_subparsers.add_parser("list", help="List snapshots")
    list_parser.add_argument("path", help="Path to list snapshots from")
    list_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # Snapshot delete
    delete_parser = snapshot_subparsers.add_parser("delete", help="Delete a snapshot")
    delete_parser.add_argument("snapshot", help="Snapshot path to delete")
    delete_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # Schedule command
    schedule_parser = subparsers.add_parser("schedule", help="Manage backup schedule")
    schedule_parser.add_argument(
        "--enable", action="store_true", help="Enable the scheduler"
    )
    schedule_parser.add_argument(
        "--disable", action="store_true", help="Disable the scheduler"
    )

    args = parser.parse_args()

    if args.command == "backup":
        # Initialize config
        config = get_config()
        apply_config_defaults()
        
        # Get backup location from config or use provided destination
        backup_location = args.destination if args.destination else config.get_backup_location()
        
        if not backup_location:
            print("Error: No backup location specified. Provide --destination or configure backup-location in settings.")
            sys.exit(1)
        
        # Check if source exists
        if not os.path.exists(args.source):
            print(f"Error: Source directory '{args.source}' does not exist")
            sys.exit(1)
        
        # Use restic to backup the source directory
        cmd = ["restic", "backup", args.source, "--repo", backup_location]
        try:
            if not args.json:
                print(f"Starting backup of {args.source} to {backup_location}...")
            # restic's own stderr is captured so a failure can report *why*: the
            # old code printed "Command '[...]' returned non-zero exit status 1",
            # which is the shell's phrasing, not a reason (a missing repository,
            # a wrong password and an unreachable disk all read the same).
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                if args.json:
                    sys.exit(_json_out({"ok": True, "source": args.source,
                                        "repository": backup_location}, True))
                print(f"Backup of {args.source} completed successfully.")
                print(f"Repository: {backup_location}")
                return
            reason = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = reason[-1].strip() if reason else f"restic exited {proc.returncode}"
            if args.json:
                sys.exit(_json_out({"ok": False, "source": args.source,
                                    "repository": backup_location, "error": detail},
                                   False))
            print(f"Backup failed: {detail}")
            sys.exit(1)
        except FileNotFoundError:
            if args.json:
                sys.exit(_json_out({"ok": False, "error": "restic is not installed"},
                                   False))
            print("Error: restic command not found. Please install restic.")
            sys.exit(1)
    elif args.command == "restore":
        # Initialize config
        config = get_config()
        apply_config_defaults()
        
        # Get repository from config or use provided backup
        repository = args.backup if args.backup else config.get_backup_location()
        
        if not repository:
            print("Error: No backup repository specified. Provide backup argument or configure backup-location in settings.")
            sys.exit(1)
        
        # Check if target directory exists
        if not os.path.exists(args.target):
            print(f"Error: Target directory '{args.target}' does not exist")
            sys.exit(1)
        
        # Use restic to restore
        # In a real implementation, we would let the user choose which snapshot to restore
        cmd = ["restic", "restore", "latest", "--target", args.target, "--repo", repository]
        try:
            if not args.json:
                print(f"Starting restore from {repository} to {args.target}...")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                if args.json:
                    sys.exit(_json_out({"ok": True, "repository": repository,
                                        "target": args.target, "snapshot": "latest"},
                                       True))
                print(f"Restored latest snapshot to {args.target}.")
                print(f"Repository: {repository}")
                return
            reason = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = reason[-1].strip() if reason else f"restic exited {proc.returncode}"
            if args.json:
                sys.exit(_json_out({"ok": False, "repository": repository,
                                    "target": args.target, "error": detail}, False))
            print(f"Restore failed: {detail}")
            sys.exit(1)
        except FileNotFoundError:
            if args.json:
                sys.exit(_json_out({"ok": False, "error": "restic is not installed"},
                                   False))
            print("Error: restic command not found. Please install restic.")
            sys.exit(1)
    elif args.command == "snapshot":
        if args.snapshot_command == "create":
            # The detailed form, because the plain bool throws away the reason and
            # the reason is almost always what the user needs: on a btrfs OS the
            # source is usually not a subvolume, or the destination is on another
            # filesystem.
            result = btrfs.create_snapshot_detailed(
                args.source, args.destination, args.readonly
            )
            if args.json:
                sys.exit(_json_out(
                    {"ok": result.ok, "source": args.source,
                     "destination": args.destination, "readonly": args.readonly,
                     **({} if result.ok else {"error": result.detail})},
                    result.ok,
                ))
            if result.ok:
                print(f"Created snapshot of {args.source} at {args.destination}")
            else:
                print(f"Failed to create snapshot: {result.detail}")
                sys.exit(1)
        elif args.snapshot_command == "list":
            listing = btrfs.list_snapshots_detailed(args.path)
            if args.json:
                # `ok:false` for a read that never answered, so a caller can tell
                # "there are none" from "btrfs said nothing" - an empty list beside
                # a zero count is the most reassuring lie available on this page.
                sys.exit(_json_out(
                    {"ok": listing.ok, "path": args.path,
                     "snapshots": listing.paths if listing.ok else [],
                     "count": len(listing.paths) if listing.ok else None,
                     # Subvolumes the tool listed that are not under this path and
                     # so have no absolute path to hand a caller. Reported so that
                     # an empty list beside a zero count cannot mean "and the rest
                     # of the filesystem is somewhere you did not ask about".
                     "elsewhere": listing.dropped if listing.ok else None,
                     "note": listing.note if listing.ok else None,
                     **({} if listing.ok else {"error": listing.detail})},
                    listing.ok))
            snapshots = listing.paths
            if listing.ok and not snapshots and listing.note:
                print(listing.note)
            elif listing.ok and not snapshots:
                print(f"Could not list snapshots under {args.path}: {listing.detail}")
                sys.exit(1)
            elif listing.ok and listing.dropped:
                print(f"{len(snapshots)} under {args.path}; {listing.dropped} "
                      "subvolume(s) elsewhere in this filesystem are not under "
                      "it, so they cannot be listed here")
            if snapshots:
                print("Snapshots:")
                for snap in snapshots:
                    print(f"  {snap}")
            else:
                print("No snapshots found.")
        elif args.snapshot_command == "delete":
            result = btrfs.delete_snapshot_detailed(args.snapshot)
            if args.json:
                sys.exit(_json_out(
                    {"ok": result.ok, "snapshot": args.snapshot,
                     **({} if result.ok else {"error": result.detail})},
                    result.ok,
                ))
            if result.ok:
                print(f"Deleted snapshot {args.snapshot}")
            else:
                print(f"Failed to delete snapshot {args.snapshot}: {result.detail}")
                sys.exit(1)
        else:
            snapshot_parser.print_help()
            sys.exit(1)
    elif args.command == "schedule":
        config = get_config()
        if args.enable:
            print("Enabling scheduler")
            success = config.set_scheduler_enabled(True)
            if success:
                print("Scheduler enabled successfully")
            else:
                print("Failed to enable scheduler")
                sys.exit(1)
        elif args.disable:
            print("Disabling scheduler")
            success = config.set_scheduler_enabled(False)
            if success:
                print("Scheduler disabled successfully")
            else:
                print("Failed to disable scheduler")
                sys.exit(1)
        else:
            # Show current scheduler status
            enabled = config.get_scheduler_enabled()
            if enabled is None:
                print("Scheduler status: GSettings not available")
            elif enabled:
                print("Scheduler status: ENABLED")
            else:
                print("Scheduler status: DISABLED")
            schedule_time = config.get_schedule_time()
            if schedule_time:
                print(f"Schedule time: {schedule_time}")
            backup_location = config.get_backup_location()
            if backup_location:
                print(f"Backup location: {backup_location}")
            
            # Validate configuration
            errors = config.validate_configuration()
            if errors:
                print("Configuration issues:")
                for error in errors:
                    print(f"  - {error}")
    elif os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        # no subcommand on a desktop: open the app. The .desktop entry runs
        # plain `shani-backup`, which used to print this help and exit, so
        # the launcher never opened anything.
        from .ui.main_window import main as gui_main
        sys.exit(gui_main())
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()