#!/usr/bin/env python3
"""
Complete GTK4/Libadwaita UI for Shani Backup.
Time Machine-like interface with full backend integration.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GObject, Gdk, Gio
import subprocess
import threading
import os
from datetime import datetime
from typing import Optional, List
from enum import Enum

from shani_backup.config import get_config, apply_config_defaults
from shani_backup.btrfs import list_snapshots, create_snapshot, delete_snapshot

__all__ = ['ShaniBackupWindow']

class BackupStatus(Enum):
    READY = "Ready"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"


class BackupJob:
    """Represents a running backup job with progress tracking."""
    
    def __init__(self, job_id: str, source: str, destination: str, operation: str):
        self.job_id = job_id
        self.source = source
        self.destination = destination
        self.operation = operation
        self.progress = 0.0
        self.status = BackupStatus.READY
        self.start_time = datetime.now()
        self.error = None
        self.thread = None


class ShaniBackupWindow(Adw.Window):
    """Complete main application window for Shani Backup."""
    
    def __init__(self):
        super().__init__()
        
        # Initialize configuration
        self.config = get_config()
        apply_config_defaults()
        
        # Application state
        self.backup_jobs: List[BackupJob] = []
        self.current_snapshot_path: Optional[str] = None
        self.is_scheduler_enabled: bool = False
        
        # Setup UI. _setup_navigation() already builds and adds all four
        # tabs (backup/restore/snapshots/scheduler) itself; there were also
        # calls here to _setup_backup_tab()/_setup_restore_tab()/
        # _setup_snapshots_tab()/_setup_scheduler_tab(), none of which are
        # defined anywhere in this class - dead leftovers from a refactor
        # that would have crashed with AttributeError on window construction.
        self._setup_window()
        self._setup_header()
        self._setup_navigation()
        self._setup_status_bar()
        
        # Load initial data
        self._refresh_backup_location()
        self._update_scheduler_status()
        self._refresh_snapshot_list()
        
    def _setup_window(self):
        """Setup the complete main application window."""
        self.set_title("Shani Backup - Time Machine Interface")
        self.set_default_size(1200, 800)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_margin_start(8)
        self.set_margin_end(8)
        
        # Apply modern Adwaita styling
        self.add_css_class("background")

        # Setup window icon and theme
        self._setup_appearance()

        # Adw.Window has no set_header_bar()/set_bottom_bar() of its own -
        # Adw.ToolbarView is libadwaita's actual widget for "header bar +
        # content + optional bottom bar", added via add_top_bar()/
        # set_content()/add_bottom_bar() and set as the window's one child.
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

    def _setup_appearance(self):
        """Setup window appearance and styling."""
        # Set up custom CSS for better visual appearance
        css_provider = Gtk.CssProvider.new()
        css = """
        window.main-window {
            margin: 8px;
        }
        
        .backup-card {
            margin: 8px;
            padding: 16px;
            border-radius: 8px;
            background: @surface;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        
        .action-button {
            margin: 4px;
            padding: 8px 16px;
        }
        
        .status-indicator {
            min-width: 12px;
            min-height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }
        
        .status-ready {
            background: @green;
        }
        
        .status-running {
            background: @yellow;
            animation: pulse 2s infinite;
        }
        
        .status-completed {
            background: @blue;
        }
        
        .status-failed {
            background: @red;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        """
        css_provider.load_from_string(css)
        
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            
    def _setup_header(self):
        """Setup the application header with status and controls."""
        header = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header)
        
        # App icon and title
        icon = Gtk.Image.new_from_icon_name("applications-system-symbolic")
        icon.set_pixel_size(24)
        header.set_title_widget(icon)
        
        # Status indicator in header
        self.status_indicator = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        self.status_indicator.add_css_class("status-indicator")
        self.status_indicator.add_css_class("status-ready")
        
        # Named header_status_label (not status_label): the bottom status
        # bar (_setup_status_bar) uses self.status_label for its own,
        # unrelated label, and is set up after this one - reusing the name
        # here would leave every later self.status_label.set_text(...) call
        # updating this static header indicator instead of the operational
        # status message it's actually meant for.
        self.header_status_label = Gtk.Label.new("● Ready")
        self.status_indicator.append(self.header_status_label)
        
        status_button = Gtk.Button.new()
        status_button.set_child(self.status_indicator)
        status_button.add_css_class("flat")
        status_button.connect("clicked", self._show_status_dialog)
        header.pack_end(status_button)
        
        # Help button
        help_button = Gtk.Button.new_from_icon_name("help-about-symbolic")
        help_button.set_tooltip_text("About Shani Backup")
        help_button.connect("clicked", self._show_about_dialog)
        header.pack_start(help_button)
        
    def _setup_navigation(self):
        """Setup the complete tabbed navigation system."""
        # Create tab view with modern styling
        self.tab_view = Adw.TabView.new()
        self.tab_view.set_vexpand(True)

        # Adw.TabBar is the actual clickable tab strip - TabView alone only
        # switches content, it has no UI of its own for the user to pick a tab.
        self.tab_bar = Adw.TabBar.new()
        self.tab_bar.set_view(self.tab_view)

        # Create all tabs - each returns a plain Gtk.Widget (the tab's
        # content), not a wrapper object; Adw.TabView has no such wrapper.
        self.backup_tab = self._create_enhanced_backup_tab()
        self.restore_tab = self._create_enhanced_restore_tab()
        self.snapshots_tab = self._create_enhanced_snapshots_tab()
        self.scheduler_tab = self._create_enhanced_scheduler_tab()

        # Add tabs with icons. TabView.append() returns an Adw.TabPage,
        # which is what actually carries the title/icon - icons are Gio.Icon,
        # not Gtk.Image.
        for widget, title, icon_name in (
            (self.backup_tab, "Backup", "document-open-symbolic"),
            (self.restore_tab, "Restore", "document-revert-symbolic"),
            (self.snapshots_tab, "Snapshots", "weather-few-snow-symbolic"),
            (self.scheduler_tab, "Scheduler", "emblem-system-symbolic"),
        ):
            page = self.tab_view.append(widget)
            page.set_title(title)
            page.set_icon(Gio.ThemedIcon.new(icon_name))

        # Add tab bar + tab view to the shared root layout.
        content_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        content_box.append(self.tab_bar)
        content_box.append(self.tab_view)
        self.toolbar_view.set_content(content_box)
        
    def _create_enhanced_backup_tab(self) -> Gtk.Widget:
        """Create the enhanced backup tab with comprehensive UI."""
        # Main container with modern layout
        main_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        
        # Header section
        header_section = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        header_section.add_css_class("backup-card")
        
        title_label = Gtk.Label.new("Create Backup")
        title_label.add_css_class("title-2")
        title_label.set_hexpand(True)
        
        refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_button.set_tooltip_text("Refresh backup location")
        refresh_button.connect("clicked", self._refresh_backup_location)
        
        header_section.append(title_label)
        header_section.append(refresh_button)
        
        main_box.append(header_section)
        
        # Backup location configuration
        location_frame = self._create_enhanced_card("Backup Location Configuration")
        
        location_grid = Gtk.Grid.new()
        location_grid.set_row_spacing(12)
        location_grid.set_column_spacing(12)
        location_grid.set_hexpand(True)
        
        # Current location display
        current_location_label = Gtk.Label.new("Current Backup Location:")
        current_location_label.add_css_class("title-4")
        current_location_label.set_halign(Gtk.Align.START)
        
        self.location_value_label = Gtk.Label.new("Not configured")
        self.location_value_label.add_css_class("mono")
        self.location_value_label.set_halign(Gtk.Align.START)
        
        # Location input
        location_input_label = Gtk.Label.new("New Location:")
        location_input_label.add_css_class("title-4")
        location_input_label.set_halign(Gtk.Align.START)
        
        self.location_entry = Gtk.Entry.new()
        self.location_entry.set_hexpand(True)
        self.location_entry.set_placeholder_text("/mnt/backups")
        self.location_entry.set_text(self.config.get_backup_location() or "")
        
        set_location_button = Gtk.Button.new_with_label("Set Location")
        set_location_button.add_css_class("suggested-action")
        set_location_button.connect("clicked", self._set_backup_location)
        
        # Add to grid
        location_grid.attach(current_location_label, 0, 0, 1, 1)
        location_grid.attach(self.location_value_label, 1, 0, 1, 1)
        location_grid.attach(location_input_label, 0, 1, 1, 1)
        location_grid.attach(self.location_entry, 1, 1, 1, 1)
        location_grid.attach(set_location_button, 2, 1, 1, 1)
        
        location_frame.set_child(location_grid)
        main_box.append(location_frame)
        
        # Source directory selection
        source_frame = self._create_enhanced_card("Source Directory")
        
        source_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        source_label = Gtk.Label.new("Directory to backup:")
        source_label.set_hexpand(True)
        source_label.set_halign(Gtk.Align.START)
        
        self.source_entry = Gtk.Entry.new()
        self.source_entry.set_hexpand(True)
        self.source_entry.set_text("/")
        self.source_entry.set_placeholder_text("Path to backup")
        
        browse_button = Gtk.Button.new_with_label("Browse...")
        browse_button.connect("clicked", self._show_source_browser)
        
        source_box.append(source_label)
        source_box.append(self.source_entry)
        source_box.append(browse_button)
        
        source_frame.set_child(source_box)
        main_box.append(source_frame)
        
        # Action buttons
        action_frame = self._create_enhanced_card("Backup Actions")
        
        action_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        self.backup_button = Gtk.Button.new_with_label("Start Backup")
        self.backup_button.add_css_class("suggested-action")
        self.backup_button.set_hexpand(True)
        self.backup_button.connect("clicked", self._start_backup_operation)
        
        clear_button = Gtk.Button.new_with_label("Clear")
        clear_button.add_css_class("destructive-action")
        clear_button.connect("clicked", self._clear_backup_form)
        
        action_box.append(self.backup_button)
        action_box.append(clear_button)
        
        action_frame.set_child(action_box)
        main_box.append(action_frame)
        
        # Progress monitoring section
        progress_frame = self._create_enhanced_card("Backup Progress")
        progress_frame.set_expanded(False)
        
        progress_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        
        progress_header = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        progress_header.set_margin_bottom(8)
        
        self.progress_icon = Gtk.Image.new_from_icon_name("media-optical-symbolic")
        self.progress_icon.set_pixel_size(32)
        
        progress_title = Gtk.Label.new("Backup Progress")
        progress_title.add_css_class("title-3")
        progress_title.set_hexpand(True)
        
        self.status_indicator = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        self.status_indicator.add_css_class("status-indicator status-running")
        
        self.progress_status_label = Gtk.Label.new("Initializing...")
        self.progress_status_label.add_css_class("title-4")
        
        progress_header.append(self.progress_icon)
        progress_header.append(progress_title)
        progress_header.append(self.status_indicator)
        progress_header.append(self.progress_status_label)
        
        # Progress bar
        self.progress_bar = Gtk.ProgressBar.new()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("0%")
        
        # Progress details
        details_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        self.source_info_label = Gtk.Label.new("Source: --")
        self.source_info_label.add_css_class("small")
        
        self.dest_info_label = Gtk.Label.new("Destination: --")
        self.dest_info_label.add_css_class("small")
        
        self.time_info_label = Gtk.Label.new("Time: --:--:--")
        self.time_info_label.add_css_class("small")
        
        details_box.append(self.source_info_label)
        details_box.append(self.dest_info_label)
        details_box.append(self.time_info_label)
        
        progress_box.append(progress_header)
        progress_box.append(self.progress_bar)
        progress_box.append(details_box)
        
        progress_frame.set_child(progress_box)
        main_box.append(progress_frame)
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(main_box)
        
        
        return scroll
        
    def _create_enhanced_restore_tab(self) -> Gtk.Widget:
        """Create the enhanced restore tab with comprehensive UI."""
        main_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        
        # Header
        header_section = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        header_section.add_css_class("backup-card")
        
        title_label = Gtk.Label.new("Restore Backup")
        title_label.add_css_class("title-2")
        title_label.set_hexpand(True)
        
        refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_button.set_tooltip_text("Refresh backup list")
        refresh_button.connect("clicked", self._refresh_backup_list)
        
        header_section.append(title_label)
        header_section.append(refresh_button)
        
        main_box.append(header_section)
        
        # Repository selection
        repo_frame = self._create_enhanced_card("Backup Repository")
        
        repo_grid = Gtk.Grid.new()
        repo_grid.set_row_spacing(12)
        repo_grid.set_column_spacing(12)
        repo_grid.set_hexpand(True)
        
        repo_label = Gtk.Label.new("Repository Location:")
        repo_label.add_css_class("title-4")
        repo_label.set_halign(Gtk.Align.START)
        
        self.repo_combo = Gtk.ComboBoxText.new()
        self.repo_combo.set_hexpand(True)
        
        repo_combo_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        repo_combo_box.append(repo_label)
        repo_combo_box.append(self.repo_combo)
        
        repo_grid.attach(repo_combo_box, 0, 0, 2, 1)
        
        # Target directory
        target_label = Gtk.Label.new("Target Directory:")
        target_label.add_css_class("title-4")
        target_label.set_halign(Gtk.Align.START)
        
        self.target_entry = Gtk.Entry.new()
        self.target_entry.set_hexpand(True)
        self.target_entry.set_text("/tmp/restore")
        self.target_entry.set_placeholder_text("Directory to restore to")
        
        target_browse_button = Gtk.Button.new_with_label("Browse...")
        target_browse_button.connect("clicked", self._show_target_browser)
        
        repo_grid.attach(target_label, 0, 1, 1, 1)
        repo_grid.attach(self.target_entry, 1, 1, 1, 1)
        repo_grid.attach(target_browse_button, 2, 1, 1, 1)
        
        repo_frame.set_child(repo_grid)
        main_box.append(repo_frame)
        
        # Restore options
        options_frame = self._create_enhanced_card("Restore Options")
        options_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        
        # Snapshot selection
        snapshot_frame = Gtk.Expander.new("Snapshot Selection")
        snapshot_frame.set_expanded(True)
        
        snapshot_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        
        snapshot_label = Gtk.Label.new("Select snapshot:")
        snapshot_label.set_hexpand(True)
        snapshot_label.set_halign(Gtk.Align.START)
        
        self.snapshot_combo = Gtk.ComboBoxText.new()
        self.snapshot_combo.set_hexpand(True)
        
        snapshot_box.append(snapshot_label)
        snapshot_box.append(self.snapshot_combo)
        
        snapshot_frame.set_child(snapshot_box)
        options_box.append(snapshot_frame)
        
        # Restore options
        restore_options_frame = Gtk.Expander.new("Restore Options")
        restore_options_frame.set_expanded(True)
        
        restore_options_grid = Gtk.Grid.new()
        restore_options_grid.set_row_spacing(8)
        restore_options_grid.set_column_spacing(8)
        
        # Options checkboxes
        overwrite_label = Gtk.Label.new("Overwrite existing files:")
        overwrite_label.set_halign(Gtk.Align.START)
        
        self.overwrite_check = Gtk.CheckButton.new_with_label("Overwrite")
        self.overwrite_check.set_active(True)
        
        preserve_attr_label = Gtk.Label.new("Preserve file attributes:")
        preserve_attr_label.set_halign(Gtk.Align.START)
        
        self.preserve_attr_check = Gtk.CheckButton.new_with_label("Preserve")
        self.preserve_attr_check.set_active(True)
        
        restore_options_grid.attach(overwrite_label, 0, 0, 1, 1)
        restore_options_grid.attach(self.overwrite_check, 1, 0, 1, 1)
        restore_options_grid.attach(preserve_attr_label, 0, 1, 1, 1)
        restore_options_grid.attach(self.preserve_attr_check, 1, 1, 1, 1)
        
        restore_options_frame.set_child(restore_options_grid)
        options_box.append(restore_options_frame)
        
        options_frame.set_child(options_box)
        main_box.append(options_frame)
        
        # Action buttons
        action_frame = self._create_enhanced_card("Restore Actions")
        
        action_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        self.restore_button = Gtk.Button.new_with_label("Start Restore")
        self.restore_button.add_css_class("suggested-action")
        self.restore_button.set_hexpand(True)
        self.restore_button.connect("clicked", self._start_restore_operation)
        self.restore_button.set_sensitive(False)
        
        restore_clear_button = Gtk.Button.new_with_label("Clear")
        restore_clear_button.add_css_class("destructive-action")
        restore_clear_button.connect("clicked", self._clear_restore_form)
        
        action_box.append(self.restore_button)
        action_box.append(restore_clear_button)
        
        action_frame.set_child(action_box)
        main_box.append(action_frame)
        
        # Progress section
        progress_frame = self._create_enhanced_card("Restore Progress")
        progress_frame.set_expanded(False)
        
        progress_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        
        self.restore_progress_bar = Gtk.ProgressBar.new()
        self.restore_progress_bar.set_show_text(True)
        self.restore_progress_bar.set_text("0%")
        
        self.restore_status_label = Gtk.Label.new("Ready")
        self.restore_status_label.add_css_class("title-5")
        
        progress_box.append(self.restore_progress_bar)
        progress_box.append(self.restore_status_label)
        
        progress_frame.set_child(progress_box)
        main_box.append(progress_frame)
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(main_box)
        
        
        return scroll
        
    def _create_enhanced_snapshots_tab(self) -> Gtk.Widget:
        """Create the enhanced snapshots management tab."""
        main_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        
        # Path selection
        path_frame = self._create_enhanced_card("Btrfs Filesystem Path")
        
        path_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        path_label = Gtk.Label.new("Filesystem path:")
        path_label.set_hexpand(True)
        path_label.set_halign(Gtk.Align.START)
        
        self.snapshot_path_entry = Gtk.Entry.new()
        self.snapshot_path_entry.set_hexpand(True)
        self.snapshot_path_entry.set_text("/")
        
        path_browse_button = Gtk.Button.new_with_label("Browse...")
        path_browse_button.connect("clicked", self._show_path_browser)
        
        path_box.append(path_label)
        path_box.append(self.snapshot_path_entry)
        path_box.append(path_browse_button)
        
        path_frame.set_child(path_box)
        main_box.append(path_frame)
        
        # Snapshot list
        list_frame = self._create_enhanced_card("Snapshot Management")
        list_frame.set_expanded(True)

        list_container = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)

        # Filter and search box
        filter_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        filter_box.set_margin_bottom(12)

        self.snapshot_filter_entry = Gtk.SearchEntry.new()
        self.snapshot_filter_entry.set_placeholder_text("Filter snapshots...")
        self.snapshot_filter_entry.connect("search-changed", self._filter_snapshots)

        filter_box.append(self.snapshot_filter_entry)
        list_container.append(filter_box)

        # The actual snapshot list - there was previously no widget here at
        # all to display what _refresh_snapshot_list/_filter_snapshots find.
        self._all_snapshots = []
        self._selected_snapshot = None
        self.snapshot_listbox = Gtk.ListBox()
        self.snapshot_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.snapshot_listbox.connect("row-selected", self._on_snapshot_selected)
        self.snapshot_listbox.add_css_class("boxed-list")

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_min_content_height(200)
        list_scroll.set_child(self.snapshot_listbox)
        list_container.append(list_scroll)

        list_frame.set_child(list_container)
        main_box.append(list_frame)

        # Action buttons
        action_frame = self._create_enhanced_card("Snapshot Actions")
        action_frame.set_expanded(False)
        
        action_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        self.create_snapshot_button = Gtk.Button.new_with_label("Create Snapshot")
        self.create_snapshot_button.add_css_class("suggested-action")
        self.create_snapshot_button.set_hexpand(True)
        self.create_snapshot_button.connect("clicked", self._create_snapshot)
        
        self.delete_snapshot_button = Gtk.Button.new_with_label("Delete Snapshot")
        self.delete_snapshot_button.add_css_class("destructive-action")
        self.delete_snapshot_button.set_sensitive(False)
        self.delete_snapshot_button.connect("clicked", self._delete_snapshot)
        
        refresh_button = Gtk.Button.new_with_label("Refresh")
        refresh_button.connect("clicked", self._refresh_snapshot_list)
        
        action_box.append(self.create_snapshot_button)
        action_box.append(self.delete_snapshot_button)
        action_box.append(refresh_button)
        
        action_frame.set_child(action_box)
        main_box.append(action_frame)
        
        # Snapshot details
        details_frame = self._create_enhanced_card("Snapshot Details")
        details_frame.set_expanded(False)
        
        self.snapshot_details_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        
        self.snapshot_info_label = Gtk.Label.new("Select a snapshot to view details")
        self.snapshot_info_label.add_css_class("dim-label")
        
        details_frame.set_child(self.snapshot_details_box)
        main_box.append(details_frame)
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(main_box)
        
        
        return scroll
        
    def _create_enhanced_scheduler_tab(self) -> Gtk.Widget:
        """Create the enhanced scheduler configuration tab."""
        main_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        
        # Status overview
        status_frame = self._create_enhanced_card("Scheduler Status")
        status_frame.set_expanded(True)
        
        status_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        
        status_header = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        self.scheduler_icon = Gtk.Image.new_from_icon_name("emblem-system-symbolic")
        self.scheduler_icon.set_pixel_size(32)
        
        status_title = Gtk.Label.new("Backup Scheduler")
        status_title.add_css_class("title-3")
        status_title.set_hexpand(True)
        
        self.scheduler_status_indicator = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        self.scheduler_status_indicator.add_css_class("status-indicator status-running")
        
        self.scheduler_status_label = Gtk.Label.new("Running")
        self.scheduler_status_label.add_css_class("title-4")
        
        status_header.append(self.scheduler_icon)
        status_header.append(status_title)
        status_header.append(self.scheduler_status_indicator)
        status_header.append(self.scheduler_status_label)
        
        # Status information
        info_grid = Gtk.Grid.new()
        info_grid.set_row_spacing(8)
        info_grid.set_column_spacing(12)
        info_grid.set_hexpand(True)
        
        # Schedule time
        schedule_label = Gtk.Label.new("Schedule Time:")
        schedule_label.set_halign(Gtk.Align.START)
        
        self.scheduler_time_label = Gtk.Label.new("02:00")
        self.scheduler_time_label.set_halign(Gtk.Align.START)
        
        info_grid.attach(schedule_label, 0, 0, 1, 1)
        info_grid.attach(self.scheduler_time_label, 1, 0, 1, 1)
        
        # Backup location
        location_label = Gtk.Label.new("Backup Location:")
        location_label.set_halign(Gtk.Align.START)
        
        self.scheduler_location_label = Gtk.Label.new("/mnt/backups")
        self.scheduler_location_label.set_halign(Gtk.Align.START)
        
        info_grid.attach(location_label, 0, 1, 1, 1)
        info_grid.attach(self.scheduler_location_label, 1, 1, 1, 1)
        
        # Next run
        next_run_label = Gtk.Label.new("Next Run:")
        next_run_label.set_halign(Gtk.Align.START)
        
        self.next_run_label = Gtk.Label.new("Tomorrow at 02:00")
        self.next_run_label.set_halign(Gtk.Align.START)
        
        info_grid.attach(next_run_label, 0, 2, 1, 1)
        info_grid.attach(self.next_run_label, 1, 2, 1, 1)
        
        status_box.append(status_header)
        status_box.append(info_grid)
        
        status_frame.set_child(status_box)
        main_box.append(status_frame)
        
        # Configuration section
        config_frame = self._create_enhanced_card("Scheduler Configuration")
        config_frame.set_expanded(True)
        
        config_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        
        # Enable/disable toggle
        toggle_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        toggle_box.set_margin_bottom(16)
        
        toggle_label = Gtk.Label.new("Enable Automatic Backups:")
        toggle_label.set_hexpand(True)
        toggle_label.set_halign(Gtk.Align.START)
        
        self.scheduler_toggle = Gtk.Switch.new()
        self.scheduler_toggle.set_active(self.is_scheduler_enabled)
        self.scheduler_toggle.connect("notify::active", self._on_scheduler_toggle_changed)
        
        toggle_box.append(toggle_label)
        toggle_box.append(self.scheduler_toggle)
        
        # Schedule time configuration
        time_frame = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        
        time_label = Gtk.Label.new("Schedule Time (HH:MM):")
        time_label.set_halign(Gtk.Align.START)
        
        time_entry_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        
        self.scheduler_time_entry = Gtk.Entry.new()
        self.scheduler_time_entry.set_hexpand(True)
        self.scheduler_time_entry.set_text(self.config.get_schedule_time() or "02:00")
        self.scheduler_time_entry.set_placeholder_text("HH:MM")
        
        time_help_button = Gtk.Button.new_from_icon_name("help-about-symbolic")
        time_help_button.set_tooltip_text("Format: 02:00 (2 AM), 14:30 (2:30 PM)")
        time_help_button.connect("clicked", self._show_time_help)
        
        time_entry_box.append(self.scheduler_time_entry)
        time_entry_box.append(time_help_button)
        
        time_frame.append(time_label)
        time_frame.append(time_entry_box)
        
        # Time validation info
        time_info_label = Gtk.Label.new(
            "Examples: 02:00 (2 AM), 14:30 (2:30 PM), 23:59 (11:59 PM)")
        time_info_label.add_css_class("caption")
        time_info_label.add_css_class("dim-label")
        
        time_frame.append(time_info_label)
        
        config_box.append(toggle_box)
        config_box.append(time_frame)
        
        config_frame.set_child(config_box)
        main_box.append(config_frame)
        
        # Current schedule info
        current_frame = self._create_enhanced_card("Current Schedule Information")
        current_frame.set_expanded(False)
        
        self.current_info_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        
        self.schedule_details_label = Gtk.Label.new(
            f"Schedule: {self.config.get_schedule_time() or 'Not configured'} (Daily)")
        
        self.backup_details_label = Gtk.Label.new(
            f"Backup Location: {self.config.get_backup_location() or 'Not configured'}")
        
        self.scheduler_status_details_label = Gtk.Label.new(
            f"Status: {'ENABLED' if self.is_scheduler_enabled else 'DISABLED'}")
        
        self.current_info_box.append(self.schedule_details_label)
        self.current_info_box.append(self.backup_details_label)
        self.current_info_box.append(self.scheduler_status_details_label)
        
        current_frame.set_child(self.current_info_box)
        main_box.append(current_frame)
        
        # Action buttons
        action_frame = self._create_enhanced_card("Scheduler Actions")
        action_frame.set_expanded(False)
        
        action_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        
        apply_button = Gtk.Button.new_with_label("Apply Configuration")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", self._apply_scheduler_config)
        
        restart_button = Gtk.Button.new_with_label("Restart Service")
        restart_button.connect("clicked", self._restart_scheduler_service)
        
        status_button = Gtk.Button.new_with_label("Check Status")
        status_button.connect("clicked", self._check_scheduler_status)
        
        action_box.append(apply_button)
        action_box.append(restart_button)
        action_box.append(status_button)
        
        action_frame.set_child(action_box)
        main_box.append(action_frame)
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(main_box)
        
        
        return scroll
        
    def _setup_status_bar(self):
        """Setup the enhanced status bar."""
        # A plain Gtk.Box styled as a bottom bar - Adw.StatusBar doesn't
        # exist. self.status_label here (not e.g. bottom_status_label) is
        # deliberate: every _perform_backup()/etc. status message elsewhere
        # in this class already calls self.status_label.set_text(...)
        # expecting this bottom-bar label, not the header's own indicator
        # (which is self.header_status_label - see _setup_header()).
        self.status_bar = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        self.status_bar.set_margin_top(4)
        self.status_bar.set_margin_bottom(4)
        self.status_bar.set_margin_start(8)
        self.status_bar.set_margin_end(8)

        # Status icon
        self.status_icon = Gtk.Image.new_from_icon_name("applications-system-symbolic")
        self.status_icon.set_pixel_size(16)

        # Status label
        self.status_label = Gtk.Label.new("Ready")
        self.status_label.set_margin_start(12)

        # Connection status
        self.connection_indicator = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        self.connection_indicator.add_css_class("status-indicator status-ready")

        self.connection_label = Gtk.Label.new("● Connected")
        self.connection_indicator.append(self.connection_label)

        # Version info
        self.version_label = Gtk.Label.new("v0.0.1")
        self.version_label.add_css_class("dim-label")
        self.version_label.set_margin_start(12)

        # Add all elements to status bar
        self.status_bar.append(self.status_icon)
        self.status_bar.append(self.status_label)
        self.status_bar.append(self.connection_indicator)
        self.status_bar.append(self.version_label)

        # Add status bar to the toolbar view's bottom bar slot.
        self.toolbar_view.add_bottom_bar(self.status_bar)
        
    def _create_enhanced_card(self, title: str) -> Gtk.Widget:
        """Create an enhanced card with title and styling.

        Adw.Card/Adw.CardHeader don't exist. Callers also call
        .set_expanded(True/False) on the result with real intent (some
        cards start collapsed) - Gtk.Expander is the real widget with both
        a title and .set_expanded()/.set_child(), so it's a closer match
        than a plain Gtk.Frame (which has neither).
        """
        card = Gtk.Expander.new(title)
        card.add_css_class("card")
        card.set_margin_bottom(16)
        card.set_expanded(True)
        return card
        
    def _refresh_backup_location(self, button=None):
        """Refresh the backup location display."""
        location = self.config.get_backup_location()
        if location:
            self.location_value_label.set_text(f"Current: {location}")
            self.location_entry.set_text(location)
        else:
            self.location_value_label.set_text("Not configured")
            self.location_entry.set_text("")
            
    def _set_backup_location(self, button):
        """Set the backup location from the entry."""
        location = self.location_entry.get_text()
        
        if not location:
            self.location_value_label.set_text("Error: Location cannot be empty")
            return
            
        if not location.startswith('/'):
            self.location_value_label.set_text("Error: Must be absolute path")
            return
            
        if self.config.set_backup_location(location):
            self.location_value_label.set_text(f"Set to: {location}")
            self._refresh_backup_location()
            self._update_scheduler_status()
        else:
            self.location_value_label.set_text("Error: Failed to set location")
            
    def _show_source_browser(self, button):
        """Show directory browser for source selection."""
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Select Source Directory")
        dialog.set_modal(True)
        dialog.set_directory("/")
        
        dialog.open(self, self._on_source_dialog_response)
        
    def _on_source_dialog_response(self, dialog, result):
        """Handle source directory selection."""
        if result == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file()
            if folder:
                path = folder.get_path()
                self.source_entry.set_text(path)
                
    def _show_target_browser(self, button):
        """Show directory browser for target selection."""
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Select Target Directory")
        dialog.set_modal(True)
        dialog.set_directory("/tmp")
        
        dialog.open(self, self._on_target_dialog_response)
        
    def _on_target_dialog_response(self, dialog, result):
        """Handle target directory selection."""
        if result == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file()
            if folder:
                path = folder.get_path()
                self.target_entry.set_text(path)
                
    def _show_path_browser(self, button):
        """Show path browser for snapshot selection."""
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Select Btrfs Filesystem Path")
        dialog.set_modal(True)
        dialog.set_directory("/")
        
        dialog.open(self, self._on_path_dialog_response)
        
    def _on_path_dialog_response(self, dialog, result):
        """Handle path selection for snapshots."""
        if result == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file()
            if folder:
                path = folder.get_path()
                self.snapshot_path_entry.set_text(path)
                self._refresh_snapshot_list()
                
    def _start_backup_operation(self, button):
        """Start a backup operation with full UI feedback."""
        source = self.source_entry.get_text()
        location = self.location_entry.get_text()
        
        if not source or not location:
            self.status_label.set_text("Error: Source and backup location required")
            return
            
        if not os.path.exists(source):
            self.status_label.set_text(f"Error: Source directory '{source}' does not exist")
            return
            
        # Disable UI controls
        self.backup_button.set_sensitive(False)
        self.source_entry.set_sensitive(False)
        self.location_entry.set_sensitive(False)
        
        # Update progress indicators
        self.progress_status_label.set_text("Starting backup...")
        self.status_indicator.remove_css_class("status-ready")
        self.status_indicator.add_css_class("status-running")
        
        self.progress_bar.set_fraction(0.1)
        self.progress_bar.set_text("10%")
        
        self.source_info_label.set_text(f"Source: {source}")
        self.dest_info_label.set_text(f"Destination: {location}")
        self.time_info_label.set_text(f"Time: {datetime.now().strftime('%H:%M:%S')}")
        
        # Start backup in background thread
        job = BackupJob("job-1", source, location, "backup")
        self.backup_jobs.append(job)
        
        thread = threading.Thread(target=self._run_backup_with_progress, args=(job,))
        thread.daemon = True
        thread.start()
        
        self.status_label.set_text(f"Backup started: {source} -> {location}")
        
    def _run_backup_with_progress(self, job):
        """Run backup with progress updates."""
        try:
            # Use restic to backup with progress simulation
            cmd = ["restic", "backup", job.source, "--repo", job.destination, "--verbose"]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                job.status = BackupStatus.COMPLETED
                job.progress = 1.0
                
                # Update UI in main thread
                GObject.idle_add(self._update_backup_ui_completed, job)
            else:
                job.status = BackupStatus.FAILED
                job.error = f"Backup failed: {result.stderr}"
                
                # Update UI in main thread
                GObject.idle_add(self._update_backup_ui_failed, job)
                
        except Exception as e:
            job.status = BackupStatus.FAILED
            job.error = str(e)
            
            # Update UI in main thread
            GObject.idle_add(self._update_backup_ui_failed, job)
            
    def _update_backup_ui_completed(self, job):
        """Update UI when backup completes successfully."""
        self.progress_status_label.set_text("Backup completed successfully")
        self.status_indicator.remove_css_class("status-running")
        self.status_indicator.add_css_class("status-completed")
        
        self.progress_bar.set_fraction(1.0)
        self.progress_bar.set_text("100%")
        
        self.status_label.set_text(f"Backup completed: {job.source} -> {job.destination}")
        
        # Re-enable UI controls
        self.backup_button.set_sensitive(True)
        self.source_entry.set_sensitive(True)
        self.location_entry.set_sensitive(True)
        
        # Refresh snapshot list
        self._refresh_snapshot_list()
        
    def _update_backup_ui_failed(self, job):
        """Update UI when backup fails."""
        self.progress_status_label.set_text(f"Backup failed: {job.error}")
        self.status_indicator.remove_css_class("status-running")
        self.status_indicator.add_css_class("status-failed")
        
        self.status_label.set_text(f"Backup failed: {job.error}")
        
        # Re-enable UI controls
        self.backup_button.set_sensitive(True)
        self.source_entry.set_sensitive(True)
        self.location_entry.set_sensitive(True)

    def _clear_backup_form(self, button):
        """Reset the backup form to its defaults."""
        self.source_entry.set_text("/")
        self._refresh_backup_location()
        self.backup_button.set_sensitive(True)
        self.source_entry.set_sensitive(True)
        self.location_entry.set_sensitive(True)
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text("")
        self.progress_status_label.set_text("Ready")
        self.source_info_label.set_text("")
        self.dest_info_label.set_text("")
        self.time_info_label.set_text("")
        self.status_label.set_text("Backup form cleared")

    # ── restore tab ──────────────────────────────────────────────────────

    def _refresh_backup_list(self, button=None):
        """Refresh the list of available backup repositories.

        This app only ever has one configured backup-location (same as the
        CLI), so the "list" is that single entry, or empty if unconfigured.
        """
        self.repo_combo.remove_all()
        self.snapshot_combo.remove_all()
        location = self.config.get_backup_location()
        if location:
            self.repo_combo.append_text(location)
            self.repo_combo.set_active(0)
            # cli.py's restore command only ever restores "latest" - no
            # per-snapshot selection exists yet, so that's the only option.
            self.snapshot_combo.append_text("latest")
            self.snapshot_combo.set_active(0)
            self.restore_button.set_sensitive(True)
        else:
            self.restore_button.set_sensitive(False)
        self.status_label.set_text("Backup repository list refreshed")

    def _clear_restore_form(self, button):
        """Reset the restore form to its defaults."""
        self.target_entry.set_text("/tmp/restore")
        self.overwrite_check.set_active(True)
        self.preserve_attr_check.set_active(True)
        self.restore_progress_bar.set_fraction(0.0)
        self.restore_progress_bar.set_text("0%")
        self.status_label.set_text("Restore form cleared")

    def _start_restore_operation(self, button):
        """Restore the latest snapshot from the configured repository."""
        repository = self.repo_combo.get_active_text()
        target = self.target_entry.get_text().strip()

        if not repository:
            self.status_label.set_text("Error: no backup repository configured")
            return
        if not target:
            self.status_label.set_text("Error: target directory required")
            return
        if not os.path.exists(target):
            self.status_label.set_text(f"Error: target directory '{target}' does not exist")
            return

        self.restore_button.set_sensitive(False)
        self.target_entry.set_sensitive(False)
        self.restore_progress_bar.set_fraction(0.1)
        self.restore_progress_bar.set_text("Restoring...")
        self.status_label.set_text(f"Starting restore from {repository} to {target}...")

        thread = threading.Thread(
            target=self._run_restore_with_progress, args=(repository, target)
        )
        thread.daemon = True
        thread.start()

    def _run_restore_with_progress(self, repository: str, target: str):
        """Run restic restore in a background thread (mirrors _run_backup_with_progress)."""
        cmd = ["restic", "restore", "latest", "--target", target, "--repo", repository]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            success = result.returncode == 0
            message = (
                f"Restored latest snapshot to {target}."
                if success
                else f"Restore failed: {result.stderr.strip()}"
            )
        except FileNotFoundError:
            success = False
            message = "Error: restic command not found. Please install restic."

        def finish():
            self.restore_button.set_sensitive(True)
            self.target_entry.set_sensitive(True)
            self.restore_progress_bar.set_fraction(1.0 if success else 0.0)
            self.restore_progress_bar.set_text("Completed" if success else "Failed")
            self.status_label.set_text(message)
            return False

        GObject.idle_add(finish)

    # ── snapshots tab ────────────────────────────────────────────────────

    def _populate_snapshot_listbox(self, snapshots):
        """Replace the snapshot listbox's rows with the given paths."""
        child = self.snapshot_listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.snapshot_listbox.remove(child)
            child = next_child

        for snap in snapshots:
            row = Gtk.ListBoxRow()
            row.snapshot_path = snap
            label = Gtk.Label.new(snap)
            label.set_halign(Gtk.Align.START)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            label.set_margin_start(8)
            label.set_margin_end(8)
            row.set_child(label)
            self.snapshot_listbox.append(row)

    def _refresh_snapshot_list(self, button=None):
        """Refresh the snapshot list for the configured filesystem path."""
        path = self.snapshot_path_entry.get_text().strip() or "/"
        self._all_snapshots = list_snapshots(path)
        self._selected_snapshot = None
        self.delete_snapshot_button.set_sensitive(False)
        self.snapshot_filter_entry.set_text("")
        self._populate_snapshot_listbox(self._all_snapshots)
        self.status_label.set_text(
            f"Found {len(self._all_snapshots)} snapshot(s) under {path}"
        )

    def _filter_snapshots(self, search_entry):
        """Filter the displayed snapshot list by the search text."""
        query = search_entry.get_text().strip().lower()
        filtered = (
            self._all_snapshots if not query
            else [s for s in self._all_snapshots if query in s.lower()]
        )
        self._populate_snapshot_listbox(filtered)

    def _on_snapshot_selected(self, listbox, row):
        """Track the selected snapshot row, enable/disable delete, show details."""
        if row is None:
            self._selected_snapshot = None
            self.delete_snapshot_button.set_sensitive(False)
            self.snapshot_info_label.set_text("Select a snapshot to view details")
            return
        path = getattr(row, "snapshot_path", None)
        self._selected_snapshot = path
        self.delete_snapshot_button.set_sensitive(path is not None)
        if path:
            self.snapshot_info_label.set_text(f"Selected: {path}")

    def _create_snapshot(self, button):
        """Create a new (readonly) Btrfs snapshot of the configured path."""
        source = self.snapshot_path_entry.get_text().strip()
        if not source or not os.path.exists(source):
            self.status_label.set_text(f"Error: path '{source}' does not exist")
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = f"{source.rstrip('/')}-snapshot-{timestamp}"
        if create_snapshot(source, destination, readonly=True):
            self.status_label.set_text(f"Snapshot created: {destination}")
            self._refresh_snapshot_list()
        else:
            self.status_label.set_text(f"Failed to create snapshot of {source}")

    def _delete_snapshot(self, button):
        """Delete the currently selected snapshot."""
        if not self._selected_snapshot:
            self.status_label.set_text("No snapshot selected")
            return
        target = self._selected_snapshot
        if delete_snapshot(target):
            self.status_label.set_text(f"Deleted snapshot: {target}")
            self._refresh_snapshot_list()
        else:
            self.status_label.set_text(f"Failed to delete snapshot: {target}")

    # ── scheduler tab ────────────────────────────────────────────────────

    def _update_scheduler_status(self):
        """Sync every scheduler-tab display widget with the current config."""
        enabled = bool(self.config.get_scheduler_enabled())
        self.is_scheduler_enabled = enabled
        schedule_time = self.config.get_schedule_time() or "Not configured"
        location = self.config.get_backup_location() or "Not configured"

        self.scheduler_toggle.set_active(enabled)
        self.scheduler_time_label.set_text(schedule_time)
        self.scheduler_location_label.set_text(location)

        if enabled:
            self.scheduler_status_label.set_text("Enabled")
            self.scheduler_status_indicator.remove_css_class("status-failed")
            self.scheduler_status_indicator.add_css_class("status-running")
            self.next_run_label.set_text(f"Daily at {schedule_time}")
        else:
            self.scheduler_status_label.set_text("Disabled")
            self.scheduler_status_indicator.remove_css_class("status-running")
            self.scheduler_status_indicator.add_css_class("status-failed")
            self.next_run_label.set_text("Not scheduled")

        self.schedule_details_label.set_text(f"Schedule: {schedule_time} (Daily)")
        self.backup_details_label.set_text(f"Backup Location: {location}")
        self.scheduler_status_details_label.set_text(
            f"Status: {'ENABLED' if enabled else 'DISABLED'}"
        )

    def _on_scheduler_toggle_changed(self, switch, param):
        """Handle the enable/disable scheduler switch."""
        enabled = switch.get_active()
        if self.config.set_scheduler_enabled(enabled):
            self.status_label.set_text(f"Scheduler {'enabled' if enabled else 'disabled'}")
            self._update_scheduler_status()
        else:
            self.status_label.set_text("Failed to update scheduler state")

    def _apply_scheduler_config(self, button):
        """Apply the schedule time entered in the scheduler tab."""
        time_str = self.scheduler_time_entry.get_text().strip()
        if not self.config.validate_schedule_time(time_str):
            self.status_label.set_text(f"Error: '{time_str}' is not a valid HH:MM time")
            return
        if self.config.set_schedule_time(time_str):
            self.status_label.set_text(f"Schedule time updated to {time_str}")
            self._update_scheduler_status()
        else:
            self.status_label.set_text("Failed to update schedule time")

    def _check_scheduler_status(self, button):
        """Refresh and display the current scheduler status."""
        self._update_scheduler_status()
        self.status_label.set_text("Scheduler status refreshed")

    def _restart_scheduler_service(self, button):
        """Restart the scheduler's backing service.

        No systemd unit ships with this project yet - enable-scheduler/
        schedule-time in GSettings currently have nothing that reads and
        acts on them to actually run backups on a timer. Reporting that
        honestly here rather than guessing a specific unit name with no
        real backing service to match it.
        """
        self.status_label.set_text(
            "No scheduler service is installed yet - enabling the toggle "
            "only saves the setting; nothing currently runs backups on a timer."
        )

    def _show_about_dialog(self, button):
        """Show about dialog."""
        dialog = Adw.Dialog.new(self)
        dialog.set_title("About Shani Backup")
        
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        
        # Logo and title
        icon = Gtk.Image.new_from_icon_name("applications-system-symbolic")
        icon.set_pixel_size(64)
        
        title_label = Gtk.Label.new("Shani Backup")
        title_label.add_css_class("title-1")
        
        version_label = Gtk.Label.new("Version 0.0.1")
        version_label.add_css_class("title-4")
        version_label.add_css_class("dim-label")
        
        box.append(icon)
        box.append(title_label)
        box.append(version_label)
        
        # Description
        desc_label = Gtk.Label.new(
            "Time Machine-like backup system for Shanios\n"
            "with Btrfs snapshots, restic integration, and GTK4 UI."
        )
        desc_label.set_wrap(True)
        desc_label.set_margin_top(16)
        desc_label.set_margin_bottom(16)
        box.append(desc_label)
        
        # Close button
        close_button = Gtk.Button.new_with_label("OK")
        close_button.connect("clicked", lambda b: dialog.close())
        box.append(close_button)
        
        dialog.set_child(box)
        dialog.present()
        
    def _show_status_dialog(self, button):
        """Show detailed status dialog."""
        dialog = Adw.Dialog.new(self)
        dialog.set_title("System Status")
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        
        status_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        status_box.set_margin_top(12)
        status_box.set_margin_bottom(12)
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)
        
        # System status
        status_title = Gtk.Label.new("System Status")
        status_title.add_css_class("title-2")
        status_box.append(status_title)
        
        # Application status
        app_status_frame = Gtk.Frame.new(None)
        app_status_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        app_status_box.set_margin_top(8)
        app_status_box.set_margin_bottom(8)
        app_status_box.set_margin_start(8)
        app_status_box.set_margin_end(8)
        
        self.status_overview_label = Gtk.Label.new(
            f"Application: Running\n"
            f"Backup Jobs: {len(self.backup_jobs)}\n"
            f"Scheduler: {'ENABLED' if self.is_scheduler_enabled else 'DISABLED'}\n"
            f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        app_status_box.append(self.status_overview_label)
        app_status_frame.set_child(app_status_box)
        status_box.append(app_status_frame)
        
        # Configuration status
        config_frame = Gtk.Frame.new(None)
        config_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        config_box.set_margin_top(8)
        config_box.set_margin_bottom(8)
        config_box.set_margin_start(8)
        config_box.set_margin_end(8)
        
        config_title = Gtk.Label.new("Configuration")
        config_title.add_css_class("title-3")
        config_box.append(config_title)
        
        # Current configuration
        self.config_overview_label = Gtk.Label.new(
            f"Backup Location: {self.config.get_backup_location() or 'Not configured'}\n"
            f"Schedule Time: {self.config.get_schedule_time() or 'Not configured'}\n"
            f"Scheduler Enabled: {self.is_scheduler_enabled}\n"
            f"GSettings Status: {'Available' if self.config._settings else 'Not Available'}"
        )
        config_box.append(self.config_overview_label)
        
        config_frame.set_child(config_box)
        status_box.append(config_frame)
        
        # Active jobs
        if self.backup_jobs:
            jobs_frame = Gtk.Frame.new(None)
            jobs_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
            jobs_box.set_margin_top(8)
            jobs_box.set_margin_bottom(8)
            jobs_box.set_margin_start(8)
            jobs_box.set_margin_end(8)
            
            jobs_title = Gtk.Label.new("Active Backup Jobs")
            jobs_title.add_css_class("title-3")
            jobs_box.append(jobs_title)
            
            for job in self.backup_jobs:
                job_info = Gtk.Label.new(
                    f"{job.operation}: {job.source} -> {job.destination}\n"
                    f"Status: {job.status.value}\n"
                    f"Progress: {job.progress * 100:.1f}%\n"
                    f"Started: {job.start_time.strftime('%H:%M:%S')}"
                )
                job_info.add_css_class("mono")
                job_info.set_margin_bottom(8)
                job_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
                job_box.append(job_info)
                jobs_box.append(job_box)
            
            jobs_frame.set_child(jobs_box)
            status_box.append(jobs_frame)
        
        # Close button
        close_button = Gtk.Button.new_with_label("OK")
        close_button.connect("clicked", lambda b: dialog.close())
        status_box.append(close_button)
        
        scroll.set_child(status_box)
        dialog.set_child(scroll)
        dialog.present()
        
    def _show_time_help(self, button):
        """Show time format help dialog."""
        dialog = Adw.Dialog.new(self)
        dialog.set_title("Time Format Help")
        
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        
        help_label = Gtk.Label.new(
            "Time format for backup scheduler: HH:MM\n\n"
            "Format examples:\n"
            "  • 02:00 (2 AM midnight)\n"
            "  • 14:30 (2:30 PM)\n"
            "  • 23:59 (11:59 PM)\n"
            "  • 00:00 (12 AM midnight)\n\n"
            "Rules:\n"
            "  • Hours: 00-23 (24-hour format)\n"
            "  • Minutes: 00-59\n"
            "  • Leading zeros recommended (02:00, not 2:0)\n\n"
            "The scheduler will run backups daily at this time."
        )
        help_label.set_wrap(True)
        help_label.set_margin_bottom(16)
        box.append(help_label)
        
        close_button = Gtk.Button.new_with_label("OK")
        close_button.connect("clicked", lambda b: dialog.close())
        box.append(close_button)
        
        dialog.set_child(box)
        dialog.present()


# Application entry point
class ShaniBackupApp(Adw.Application):
    """Shani Backup GTK4 application class."""

    def __init__(self):
        super().__init__(application_id="org.shani.backup")
        self.window = None

    def do_activate(self):
        if not self.window:
            self.window = ShaniBackupWindow()
            self.window.set_application(self)
        self.window.present()


def main():
    """Application entry point."""
    app = ShaniBackupApp()
    return app.run(None)


if __name__ == "__main__":
    main()