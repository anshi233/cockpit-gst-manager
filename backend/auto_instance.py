"""Auto Instance Manager - Single auto-managed GStreamer instance.

Manages a single auto-generated instance that:
1. Captures HDMI TX output from /dev/video71
2. Uses dynamic resolution/framerate from HDMI TX
3. Auto-starts/stops based on HDMI RX/TX state
4. Supports SRT streaming (always on) + optional recording (MPEG-TS)
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
    """Audio input source options."""
    HDMI_RX = "hdmi_rx"  # hw:0,6 - HDMI RX loopback audio
    LINE_IN = "line_in"  # hw:0,0 - Line in audio


@dataclass
class AutoInstanceConfig:
    """Configuration for auto-generated instance.
    
    GOP is calculated as: framerate * gop_interval_seconds
    """
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
    recording_path: str = "/mnt/sdcard/recordings/capture.ts"
    
    # Auto-start behavior
    autostart_on_ready: bool = True
    
    # Runtime info (from HDMI TX detection)
    width: int = 3840
    height: int = 2160
    framerate: int = 60
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["audio_source"] = self.audio_source.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoInstanceConfig":
        """Create config from dictionary."""
        if "audio_source" in data and isinstance(data["audio_source"], str):
            data["audio_source"] = AudioSource(data["audio_source"])
        # Filter out unknown fields
        valid_fields = cls.__dataclass_fields__.keys()
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


class PipelineBuilder:
    """Builds GStreamer pipeline for auto instance.
    
    Based on the working command provided:
    - Video: v4l2src (vdin1 at /dev/video71) -> amlvenc (H.265)
    - Audio: alsasrc (HDMI RX hw:0,6 or Line In hw:0,0) -> avenc_aac
    - Muxer: mpegtsmux
    - Output: srtsink (always) + optional filesink for recording
    """
    
    def build(self, config: AutoInstanceConfig) -> str:
        """Build complete pipeline string.
        
        Args:
            config: Auto instance configuration
            
        Returns:
            Complete gst-launch-1.0 pipeline string
        """
        # Calculate GOP from framerate and interval
        gop = int(config.framerate * config.gop_interval_seconds)
        
        audio_device = "hw:0,6" if config.audio_source == AudioSource.HDMI_RX else "hw:0,0"
        
        # Build pipeline
        # Structure:
        #   video_branch ! mux. 
        #   audio_branch ! mux. 
        #   mpegtsmux name=mux ... ! output
        
        pipeline = (
            # Video branch ending with reference to muxer
            f'v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! '
            f'video/x-raw,format=NV21,width={config.width},height={config.height},'
            f'framerate={config.framerate}/1 ! '
            f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
            f'amlvenc gop={gop} gop-pattern=0 framerate={config.framerate} '
            f'bitrate={config.bitrate_kbps} rc-mode={config.rc_mode} ! '
            f'video/x-h265 ! '
            f'h265parse config-interval=-1 ! '
            f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
            f'mux. '  # Video goes to muxer
            # Audio branch ending with reference to muxer  
            f'alsasrc device={audio_device} buffer-time=50000 provide-clock=false '
            f'slave-method=re-timestamp ! '
            f'audio/x-raw,rate=48000,channels=2,format=S16LE ! '
            f'queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! '
            f'audioconvert ! audioresample ! avenc_aac bitrate=128000 ! aacparse ! '
            f'queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! '
            f'mux. '  # Audio goes to muxer
            # Muxer definition and output
            f'mpegtsmux name=mux alignment=7 latency=100000000'
        )
        
        # Output
        if config.recording_enabled:
            # Both recording and streaming - use tee
            pipeline += (
                f' ! tee name=t '
                f't. ! queue ! filesink location="{config.recording_path}" '
                f't. ! queue ! srtsink uri="srt://:{config.srt_port}" '
                f'wait-for-connection=false latency=600 sync=false'
            )
        else:
            # Streaming only
            pipeline += (
                f' ! srtsink uri="srt://:{config.srt_port}" '
                f'wait-for-connection=false latency=600 sync=false'
            )
        
        return pipeline
    
    def build_preview(self, config: AutoInstanceConfig) -> str:
        """Build pipeline preview with line breaks for readability."""
        pipeline = self.build(config)
        # Add line breaks after each element
        return pipeline.replace(' ! ', ' ! \\\n   ')


class AutoInstanceManager:
    """Manages the single auto instance.
    
    Only one auto instance is allowed per system. Creating a new one
    will replace the existing instance.
    
    Auto-creates with default settings on first boot if no config exists.
    """
    
    CONFIG_FILE = Path("/var/lib/gst-manager/auto_instance.json")
    
    # Default configuration for out-of-box experience
    DEFAULT_CONFIG = AutoInstanceConfig(
        gop_interval_seconds=1.0,
        bitrate_kbps=20000,
        rc_mode=1,  # CBR
        audio_source=AudioSource.HDMI_RX,
        srt_port=8888,
        recording_enabled=False,
        recording_path="/mnt/sdcard/recordings/capture.ts",
        autostart_on_ready=True  # Key: auto-start when HDMI ready
    )
    
    def __init__(self, instance_manager, event_manager=None):
        """Initialize auto instance manager.
        
        Args:
            instance_manager: InstanceManager for creating/managing instances
            event_manager: EventManager for HDMI state callbacks (set later)
        """
        self.instance_manager = instance_manager
        self.event_manager = event_manager
        self.config: Optional[AutoInstanceConfig] = None
        self.instance_id: Optional[str] = None
        self._builder = PipelineBuilder()
        
    async def load(self) -> bool:
        """Initialize auto instance configuration.
        
        Always uses default settings - no config file required.
        Settings can be updated via D-Bus and are persisted for next boot.
        
        Returns:
            True if config is ready
        """
        # Always start with default config
        self.config = self.DEFAULT_CONFIG
        
        # Try to load user customizations if they exist
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    data = json.load(f)
                
                # Merge user settings with defaults
                user_config = data.get("config", {})
                if user_config:
                    self.config = AutoInstanceConfig.from_dict(user_config)
                    logger.info("Loaded user customizations from config file")
                
                # Remember instance ID if exists
                self.instance_id = data.get("instance_id")
                
            except Exception as e:
                logger.warning(f"Could not load config file, using defaults: {e}")
        else:
            logger.info("No config file found, using default settings")
        
        return True
    
    async def save(self) -> bool:
        """Save auto instance configuration to disk.
        
        Returns:
            True if saved successfully
        """
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
        
        Only one auto instance is allowed. Creating a new one will
        delete and replace the existing instance.
        
        Args:
            config: New configuration
            hdmi_tx_status: Current HDMI TX status for resolution detection
            
        Returns:
            instance_id: The auto instance ID
        """
        # Update config with current HDMI TX info if available
        if hdmi_tx_status:
            config.width = hdmi_tx_status.width or config.width
            config.height = hdmi_tx_status.height or config.height
            config.framerate = hdmi_tx_status.fps or config.framerate
        
        self.config = config
        
        # Generate pipeline
        pipeline = self._builder.build(config)
        
        # Delete existing auto instance if present
        if self.instance_id:
            try:
                existing = self.instance_manager.get_instance(self.instance_id)
                if existing:
                    if existing.status.value == "running":
                        logger.info(f"Stopping existing auto instance: {self.instance_id}")
                        await self.instance_manager.stop_instance(self.instance_id)
                    logger.info(f"Deleting existing auto instance: {self.instance_id}")
                    await self.instance_manager.delete_instance(self.instance_id)
            except Exception as e:
                logger.warning(f"Error cleaning up existing instance: {e}")
        
        # Create new auto instance
        from instances import InstanceType
        
        instance_id = await self.instance_manager.create_instance(
            name="Auto HDMI Capture",
            pipeline=pipeline
        )
        
        # Mark as auto instance with configuration
        instance = self.instance_manager.get_instance(instance_id)
        if instance:
            instance.instance_type = InstanceType.AUTO
            instance.auto_config = config.to_dict()
            instance.autostart = config.autostart_on_ready
            instance.trigger_event = "hdmi_passthrough_ready"
            logger.info(f"Marked instance {instance_id} as AUTO type, autostart={instance.autostart}")
            
            # Re-save to persist the instance_type change
            await self.instance_manager.history_manager.save_instance(instance.to_dict())
        
        self.instance_id = instance_id
        await self.save()
        
        logger.info(f"Created auto instance: {instance_id}")
        return instance_id
    
    def get_pipeline_preview(self, config: AutoInstanceConfig) -> str:
        """Get pipeline preview without creating instance.
        
        Args:
            config: Configuration to preview
            
        Returns:
            Formatted pipeline string with line breaks
        """
        return self._builder.build_preview(config)
    
    async def on_passthrough_ready(self, hdmi_tx_status: Any) -> None:
        """Called when HDMI passthrough becomes ready.
        
        Starts the auto instance if autostart is enabled.
        
        Args:
            hdmi_tx_status: Current HDMI TX status
        """
        if not self.config:
            logger.debug("No auto instance config, skipping passthrough ready")
            return
        
        if not self.config.autostart_on_ready:
            logger.debug("Auto-start disabled")
            return
        
        # Update resolution from current TX status
        self.config.width = hdmi_tx_status.width or self.config.width
        self.config.height = hdmi_tx_status.height or self.config.height
        self.config.framerate = hdmi_tx_status.fps or self.config.framerate
        
        # If instance exists and is stopped, recreate with new pipeline
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.status.value == "stopped":
                logger.info("Recreating auto instance with updated resolution")
                await self.create_or_update(self.config, hdmi_tx_status)
        else:
            # Create new instance
            logger.info("Creating auto instance for passthrough")
            await self.create_or_update(self.config, hdmi_tx_status)
        
        # Start the instance
        if self.instance_id:
            try:
                await self.instance_manager.start_instance(self.instance_id)
                logger.info(f"Auto-started instance {self.instance_id}")
            except Exception as e:
                logger.error(f"Failed to auto-start instance: {e}")
    
    async def on_passthrough_lost(self) -> None:
        """Called when HDMI passthrough is lost.
        
        Stops the auto instance if it's running.
        """
        if not self.instance_id:
            return
        
        instance = self.instance_manager.get_instance(self.instance_id)
        if instance and instance.status.value == "running":
            try:
                await self.instance_manager.stop_instance(self.instance_id)
                logger.info(f"Auto-stopped instance {self.instance_id} due to passthrough loss")
            except Exception as e:
                logger.error(f"Failed to auto-stop instance: {e}")
    
    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get current config as dict.
        
        Returns:
            Config dictionary or None if not configured
        """
        if self.config:
            return self.config.to_dict()
        return None
    
    async def update_config(self, updates: Dict[str, Any]) -> bool:
        """Update configuration (preserves instance if stopped).
        
        Args:
            updates: Dictionary of config fields to update
            
        Returns:
            True if updated successfully
        """
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
        
        # Recreate instance with new pipeline if it exists and is stopped
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.status.value == "stopped":
                await self.create_or_update(self.config)
        
        await self.save()
        return True
    
    async def delete(self) -> bool:
        """Delete the auto instance and config.
        
        Returns:
            True if deleted successfully
        """
        if self.instance_id:
            try:
                instance = self.instance_manager.get_instance(self.instance_id)
                if instance:
                    if instance.status.value == "running":
                        await self.instance_manager.stop_instance(self.instance_id)
                    await self.instance_manager.delete_instance(self.instance_id)
            except Exception as e:
                logger.error(f"Error deleting instance: {e}")
        
        self.instance_id = None
        self.config = None
        
        # Remove config file
        try:
            if self.CONFIG_FILE.exists():
                self.CONFIG_FILE.unlink()
        except Exception as e:
            logger.error(f"Error removing config file: {e}")
        
        return True
