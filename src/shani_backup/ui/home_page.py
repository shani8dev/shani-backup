"""The "Your files" page: earlier versions of your home, from the daily snapshots.

Time Machine's promise, on the same disk: pick a day and look around in it, or
type a file's name and get back the copy from before. Nothing here overwrites
anything - a restored file lands beside the current one as "name (29 Oct).ext"
when the name is taken - and nothing here needs root except taking a snapshot
by hand, which asks through pkexec.

Everything slow (listing, searching, copying) runs on a thread and comes back
through GLib.idle_add, so the window never stalls on a large home.
"""

import datetime as dt
import os
import pwd
import shutil
import subprocess
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from shani_backup import home

TIMER = "shani-backup-home.timer"
SEARCH_LIMIT = 60


def _when(name: str):
    """The moment a snapshot was taken, from its name."""
    kind, _, stamp = name.partition("-")
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(stamp, fmt)
        except ValueError:
            continue
    return None


def _day(d: dt.date, today: dt.date) -> str:
    if d == today:
        return "Today"
    if d == today - dt.timedelta(days=1):
        return "Yesterday"
    if (today - d).days < 7:
        return d.strftime("%A")
    return d.strftime("%-d %B" if d.year == today.year else "%-d %B %Y")


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def restore_target(original: str, when: dt.datetime) -> str:
    """Where a restored copy goes: its old place, or beside it with the date."""
    if not os.path.lexists(original):
        return original
    folder, name = os.path.split(original)
    stem, ext = os.path.splitext(name) if not os.path.isdir(original) else (name, "")
    label = when.strftime("%-d %b") if when else "earlier"
    for n in range(1, 100):
        tag = f" ({label})" if n == 1 else f" ({label}, {n})"
        cand = os.path.join(folder, f"{stem}{tag}{ext}")
        if not os.path.lexists(cand):
            return cand
    raise FileExistsError(original)


def restore(src: str, original: str, when) -> str:
    dest = restore_target(original, when)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(src) and not os.path.islink(src):
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest, follow_symlinks=False)
    return dest


class HomeSnapshotsPage(Gtk.Box):
    """The page itself: a toast overlay over a stack of empty / list / search."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.home_dir = os.path.join(home.HOME, self.user)
        self.snapshots = []

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_vexpand(True)
        self.append(self.toasts)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)

        # loading
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.stack.add_named(spinner, "loading")

        # nothing yet
        self.empty = Adw.StatusPage(
            icon_name="document-open-recent-symbolic",
            title="No earlier versions yet",
            description="Once a day your home folder is snapshotted on this disk, so a "
                        "deleted or changed file can be brought back. The first one "
                        "can be taken now.")
        take = Gtk.Button(label="Take a Snapshot Now", halign=Gtk.Align.CENTER)
        take.add_css_class("pill")
        take.add_css_class("suggested-action")
        take.connect("clicked", self._take)
        self.empty.set_child(take)
        self.stack.add_named(self.empty, "empty")

        # the list
        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "list")
        self._build_top()
        self.day_groups = []

        GLib.idle_add(self.refresh)

    # ---- the top of the list: status, search, take now -----------------------------------
    def _build_top(self):
        top = Adw.PreferencesGroup()
        self.page.add(top)

        self.status_row = Adw.ActionRow(title="Daily snapshots")
        self.status_icon = Gtk.Image(icon_name="object-select-symbolic", pixel_size=20)
        self.status_row.add_prefix(self.status_icon)
        self.on_btn = Gtk.Button(label="Turn On", valign=Gtk.Align.CENTER, visible=False)
        self.on_btn.add_css_class("suggested-action")
        self.on_btn.connect("clicked", lambda b: self._pkexec(
            ["systemctl", "enable", "--now", TIMER], "Daily snapshots are on", b))
        self.status_row.add_suffix(self.on_btn)
        self.take_btn = Gtk.Button(label="Take Now", valign=Gtk.Align.CENTER)
        self.take_btn.connect("clicked", self._take)
        self.status_row.add_suffix(self.take_btn)
        top.add(self.status_row)

        self.space_row = Adw.ActionRow(title="Space on this disk")
        self.space_bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER, width_request=140)
        self.space_bar.add_offset_value("low", 0.85)
        self.space_bar.add_offset_value("high", 0.95)
        self.space_bar.add_offset_value("full", 1.0)
        self.space_row.add_suffix(self.space_bar)
        top.add(self.space_row)

        find = Adw.PreferencesGroup(title="Find a File",
                                    description="Type part of a name to see every earlier copy of it")
        self.page.add(find)
        self.search = Gtk.SearchEntry(placeholder_text="e.g. report, IMG_2041, notes.md")
        self.search.set_margin_bottom(6)
        self.search.connect("search-changed", self._on_search)
        find.add(self.search)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.results.add_css_class("boxed-list")
        find.add(self.results)
        self._search_gen = 0

    # ---- loading -------------------------------------------------------------------------
    def refresh(self, *_):
        if not self.snapshots:
            self.stack.set_visible_child_name("loading")
        threading.Thread(target=self._load, daemon=True).start()
        return False

    def _load(self):
        snaps = home.list_for(self.user)
        state = _timer_state()
        try:
            st = os.statvfs(home.HOME)
            space = (st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize)
        except OSError:
            space = (0, 0)
        GLib.idle_add(self._show, snaps, state, space)

    def _show(self, snaps, state, space):
        self.snapshots = snaps
        on, nxt = state
        self.on_btn.set_visible(not on)
        if on:
            self.status_row.set_subtitle("Kept for two weeks" + (f" · next {nxt}" if nxt else ""))
            self.status_icon.set_from_icon_name("object-select-symbolic")
            self.status_icon.remove_css_class("warning")
            self.status_icon.add_css_class("success")
        else:
            self.status_row.set_subtitle("Off — only snapshots taken by hand are made")
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.status_icon.remove_css_class("success")
            self.status_icon.add_css_class("warning")
        total, free = space
        if total:
            self.space_bar.set_value(1 - free / total)
            self.space_row.set_subtitle(f"{_size(free)} free of {_size(total)}"
                                        + (" · keeping fewer snapshots" if free / total < home.FREE_LIMIT else ""))

        for g in self.day_groups:
            self.page.remove(g)
        self.day_groups = []
        if not snaps:
            self.empty.set_description(
                ("Once a day your home folder is snapshotted on this disk, so a deleted or changed "
                 "file can be brought back. The first one can be taken now.") if on else
                ("Snapshots keep earlier versions of your home folder on this disk, so a deleted or "
                 "changed file can be brought back. Daily snapshots are off; take one now, or turn "
                 "them on with: systemctl enable --now " + TIMER))
            self.stack.set_visible_child_name("empty")
            return False
        g = Adw.PreferencesGroup(title="Earlier Versions",
                                 description="Your home folder as it was — open one to look around")
        for s in snaps:
            g.add(self._snapshot_row(s, _when(s.name)))
        self.page.add(g)
        self.day_groups.append(g)
        self.stack.set_visible_child_name("list")
        return False

    def _snapshot_row(self, s, w):
        manual = s.name.startswith("manual-")
        today = dt.date.today()
        title = (_day(w.date(), today) + (w.strftime(", %H:%M") if manual else "")) if w else s.name
        if w and (today - w.date()).days >= 7:
            title += w.strftime(" · %A")
        row = Adw.ActionRow(title=title, subtitle="Taken by hand" if manual else "Daily", activatable=True)
        row.add_prefix(Gtk.Image(icon_name="camera-photo-symbolic" if manual else "document-open-recent-symbolic"))
        browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse it")
        browse.add_css_class("flat")
        browse.connect("clicked", lambda *_: self._open(s.path))
        row.add_suffix(browse)
        row.connect("activated", lambda *_: self._open(s.path))
        if manual and not s.whole:
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
            rm.add_css_class("flat")
            rm.connect("clicked", lambda *_: self._remove(s))
            row.add_suffix(rm)
        return row

    # ---- actions ---------------------------------------------------------------------------
    def toast(self, text, button=None, action=None):
        t = Adw.Toast(title=text, timeout=4)
        if button and action:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    def _open(self, path):
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self.get_root(), None, None, None)

    def _reveal(self, path):
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(self.get_root(), None, None, None)

    def _pkexec(self, args, done_text, button):
        button.set_sensitive(False)

        def run():
            r = subprocess.run(["pkexec", *args], capture_output=True, text=True)
            GLib.idle_add(finish, r)

        def finish(r):
            button.set_sensitive(True)
            if r.returncode == 0:
                self.toast(done_text)
                self.refresh()
            elif r.returncode in (126, 127):
                pass                                  # the password prompt was dismissed
            else:
                err = ""
                try:
                    import json
                    err = json.loads(r.stdout).get("error", "")
                except ValueError:
                    err = (r.stderr or "").strip().splitlines()[-1:] and r.stderr.strip().splitlines()[-1]
                self.toast(err or "That did not work")
            return False
        threading.Thread(target=run, daemon=True).start()

    def _take(self, button):
        self._pkexec(["shani-backup", "home", "snapshot", "--json"], "Snapshot taken", button)

    def _remove(self, s):
        dialog = Adw.AlertDialog(heading="Remove this snapshot?",
                                 body="The files in your home are not touched; only this earlier version goes.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: r == "remove" and self._pkexec(
            ["shani-backup", "home", "remove", s.name, "--json"], "Snapshot removed", self.take_btn))
        dialog.present(self.get_root())

    # ---- find a file ------------------------------------------------------------------------
    def _on_search(self, entry):
        self._search_gen += 1
        gen, q = self._search_gen, entry.get_text().strip().lower()
        while (child := self.results.get_first_child()):
            self.results.remove(child)
        if len(q) < 2:
            self.results.set_visible(False)
            return
        snaps = list(self.snapshots)
        threading.Thread(target=self._find, args=(gen, q, snaps), daemon=True).start()

    def _find(self, gen, q, snaps):
        hits = []
        for s in snaps:
            for root, dirs, files in os.walk(s.path):
                if gen != self._search_gen or len(hits) >= SEARCH_LIMIT:
                    break
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in files + dirs:
                    if q in name.lower():
                        rel = os.path.relpath(os.path.join(root, name), s.path)
                        hits.append((s, rel))
        GLib.idle_add(self._show_hits, gen, q, hits)

    def _show_hits(self, gen, q, hits):
        if gen != self._search_gen:
            return False
        self.results.set_visible(True)
        if not hits:
            self.results.append(Adw.ActionRow(title=f"Nothing named “{GLib.markup_escape_text(q)}” in any snapshot"))
            return False
        today = dt.date.today()
        by_path = {}
        for s, rel in hits:                      # snapshots come newest first
            by_path.setdefault(rel, []).append(s)
        for rel, snaps in by_path.items():
            versions, seen = [], None
            for s in snaps:                      # a copy unchanged since the newer one is not another version
                src = os.path.join(s.path, rel)
                try:
                    st = os.lstat(src)
                    sig = (st.st_size, int(st.st_mtime))
                except OSError:
                    continue
                if sig != seen:
                    versions.append((s, src, st))
                seen = sig
            if not versions:
                continue
            gone = not os.path.lexists(os.path.join(self.home_dir, rel))
            n = len(versions)
            row = Adw.ExpanderRow(
                title=GLib.markup_escape_text(os.path.basename(rel)),
                subtitle=GLib.markup_escape_text(
                    (os.path.dirname(rel) or "Home") + " · "
                    + ("deleted · " if gone else "") + (f"{n} earlier versions" if n > 1 else "1 earlier version")))
            row.add_prefix(Gtk.Image(gicon=_icon_for(versions[0][1])))
            for s, src, st in versions:
                w = _when(s.name)
                when = (_day(w.date(), today) + (w.strftime(", %H:%M") if s.name.startswith("manual-") else "")) if w else s.name
                v = Adw.ActionRow(title=when, subtitle=_size(st.st_size) if not os.path.isdir(src) else "Folder")
                show = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Show it")
                show.add_css_class("flat")
                show.connect("clicked", lambda *_, p=src: self._reveal(p))
                back = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
                if gone:
                    back.add_css_class("suggested-action")
                back.connect("clicked", lambda *_, p=src, r=rel, wh=w: self._restore(p, r, wh))
                v.add_suffix(show)
                v.add_suffix(back)
                row.add_row(v)
            row.set_expanded(len(by_path) == 1)
            self.results.append(row)
        if len(hits) >= SEARCH_LIMIT:
            self.results.append(Adw.ActionRow(title="More than this — type more of the name"))
        return False

    def _restore(self, src, rel, when):
        original = os.path.join(self.home_dir, rel)

        def run():
            try:
                dest = restore(src, original, when)
                GLib.idle_add(lambda: self.toast(f"Restored as {os.path.basename(dest)}", "Show",
                                                 lambda: self._reveal(dest)) and False)
            except OSError as e:
                GLib.idle_add(lambda: self.toast(f"Could not restore: {e.strerror or e}") and False)
        threading.Thread(target=run, daemon=True).start()


def _icon_for(path):
    try:
        info = Gio.File.new_for_path(path).query_info("standard::icon", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
        return info.get_icon()
    except GLib.Error:
        return Gio.ThemedIcon.new("text-x-generic")


def _timer_state():
    """(on, "tomorrow 00:12") from systemd, or (False, "") when it cannot say."""
    try:
        on = subprocess.run(["systemctl", "is-enabled", TIMER], capture_output=True, text=True, timeout=5).stdout.strip() == "enabled"
        us = subprocess.run(["systemctl", "show", TIMER, "-p", "NextElapseUSecRealtime", "--value", "--timestamp=unix"],
                            capture_output=True, text=True, timeout=5).stdout.strip().lstrip("@")
    except (OSError, subprocess.SubprocessError):
        return False, ""
    nxt = ""
    if us.isdigit():
        t = dt.datetime.fromtimestamp(int(us))
        nxt = ("today " if t.date() == dt.date.today() else "tomorrow " if t.date() == dt.date.today() + dt.timedelta(days=1)
               else t.strftime("%-d %b ")) + t.strftime("%H:%M")
    return on, nxt
