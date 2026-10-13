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
    if os.geteuid() != 0 or not shutil.which("mkfs.btrfs"):
        pytest.skip("needs root and btrfs-progs (throwaway loopback fs)")
    img, mnt = tmp_path / "fs.img", tmp_path / "mnt"
    with open(img, "wb") as f:
        f.truncate(256 * 1024 * 1024)
    subprocess.run(["mkfs.btrfs", "-q", str(img)], check=True)
    mnt.mkdir()
    subprocess.run(["mount", "-o", "loop", str(img), str(mnt)], check=True)
    try:
        yield mnt
    finally:
        subprocess.run(["umount", str(mnt)])


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

    r = cli("snapshot", "list", str(snaps))
    assert str(snap) in r.stdout
    assert str(src) not in r.stdout  # only what is under the given path

    r = cli("snapshot", "delete", str(snap))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not snap.exists()
    assert "No snapshots found." in cli("snapshot", "list", str(snaps)).stdout


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
def test_the_header_indicator_is_not_the_cards_indicator(gui_env):
    """`status_indicator` was assigned twice, so the header's dot was dead.

    The header built its own indicator, then the Backup tab's progress card
    assigned a second one to the same name - so every "update the indicator" call
    updated the card's dot and the header's said "Ready" for ever, through a
    running backup and through a failure.
    """
    code = """
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, GLib
from shani_backup.ui.main_window import ShaniBackupWindow, ShaniBackupApp
import shani_backup.ui.main_window as mw

def activate(a):
    w = ShaniBackupWindow()
    print('distinct', w.header_status_indicator is not w.status_indicator)
    print('before', w.header_status_label.get_text())
    w._set_header_status('Running', 'status-running')
    print('after', w.header_status_label.get_text())
    print('card_untouched', w.status_indicator.get_style_context().has_class('status-running'))
    a.quit()

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    r = run_gui(code, gui_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "distinct True" in r.stdout
    assert "before ● Ready" in r.stdout
    # The whole point: the header indicator must be able to leave "Ready".
    assert "after ● Running" in r.stdout


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_a_failing_backup_moves_the_header_indicator(gui_env):
    """End to end through the callback, not through the helper directly.

    Driving `_set_header_status` proves the helper works; driving the completed
    and failed callbacks is what proves the *app* calls it.
    """
    code = """
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk
from shani_backup.ui.main_window import ShaniBackupWindow
from shani_backup.ui.main_window import BackupJob, BackupStatus

def activate(a):
    w = ShaniBackupWindow()
    job = BackupJob('j1', '/src', '/dst', 'backup')
    w._update_backup_ui_failed(job)
    print('failed:', w.header_status_label.get_text())
    w._update_backup_ui_completed(job)
    print('completed:', w.header_status_label.get_text())
    a.quit()

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    r = run_gui(code, gui_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "failed: ● Failed" in r.stdout
    assert "completed: ● Completed" in r.stdout


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_the_restore_tab_is_populated_on_arrival(gui_env):
    """It opened with an empty combo and a greyed button until Refresh was pressed.

    Set up by writing the configured location into GSettings *before* the window
    is built, which is what an installed machine looks like.
    """
    code = """
import gi; gi.require_version('Gio','2.0')
from gi.repository import Gio
Gio.Settings.new('org.shani.backup').set_string('backup-location', '/mnt/backups')
import gi as _gi; _gi.require_version('Gtk','4.0'); _gi.require_version('Adw','1')
from gi.repository import Gtk
from shani_backup.ui.main_window import ShaniBackupWindow

def activate(a):
    w = ShaniBackupWindow()
    # ListStore has no get_n_items(); GListModel's iter_children is the
    # interface that does, and it is what the assertion below needs.
    print('repo_count', w.repo_combo.get_model().iter_n_children(None))
    print('active', w.repo_combo.get_active_text())
    print('button_sensitive', w.restore_button.get_sensitive())
    a.quit()

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    r = run_gui(code, gui_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "repo_count 1" in r.stdout
    assert "active /mnt/backups" in r.stdout
    # A configured repository means the button works without pressing Refresh.
    assert "button_sensitive True" in r.stdout


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_the_restore_checkboxes_are_gone(gui_env):
    """Two options nothing read are worse than no options on a restore path."""
    code = """
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk
from shani_backup.ui.main_window import ShaniBackupWindow

def activate(a):
    w = ShaniBackupWindow()
    print('has_overwrite', hasattr(w, 'overwrite_check'))
    print('has_preserve', hasattr(w, 'preserve_attr_check'))
    a.quit()

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    r = run_gui(code, gui_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "has_overwrite False" in r.stdout
    assert "has_preserve False" in r.stdout


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_delete_refuses_the_path_being_listed_and_asks_before_destroying(gui_env):
    """The safety rules, without needing a btrfs filesystem.

    Two rules, and neither is exercised by a real delete in a unit test:
    the path the list is showing is never deletable from its own list, and a
    real delete goes through a confirmation that names the path.
    """
    code = """
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk
from shani_backup.ui.main_window import ShaniBackupWindow
import shani_backup.ui.main_window as mw

calls = []
def fake_delete(path):
    calls.append(path)
    return type('R', (), {'ok': True, 'detail': ''})()
mw.delete_snapshot_detailed = fake_delete

def activate(a):
    w = ShaniBackupWindow()
    # 1. the path being pointed at is never deletable
    w.snapshot_path_entry.set_text('/')
    w._selected_snapshot = '/'
    w._delete_snapshot(None)
    print('self_delete_refused', w.status_label.get_text().startswith('Refusing'))
    print('no_delete_ran', calls == [])

    # 2. a real target goes through the confirmation
    w.snapshot_path_entry.set_text('/')
    w._selected_snapshot = '/snapshots/keep-me'
    w._confirm_delete = lambda target: False
    w._delete_snapshot(None)
    print('declined', w.status_label.get_text() == 'Delete cancelled')
    print('still_nothing_ran', calls == [])

    # 3. confirmed -> the delete actually runs
    w._confirm_delete = lambda target: target == '/snapshots/keep-me'
    w._delete_snapshot(None)
    print('ran_on_confirm', calls == ['/snapshots/keep-me'])
    a.quit()

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    r = run_gui(code, gui_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "self_delete_refused True" in r.stdout
    assert "no_delete_ran True" in r.stdout
    assert "declined True" in r.stdout
    assert "still_nothing_ran True" in r.stdout
    assert "ran_on_confirm True" in r.stdout


@pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    reason="needs a display (CI runs under xvfb-run)")
def test_the_version_shown_is_the_package_version(gui_env):
    """The status bar and the About box hardcoded v0.0.1 while __version__ moved."""
    code = """
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk
from shani_backup.ui.main_window import ShaniBackupWindow
from shani_backup import __version__

def activate(a):
    w = ShaniBackupWindow()
    print('version', w.version_label.get_text())
    print('package', __version__)
    a.quit()

app = Gtk.Application(application_id='test.shani.backup')
app.connect('activate', activate)
app.run([])
"""
    r = run_gui(code, gui_env)
    assert r.returncode == 0, r.stdout + r.stderr
    shown = r.stdout.split("version ")[1].splitlines()[0]
    assert shown == f"v{r.stdout.split('package ')[1].splitlines()[0]}"
