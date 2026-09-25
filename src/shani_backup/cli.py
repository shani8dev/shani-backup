#!/usr/bin/env python3
"""
Command-line interface for Shani Backup.
"""

import argparse
import subprocess
import sys
import os
from . import __version__
from . import btrfs
from .config import get_config, apply_config_defaults


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
            print(f"Starting backup of {args.source} to {backup_location}...")
            subprocess.run(cmd, check=True)
            print(f"Backup of {args.source} completed successfully.")
            print(f"Repository: {backup_location}")
        except subprocess.CalledProcessError as e:
            print(f"Backup failed: {e}")
            sys.exit(1)
        except FileNotFoundError:
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
            print(f"Starting restore from {repository} to {args.target}...")
            subprocess.run(cmd, check=True)
            print(f"Restored latest snapshot to {args.target}.")
            print(f"Repository: {repository}")
        except subprocess.CalledProcessError as e:
            print(f"Restore failed: {e}")
            sys.exit(1)
        except FileNotFoundError:
            print("Error: restic command not found. Please install restic.")
            sys.exit(1)
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