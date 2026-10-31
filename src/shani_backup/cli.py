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
from . import home
from . import restic
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


def _me() -> str:
    """The person asking - the one who ran pkexec/sudo, not root."""
    import pwd
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    return pwd.getpwuid(int(uid) if uid else os.getuid()).pw_name


def _home(args, home_parser) -> int:
    """`shani-backup home …` - see home.py."""
    as_json = getattr(args, "json", False)
    cmd = args.home_command
    if cmd is None:
        home_parser.print_help()
        return 1
    user = getattr(args, "user", None) or _me()
    if user != _me() and os.geteuid() != 0:
        msg = "only root may act on somebody else's snapshots"
        return _json_out({"ok": False, "error": msg}, False) if as_json else (print(f"Error: {msg}", file=sys.stderr) or 1)

    if cmd == "list":
        snaps = home.list_for(user)
        if as_json:
            return _json_out({"ok": True, "user": user, "snapshots": [s._asdict() for s in snaps]}, True)
        if not snaps:
            print(f"No snapshots of {user}'s home yet.")
        for s in snaps:
            print(f"{s.name:28} {s.path}")
        return 0

    if cmd in ("daily", "predeploy"):
        results = home.daily(keep=args.keep) if cmd == "daily" else home.predeploy(keep=args.keep)
        ok = all(r.ok for _, r in results)
        if as_json:
            return _json_out({"ok": ok, "results": [{"who": w, "ok": r.ok, "detail": r.detail} for w, r in results],
                              **({} if ok else {"error": next(r.detail for _, r in results if not r.ok)})}, ok)
        for who, r in results:
            print(f"{who}: {'ok' if r.ok else 'FAILED'} {r.detail}")
        return 0 if ok else 1

    result = home.snapshot_person(user, "manual") if cmd == "snapshot" else home.remove(user, args.name)
    if as_json:
        return _json_out({"ok": result.ok, **({"path": result.detail} if result.ok else {"error": result.detail})}, result.ok)
    print(result.detail if result.ok else f"Error: {result.detail}", file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


def main():
    parser = argparse.ArgumentParser(description="Shani Backup utility")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Backup command (restic.py): the home folder to the backup drive
    backup_parser = subparsers.add_parser("backup", help="Back up your home folder to the backup drive (restic)")
    backup_parser.add_argument(
        "source", nargs="?", help="What to back up (default: your home folder)"
    )
    backup_parser.add_argument(
        "--destination", help="The backup drive or folder (default: the one set in Backups)"
    )
    backup_parser.add_argument(
        "--scheduled", action="store_true",
        help="As the timer runs it: also thin out old backups (7 daily, 4 weekly, 12 monthly)"
    )
    backup_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore a backup into a new folder")
    restore_parser.add_argument(
        "target", help="An empty or new folder to restore into"
    )
    restore_parser.add_argument(
        "--snapshot", default="latest", help="Which backup (an id from `backups list`; default: latest)"
    )
    restore_parser.add_argument(
        "--from", dest="repository", help="The backup drive or folder (default: the one set in Backups)"
    )
    restore_parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable output: one JSON object on stdout"
    )

    # What is on the backup drive, and the schedule
    backups_parser = subparsers.add_parser("backups", help="The backups on the backup drive")
    backups_parser.add_argument("action", nargs="?", choices=["list"], default="list")
    backups_parser.add_argument("--json", action="store_true",
                                help="Machine-readable output: one JSON object on stdout")

    # Open the app on a page (Cassini's "Open Your Files")
    open_parser = subparsers.add_parser("open", help="Open the app")
    open_parser.add_argument("page", nargs="?", choices=["home", "backups"], default="home")

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

    # Home snapshots (home.py): per person, on the same disk
    home_parser = subparsers.add_parser(
        "home", help="Snapshots of home folders under /home/.snapshots")
    home_sub = home_parser.add_subparsers(dest="home_command", help="Home snapshot commands")
    h = home_sub.add_parser("snapshot", help="Take a snapshot of a home now (root)")
    h.add_argument("user", nargs="?", help="Whose home; yours when left out")
    h = home_sub.add_parser("list", help="List the snapshots holding a home")
    h.add_argument("user", nargs="?", help="Whose home (root only); yours when left out")
    h = home_sub.add_parser("daily", help="Today's snapshot of every home, and old ones removed (root; the timer runs this)")
    h.add_argument("--keep", type=int, default=14, help="Daily snapshots kept per home (default 14; 0 keeps all)")
    h = home_sub.add_parser("predeploy", help="A fresh snapshot before an update, and old ones removed (root; shani-deploy runs this)")
    h.add_argument("--keep", type=int, default=home.PREDEPLOY_KEEP, help="Pre-deploy snapshots kept per home (default 2; 0 keeps all)")
    h = home_sub.add_parser("remove", help="Remove one of your own snapshots (root)")
    h.add_argument("name", help="The snapshot's name, as `home list` shows it")
    h.add_argument("--user", help="Whose snapshot; yours when left out")
    for h in home_sub.choices.values():
        h.add_argument("--json", action="store_true",
                       help="Machine-readable output: one JSON object on stdout")

    # Schedule command
    schedule_parser = subparsers.add_parser("schedule", help="Manage backup schedule")
    schedule_parser.add_argument(
        "--enable", action="store_true", help="Back up every day (a systemd user timer)"
    )
    schedule_parser.add_argument(
        "--disable", action="store_true", help="Stop the daily backup"
    )
    schedule_parser.add_argument(
        "--at", metavar="HH:MM", help="The time of day for the daily backup"
    )

    args = parser.parse_args()

    if args.command == "backup":
        repo = args.destination or get_config().get_backup_location() or ""
        if not repo:
            msg = "No backup drive is set - choose one in Backups, or pass --destination"
            sys.exit(_json_out({"ok": False, "error": msg}, False) if args.json else (print(f"Error: {msg}") or 1))
        last = [-1]

        def progress(frac, current):
            pct = int(frac * 100)
            if not args.json and pct != last[0]:
                last[0] = pct
                print(f"\r{pct:3d}%  {current[-60:]:60}", end="", flush=True)
        result = restic.backup(repo, progress, args.source)
        if result.ok and args.scheduled:
            restic.thin(repo)
        if args.json:
            sys.exit(_json_out({"ok": result.ok, **({"snapshot": result.detail} if result.ok else {"error": result.detail})},
                               result.ok))
        print()
        print(f"Backed up: {result.detail}" if result.ok else f"Error: {result.detail}")
        sys.exit(0 if result.ok else 1)
    elif args.command == "restore":
        repo = args.repository or get_config().get_backup_location() or ""
        result = restic.restore(repo, args.snapshot, args.target) if repo else \
            restic.Result(False, "No backup drive is set - choose one in Backups, or pass --from")
        if args.json:
            sys.exit(_json_out({"ok": result.ok, **({"target": result.detail} if result.ok else {"error": result.detail})},
                               result.ok))
        print(f"Restored into {result.detail}" if result.ok else f"Error: {result.detail}")
        sys.exit(0 if result.ok else 1)
    elif args.command == "backups":
        repo = get_config().get_backup_location() or ""
        p = restic.place(repo)
        ok, snaps, why = restic.snapshots(repo) if p.is_repo else (True, [], "")
        payload = {"ok": ok, "repository": repo, "reachable": p.reachable, "set_up": p.is_repo,
                   "schedule": {"on": restic.schedule_on(), "time": restic.schedule_time()},
                   "backups": [s._asdict() for s in snaps], **({} if ok else {"error": why})}
        if args.json:
            sys.exit(_json_out(payload, ok))
        print(f"Backup drive: {repo or '(none)'}" + ("" if p.reachable or not repo else " - not connected"))
        for s in snaps:
            print(f"  {s.id}  {s.time[:16].replace('T', ' ')}")
        sys.exit(0 if ok else 1)
    elif args.command == "open":
        from .ui.main_window import main as gui_main
        sys.exit(gui_main(page=args.page))
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
    elif args.command == "home":
        sys.exit(_home(args, home_parser))
    elif args.command == "schedule":
        # the daily backup is a systemd user timer (shani-backup-restic.timer);
        # the enable-scheduler key it used to flip was read by nothing
        if args.enable or args.disable or args.at:
            on = not args.disable and (args.enable or restic.schedule_on())
            result = restic.set_schedule(on, args.at)
            print(("Daily backup " + (f"on, at {restic.schedule_time()}" if on else "off"))
                  if result.ok else f"Error: {result.detail}")
            sys.exit(0 if result.ok else 1)
        print(f"Daily backup: {'on' if restic.schedule_on() else 'off'}, at {restic.schedule_time()}")
        print(f"Backup drive: {get_config().get_backup_location() or '(none chosen)'}")
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