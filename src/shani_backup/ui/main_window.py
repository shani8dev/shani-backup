#!/usr/bin/env python3
"""
The Shani Backup window: two pages, switched from the header.

* **Your Files** (home_page.py) - earlier versions of your home from the daily
  snapshots on this disk: browse a day, find a file, restore a copy.
* **Backups** (backups_page.py) - your home folder copied to another drive with
  restic: set up, back up now, the daily schedule, restore.

The raw btrfs snapshot tool that used to be a third page (create/delete any
subvolume by path) is Cassini's Backup page now, driving the same
`shani-backup snapshot ... --json` commands; it is a system tool, not
something a person restoring a file should be one click from.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk

from shani_backup import __version__
from shani_backup.ui.home_page import HomeSnapshotsPage
from shani_backup.ui.backups_page import BackupsPage

__all__ = ['ShaniBackupWindow', 'ShaniBackupApp']

PAGES = ("home", "backups")


class ShaniBackupWindow(Adw.ApplicationWindow):
    def __init__(self, page: str = "home", **kw):
        super().__init__(title="Backup", default_width=860, default_height=720,
                         width_request=360, height_request=400, **kw)
        view = Adw.ToolbarView()
        self.set_content(view)

        self.view_stack = Adw.ViewStack(vexpand=True)
        self.home_page = HomeSnapshotsPage()
        self.backups_page = BackupsPage()
        self.view_stack.add_titled_with_icon(self.home_page, "home", "Your Files", "document-open-recent-symbolic")
        self.view_stack.add_titled_with_icon(self.backups_page, "backups", "Backups", "drive-harddisk-symbolic")
        self.view_stack.set_visible_child_name(page if page in PAGES else "home")
        view.set_content(self.view_stack)

        self.header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.header.set_title_widget(switcher)
        menu = Gio.Menu()
        menu.append("About Backup", "win.about")
        self.header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                            tooltip_text="Main Menu", primary=True))
        view.add_top_bar(self.header)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self._about())
        self.add_action(about)

        # narrow: the page switcher moves to a bar at the bottom
        bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        view.add_bottom_bar(bar)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(self.header, "title-widget", Adw.WindowTitle(title="Backup"))
        bp.add_setter(bar, "reveal", True)
        self.add_breakpoint(bp)

    def _about(self):
        Adw.AboutDialog(application_name="Backup", application_icon="drive-harddisk",
                        developer_name="Shani OS", version=__version__,
                        website="https://shani.dev", license_type=Gtk.License.GPL_3_0_ONLY,
                        comments="Earlier versions of your files, and backups to another drive."
                        ).present(self)


class ShaniBackupApp(Adw.Application):
    def __init__(self, page: str = "home"):
        super().__init__(application_id="org.shani.backup")
        self.page = page
        self.window = None

    def do_activate(self):
        if not self.window:
            self.window = ShaniBackupWindow(page=self.page, application=self)
        else:
            self.window.view_stack.set_visible_child_name(self.page)
        self.window.present()


def main(page: str = "home"):
    return ShaniBackupApp(page).run(None)


if __name__ == "__main__":
    main()
