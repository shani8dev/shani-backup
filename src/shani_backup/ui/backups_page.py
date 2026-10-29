"""The "Backups" page: your home folder, copied to another drive (restic.py).

One place to answer the three questions a backup app is opened for: is it
working (the last backup, the drive, the schedule), back it up now, and get it
back. Setting a drive up asks for a password once and keeps it in the keyring;
restoring always goes into a new folder, never over the files in your home.
"""

import datetime as dt
import os
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from shani_backup import restic
from shani_backup.config import get_config
from shani_backup.ui.home_page import _day, _size


def _parse(ts: str):
    try:
        return dt.datetime.fromisoformat(ts[:19])
    except ValueError:
        return None


def _when(t, today=None) -> str:
    if not t:
        return "never"
    today = today or dt.date.today()
    return f"{_day(t.date(), today)}, {t.strftime('%H:%M')}"


class BackupsPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.config = get_config()
        self.busy = False

        self.banner = Adw.Banner(title="Your backup drive is not connected", revealed=False)
        self.append(self.banner)
        self.toasts = Adw.ToastOverlay(vexpand=True)
        self.append(self.toasts)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)

        # no drive yet
        start = Adw.StatusPage(icon_name="drive-harddisk-symbolic", title="Back Up to Another Drive",
                               description="Snapshots keep earlier versions on this disk. A backup keeps "
                                           "a copy of your home folder somewhere else — a USB drive, a "
                                           "second disk — in case this one fails.")
        choose = Gtk.Button(label="Choose a Drive or Folder…", halign=Gtk.Align.CENTER)
        choose.add_css_class("pill")
        choose.add_css_class("suggested-action")
        choose.connect("clicked", self._choose)
        start.set_child(choose)
        self.stack.add_named(start, "start")

        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "main")
        self._build()
        GLib.idle_add(self.refresh)

    # ---- layout --------------------------------------------------------------------------
    def _build(self):
        now = Adw.PreferencesGroup()
        self.page.add(now)
        self.last_row = Adw.ActionRow(title="Last backup")
        self.last_icon = Gtk.Image(icon_name="object-select-symbolic", pixel_size=20)
        self.last_row.add_prefix(self.last_icon)
        self.now_btn = Gtk.Button(label="Back Up Now", valign=Gtk.Align.CENTER)
        self.now_btn.add_css_class("suggested-action")
        self.now_btn.connect("clicked", self._back_up)
        self.last_row.add_suffix(self.now_btn)
        now.add(self.last_row)
        self.progress_row = Adw.ActionRow(title="Backing up…", visible=False)
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=160)
        self.progress_row.add_suffix(self.progress)
        now.add(self.progress_row)

        where = Adw.PreferencesGroup(title="Where and What")
        self.page.add(where)
        self.drive_row = Adw.ActionRow(title="Backup drive")
        self.drive_row.add_prefix(Gtk.Image(icon_name="drive-harddisk-symbolic"))
        change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        change.add_css_class("flat")
        change.connect("clicked", self._choose)
        self.drive_row.add_suffix(change)
        where.add(self.drive_row)
        what = Adw.ActionRow(title="Your home folder",
                             subtitle="Except caches and the trash · a folder with a .nobackup file in it is left out")
        what.add_prefix(Gtk.Image(icon_name="user-home-symbolic"))
        where.add(what)

        when = Adw.PreferencesGroup(title="Schedule")
        self.page.add(when)
        self.auto = Adw.SwitchRow(title="Back up every day",
                                  subtitle="While you are logged in; a missed day is made up later")
        self.auto.connect("notify::active", self._on_auto)
        when.add(self.auto)
        self.time_row = Adw.EntryRow(title="At", show_apply_button=True)
        self.time_row.connect("apply", self._on_time)
        when.add(self.time_row)
        keep = Adw.ActionRow(title="Old backups", subtitle="Kept: one a day for a week, a week for a month, a month for a year")
        when.add(keep)

        self.list_group = Adw.PreferencesGroup(title="On the Drive")
        self.page.add(self.list_group)
        self.rows = []

    # ---- state ---------------------------------------------------------------------------
    def refresh(self, *_):
        threading.Thread(target=self._load, daemon=True).start()
        return False

    def _load(self):
        repo = self.config.get_backup_location() or ""
        p = restic.place(repo)
        ok, snaps, why = restic.snapshots(repo) if p.is_repo else (True, [], "")
        GLib.idle_add(self._show, p, ok, snaps, why, restic.schedule_on(), restic.schedule_time())

    def _show(self, p, ok, snaps, why, on, hhmm):
        self.place = p
        if not p.path or (p.reachable and not p.is_repo):
            self.stack.set_visible_child_name("start")
            self.banner.set_revealed(False)
            return False
        self.stack.set_visible_child_name("main")
        self.banner.set_revealed(not p.reachable)
        self.drive_row.set_subtitle(GLib.markup_escape_text(p.path))
        self.drive_row.set_title(GLib.markup_escape_text(os.path.basename(p.path.rstrip("/")) or p.path))
        last = _parse(snaps[0].time) if snaps else None
        stale = not last or (dt.datetime.now() - last).days >= 3
        if not p.reachable:
            self.last_row.set_subtitle("Connect the drive to back up or see what is on it")
        else:
            self.last_row.set_subtitle(_when(last) if ok else GLib.markup_escape_text(why))
        self.last_icon.set_from_icon_name("dialog-warning-symbolic" if stale or not ok else "object-select-symbolic")
        for c in ("success", "warning"):
            self.last_icon.remove_css_class(c)
        self.last_icon.add_css_class("warning" if stale or not ok else "success")
        self.now_btn.set_sensitive(p.reachable and not self.busy)

        self._setting = True
        self.auto.set_active(on)
        self.time_row.set_text(hhmm)
        self.time_row.set_visible(on)
        self._setting = False

        for r in self.rows:
            self.list_group.remove(r)
        self.rows = []
        today = dt.date.today()
        for s in snaps[:30]:
            t = _parse(s.time)
            row = Adw.ActionRow(title=_when(t, today),
                                subtitle=(f"{_size(s.size)} new" if s.size else "") or s.id)
            btn = Gtk.Button(label="Restore…", valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_, s=s, t=t: self._restore(s, t))
            btn.set_sensitive(p.reachable)
            row.add_suffix(btn)
            self.list_group.add(row)
            self.rows.append(row)
        self.list_group.set_visible(p.reachable)
        self.list_group.set_description(None if snaps else "No backups yet — Back Up Now makes the first")
        return False

    # ---- actions ---------------------------------------------------------------------------
    def toast(self, text, button=None, action=None):
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=5)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    def _choose(self, *_):
        d = Gtk.FileDialog(title="Choose Where to Keep Backups")
        d.select_folder(self.get_root(), None, self._chosen)

    def _chosen(self, dialog, res):
        try:
            folder = dialog.select_folder_finish(res)
        except GLib.Error:
            return
        path = folder.get_path()
        if os.path.realpath(path).startswith(os.path.realpath(restic.home_dir()) + os.sep):
            self.toast("Choose a place outside your home folder — ideally another drive")
            return
        self._ask_password(path, restic.place(path).is_repo)

    def _ask_password(self, path, existing):
        d = Adw.AlertDialog(
            heading="Open This Backup" if existing else "Protect Your Backups",
            body=(f"{path} already holds backups. Enter their password." if existing else
                  "Backups are encrypted with a password. It is kept in your keyring, so you only "
                  "type it again to restore on another computer — write it down somewhere safe."))
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.add_css_class("boxed-list")
        pw = Adw.PasswordEntryRow(title="Password")
        box.append(pw)
        again = Adw.PasswordEntryRow(title="Again", visible=not existing)
        box.append(again)
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "Open" if existing else "Set Up")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")

        def check(*_):
            d.set_response_enabled("ok", bool(pw.get_text()) and (existing or pw.get_text() == again.get_text()))
        pw.connect("changed", check)
        again.connect("changed", check)
        check()

        def answered(_d, r):
            if r != "ok":
                return
            secret = pw.get_text()

            def run():
                res = restic.set_up(path, secret)
                GLib.idle_add(done, res)

            def done(res):
                if res.ok:
                    self.config.set_backup_location(path)
                    self.toast("Backup drive ready" if res.detail == "created" else "Backups found")
                    self.refresh()
                else:
                    self.toast(res.detail)
                return False
            threading.Thread(target=run, daemon=True).start()
        d.connect("response", answered)
        d.present(self.get_root())

    def _back_up(self, *_):
        self.busy = True
        self.now_btn.set_sensitive(False)
        self.progress_row.set_visible(True)
        self.progress.set_fraction(0)
        repo = self.place.path

        def tick(frac, current):
            GLib.idle_add(lambda: (self.progress.set_fraction(frac),
                                   self.progress_row.set_subtitle(GLib.markup_escape_text(os.path.basename(current))))
                          and False)

        def run():
            res = restic.backup(repo, tick)
            GLib.idle_add(done, res)

        def done(res):
            self.busy = False
            self.progress_row.set_visible(False)
            self.toast("Backed up" if res.ok else res.detail)
            self.refresh()
            return False
        threading.Thread(target=run, daemon=True).start()

    def _restore(self, s, t):
        label = t.strftime("%-d %b %Y %H-%M") if t else s.id
        target = os.path.join(restic.home_dir(), "Restored", f"Backup of {label}")
        n = 2
        while os.path.exists(target):
            target = os.path.join(restic.home_dir(), "Restored", f"Backup of {label} ({n})")
            n += 1
        d = Adw.AlertDialog(heading="Restore This Backup?",
                            body=f"Everything in it is copied into a new folder:\n{target}\n\n"
                                 "Nothing in your home folder is replaced; take what you need from there.")
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "Restore")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, r):
            if r != "ok":
                return
            self.toast("Restoring…")
            repo = self.place.path

            def run():
                res = restic.restore(repo, s.id, target)
                GLib.idle_add(done, res)

            def done(res):
                if res.ok:
                    self.toast("Restored", "Show", lambda: Gtk.FileLauncher.new(
                        Gio.File.new_for_path(target)).launch(self.get_root(), None, None, None))
                else:
                    self.toast(res.detail)
                return False
            threading.Thread(target=run, daemon=True).start()
        d.connect("response", answered)
        d.present(self.get_root())

    def _on_auto(self, row, _p):
        if getattr(self, "_setting", False):
            return
        res = restic.set_schedule(row.get_active())
        if not res.ok:
            self.toast(res.detail)
        self.time_row.set_visible(row.get_active())

    def _on_time(self, row):
        res = restic.set_schedule(True, row.get_text().strip())
        self.toast(f"Daily at {row.get_text().strip()}" if res.ok else res.detail)
