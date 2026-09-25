"""The checks AGENTS.md prescribes, as a suite: CLI dispatch, the GSettings
schema, real GTK4 window construction, and a real btrfs snapshot
create/list/delete on a throwaway loopback filesystem (root only - never
a real subvolume).

    python3 -m pytest tests/ -v            (GTK test needs a display: xvfb-run)
    sudo python3 -m pytest tests/ -v -k btrfs
"""

import os
import shutil
import subprocess
import sys
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


def test_cli_without_command_fails():
    assert cli().returncode == 1


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
