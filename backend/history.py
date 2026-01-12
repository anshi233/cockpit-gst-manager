"""History Manager - JSON file persistence for instances.

Handles saving, loading, and backup of instance configurations.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("gst-manager.history")


class HistoryManager:
    """Manages instance configuration persistence."""

    def __init__(self, instances_dir: Path, max_history_files: int = 100):
        self.instances_dir = Path(instances_dir)
        self.max_history_files = max_history_files

    async def load_all_instances(self) -> List[Dict[str, Any]]:
        """Load all saved instance configurations.

        Returns:
            List of instance dictionaries.
        """
        instances = []

        if not self.instances_dir.exists():
            logger.debug(f"Instances directory does not exist: {self.instances_dir}")
            return instances

        for instance_dir in self.instances_dir.iterdir():
            if instance_dir.is_dir():
                config = await self._load_instance_config(instance_dir)
                if config:
                    instances.append(config)

        logger.info(f"Loaded {len(instances)} instances from disk")
        return instances

    async def _load_instance_config(
        self,
        instance_dir: Path
    ) -> Optional[Dict[str, Any]]:
        """Load current config for an instance.

        Args:
            instance_dir: Path to instance directory.

        Returns:
            Instance configuration dict or None.
        """
        current_file = instance_dir / "current.json"

        if not current_file.exists():
            logger.warning(f"No current.json in {instance_dir}")
            return None

        try:
            with open(current_file, "r") as f:
                config = json.load(f)
                logger.debug(f"Loaded instance: {config.get('id', 'unknown')}")
                return config
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load {current_file}: {e}")
            return None

    async def save_instance(self, instance: Dict[str, Any]) -> bool:
        """Save instance configuration.

        Creates backup of previous config before saving.

        Args:
            instance: Instance configuration dict.

        Returns:
            bool: Success status.
        """
        instance_id = instance.get("id")
        if not instance_id:
            logger.error("Cannot save instance without ID")
            return False

        instance_dir = self.instances_dir / instance_id
        instance_dir.mkdir(parents=True, exist_ok=True)

        current_file = instance_dir / "current.json"
        history_dir = instance_dir / "history"

        # Backup existing config
        if current_file.exists():
            await self._backup_config(current_file, history_dir)

        # Save new config
        try:
            with open(current_file, "w") as f:
                json.dump(instance, f, indent=2)
            logger.debug(f"Saved instance: {instance_id}")
            return True
        except IOError as e:
            logger.error(f"Failed to save instance {instance_id}: {e}")
            return False

    async def _backup_config(
        self,
        current_file: Path,
        history_dir: Path
    ) -> None:
        """Backup current config to history directory.

        Args:
            current_file: Path to current.json.
            history_dir: Path to history directory.
        """
        history_dir.mkdir(exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_file = history_dir / f"{timestamp}.json"

        try:
            shutil.copy2(current_file, backup_file)
            logger.debug(f"Created backup: {backup_file}")

            # Cleanup old backups
            await self._cleanup_history(history_dir)
        except IOError as e:
            logger.warning(f"Failed to backup config: {e}")

    async def _cleanup_history(self, history_dir: Path) -> None:
        """Remove old history files exceeding max limit.

        Args:
            history_dir: Path to history directory.
        """
        history_files = sorted(
            history_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if len(history_files) > self.max_history_files:
            for old_file in history_files[self.max_history_files:]:
                try:
                    old_file.unlink()
                    logger.debug(f"Removed old history: {old_file}")
                except IOError:
                    pass

    async def delete_instance(self, instance_id: str) -> bool:
        """Delete instance and all its history.

        Args:
            instance_id: Instance ID to delete.

        Returns:
            bool: Success status.
        """
        instance_dir = self.instances_dir / instance_id

        if not instance_dir.exists():
            logger.warning(f"Instance directory not found: {instance_id}")
            return False

        try:
            shutil.rmtree(instance_dir)
            logger.info(f"Deleted instance directory: {instance_id}")
            return True
        except IOError as e:
            logger.error(f"Failed to delete instance {instance_id}: {e}")
            return False

    async def get_instance_history(
        self,
        instance_id: str
    ) -> List[Dict[str, Any]]:
        """Get history of an instance's configurations.

        Args:
            instance_id: Instance ID.

        Returns:
            List of historical configurations (newest first).
        """
        history_dir = self.instances_dir / instance_id / "history"

        if not history_dir.exists():
            return []

        history = []
        for history_file in sorted(
            history_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        ):
            try:
                with open(history_file, "r") as f:
                    config = json.load(f)
                    config["_history_file"] = history_file.name
                    history.append(config)
            except (json.JSONDecodeError, IOError):
                continue

        return history

    async def export_instance(self, instance_id: str) -> Optional[str]:
        """Export instance configuration as JSON string.

        Args:
            instance_id: Instance ID to export.

        Returns:
            JSON string or None if not found.
        """
        instance_dir = self.instances_dir / instance_id
        current_file = instance_dir / "current.json"

        if not current_file.exists():
            return None

        try:
            with open(current_file, "r") as f:
                config = json.load(f)
                # Remove internal fields
                config.pop("_history_file", None)
                return json.dumps(config, indent=2)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to export instance {instance_id}: {e}")
            return None

    async def import_instance(self, config_json: str) -> Optional[str]:
        """Import instance from JSON string.

        Creates a new instance with a new ID if the ID already exists.

        Args:
            config_json: JSON configuration string.

        Returns:
            New instance ID or None on error.
        """
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON for import: {e}")
            return None

        instance_id = config.get("id")
        if not instance_id:
            logger.error("Import config missing ID")
            return None

        # Check if ID already exists
        existing_dir = self.instances_dir / instance_id
        if existing_dir.exists():
            # Generate new ID
            import uuid
            new_id = str(uuid.uuid4())[:8]
            config["id"] = new_id
            config["name"] = f"{config.get('name', 'Imported')} (copy)"
            instance_id = new_id
            logger.info(f"Instance ID already exists, using new ID: {new_id}")

        # Update timestamps
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        config["modified_at"] = timestamp
        if "created_at" not in config:
            config["created_at"] = timestamp

        # Save the instance
        success = await self.save_instance(config)

        return instance_id if success else None
