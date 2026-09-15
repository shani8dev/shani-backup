#!/usr/bin/env python3
"""
Command-line interface for Shani Backup.
"""

import argparse
import sys
from . import __version__
from . import btrfs


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

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from a backup")
    restore_parser.add_argument(
        "backup", help="Backup to restore from"
    )
    restore_parser.add_argument(
        "target", help="Target directory to restore to"
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

    # Snapshot list
    list_parser = snapshot_subparsers.add_parser("list", help="List snapshots")
    list_parser.add_argument("path", help="Path to list snapshots from")

    # Snapshot delete
    delete_parser = snapshot_subparsers.add_parser("delete", help="Delete a snapshot")
    delete_parser.add_argument("snapshot", help="Snapshot path to delete")

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
        print(f"Backing up {args.source} to {args.destination or '<default>'}")
    elif args.command == "restore":
        print(f"Restoring {args.backup} to {args.target}")
    elif args.command == "snapshot":
        if args.snapshot_command == "create":
            success = btrfs.create_snapshot(args.source, args.destination, args.readonly)
            if success:
                print(f"Created snapshot of {args.source} at {args.destination}")
            else:
                print(f"Failed to create snapshot")
                sys.exit(1)
        elif args.snapshot_command == "list":
            snapshots = btrfs.list_snapshots(args.path)
            if snapshots:
                print("Snapshots:")
                for snap in snapshots:
                    print(f"  {snap}")
            else:
                print("No snapshots found.")
        elif args.snapshot_command == "delete":
            success = btrfs.delete_snapshot(args.snapshot)
            if success:
                print(f"Deleted snapshot {args.snapshot}")
            else:
                print(f"Failed to delete snapshot {args.snapshot}")
                sys.exit(1)
        else:
            snapshot_parser.print_help()
            sys.exit(1)
    elif args.command == "schedule":
        if args.enable:
            print("Enabling scheduler")
        elif args.disable:
            print("Disabling scheduler")
        else:
            print("Scheduler status: <not implemented>")
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()