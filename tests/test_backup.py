"""The checks AGENTS.md prescribes, as a suite: CLI dispatch, the GSettings
schema, real GTK4 window construction, and a real btrfs snapshot
create/list/delete on a throwaway loopback filesystem (root only - never
a real subvolume).

    python3 -m pytest tests/ -v            (GTK test needs a display: xvfb-run)
    sudo python3 -m pytest tests/ -v -k btrfs
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}


def cli(*args):
    return subprocess.run([sys.executable, "-m", "shani_backup.cli", *args],
                          capture_output=True, text=True, env=ENV, cwd=REPO)


def test_cli_help_lists_commands():
    r = cli("--help")
    assert r.returncode == 0, r.stderr
    for cmd in ("backup", "restore", "snapshot", "schedule"):
        assert cmd in r.stdout


def test_cli_without_command_and_display_prints_help():
    env = {k: v for k, v in ENV.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    r = subprocess.run([sys.executable, "-m", "shani_backup.cli"], capture_output=True,
                       text=True, env=env, cwd=REPO)
    assert r.returncode == 1 and "usage:" in r.stdout


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_launcher_command_opens_the_window(schema_dir):
    """What the .desktop entry runs (plain `shani-backup`) opens the app."""
    p = subprocess.Popen([sys.executable, "-m", "shani_backup.cli"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         env={**ENV, "GSETTINGS_SCHEMA_DIR": str(schema_dir), "GSETTINGS_BACKEND": "memory"},
                         cwd=REPO)
    try:
        time.sleep(4)
        assert p.poll() is None, "exited instead of opening the window: " + p.stdout.read()
    finally:
        p.terminate()
        p.wait(10)


@pytest.fixture(scope="session")
def schema_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("schema")
    for f in (REPO / "data").glob("*.gschema.xml"):
        shutil.copy(f, d)
    subprocess.run(["glib-compile-schemas", "--strict", str(d)], check=True)
    assert (d / "gschemas.compiled").exists()
    return d


def test_schema_compiles(schema_dir):
    pass


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_gtk_window_constructs(schema_dir):
    # A child process: Gtk/Adw init is per process, and a construction
    # error must fail this test, not abort pytest.
    code = """
import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk
from shani_backup.ui.main_window import ShaniBackupWindow, ShaniBackupApp
built = []
app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', lambda a: (built.append(ShaniBackupWindow()), a.quit()))
app.run([])
assert built and isinstance(built[0], ShaniBackupWindow)
print('constructed', type(built[0]).__name__)
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**ENV, "GSETTINGS_SCHEMA_DIR": str(schema_dir), "GSETTINGS_BACKEND": "memory"},
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "constructed ShaniBackupWindow" in r.stdout


@pytest.fixture
def btrfs_mount(tmp_path):
    """A throwaway btrfs filesystem, or an honest skip.

    **The mount is probed, not assumed.** `mkfs.btrfs` being present and the
    process being root are not enough: a host with no loop devices - every
    container, and a systemd-nspawn slot, where `/dev/loop-control` does not
    exist - fails `mount -o loop` with exit 32. Before this was probed, those
    hosts got four **errors** rather than four skips, which reads as "these tests
    are broken here" rather than "these tests cannot run here", and it is why
    shani-backup's suite was reported failing in-slot while nothing about it was.

    The btrfs behaviour that cannot be measured without a loop device is measured
    where a filesystem *is* available instead: `slot-tests/backup-snapshots.sh`
    in shani-testbed drives this same module inside a real slot against a scratch
    subvolume on the slot's own device.
    """
    if os.geteuid() != 0 or not shutil.which("mkfs.btrfs"):
        pytest.skip("needs root and btrfs-progs (throwaway loopback fs)")
    img, mnt = tmp_path / "fs.img", tmp_path / "mnt"
    with open(img, "wb") as f:
        f.truncate(256 * 1024 * 1024)
    subprocess.run(["mkfs.btrfs", "-q", str(img)], check=True)
    mnt.mkdir()
    mounted = subprocess.run(["mount", "-o", "loop", str(img), str(mnt)],
                             capture_output=True, text=True)
    if mounted.returncode != 0:
        # ENOENT from mount(8) here is the loop device, not the image.
        pytest.skip(f"this host cannot mount a loopback filesystem "
                    f"(mount -o loop exited {mounted.returncode}: "
                    f"{(mounted.stderr or mounted.stdout).strip()[:90]}) - "
                    f"btrfs behaviour is covered by shani-testbed's "
                    f"backup-snapshots check")
    try:
        yield mnt
    finally:
        subprocess.run(["umount", str(mnt)], capture_output=True)


def test_btrfs_snapshot_create_list_delete(btrfs_mount):
    src, snaps = btrfs_mount / "data", btrfs_mount / "snaps"
    subprocess.run(["btrfs", "subvolume", "create", str(src)], check=True, capture_output=True)
    (src / "file").write_text("payload")
    snaps.mkdir()
    snap = snaps / "s1"

    r = cli("snapshot", "create", "--readonly", str(src), str(snap))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (snap / "file").read_text() == "payload"
    ro = subprocess.run(["btrfs", "property", "get", str(snap), "ro"], capture_output=True, text=True)
    assert "ro=true" in ro.stdout

    # `snapshot list` can name subvolumes by path only from the subvolume that
    # CONTAINS the path asked about (see list_snapshots_detailed). On this
    # loopback mount that container is the mount root itself, which holds
    # exactly two subvolumes - src and the new snapshot - both with absolute
    # paths that exist.
    r = cli("snapshot", "list", str(btrfs_mount))
    assert str(snap) in r.stdout, r.stdout
    assert str(src) in r.stdout, r.stdout

    # A plain directory inside that container is not a subvolume and not a
    # mount point, so its filesystem's subvolumes cannot be named by path from
    # it at all. The refusal is said out loud, and NOT A SINGLE PATH is offered
    # - reporting a path this process cannot name was the 2026-10-14 defect
    # found on a real slot, where the same read offered 27 of them.
    r = cli("snapshot", "list", str(snaps))
    assert "is not a btrfs subvolume" in r.stdout, r.stdout
    assert "No snapshots found." in r.stdout, r.stdout
    assert str(snap) not in r.stdout
    assert str(src) not in r.stdout

    r = cli("snapshot", "delete", str(snap))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not snap.exists()
    # The same read that produced the path no longer names it, and the plain
    # directory still says why it can say nothing instead of claiming empty.
    assert str(snap) not in cli("snapshot", "list", str(btrfs_mount)).stdout
    r = cli("snapshot", "list", str(snaps))
    assert "No snapshots found." in r.stdout
    assert "is not a btrfs subvolume" in r.stdout


def test_btrfs_snapshot_of_missing_source_fails(btrfs_mount):
    r = cli("snapshot", "create", str(btrfs_mount / "nope"), str(btrfs_mount / "s"))
    assert r.returncode == 1
    assert "Failed to create snapshot" in r.stdout


# ── the --json contract, which is what Cassini's Backup page reads ───────────
#
# Every one of these states was measured, not guessed. The four that matter most
# are the ones where an empty list would be a lie: btrfs absent, a path that is
# not btrfs, and the "subvolumes elsewhere in the filesystem" count. A page built
# on this contract was deleted once already for parsing text that was never JSON.

def test_json_list_refuses_to_say_empty_when_nothing_answered(btrfs_mount, tmp_path):
    """btrfs absent -> ok:false with a reason, never ok:true and []."""
    r = json.loads(cli("snapshot", "list", "--json", str(tmp_path)).stdout)
    if shutil.which("btrfs"):
        # btrfs exists but tmp_path is not btrfs: a refusal for a different reason.
        assert r["ok"] is False and r["error"]
    else:
        assert r["ok"] is False
        assert "btrfs is not installed" in r["error"]
        # The affirmative that must never be emitted for an unanswered read.
        assert r["count"] is None and r["snapshots"] == []


def test_json_failure_carries_the_reason_not_a_generic_string():
    r = json.loads(cli("snapshot", "create", "--json", "/etc", "/tmp/x").stdout)
    assert r["ok"] is False
    # The old human path said only "Failed to create snapshot" for every cause.
    assert r["error"] != "" and "Failed to create snapshot" not in r["error"]
    assert r["source"] == "/etc" and r["destination"] == "/tmp/x"


def test_json_exit_status_matches_ok():
    """ok:false and a non-zero exit are the same fact, stated twice."""
    bad = cli("snapshot", "create", "--json", "/etc", "/tmp/x")
    payload = json.loads(bad.stdout)
    assert (payload["ok"] is False) == (bad.returncode != 0)
    good = cli("snapshot", "list", "--json", "/etc")
    assert (json.loads(good.stdout)["ok"] is True) == (good.returncode == 0)


def test_human_output_is_unchanged_by_the_json_flag():
    """The contract is additive: without --json the old sentences still appear."""
    r = cli("snapshot", "create", "/etc", "/tmp/x")
    assert r.returncode == 1
    assert "Failed to create snapshot" in r.stdout
    assert "{" not in r.stdout  # no JSON leaking into the human path


def test_every_snapshot_subcommand_accepts_json():
    for args in (["snapshot", "list", "/etc"], ["snapshot", "create", "/etc", "/tmp/x"],
                 ["snapshot", "delete", "/tmp/nope"]):
        r = cli(*args, "--json")
        payload = json.loads(r.stdout)  # must parse, whatever the outcome
        assert isinstance(payload, dict) and "ok" in payload, args


def test_json_stdout_is_only_the_object():
    """A caller must be able to parse stdout without stripping chatter.

    This is the bug Cassini's old Backup page had: it parsed `snapshot list`
    output as JSON, and the human path is English on purpose.
    """
    r = cli("snapshot", "list", "--json", "/etc")
    assert r.stdout.strip().startswith("{")
    assert json.loads(r.stdout.strip())  # the whole of it, not a prefix


def test_list_reports_subvolumes_elsewhere_rather_than_hiding_them(btrfs_mount):
    """`elsewhere` is the difference between "none here" and "27 over there"."""
    r = json.loads(cli("snapshot", "list", "--json", str(btrfs_mount)).stdout)
    assert r["ok"] is True
    assert r["elsewhere"] >= 0
    # Every path handed to a caller must exist: the Snapshots tab feeds one of
    # these straight to `btrfs subvolume delete`.
    for path in r["snapshots"]:
        assert os.path.exists(path), path


# ── GUI defects, each with a control that can fail ───────────────────────────

@pytest.fixture(scope="session")
def gui_env(schema_dir):
    return {**ENV, "GSETTINGS_SCHEMA_DIR": str(schema_dir), "GSETTINGS_BACKEND": "memory"}


def run_gui(code, env):
    """GTK initialises once per process, so every widget check is a child."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=90)


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_window_has_the_two_pages_and_opens_on_the_one_asked(gui_env, tmp_path):
    """Your Files and Backups, nothing else; `shani-backup open backups` opens there.
    No backup drive set, so Backups shows its start page, not an empty form."""
    code = """
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, GLib
from shani_backup.ui.main_window import ShaniBackupWindow

def activate(a):
    w = ShaniBackupWindow(page='backups', application=a)
    w.present()
    pages = []
    m = w.view_stack.get_pages()
    for i in range(m.get_n_items()):
        pages.append(m.get_item(i).get_name())
    print('pages', ','.join(pages))
    print('open', w.view_stack.get_visible_child_name())
    def later():
        print('backups_state', w.backups_page.stack.get_visible_child_name())
        a.quit()
    GLib.timeout_add(1500, later)

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    env = {**gui_env, "SHANI_BACKUP_HOME": str(tmp_path)}
    r = run_gui(code, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pages home,backups" in r.stdout
    assert "open backups" in r.stdout
    # no drive chosen (the schema default is empty): the start page
    assert "backups_state start" in r.stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real restic repository with the password in the environment (the
    keyring path is what a person uses; restic.py leaves a set RESTIC_PASSWORD alone)."""
    if not shutil.which("restic"):
        pytest.skip("restic is not installed")
    sys.path.insert(0, str(REPO / "src"))
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")
    monkeypatch.setenv("RESTIC_CACHE_DIR", str(tmp_path / "cache"))
    r = tmp_path / "drive" / "backups"
    subprocess.run(["restic", "init", "--repo", str(r)], check=True, capture_output=True)
    return r


def test_restic_back_up_list_restore(repo, tmp_path, monkeypatch):
    from shani_backup import restic
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "thesis.md").write_text("chapter one")
    (home / ".cache").mkdir()
    (home / ".cache" / "junk").write_text("x" * 1000)
    (home / "Videos").mkdir()
    (home / "Videos" / ".nobackup").touch()
    (home / "Videos" / "big.mkv").write_text("y")
    monkeypatch.setenv("HOME", str(home))

    assert restic.place(str(repo)) == restic.Place(str(repo), True, True)
    seen = []
    r = restic.backup(str(repo), lambda f, c: seen.append(f))
    assert r.ok and len(r.detail) == 8, r.detail
    ok, snaps, why = restic.snapshots(str(repo))
    assert ok and len(snaps) == 1 and snaps[0].id == r.detail, why

    out = tmp_path / "out"
    r = restic.restore(str(repo), snaps[0].id, str(out))
    assert r.ok, r.detail
    got = {os.path.relpath(os.path.join(d, f), out) for d, _, fs in os.walk(out) for f in fs}
    rel = os.path.relpath(home, "/")
    assert os.path.join(rel, "Documents/thesis.md") in got
    # caches and a .nobackup folder are left out
    assert not any(".cache" in g or "big.mkv" in g for g in got), got

    # never into a folder that has things in it
    r = restic.restore(str(repo), snaps[0].id, str(out))
    assert not r.ok and "not empty" in r.detail


def test_restic_wrong_password_says_so(repo, monkeypatch):
    from shani_backup import restic
    monkeypatch.setenv("RESTIC_PASSWORD", "wrong")
    ok, _, why = restic.snapshots(str(repo))
    assert not ok and why == "The password does not open this backup"


def test_backup_to_a_missing_drive_says_so(tmp_path):
    sys.path.insert(0, str(REPO / "src"))
    from shani_backup import restic
    if not shutil.which("restic"):
        pytest.skip("restic is not installed")
    r = restic.backup(str(tmp_path / "unplugged"))
    assert not r.ok and "plugged in" in r.detail


def test_schedule_time_is_checked_and_kept(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "src"))
    from shani_backup import restic
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for bad in ("25:00", "7pm", "12:60", "1230"):
        assert not restic.set_schedule(True, bad).ok
    assert restic.schedule_time() == "12:30"          # the timer's default
    restic.set_schedule(True, "21:15")                # systemctl --user may be absent here
    assert restic.schedule_time() == "21:15"
    conf = (tmp_path / "systemd/user/shani-backup-restic.timer.d/time.conf").read_text()
    assert "OnCalendar=\nOnCalendar=*-*-* 21:15:00" in conf


# ---- home snapshots (home.py) without a filesystem: the refusals ---------------------
# The behaviour on btrfs - coverage, read-only, privacy between accounts,
# retention, a planted symlink - is measured in a real slot by shani-testbed's
# slot-tests/home-snapshots.sh, on the real /home layout.

def test_home_snapshot_needs_root(monkeypatch):
    sys.path.insert(0, str(REPO / "src"))
    from shani_backup import home
    monkeypatch.setattr(home.os, "geteuid", lambda: 1000)
    for r in (home.snapshot_person("root"), home.snapshot_whole()):
        assert not r.ok and "root" in r.detail


def test_home_remove_refuses_paths(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "src"))
    from shani_backup import home
    monkeypatch.setattr(home, "HOME", str(tmp_path))
    for name in ("../../etc", "a/b", "..", ""):
        r = home.remove("alice", name)
        assert not r.ok, name


def test_home_low_space_keeps_fewer(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "src"))
    from shani_backup import home
    removed = []
    monkeypatch.setattr(home, "HOME", str(tmp_path))
    monkeypatch.setattr(home, "people", lambda: [])
    monkeypatch.setattr(home, "_delete", lambda p: removed.append(os.path.basename(p)) or home.Result(True, p))
    whole = tmp_path / ".snapshots" / "@home"
    for d in range(1, 8):
        (whole / f"daily-2026-10-0{d}").mkdir(parents=True)
    (whole / "manual-2026-10-01_09-00-00").mkdir()
    monkeypatch.setattr(home, "low_on_space", lambda: False)
    home.daily(keep=5)
    assert removed == ["daily-2026-10-01", "daily-2026-10-02"]
    removed.clear()
    monkeypatch.setattr(home, "low_on_space", lambda: True)
    home.daily(keep=5)
    # the oldest five of seven go; the manual one stays (_delete is stubbed, so
    # the folders are still there for the second count)
    assert removed == [f"daily-2026-10-0{d}" for d in range(1, 6)]


def test_home_list_as_a_person_is_json():
    r = cli("home", "list", "--json")
    d = json.loads(r.stdout)
    assert r.returncode == 0 and d["ok"] and isinstance(d["snapshots"], list)
    r = cli("home", "list", "root", "--json") if os.geteuid() != 0 else None
    if r is not None:
        d = json.loads(r.stdout)
        assert r.returncode == 1 and not d["ok"] and "root" in d["error"]


def test_restore_never_overwrites(tmp_path):
    """A restored copy takes its old place only when that is free; otherwise it
    lands beside the current file, dated - and a second restore does not
    replace the first."""
    sys.path.insert(0, str(REPO / "src"))
    pytest.importorskip("gi")
    from shani_backup.ui.home_page import restore
    import datetime as dt
    when = dt.datetime(2026, 10, 29, 2, 0)
    snap = tmp_path / "snap" / "Documents"
    snap.mkdir(parents=True)
    (snap / "report.odt").write_text("old")
    (snap / "album").mkdir()
    (snap / "album" / "a.jpg").write_text("pic")
    home_docs = tmp_path / "home" / "Documents"

    # gone from home: back in its place (folders made as needed)
    p = restore(str(snap / "report.odt"), str(home_docs / "report.odt"), when)
    assert p == str(home_docs / "report.odt") and open(p).read() == "old"

    # there now, and changed: beside it, dated; the current one untouched
    (home_docs / "report.odt").write_text("new")
    p2 = restore(str(snap / "report.odt"), str(home_docs / "report.odt"), when)
    p3 = restore(str(snap / "report.odt"), str(home_docs / "report.odt"), when)
    assert os.path.basename(p2) == "report (29 Oct).odt"
    assert os.path.basename(p3) == "report (29 Oct, 2).odt"
    assert (home_docs / "report.odt").read_text() == "new"

    # a folder, beside the current one, contents and all
    (home_docs / "album").mkdir()
    p4 = restore(str(snap / "album"), str(home_docs / "album"), when)
    assert os.path.basename(p4) == "album (29 Oct)" and open(os.path.join(p4, "a.jpg")).read() == "pic"


def test_set_up_keeps_the_password_in_the_keyring(tmp_path, monkeypatch):
    """The person's path: set_up() with a password, then a backup with no
    password anywhere in the environment - restic gets it from the keyring."""
    if not (shutil.which("restic") and shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        pytest.skip("needs restic, secret-tool and a Secret Service on the session bus")
    probe = subprocess.run(["secret-tool", "store", "--label", "probe", "app", "shani-backup-probe"],
                           input="x", capture_output=True, text=True, timeout=10)
    if probe.returncode != 0:
        pytest.skip(f"no usable Secret Service: {probe.stderr.strip()[:100]}")
    subprocess.run(["secret-tool", "clear", "app", "shani-backup-probe"], capture_output=True)
    sys.path.insert(0, str(REPO / "src"))
    from shani_backup import restic
    for k in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RESTIC_CACHE_DIR", str(tmp_path / "cache"))
    home = tmp_path / "home"
    home.mkdir()
    (home / "a.txt").write_text("a")
    monkeypatch.setenv("HOME", str(home))
    repo = str(tmp_path / "drive")
    try:
        assert restic.set_up(repo, "").ok is False                    # no password, no backup
        r = restic.set_up(repo, "tr0ub4dor")
        assert r.ok and r.detail == "created", r.detail
        assert restic.backup(repo).ok
        # the same drive again, as on a new computer: a wrong password is refused,
        # the right one opens it
        assert restic.set_up(repo, "nope").ok is False
        r = restic.set_up(repo, "tr0ub4dor")
        assert r.ok and r.detail == "opened", r.detail
        ok, snaps, why = restic.snapshots(repo)
        assert ok and len(snaps) == 1, why
    finally:
        subprocess.run(["secret-tool", "clear", "app", "shani-backup", "repository", repo], capture_output=True)
