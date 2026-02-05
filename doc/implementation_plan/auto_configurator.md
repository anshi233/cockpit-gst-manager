# Auto Configurator Implementation Plan (Refined)

## Overview

This document outlines the refined implementation for the Auto Configurator feature based on specific requirements for a single auto-managed GStreamer instance that captures HDMI TX output.

---

## Refined Requirements Summary

### Default Pipeline Template

```bash
gst-launch-1.0 -e -v \
   v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! \
   video/x-raw,format=NV21,width=3840,height=2160,framerate=60/1 ! \
   queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! \
   amlvenc gop=60 gop-pattern=0 framerate=60 bitrate=20000 rc-mode=1 ! video/x-h265 ! \
   h265parse config-interval=-1 ! \
   queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! \
   mux. \
   alsasrc device=hw:0,6 buffer-time=50000 provide-clock=false slave-method=re-timestamp ! \
   audio/x-raw,rate=48000,channels=2,format=S16LE ! \
   queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! \
   audioconvert ! audioresample ! \
   avenc_aac bitrate=128000 ! aacparse ! \
   queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! \
   mux. \
   mpegtsmux name=mux alignment=7 latency=100000000 ! \
   srtsink uri="srt://:8888" wait-for-connection=false latency=600 sync=false
```

### Dynamic Values (from HDMI TX)
- `width`, `height` - Resolution from HDMI TX
- `framerate` - Framerate from HDMI TX
- `gop` - Calculated as: `framerate × gop_interval_seconds`

### User-Configurable Options

| Setting | Description | Default |
|---------|-------------|---------|
| **GOP Interval (seconds)** | GOP size in seconds | 1 second |
| **Bitrate** | Video bitrate in kbps | 20000 (20 Mbps) |
| **RC Mode** | Rate control mode | CBR (rc-mode=1) |
| **Audio Source** | Audio input device | HDMI RX (hw:0,6) |
| **Recording** | Enable file recording | Disabled |
| **Recording Path** | MKV file path | User specified |
| **Streaming** | SRT streaming | **Always Enabled** |
| **SRT Port** | SRT listener port | 8888 |

### Audio Source Options
- **HDMI RX Audio**: `alsasrc device=hw:0,6 buffer-time=50000 provide-clock=false slave-method=re-timestamp`
- **Line In**: `alsasrc device=hw:0,0 buffer-time=50000 provide-clock=false slave-method=re-timestamp`

### Streaming Behavior
- **Always enabled** via SRT
- Port: **8888** (configurable)
- URI: `srt://:PORT` (listener mode)

### Recording Format
- Container: **MKV** (Matroska)
- Uses `matroskamux` element
- Enabled/disabled by user

### Instance Limit
- **Only ONE auto instance** allowed per system
- If user creates a new one, replace the existing

### HDMI State Management
```
HDMI RX Connected + Stable
        ↓
Wait 1-2 seconds for TX stabilization
        ↓
Poll sysfs for TX resolution/framerate
        ↓
Both RX and TX ready + passthrough active
        ↓
[Start Auto Instance]
        ↓
Monitor HDMI states
        ↓
[If RX disconnects OR TX disconnects OR passthrough lost]
        ↓
[Stop Auto Instance]
        ↓
[Wait for both ready again]
        ↓
[Restart Auto Instance with new resolution/framerate]
```

---

## Architecture Changes (Refined)

### Backend

```
backend/
├── main.py                    # Add AutoInstanceManager initialization
├── api.py                     # Add D-Bus methods for auto-instance
├── instances.py               # Add 'instance_type' field (auto/custom)
├── events.py                  # Enhance for HDMI TX/RX coordination
├── tvservice.py               # Extend for HDMI TX state detection
└── auto_instance.py           # NEW: Single auto instance manager
```

### Frontend

```
frontend/
├── index.html                 # Add Auto Configurator panel (simplified)
├── gst-manager.css            # Add styles
├── gst-manager.js             # Add auto-instance handling
└── auto-configurator.js       # NEW: Auto configurator UI logic (simplified)
```

---

## Phase 1: Backend - Extend tvservice.py for HDMI TX Monitoring

### Current State
The `tvservice.py` monitors HDMI RX (input) via TvClientLib. Need to add HDMI TX monitoring.

### Implementation

```python
# Add to tvservice.py

@dataclass
class HdmiTxStatus:
    """HDMI TX (output) status."""
    connected: bool = False
    enabled: bool = False
    width: int = 0
    height: int = 0
    fps: int = 0
    
    @property
    def resolution(self) -> str:
        if self.width == 0 or self.height == 0:
            return ""
        return f"{self.width}x{self.height}p{self.fps}"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "enabled": self.enabled,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "resolution": self.resolution
        }


class TvClientLib:
    # ... existing code ...
    
    def get_hdmi_tx_status(self) -> HdmiTxStatus:
        """Get HDMI TX output status from sysfs.
        
        Reads from /sys/class/amhdmitx/amhdmitx0/ or similar.
        """
        status = HdmiTxStatus()
        
        # Try sysfs paths for HDMI TX
        sysfs_paths = [
            "/sys/class/amhdmitx/amhdmitx0",
            "/sys/class/amhdmitx/amhdmitx1",
            "/sys/devices/platform/amhdmitx",
        ]
        
        for path in sysfs_paths:
            p = Path(path)
            if p.exists():
                status = self._read_hdmi_tx_sysfs(p)
                if status.connected:
                    break
        
        return status
    
    def _read_hdmi_tx_sysfs(self, path: Path) -> HdmiTxStatus:
        """Read HDMI TX status from sysfs."""
        status = HdmiTxStatus()
        
        try:
            # Check if output is enabled/configured
            config_path = path / "config"
            if config_path.exists():
                config = self._read_file(config_path)
                status.enabled = "VIC:" in config or "vic:" in config
            
            # Read current output mode
            disp_cap = path / "disp_cap"
            if disp_cap.exists():
                # Parse resolution from disp_cap
                content = self._read_file(disp_cap)
                parsed = self._parse_resolution(content)
                status.width = parsed.get("width", 0)
                status.height = parsed.get("height", 0)
                status.fps = parsed.get("fps", 0)
                status.connected = status.width > 0
            
            # Alternative: read from /sys/class/display/ or similar
            if not status.connected:
                # Try alternative paths
                status = self._read_alt_hdmi_tx_sysfs()
                
        except Exception as e:
            logger.debug(f"Failed to read HDMI TX sysfs: {e}")
        
        return status
    
    def _parse_resolution(self, content: str) -> Dict[str, int]:
        """Parse resolution string like '3840x2160p60hz'."""
        result = {"width": 0, "height": 0, "fps": 0}
        
        # Pattern: 3840x2160p60hz or 1920x1080i50hz
        match = re.search(r"(\d+)x(\d+)([pi])(\d+)", content.lower())
        if match:
            result["width"] = int(match.group(1))
            result["height"] = int(match.group(2))
            result["fps"] = int(match.group(4))
        
        return result
    
    def _read_file(self, path: Path) -> str:
        """Read file content safely."""
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except Exception:
            return ""


class TvServiceMonitor:
    """Extend to support TX monitoring."""
    
    def __init__(self, ...):
        # ... existing ...
        self._tx_monitoring = False
        self._last_tx_status: Optional[HdmiTxStatus] = None
    
    def get_hdmi_tx_status(self) -> HdmiTxStatus:
        """Get current HDMI TX status."""
        if self._client and self._client.available:
            return self._client.get_hdmi_tx_status()
        return HdmiTxStatus()
```

### Tasks
- [ ] Add `HdmiTxStatus` dataclass
- [ ] Add `get_hdmi_tx_status()` method to TvClientLib
- [ ] Add sysfs reading for HDMI TX state
- [ ] Add resolution parsing from TX output
- [ ] Update TvServiceMonitor for TX polling

---

## Phase 2: Backend - Create Auto Instance Manager

### New File: `backend/auto_instance.py`

```python
"""Auto Instance Manager - Single auto-managed GStreamer instance.

Manages a single auto-generated instance that:
1. Captures HDMI TX output from /dev/video71
2. Uses dynamic resolution/framerate from HDMI TX
3. Auto-starts/stops based on HDMI RX/TX state
4. Supports SRT streaming (always on) + optional MKV recording
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("gst-manager.auto_instance")


class AudioSource(Enum):
    HDMI_RX = "hdmi_rx"  # hw:0,6
    LINE_IN = "line_in"  # hw:0,0


@dataclass
class AutoInstanceConfig:
    """Configuration for auto-generated instance."""
    # GOP interval in seconds (used to calculate gop = framerate * interval)
    gop_interval_seconds: float = 1.0
    
    # Video settings
    bitrate_kbps: int = 20000  # 20 Mbps default
    rc_mode: int = 1  # 0=VBR, 1=CBR, 2=FixQP (CBR default)
    
    # Audio settings
    audio_source: AudioSource = AudioSource.HDMI_RX
    
    # Streaming (always enabled)
    srt_port: int = 8888
    
    # Recording (optional)
    recording_enabled: bool = False
    recording_path: str = "/mnt/sdcard/recordings/capture.mkv"
    
    # Auto-start behavior
    autostart_on_ready: bool = True
    
    # Runtime info (from HDMI TX detection)
    width: int = 3840
    height: int = 2160
    framerate: int = 60
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["audio_source"] = self.audio_source.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoInstanceConfig":
        if "audio_source" in data:
            data["audio_source"] = AudioSource(data["audio_source"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class PipelineBuilder:
    """Builds GStreamer pipeline for auto instance."""
    
    # Template based on provided working command
    VIDEO_SOURCE = (
        'v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! '
        'video/x-raw,format=NV21,width={width},height={height},framerate={framerate}/1 ! '
        'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0'
    )
    
    VIDEO_ENCODER = (
        'amlvenc gop={gop} gop-pattern=0 framerate={framerate} bitrate={bitrate} rc-mode={rc_mode} ! '
        'video/x-h265'
    )
    
    VIDEO_PARSER = 'h265parse config-interval=-1'
    
    VIDEO_QUEUE = 'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0'
    
    AUDIO_SOURCE_HDMI = (
        'alsasrc device=hw:0,6 buffer-time=50000 provide-clock=false slave-method=re-timestamp ! '
        'audio/x-raw,rate=48000,channels=2,format=S16LE'
    )
    
    AUDIO_SOURCE_LINEIN = (
        'alsasrc device=hw:0,0 buffer-time=50000 provide-clock=false slave-method=re-timestamp ! '
        'audio/x-raw,rate=48000,channels=2,format=S16LE'
    )
    
    AUDIO_QUEUE = 'queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0'
    
    AUDIO_ENCODER = 'audioconvert ! audioresample ! avenc_aac bitrate=128000 ! aacparse'
    
    MUXER_MPEGTS = 'mpegtsmux name=mux alignment=7 latency=100000000'
    MUXER_MKV = 'matroskamux name=mux'
    
    SRT_SINK = (
        'srtsink uri="srt://:{port}" wait-for-connection=false latency=600 sync=false'
    )
    
    def build(self, config: AutoInstanceConfig) -> str:
        """Build complete pipeline string."""
        # Calculate GOP from framerate and interval
        gop = int(config.framerate * config.gop_interval_seconds)
        
        elements = []
        
        # Video branch
        elements.append(self.VIDEO_SOURCE.format(
            width=config.width,
            height=config.height,
            framerate=config.framerate
        ))
        
        elements.append(self.VIDEO_ENCODER.format(
            gop=gop,
            framerate=config.framerate,
            bitrate=config.bitrate_kbps,
            rc_mode=config.rc_mode
        ))
        
        elements.append(self.VIDEO_PARSER)
        elements.append(self.VIDEO_QUEUE)
        elements.append('mux.')  # Link to muxer
        
        # Audio branch
        if config.audio_source == AudioSource.HDMI_RX:
            elements.append(self.AUDIO_SOURCE_HDMI)
        else:
            elements.append(self.AUDIO_SOURCE_LINEIN)
        
        elements.append(self.AUDIO_QUEUE)
        elements.append(self.AUDIO_ENCODER)
        elements.append(self.AUDIO_QUEUE)
        elements.append('mux.')  # Link to muxer
        
        # Output configuration
        if config.recording_enabled:
            # Both recording and streaming - use tee
            elements.append(self.MUXER_MPEGTS)
            elements.append('! tee name=t')
            elements.append(f't. ! queue ! filesink location="{config.recording_path}"')
            elements.append(f't. ! queue ! {self.SRT_SINK.format(port=config.srt_port)}')
        else:
            # Streaming only
            elements.append(self.MUXER_MPEGTS)
            elements.append(self.SRT_SINK.format(port=config.srt_port))
        
        return ' ! '.join(elements)
    
    def build_preview(self, config: AutoInstanceConfig) -> str:
        """Build pipeline for preview (with line breaks)."""
        pipeline = self.build(config)
        # Add line breaks for readability
        return pipeline.replace(' ! ', ' ! \\\n   ')


class AutoInstanceManager:
    """Manages the single auto instance."""
    
    CONFIG_FILE = Path("/var/lib/gst-manager/auto_instance.json")
    
    def __init__(self, instance_manager, event_manager):
        self.instance_manager = instance_manager
        self.event_manager = event_manager
        self.config: Optional[AutoInstanceConfig] = None
        self.instance_id: Optional[str] = None
        self._builder = PipelineBuilder()
        
    async def load(self) -> bool:
        """Load auto instance configuration from disk."""
        if not self.CONFIG_FILE.exists():
            logger.info("No auto instance config found")
            return False
        
        try:
            with open(self.CONFIG_FILE, "r") as f:
                data = json.load(f)
            
            self.config = AutoInstanceConfig.from_dict(data.get("config", {}))
            self.instance_id = data.get("instance_id")
            
            # Verify instance exists
            if self.instance_id:
                instance = self.instance_manager.get_instance(self.instance_id)
                if not instance:
                    logger.warning(f"Auto instance {self.instance_id} not found, will recreate")
                    self.instance_id = None
            
            logger.info(f"Loaded auto instance config: {self.config.to_dict()}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load auto instance config: {e}")
            return False
    
    async def save(self) -> bool:
        """Save auto instance configuration to disk."""
        try:
            data = {
                "config": self.config.to_dict() if self.config else {},
                "instance_id": self.instance_id
            }
            
            self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
            
            logger.debug("Saved auto instance config")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save auto instance config: {e}")
            return False
    
    async def create_or_update(
        self,
        config: AutoInstanceConfig,
        hdmi_tx_status: Optional[Any] = None
    ) -> str:
        """Create or update the auto instance.
        
        Args:
            config: New configuration
            hdmi_tx_status: Current HDMI TX status for resolution
            
        Returns:
            instance_id: The auto instance ID
        """
        # Update config with current HDMI TX info
        if hdmi_tx_status:
            config.width = hdmi_tx_status.width or config.width
            config.height = hdmi_tx_status.height or config.height
            config.framerate = hdmi_tx_status.fps or config.framerate
        
        self.config = config
        
        # Generate pipeline
        pipeline = self._builder.build(config)
        
        # Delete existing auto instance if present
        if self.instance_id:
            existing = self.instance_manager.get_instance(self.instance_id)
            if existing:
                if existing.status.value == "running":
                    await self.instance_manager.stop_instance(self.instance_id)
                await self.instance_manager.delete_instance(self.instance_id)
        
        # Create new auto instance
        from instances import InstanceType
        
        instance_id = await self.instance_manager.create_instance(
            name="Auto HDMI Capture",
            pipeline=pipeline
        )
        
        # Mark as auto instance
        instance = self.instance_manager.get_instance(instance_id)
        if instance:
            instance.instance_type = InstanceType.AUTO
            instance.auto_config = config.to_dict()
            instance.autostart = config.autostart_on_ready
            instance.trigger_event = "hdmi_passthrough_ready"
        
        self.instance_id = instance_id
        await self.save()
        
        logger.info(f"Created auto instance: {instance_id}")
        return instance_id
    
    def get_pipeline_preview(self, config: AutoInstanceConfig) -> str:
        """Get pipeline preview without creating instance."""
        return self._builder.build_preview(config)
    
    async def on_passthrough_ready(self, hdmi_tx_status: Any) -> None:
        """Called when HDMI passthrough becomes ready."""
        if not self.config:
            logger.debug("No auto instance config, skipping")
            return
        
        if not self.config.autostart_on_ready:
            logger.debug("Auto-start disabled")
            return
        
        # Update resolution from current TX status
        self.config.width = hdmi_tx_status.width or self.config.width
        self.config.height = hdmi_tx_status.height or self.config.height
        self.config.framerate = hdmi_tx_status.fps or self.config.framerate
        
        # Regenerate pipeline with new resolution
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.status.value == "stopped":
                # Recreate with new pipeline
                await self.create_or_update(self.config, hdmi_tx_status)
                
                # Start the instance
                await self.instance_manager.start_instance(self.instance_id)
                logger.info(f"Auto-started instance {self.instance_id}")
    
    async def on_passthrough_lost(self) -> None:
        """Called when HDMI passthrough is lost."""
        if not self.instance_id:
            return
        
        instance = self.instance_manager.get_instance(self.instance_id)
        if instance and instance.status.value == "running":
            await self.instance_manager.stop_instance(self.instance_id)
            logger.info(f"Auto-stopped instance {self.instance_id} due to passthrough loss")
    
    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get current config as dict."""
        if self.config:
            return self.config.to_dict()
        return None
    
    async def update_config(self, updates: Dict[str, Any]) -> bool:
        """Update configuration (preserves instance if stopped)."""
        if not self.config:
            return False
        
        # Apply updates
        if "gop_interval_seconds" in updates:
            self.config.gop_interval_seconds = float(updates["gop_interval_seconds"])
        if "bitrate_kbps" in updates:
            self.config.bitrate_kbps = int(updates["bitrate_kbps"])
        if "rc_mode" in updates:
            self.config.rc_mode = int(updates["rc_mode"])
        if "audio_source" in updates:
            self.config.audio_source = AudioSource(updates["audio_source"])
        if "srt_port" in updates:
            self.config.srt_port = int(updates["srt_port"])
        if "recording_enabled" in updates:
            self.config.recording_enabled = bool(updates["recording_enabled"])
        if "recording_path" in updates:
            self.config.recording_path = updates["recording_path"]
        if "autostart_on_ready" in updates:
            self.config.autostart_on_ready = bool(updates["autostart_on_ready"])
        
        # Recreate instance if it exists and is stopped
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.status.value == "stopped":
                await self.create_or_update(self.config)
        
        await self.save()
        return True
```

### Tasks
- [ ] Create `auto_instance.py` with AutoInstanceConfig dataclass
- [ ] Implement PipelineBuilder with the exact template
- [ ] Implement AutoInstanceManager for single instance management
- [ ] Add config persistence (JSON file)

---

## Phase 3: Backend - Enhance EventManager

### Changes to `events.py`

```python
class EventManager:
    def __init__(self, instance_manager, service, auto_instance_manager=None):
        self.instance_manager = instance_manager
        self.service = service
        self.auto_instance_manager = auto_instance_manager
        
        # HDMI monitoring
        self.hdmi_rx_monitor: Optional[TvServiceMonitor] = None
        self._tx_status: Optional[HdmiTxStatus] = None
        self._rx_stable_time: Optional[float] = None
        self._tx_check_scheduled = False
        
    async def start(self) -> None:
        """Start HDMI RX monitoring."""
        self.hdmi_rx_monitor = TvServiceMonitor(
            source=TvSourceInput.SOURCE_HDMI1,
            on_signal_change=self._on_rx_signal_change
        )
        await self.hdmi_rx_monitor.start()
        
    async def _on_rx_signal_change(self, info: SignalInfo) -> None:
        """Handle HDMI RX signal change."""
        # Check if RX just became stable
        if info.is_stable and not self._rx_stable_time:
            logger.info(f"HDMI RX stable: {info.resolution}")
            self._rx_stable_time = time.time()
            
            # Schedule TX check after 1-2 seconds
            if not self._tx_check_scheduled:
                self._tx_check_scheduled = True
                await asyncio.sleep(1.5)  # Wait 1.5 seconds for TX to stabilize
                await self._check_tx_status()
                self._tx_check_scheduled = False
                
        elif not info.is_stable:
            self._rx_stable_time = None
            self._tx_status = None
            
            # Notify passthrough lost
            if self.auto_instance_manager:
                await self.auto_instance_manager.on_passthrough_lost()
            
            # Emit signal
            if self.service:
                self.service.emit_passthrough_state(False, {})
    
    async def _check_tx_status(self) -> None:
        """Check HDMI TX status after RX is stable."""
        if not self._rx_stable_time:
            return
        
        # Poll sysfs for TX status
        self._tx_status = await self._poll_tx_status()
        
        if self._tx_status and self._tx_status.connected:
            logger.info(f"HDMI TX ready: {self._tx_status.resolution}")
            
            # Passthrough is ready
            state_dict = {
                "rx_connected": True,
                "rx_stable": True,
                "tx_connected": self._tx_status.connected,
                "tx_enabled": self._tx_status.enabled,
                "width": self._tx_status.width,
                "height": self._tx_status.height,
                "framerate": self._tx_status.fps,
                "can_capture": True
            }
            
            # Notify auto instance manager
            if self.auto_instance_manager:
                await self.auto_instance_manager.on_passthrough_ready(self._tx_status)
            
            # Emit D-Bus signal
            if self.service:
                self.service.emit_passthrough_state(True, state_dict)
        else:
            logger.debug("HDMI TX not ready yet")
    
    async def _poll_tx_status(self) -> Optional[HdmiTxStatus]:
        """Poll sysfs for HDMI TX status."""
        # Use TvClientLib to read TX status
        client = TvClientLib()
        if client.connect():
            return client.get_hdmi_tx_status()
        return None
    
    def get_passthrough_state(self) -> Dict[str, Any]:
        """Get current passthrough state."""
        return {
            "rx_connected": self._rx_stable_time is not None,
            "rx_stable": self._rx_stable_time is not None,
            "tx_connected": self._tx_status.connected if self._tx_status else False,
            "tx_enabled": self._tx_status.enabled if self._tx_status else False,
            "width": self._tx_status.width if self._tx_status else 0,
            "height": self._tx_status.height if self._tx_status else 0,
            "framerate": self._tx_status.fps if self._tx_status else 0,
            "can_capture": (
                self._rx_stable_time is not None and
                self._tx_status is not None and
                self._tx_status.connected
            )
        }
```

### Tasks
- [ ] Add TX status tracking to EventManager
- [ ] Implement 1.5-second delay after RX stable before TX check
- [ ] Implement TX sysfs polling
- [ ] Integrate with AutoInstanceManager
- [ ] Add `PassthroughStateChanged` D-Bus signal

---

## Phase 4: Backend - Add D-Bus Methods

### Changes to `api.py`

```python
class GstManagerInterface:
    # ... existing methods ...
    
    @method()
    def GetAutoInstanceConfig(self) -> "s":
        """Get auto instance configuration.
        
        Returns:
            JSON with config or empty object if not configured.
        """
        if self.auto_instance_manager:
            config = self.auto_instance_manager.get_config()
            return json.dumps(config or {})
        return json.dumps({})
    
    @method()
    async def SetAutoInstanceConfig(self, config_json: "s") -> "b":
        """Create or update auto instance configuration.
        
        Args:
            config_json: JSON with config fields:
                - gop_interval_seconds: float
                - bitrate_kbps: int
                - rc_mode: int (0=VBR, 1=CBR, 2=FixQP)
                - audio_source: str ("hdmi_rx" or "line_in")
                - srt_port: int
                - recording_enabled: bool
                - recording_path: str
                - autostart_on_ready: bool
                
        Returns:
            success: True if applied
        """
        try:
            if not self.auto_instance_manager:
                return False
            
            config_data = json.loads(config_json)
            
            # Get current HDMI TX status for resolution
            tx_status = None
            if self.event_manager:
                state = self.event_manager.get_passthrough_state()
                if state.get("can_capture"):
                    # Create a mock status object
                    class MockStatus:
                        pass
                    tx_status = MockStatus()
                    tx_status.width = state.get("width", 3840)
                    tx_status.height = state.get("height", 2160)
                    tx_status.fps = state.get("framerate", 60)
            
            # Create new config
            from auto_instance import AutoInstanceConfig, AudioSource
            config = AutoInstanceConfig(
                gop_interval_seconds=config_data.get("gop_interval_seconds", 1.0),
                bitrate_kbps=config_data.get("bitrate_kbps", 20000),
                rc_mode=config_data.get("rc_mode", 1),
                audio_source=AudioSource(config_data.get("audio_source", "hdmi_rx")),
                srt_port=config_data.get("srt_port", 8888),
                recording_enabled=config_data.get("recording_enabled", False),
                recording_path=config_data.get("recording_path", "/mnt/sdcard/recordings/capture.mkv"),
                autostart_on_ready=config_data.get("autostart_on_ready", True)
            )
            
            await self.auto_instance_manager.create_or_update(config, tx_status)
            return True
            
        except Exception as e:
            logger.error(f"SetAutoInstanceConfig failed: {e}")
            return False
    
    @method()
    def GetAutoInstancePipelinePreview(self, config_json: "s") -> "s":
        """Get pipeline preview for given config.
        
        Args:
            config_json: Configuration JSON
            
        Returns:
            Pipeline string with line breaks
        """
        try:
            from auto_instance import AutoInstanceConfig, PipelineBuilder, AudioSource
            
            config_data = json.loads(config_json)
            config = AutoInstanceConfig(
                gop_interval_seconds=config_data.get("gop_interval_seconds", 1.0),
                bitrate_kbps=config_data.get("bitrate_kbps", 20000),
                rc_mode=config_data.get("rc_mode", 1),
                audio_source=AudioSource(config_data.get("audio_source", "hdmi_rx")),
                srt_port=config_data.get("srt_port", 8888),
                recording_enabled=config_data.get("recording_enabled", False),
                recording_path=config_data.get("recording_path", "/mnt/sdcard/recordings/capture.mkv")
            )
            
            # Use detected resolution if available
            if self.event_manager:
                state = self.event_manager.get_passthrough_state()
                config.width = state.get("width", 3840)
                config.height = state.get("height", 2160)
                config.framerate = state.get("framerate", 60)
            
            builder = PipelineBuilder()
            return builder.build_preview(config)
            
        except Exception as e:
            logger.error(f"Pipeline preview failed: {e}")
            return f"Error: {e}"
    
    @method()
    def GetPassthroughState(self) -> "s":
        """Get current HDMI passthrough state.
        
        Returns:
            JSON with state info
        """
        if self.event_manager:
            return json.dumps(self.event_manager.get_passthrough_state())
        return json.dumps({"available": False})
    
    @method()
    async def DeleteAutoInstance(self) -> "b":
        """Delete the auto instance."""
        if not self.auto_instance_manager:
            return False
        
        if self.auto_instance_manager.instance_id:
            instance_id = self.auto_instance_manager.instance_id
            instance = self.instance_manager.get_instance(instance_id)
            if instance:
                if instance.status.value == "running":
                    await self.instance_manager.stop_instance(instance_id)
                await self.instance_manager.delete_instance(instance_id)
            
            self.auto_instance_manager.instance_id = None
            self.auto_instance_manager.config = None
            await self.auto_instance_manager.save()
        
        return True
    
    # --- Signals ---
    
    @signal()
    def PassthroughStateChanged(self, can_capture: "b", state_json: "s") -> "bs":
        """Emitted when passthrough state changes."""
        return [can_capture, state_json]
```

### Tasks
- [ ] Add `GetAutoInstanceConfig` D-Bus method
- [ ] Add `SetAutoInstanceConfig` D-Bus method
- [ ] Add `GetAutoInstancePipelinePreview` D-Bus method
- [ ] Add `GetPassthroughState` D-Bus method
- [ ] Add `DeleteAutoInstance` D-Bus method
- [ ] Add `PassthroughStateChanged` D-Bus signal

---

## Phase 5: Frontend - Simplified Auto Configurator UI

### Changes to `index.html`

Replace the complex auto-configurator modal with a simpler panel:

```html
<!-- Auto Configurator Panel (replaces complex modal) -->
<section id="auto-config-panel" class="gst-panel" style="display: none;">
    <h2 class="gst-panel-title">Auto HDMI Capture</h2>
    
    <!-- HDMI Status -->
    <div class="gst-hdmi-status-bar">
        <div class="gst-hdmi-indicator-row">
            <span class="gst-hdmi-dot" id="auto-hdmi-rx-dot"></span>
            <span>HDMI RX: <span id="auto-hdmi-rx-text">Unknown</span></span>
        </div>
        <div class="gst-hdmi-indicator-row">
            <span class="gst-hdmi-dot" id="auto-hdmi-tx-dot"></span>
            <span>HDMI TX: <span id="auto-hdmi-tx-text">Unknown</span></span>
        </div>
        <div class="gst-hdmi-indicator-row">
            <span class="gst-hdmi-dot" id="auto-passthrough-dot"></span>
            <span>Passthrough: <span id="auto-passthrough-text">Unknown</span></span>
        </div>
        <div class="gst-detected-resolution" id="auto-detected-res">
            Detected: 3840x2160p60
        </div>
    </div>
    
    <!-- Configuration Form -->
    <div class="gst-auto-config-form">
        <h3>Video Settings</h3>
        
        <div class="gst-form-row">
            <div class="gst-form-group">
                <label for="auto-gop-interval">GOP Interval (seconds)</label>
                <input type="number" id="auto-gop-interval" class="gst-input" 
                       value="1.0" min="0.5" max="10" step="0.5">
            </div>
            <div class="gst-form-group">
                <label for="auto-bitrate">Bitrate (kbps)</label>
                <input type="number" id="auto-bitrate" class="gst-input" 
                       value="20000" min="1000" max="100000" step="1000">
            </div>
        </div>
        
        <div class="gst-form-row">
            <div class="gst-form-group">
                <label for="auto-rc-mode">RC Mode</label>
                <select id="auto-rc-mode" class="gst-select">
                    <option value="1" selected>CBR</option>
                    <option value="0">VBR</option>
                    <option value="2">Fix QP</option>
                </select>
            </div>
        </div>
        
        <h3>Audio Settings</h3>
        <div class="gst-form-group">
            <label for="auto-audio-source">Audio Source</label>
            <select id="auto-audio-source" class="gst-select">
                <option value="hdmi_rx" selected>HDMI RX (hw:0,6)</option>
                <option value="line_in">Line In (hw:0,0)</option>
            </select>
        </div>
        
        <h3>Streaming (SRT)</h3>
        <div class="gst-form-group">
            <label for="auto-srt-port">SRT Port</label>
            <input type="number" id="auto-srt-port" class="gst-input" 
                   value="8888" min="1024" max="65535">
            <span class="gst-hint">Stream URL: srt://THIS_IP:8888</span>
        </div>
        
        <h3>Recording (MKV)</h3>
        <label class="gst-toggle-label">
            <input type="checkbox" id="auto-recording-enabled">
            <span class="gst-toggle-slider"></span>
            Enable Recording
        </label>
        <div id="auto-recording-path-group" class="gst-form-group" style="display: none;">
            <label for="auto-recording-path">Recording Path</label>
            <input type="text" id="auto-recording-path" class="gst-input" 
                   placeholder="/mnt/sdcard/recordings/capture.mkv">
        </div>
        
        <h3>Auto-Start</h3>
        <label class="gst-toggle-label">
            <input type="checkbox" id="auto-autostart" checked>
            <span class="gst-toggle-slider"></span>
            Start automatically when HDMI passthrough is ready
        </label>
    </div>
    
    <!-- Pipeline Preview -->
    <div class="gst-pipeline-preview-section">
        <h3>Generated Pipeline</h3>
        <pre id="auto-pipeline-preview" class="gst-code-block"></pre>
    </div>
    
    <!-- Actions -->
    <div class="gst-form-actions">
        <button id="btn-preview-auto" class="gst-btn gst-btn-secondary">Preview</button>
        <button id="btn-save-auto" class="gst-btn gst-btn-primary">Save Configuration</button>
        <button id="btn-delete-auto" class="gst-btn gst-btn-danger" style="display: none;">Delete</button>
    </div>
</section>
```

### New File: `frontend/auto-configurator.js` (Simplified)

```javascript
/**
 * Auto Configurator - Simplified UI Logic
 * 
 * Manages single auto instance configuration.
 */

class AutoConfigurator {
    constructor() {
        this.config = this.getDefaultConfig();
        this.hasExistingInstance = false;
    }

    getDefaultConfig() {
        return {
            gop_interval_seconds: 1.0,
            bitrate_kbps: 20000,
            rc_mode: 1,  // CBR
            audio_source: 'hdmi_rx',
            srt_port: 8888,
            recording_enabled: false,
            recording_path: '/mnt/sdcard/recordings/capture.mkv',
            autostart_on_ready: true
        };
    }

    init() {
        this.setupEventListeners();
        this.startStatusMonitoring();
        this.loadConfig();
    }

    setupEventListeners() {
        // Form changes - auto-preview
        const inputs = [
            'auto-gop-interval', 'auto-bitrate', 'auto-rc-mode',
            'auto-audio-source', 'auto-srt-port',
            'auto-recording-enabled', 'auto-recording-path', 'auto-autostart'
        ];
        
        inputs.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', () => this.updatePreview());
                el.addEventListener('input', () => this.debouncedUpdate());
            }
        });

        // Recording toggle
        document.getElementById('auto-recording-enabled').addEventListener('change', (e) => {
            document.getElementById('auto-recording-path-group').style.display = 
                e.target.checked ? 'block' : 'none';
            this.updatePreview();
        });

        // Buttons
        document.getElementById('btn-preview-auto').addEventListener('click', () => {
            this.updatePreview();
        });

        document.getElementById('btn-save-auto').addEventListener('click', () => {
            this.saveConfig();
        });

        document.getElementById('btn-delete-auto').addEventListener('click', () => {
            this.deleteConfig();
        });
    }

    debouncedUpdate() {
        if (this.previewTimeout) clearTimeout(this.previewTimeout);
        this.previewTimeout = setTimeout(() => this.updatePreview(), 500);
    }

    async loadConfig() {
        try {
            const result = await callMethod('GetAutoInstanceConfig');
            const config = JSON.parse(result);
            
            if (Object.keys(config).length > 0) {
                this.hasExistingInstance = true;
                this.config = { ...this.getDefaultConfig(), ...config };
                this.populateForm();
                document.getElementById('btn-delete-auto').style.display = 'inline-block';
            }
            
            this.updatePreview();
        } catch (error) {
            console.error('Failed to load auto config:', error);
        }
    }

    populateForm() {
        document.getElementById('auto-gop-interval').value = this.config.gop_interval_seconds;
        document.getElementById('auto-bitrate').value = this.config.bitrate_kbps;
        document.getElementById('auto-rc-mode').value = this.config.rc_mode;
        document.getElementById('auto-audio-source').value = this.config.audio_source;
        document.getElementById('auto-srt-port').value = this.config.srt_port;
        document.getElementById('auto-recording-enabled').checked = this.config.recording_enabled;
        document.getElementById('auto-recording-path').value = this.config.recording_path;
        document.getElementById('auto-autostart').checked = this.config.autostart_on_ready;
        
        document.getElementById('auto-recording-path-group').style.display = 
            this.config.recording_enabled ? 'block' : 'none';
    }

    getFormConfig() {
        return {
            gop_interval_seconds: parseFloat(document.getElementById('auto-gop-interval').value),
            bitrate_kbps: parseInt(document.getElementById('auto-bitrate').value),
            rc_mode: parseInt(document.getElementById('auto-rc-mode').value),
            audio_source: document.getElementById('auto-audio-source').value,
            srt_port: parseInt(document.getElementById('auto-srt-port').value),
            recording_enabled: document.getElementById('auto-recording-enabled').checked,
            recording_path: document.getElementById('auto-recording-path').value,
            autostart_on_ready: document.getElementById('auto-autostart').checked
        };
    }

    async updatePreview() {
        try {
            const config = this.getFormConfig();
            const result = await callMethod('GetAutoInstancePipelinePreview', JSON.stringify(config));
            document.getElementById('auto-pipeline-preview').textContent = result;
        } catch (error) {
            console.error('Failed to get preview:', error);
        }
    }

    async saveConfig() {
        try {
            const config = this.getFormConfig();
            const success = await callMethod('SetAutoInstanceConfig', JSON.stringify(config));
            
            if (success) {
                showToast('Auto configuration saved', 'success');
                this.hasExistingInstance = true;
                document.getElementById('btn-delete-auto').style.display = 'inline-block';
                await refreshInstances();
            } else {
                showToast('Failed to save configuration', 'error');
            }
        } catch (error) {
            console.error('Failed to save:', error);
            showToast('Error: ' + error.message, 'error');
        }
    }

    async deleteConfig() {
        if (!confirm('Delete auto instance configuration?')) return;
        
        try {
            const success = await callMethod('DeleteAutoInstance');
            if (success) {
                showToast('Auto configuration deleted', 'success');
                this.hasExistingInstance = false;
                this.config = this.getDefaultConfig();
                this.populateForm();
                document.getElementById('btn-delete-auto').style.display = 'none';
                await refreshInstances();
            }
        } catch (error) {
            console.error('Failed to delete:', error);
            showToast('Error: ' + error.message, 'error');
        }
    }

    startStatusMonitoring() {
        // Poll every 2 seconds
        setInterval(async () => {
            try {
                const result = await callMethod('GetPassthroughState');
                const state = JSON.parse(result);
                this.updateStatusUI(state);
            } catch (error) {
                console.debug('Failed to get passthrough state:', error);
            }
        }, 2000);

        // Subscribe to D-Bus signals
        if (state.dbus) {
            state.dbus.subscribe(
                { interface: DBUS_INTERFACE, member: 'PassthroughStateChanged' },
                (path, iface, signal, args) => {
                    const state = JSON.parse(args[1]);
                    this.updateStatusUI(state);
                }
            );
        }
    }

    updateStatusUI(state) {
        // HDMI RX
        const rxDot = document.getElementById('auto-hdmi-rx-dot');
        const rxText = document.getElementById('auto-hdmi-rx-text');
        if (state.rx_connected) {
            rxDot.className = 'gst-hdmi-dot connected';
            rxText.textContent = state.rx_stable ? 'Connected (Stable)' : 'Connected';
        } else {
            rxDot.className = 'gst-hdmi-dot disconnected';
            rxText.textContent = 'Disconnected';
        }

        // HDMI TX
        const txDot = document.getElementById('auto-hdmi-tx-dot');
        const txText = document.getElementById('auto-hdmi-tx-text');
        if (state.tx_connected) {
            txDot.className = 'gst-hdmi-dot connected';
            txText.textContent = state.tx_enabled ? 'Enabled' : 'Connected';
        } else {
            txDot.className = 'gst-hdmi-dot disconnected';
            txText.textContent = 'Disconnected';
        }

        // Passthrough
        const ptDot = document.getElementById('auto-passthrough-dot');
        const ptText = document.getElementById('auto-passthrough-text');
        if (state.can_capture) {
            ptDot.className = 'gst-hdmi-dot active';
            ptText.textContent = 'Ready';
        } else {
            ptDot.className = 'gst-hdmi-dot inactive';
            ptText.textContent = 'Not Ready';
        }

        // Resolution
        if (state.width && state.height) {
            document.getElementById('auto-detected-res').textContent = 
                `Detected: ${state.width}x${state.height}p${state.framerate || 60}`;
        }
    }
}

// Initialize
const autoConfigurator = new AutoConfigurator();
```

### Tasks
- [ ] Create simplified `auto-configurator.js`
- [ ] Update `index.html` with new panel
- [ ] Add CSS styles for status indicators
- [ ] Integrate with tab switching

---

## Phase 6: Integration and Testing

### Main Integration

Update `main.py`:
```python
from auto_instance import AutoInstanceManager

class GstManagerDaemon:
    def __init__(self):
        # ... existing ...
        self.auto_instance_manager = AutoInstanceManager(
            self.instance_manager,
            None  # Will set after event_manager created
        )
    
    async def start(self):
        # ... existing ...
        
        # Load auto instance config
        await self.auto_instance_manager.load()
        
        # Start event monitoring
        self.event_manager = EventManager(
            instance_manager=self.instance_manager,
            service=self.service,
            auto_instance_manager=self.auto_instance_manager
        )
        await self.event_manager.start()
        
        # Set event_manager reference
        self.auto_instance_manager.event_manager = self.event_manager
```

Update `api.py` service initialization:
```python
self.interface = GstManagerInterface(
    self.instance_manager,
    self.discovery_manager,
    self.history_manager,
    self.config,
    auto_instance_manager=self.auto_instance_manager  # Add this
)
```

### Test Scenarios

1. **Initial Setup**:
   - [ ] Open Auto Configurator tab
   - [ ] Verify default values match requirements
   - [ ] Check pipeline preview generates correctly

2. **Configuration**:
   - [ ] Change GOP interval, verify preview updates
   - [ ] Change bitrate, verify preview updates
   - [ ] Toggle recording, verify tee is added
   - [ ] Save configuration
   - [ ] Verify instance created with correct pipeline

3. **HDMI State Management**:
   - [ ] Connect HDMI RX, verify RX indicator
   - [ ] Wait 1.5s, verify TX is checked
   - [ ] Connect HDMI TX, verify passthrough ready
   - [ ] Verify auto instance starts (if autostart enabled)
   - [ ] Disconnect HDMI RX, verify instance stops
   - [ ] Reconnect, verify instance restarts

4. **Resolution Changes**:
   - [ ] Change HDMI TX resolution
   - [ ] Verify instance stops
   - [ ] Verify new resolution detected
   - [ ] Verify instance restarts with new resolution

5. **Pipeline Verification**:
   - [ ] Verify `/dev/video71` is used
   - [ ] Verify `amlvenc` with H.265 output
   - [ ] Verify SRT sink on configured port
   - [ ] Verify audio source matches selection (hw:0,6 or hw:0,0)

---

## Summary of Changes (Refined)

### Backend Files

| File | Changes |
|------|---------|
| `tvservice.py` | Add HDMI TX detection via sysfs |
| `auto_instance.py` | **NEW** - Single auto instance manager |
| `events.py` | Add TX monitoring, 1.5s delay, passthrough state |
| `api.py` | Add 5 D-Bus methods for auto config |
| `instances.py` | Add `instance_type` field |
| `main.py` | Integrate AutoInstanceManager |

### Frontend Files

| File | Changes |
|------|---------|
| `index.html` | Simplified auto config panel |
| `auto-configurator.js` | **NEW** - Simplified UI logic |
| `gst-manager.js` | Add tab switching, auto instance handling |
| `gst-manager.css` | Add status indicator styles |

### Key Differences from Original Plan

1. **Single Instance Only**: Enforced in AutoInstanceManager
2. **Fixed Pipeline Template**: Uses exact command provided
3. **Simplified Config**: Fewer options, GOP calculated as framerate × interval
4. **Always Stream**: SRT streaming is mandatory, not optional
5. **MKV Recording**: Recording format fixed to MKV
6. **Simplified UI**: Panel instead of modal, fewer fields

---

## Timeline Estimate (Refined)

| Phase | Time |
|-------|------|
| Phase 1: tvservice.py TX detection | 0.5 day |
| Phase 2: Auto Instance Manager | 1 day |
| Phase 3: EventManager enhancement | 0.5 day |
| Phase 4: D-Bus methods | 0.5 day |
| Phase 5: Frontend UI | 1 day |
| Phase 6: Integration & Testing | 0.5 day |
| **Total** | **~4 days** |
