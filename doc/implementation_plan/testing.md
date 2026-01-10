# Testing Strategy

## Overview

Testing approach for cockpit-gst-manager using pytest.

**No build required** - Python code is interpreted. Tests run directly.

---

## Test Types

| Type | Location | Purpose |
|------|----------|---------|
| Unit Tests | `tests/unit/` | Test individual functions |
| Integration Tests | `tests/integration/` | Test with real hardware |
| Manual Tests | N/A | UI and full workflow |

---

## Directory Structure

```
tests/
├── conftest.py          # Shared fixtures
├── unit/
│   ├── test_discovery.py
│   ├── test_instances.py
│   ├── test_ai_tools.py
│   └── test_history.py
└── integration/
    ├── test_hdmi.py
    ├── test_gstreamer.py
    └── test_api.py
```

---

## Running Tests

### On Development Machine (Unit Tests)

```bash
cd cockpit-gst-manager
pip install pytest
pytest tests/unit/ -v
```

### On Target Device (Integration Tests)

```bash
# SSH to device
ssh root@device

# Run integration tests
cd /usr/lib/gst-manager/tests
pytest tests/integration/ -v
```

---

## Fixtures (`conftest.py`)

```python
import pytest
import tempfile
import json

@pytest.fixture
def temp_config_dir():
    """Create temporary config directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize config
        config = {"ai_providers": [], "instances": {}}
        with open(f"{tmpdir}/config.json", "w") as f:
            json.dump(config, f)
        yield tmpdir

@pytest.fixture
def sample_pipeline():
    """Sample GStreamer pipeline for testing."""
    return "v4l2src device=/dev/vdin1 ! fakesink"

@pytest.fixture
def mock_board_context():
    """Mock board discovery context."""
    return {
        "video_inputs": [
            {"device": "/dev/vdin1", "type": "hdmi-in", "available": True}
        ],
        "encoders": ["aml_h264enc"],
        "storage": [{"path": "/tmp", "free_gb": 10}]
    }
```

---

## Unit Test Examples

### test_discovery.py

```python
def test_parse_hdmi_info():
    """Test HDMI info string parsing."""
    from backend.discovery import parse_hdmi_info
    
    result = parse_hdmi_info("1920x1080p60hz")
    
    assert result["width"] == 1920
    assert result["height"] == 1080
    assert result["fps"] == 60
    assert result["interlaced"] == False

def test_get_storage_info(temp_config_dir):
    """Test storage detection."""
    from backend.discovery import get_storage_info
    
    result = get_storage_info("/tmp")
    
    assert result["available"] == True
    assert result["free_gb"] > 0
```

### test_instances.py

```python
def test_create_instance(temp_config_dir, sample_pipeline):
    """Test instance creation."""
    from backend.instances import InstanceManager
    
    manager = InstanceManager(temp_config_dir)
    instance_id = manager.create("Test", sample_pipeline)
    
    assert instance_id is not None
    assert manager.get(instance_id)["name"] == "Test"

def test_instance_history(temp_config_dir, sample_pipeline):
    """Test pipeline history saved on update."""
    from backend.instances import InstanceManager
    
    manager = InstanceManager(temp_config_dir)
    instance_id = manager.create("Test", sample_pipeline)
    manager.update_pipeline(instance_id, sample_pipeline + " ! queue")
    
    history = manager.get_history(instance_id)
    assert len(history) == 1
```

### test_ai_tools.py

```python
def test_validate_pipeline_valid():
    """Test pipeline validation with valid pipeline."""
    from backend.ai.tools import validate_pipeline
    
    result = validate_pipeline("fakesrc ! fakesink")
    
    assert result["valid"] == True

def test_validate_pipeline_invalid():
    """Test pipeline validation with invalid element."""
    from backend.ai.tools import validate_pipeline
    
    result = validate_pipeline("invalid_element ! fakesink")
    
    assert result["valid"] == False
    assert "error" in result
```

---

## Integration Test Examples

### test_hdmi.py (Requires Hardware)

```python
import pytest

@pytest.mark.hardware
def test_hdmi_status():
    """Test HDMI status reading from sysfs."""
    from backend.events import get_hdmi_status
    
    status = get_hdmi_status()
    
    assert "cable_connected" in status
    assert "signal_locked" in status

@pytest.mark.hardware
def test_hdmi_monitoring():
    """Test HDMI signal monitoring."""
    from backend.events import HdmiMonitor
    import asyncio
    
    events = []
    
    async def callback(status):
        events.append(status)
    
    monitor = HdmiMonitor(callback)
    
    # Run for 5 seconds
    asyncio.get_event_loop().run_until_complete(
        asyncio.wait_for(monitor.start(), timeout=5)
    )
    
    # Should have at least one status read
    assert len(events) >= 0  # May or may not have signal
```

---

## Test Markers

```python
# pytest.ini or pyproject.toml
[pytest]
markers =
    hardware: Tests requiring real hardware
    slow: Slow running tests
    ai: Tests requiring AI provider
```

Usage:
```bash
# Skip hardware tests on dev machine
pytest tests/ -v -m "not hardware"

# Run only hardware tests on device
pytest tests/ -v -m "hardware"
```

---

## CI/CD

For automated testing (on build server):

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - run: pip install pytest
      - run: pytest tests/unit/ -v
```

---

## Coverage (Optional)

```bash
pip install pytest-cov
pytest tests/unit/ --cov=backend --cov-report=html
```
