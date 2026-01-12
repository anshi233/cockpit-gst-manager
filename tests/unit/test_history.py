"""Unit tests for History Manager."""

import asyncio
import json
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from history import HistoryManager


class TestHistoryManager:
    """Tests for HistoryManager class."""

    @pytest.mark.asyncio
    async def test_save_and_load_instance(self, history_manager, sample_instance):
        """Test saving and loading an instance."""
        await history_manager.save_instance(sample_instance)
        
        instances = await history_manager.load_all_instances()
        
        assert len(instances) == 1
        assert instances[0]["id"] == sample_instance["id"]
        assert instances[0]["name"] == sample_instance["name"]

    @pytest.mark.asyncio
    async def test_delete_instance(self, history_manager, sample_instance):
        """Test deleting an instance."""
        await history_manager.save_instance(sample_instance)
        
        result = await history_manager.delete_instance(sample_instance["id"])
        
        assert result is True
        instances = await history_manager.load_all_instances()
        assert len(instances) == 0

    @pytest.mark.asyncio
    async def test_backup_on_save(self, history_manager, sample_instance):
        """Test that saving creates backup of previous version."""
        await history_manager.save_instance(sample_instance)
        
        # Modify and save again
        sample_instance["name"] = "Modified Name"
        await history_manager.save_instance(sample_instance)
        
        # Check history exists
        history = await history_manager.get_instance_history(sample_instance["id"])
        assert len(history) == 1
        assert history[0]["name"] == "Test Stream"  # Original name

    @pytest.mark.asyncio
    async def test_export_instance(self, history_manager, sample_instance):
        """Test exporting instance as JSON."""
        await history_manager.save_instance(sample_instance)
        
        exported = await history_manager.export_instance(sample_instance["id"])
        
        assert exported is not None
        data = json.loads(exported)
        assert data["id"] == sample_instance["id"]

    @pytest.mark.asyncio
    async def test_import_instance(self, history_manager, sample_instance):
        """Test importing instance from JSON."""
        config_json = json.dumps(sample_instance)
        
        instance_id = await history_manager.import_instance(config_json)
        
        assert instance_id == sample_instance["id"]
        instances = await history_manager.load_all_instances()
        assert len(instances) == 1

    @pytest.mark.asyncio
    async def test_import_duplicate_creates_new_id(self, history_manager, sample_instance):
        """Test that importing duplicate ID creates new instance."""
        # First save
        await history_manager.save_instance(sample_instance)
        
        # Import same config
        config_json = json.dumps(sample_instance)
        new_id = await history_manager.import_instance(config_json)
        
        # Should have new ID
        assert new_id != sample_instance["id"]
        instances = await history_manager.load_all_instances()
        assert len(instances) == 2

    @pytest.mark.asyncio  
    async def test_load_empty_directory(self, history_manager):
        """Test loading from empty directory."""
        instances = await history_manager.load_all_instances()
        assert instances == []

    @pytest.mark.asyncio
    async def test_history_cleanup(self, history_manager, sample_instance):
        """Test that old history files are cleaned up."""
        history_manager.max_history_files = 3
        await history_manager.save_instance(sample_instance)
        
        # Save multiple times to create history
        for i in range(5):
            sample_instance["name"] = f"Version {i}"
            await history_manager.save_instance(sample_instance)
        
        history = await history_manager.get_instance_history(sample_instance["id"])
        assert len(history) <= 3
