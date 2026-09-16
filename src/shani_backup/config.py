#!/usr/bin/env python3
"""
GSettings integration module for Shani Backup.
Integrates with org.shani.backup GSettings schema for configuration management.
"""

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio
from typing import Optional

from .btrfs import list_snapshots, delete_snapshot

__all__ = ['BackupConfig', 'get_config', 'set_config']

# Schema ID matches the one in org.shani.backup.gschema.xml
SCHEMA_ID = 'org.shani.backup'


class BackupConfig:
    """
    Configuration manager for Shani Backup using GSettings.
    Provides access to backup-location, enable-scheduler, and schedule-time settings.
    """

    def __init__(self):
        self._settings = None
        self._init_settings()

    def _init_settings(self):
        """Initialize GSettings with the schema."""
        try:
            self._settings = Gio.Settings.new(SCHEMA_ID)
            print(f"GSettings initialized for schema: {SCHEMA_ID}")
        except Exception as e:
            print(f"Warning: Failed to initialize GSettings: {e}")
            self._settings = None

    def get_backup_location(self) -> Optional[str]:
        """
        Get the configured backup location.

        Returns:
            str: Backup location path, or None if error/unavailable
        """
        if self._settings is None:
            return None
        try:
            return self._settings.get_string('backup-location')
        except Exception as e:
            print(f"Error getting backup location: {e}")
            return None

    def set_backup_location(self, location: str) -> bool:
        """
        Set the backup location.

        Args:
            location: Path to set as backup location

        Returns:
            bool: True if successful, False otherwise
        """
        if self._settings is None:
            return False
        try:
            self._settings.set_string('backup-location', location)
            return True
        except Exception as e:
            print(f"Error setting backup location: {e}")
            return False

    def get_scheduler_enabled(self) -> Optional[bool]:
        """
        Get the scheduler enable state.

        Returns:
            bool: True if scheduler enabled, False if disabled,
                  None if error/unavailable
        """
        if self._settings is None:
            return None
        try:
            return self._settings.get_boolean('enable-scheduler')
        except Exception as e:
            print(f"Error getting scheduler enabled state: {e}")
            return None

    def set_scheduler_enabled(self, enabled: bool) -> bool:
        """
        Set the scheduler enable state.

        Args:
            enabled: True to enable scheduler, False to disable

        Returns:
            bool: True if successful, False otherwise
        """
        if self._settings is None:
            return False
        try:
            self._settings.set_boolean('enable-scheduler', enabled)
            return True
        except Exception as e:
            print(f"Error setting scheduler enabled state: {e}")
            return False

    def get_schedule_time(self) -> Optional[str]:
        """
        Get the configured schedule time.

        Returns:
            str: Schedule time in HH:MM format, or None if error/unavailable
        """
        if self._settings is None:
            return None
        try:
            return self._settings.get_string('schedule-time')
        except Exception as e:
            print(f"Error getting schedule time: {e}")
            return None

    def set_schedule_time(self, time_str: str) -> bool:
        """
        Set the schedule time.

        Args:
            time_str: Time in HH:MM format

        Returns:
            bool: True if successful, False otherwise
        """
        if self._settings is None:
            return False
        try:
            self._settings.set_string('schedule-time', time_str)
            return True
        except Exception as e:
            print(f"Error setting schedule time: {e}")
            return False

    def validate_schedule_time(self, time_str: str) -> bool:
        """
        Validate that the schedule time is in correct HH:MM format.

        Args:
            time_str: Time string to validate

        Returns:
            bool: True if valid format, False otherwise
        """
        if not time_str:
            return False

        # Check format HH:MM where HH is 00-23 and MM is 00-59
        import re
        pattern = r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$'
        return bool(re.match(pattern, time_str))

    def get_snapshot_info(self, path: str) -> dict:
        """
        Get information about available snapshots for a path.

        Args:
            path: Path to list snapshots for

        Returns:
            dict: Dictionary with snapshot information including:
                - 'snapshots': list of snapshot paths
                - 'count': number of snapshots
                - 'has_snapshots': boolean indicating if snapshots exist
        """
        snapshots = list_snapshots(path)
        return {
            'snapshots': snapshots,
            'count': len(snapshots),
            'has_snapshots': len(snapshots) > 0
        }

    def can_delete_snapshot(self, snapshot: str) -> bool:
        """
        Check if a snapshot can be deleted (exists and is valid).

        Args:
            snapshot: Snapshot path to check

        Returns:
            bool: True if snapshot exists and can be deleted, False otherwise
        """
        try:
            # Import here to avoid circular import
            from .btrfs import is_btrfs_path
            return is_btrfs_path(snapshot) and delete_snapshot(snapshot)
        except Exception as e:
            print(f"Error checking if snapshot can be deleted: {e}")
            return False

    def get_all_settings(self) -> dict:
        """
        Get all current settings.

        Returns:
            dict: Dictionary containing all current settings
        """
        if self._settings is None:
            return {}

        settings = {}
        try:
            settings['backup-location'] = self.get_backup_location()
            settings['enable-scheduler'] = self.get_scheduler_enabled()
            settings['schedule-time'] = self.get_schedule_time()
        except Exception as e:
            print(f"Error getting all settings: {e}")

        return settings

    def print_settings(self):
        """Print current settings to stdout."""
        if self._settings is None:
            print("GSettings not available")
            return

        print("Current Shani Backup Settings:")
        print(f"  Backup Location: {self.get_backup_location()}")
        print(f"  Scheduler Enabled: {self.get_scheduler_enabled()}")
        print(f"  Schedule Time: {self.get_schedule_time()}")

    def apply_default_settings(self) -> bool:
        """
        Apply default settings if they haven't been configured.

        Returns:
            bool: True if settings were applied, False otherwise
        """
        success = True

        # Set default backup location if not set
        if not self.get_backup_location():
            if not self.set_backup_location('/mnt/backups'):
                print("Warning: Could not set default backup location")
                success = False

        # Set default schedule time if not set
        if not self.get_schedule_time():
            if not self.set_schedule_time('02:00'):
                print("Warning: Could not set default schedule time")
                success = False

        # Ensure scheduler is disabled by default
        if self.get_scheduler_enabled() is None:
            if not self.set_scheduler_enabled(False):
                print("Warning: Could not set default scheduler state")
                success = False

        return success

    def validate_configuration(self) -> list:
        """
        Validate the current configuration and return any issues.

        Returns:
            list: List of error messages, empty if configuration is valid
        """
        errors = []

        if self._settings is None:
            errors.append("GSettings not available")
            return errors

        # Check backup location
        location = self.get_backup_location()
        if not location:
            errors.append("Backup location not configured")
        elif not location.startswith('/'):
            errors.append("Backup location must be an absolute path")

        # Check schedule time
        schedule_time = self.get_schedule_time()
        if not schedule_time:
            errors.append("Schedule time not configured")
        elif not self.validate_schedule_time(schedule_time):
            errors.append("Schedule time must be in HH:MM format")

        return errors

    def export_settings(self) -> dict:
        """
        Export settings to a dictionary for serialization.

        Returns:
            dict: Settings dictionary
        """
        return self.get_all_settings()

    def import_settings(self, settings: dict) -> bool:
        """
        Import settings from a dictionary.

        Args:
            settings: Dictionary containing settings to import

        Returns:
            bool: True if import successful, False otherwise
        """
        success = True

        if 'backup-location' in settings and settings['backup-location']:
            if not self.set_backup_location(settings['backup-location']):
                print("Warning: Could not import backup location")
                success = False

        if 'enable-scheduler' in settings:
            if not self.set_scheduler_enabled(settings['enable-scheduler']):
                print("Warning: Could not import scheduler enabled state")
                success = False

        if 'schedule-time' in settings and settings['schedule-time']:
            if not self.set_schedule_time(settings['schedule-time']):
                print("Warning: Could not import schedule time")
                success = False

        return success


# Global configuration instance
_config_instance = None


def get_config() -> BackupConfig:
    """
    Get the global BackupConfig instance.

    Returns:
        BackupConfig: Global configuration instance
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = BackupConfig()
    return _config_instance


def set_config(config: BackupConfig):
    """
    Set the global BackupConfig instance.

    Args:
        config: BackupConfig instance to set as global
    """
    global _config_instance
    _config_instance = config


def print_current_config():
    """Print current configuration using the global instance."""
    config = get_config()
    config.print_settings()


def apply_config_defaults():
    """Apply default settings using the global instance."""
    config = get_config()
    return config.apply_default_settings()


def validate_config() -> list:
    """
    Validate the current configuration using the global instance.

    Returns:
        list: List of validation errors
    """
    config = get_config()
    return config.validate_configuration()