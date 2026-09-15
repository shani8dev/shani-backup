#!/usr/bin/env python3
"""
Command-line interface for Shani Backup.
"""

import argparse
import sys
from . import __version__

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