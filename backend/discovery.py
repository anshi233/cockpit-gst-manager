"""Hardware Discovery Manager.

Detects video inputs, encoders, audio devices, and storage on the system.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("gst-manager.discovery")


# Known device paths
VDIN_DEVICES = ["/dev/vdin1", "/dev/video0", "/dev/video1", "/dev/video2"]
AUDIO_DEVICES = ["hw:0,0", "hw:1,0"]
STORAGE_PATHS = ["/mnt/sdcard", "/data", "/mnt/usb"]
HDMIRX_SYSFS = "/sys/class/hdmirx/hdmirx0"


class DiscoveryManager:
    """Manages hardware discovery and caching."""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.cache_file = config_dir / "board_context.json"
        self.context: Dict[str, Any] = {}

    async def refresh(self) -> Dict[str, Any]:
        """Refresh hardware discovery and update cache.

        Returns:
            dict: Board context with all discovered hardware.
        """
        logger.info("Starting hardware discovery")

        self.context = {
            "video_inputs": await self._discover_video_inputs(),
            "audio_inputs": await self._discover_audio_inputs(),
            "encoders": await self._discover_encoders(),
            "custom_plugins": await self._discover_custom_plugins(),
            "storage": await self._discover_storage(),
        }

        # Save to cache
        await self._save_cache()

        logger.info(
            f"Discovery complete: {len(self.context['video_inputs'])} video, "
            f"{len(self.context['encoders'])} encoders, "
            f"{len(self.context['storage'])} storage"
        )

        return self.context

    def get_context(self) -> Dict[str, Any]:
        """Get current board context."""
        return self.context

    def get_context_json(self) -> str:
        """Get board context as JSON string."""
        return json.dumps(self.context, indent=2)

    async def _discover_video_inputs(self) -> List[Dict[str, Any]]:
        """Discover video input devices."""
        inputs = []

        for device_path in VDIN_DEVICES:
            device = Path(device_path)
            if device.exists():
                info = await self._get_video_device_info(device_path)
                inputs.append(info)

        return inputs

    async def _get_video_device_info(self, device_path: str) -> Dict[str, Any]:
        """Get detailed info for a video device."""
        info = {
            "device": device_path,
            "type": "unknown",
            "available": True,
            "current_signal": None,
            "formats": []
        }

        # Determine type
        if "vdin" in device_path:
            info["type"] = "hdmi-in"
            info["name"] = "HDMI-In"
            # Try to get HDMI signal info
            signal = await self._get_hdmi_signal()
            if signal:
                info["current_signal"] = signal
        else:
            info["type"] = "v4l2"
            info["name"] = f"Video {device_path.split('/')[-1]}"

        # Try to get formats via v4l2-ctl
        try:
            proc = await asyncio.create_subprocess_exec(
                "v4l2-ctl", "-d", device_path, "--list-formats",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                formats = self._parse_v4l2_formats(stdout.decode())
                info["formats"] = formats
        except FileNotFoundError:
            logger.debug("v4l2-ctl not found, skipping format detection")
        except Exception as e:
            logger.debug(f"Failed to get formats for {device_path}: {e}")

        return info

    def _parse_v4l2_formats(self, output: str) -> List[str]:
        """Parse v4l2-ctl format output."""
        formats = []
        for line in output.split("\n"):
            # Match format names like "NV12", "YUYV"
            match = re.search(r"'(\w+)'", line)
            if match:
                formats.append(match.group(1))
        return list(set(formats))

    async def _get_hdmi_signal(self) -> Optional[str]:
        """Get current HDMI input signal info from sysfs."""
        info_path = Path(HDMIRX_SYSFS) / "info"

        if not info_path.exists():
            # Try alternate paths
            alt_paths = [
                "/sys/kernel/debug/hdmirx/info",
                "/sys/devices/platform/hdmirx/info"
            ]
            for alt in alt_paths:
                if Path(alt).exists():
                    info_path = Path(alt)
                    break
            else:
                return None

        try:
            with open(info_path, "r") as f:
                content = f.read().strip()
                # Parse resolution from info string (e.g., "1920x1080p60hz")
                return content if content else None
        except Exception as e:
            logger.debug(f"Failed to read HDMI info: {e}")
            return None

    async def _discover_audio_inputs(self) -> List[Dict[str, Any]]:
        """Discover audio input devices."""
        inputs = []

        for device in AUDIO_DEVICES:
            available = await self._check_alsa_device(device)
            device_type = "hdmi-audio" if device == "hw:0,0" else "audio"

            inputs.append({
                "device": device,
                "type": device_type,
                "available": available
            })

        return inputs

    async def _check_alsa_device(self, device: str) -> bool:
        """Check if ALSA device is available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "arecord", "-D", device, "--dump-hw-params",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            # arecord outputs to stderr even on success
            return b"error" not in stderr.lower() if stderr else proc.returncode == 0
        except FileNotFoundError:
            return False
        except Exception:
            return False

    async def _discover_encoders(self) -> List[str]:
        """Discover available hardware encoders."""
        encoders = []
        encoder_elements = ["aml_h264enc", "aml_h265enc"]

        for element in encoder_elements:
            available = await self._check_gst_element(element)
            if available:
                encoders.append(element)

        return encoders

    async def _discover_custom_plugins(self) -> List[str]:
        """Discover Amlogic custom GStreamer plugins."""
        plugins = []
        custom_elements = ["amlge2d", "amlvdec", "amlvideo2"]

        for element in custom_elements:
            available = await self._check_gst_element(element)
            if available:
                plugins.append(element)

        return plugins

    async def _check_gst_element(self, element: str) -> bool:
        """Check if a GStreamer element is available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gst-inspect-1.0", element,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            logger.warning("gst-inspect-1.0 not found")
            return False
        except Exception:
            return False

    async def _discover_storage(self) -> List[Dict[str, Any]]:
        """Discover available storage locations."""
        storage = []

        for path in STORAGE_PATHS:
            info = await self._get_storage_info(path)
            storage.append(info)

        return storage

    async def _get_storage_info(self, path: str) -> Dict[str, Any]:
        """Get storage information for a path."""
        info = {
            "path": path,
            "mounted": False,
            "available": False,
            "free_gb": 0,
            "total_gb": 0
        }

        if os.path.ismount(path) or (os.path.exists(path) and path == "/data"):
            try:
                stat = os.statvfs(path)
                info["mounted"] = True
                info["available"] = True
                info["free_gb"] = round(
                    (stat.f_frsize * stat.f_bavail) / (1024**3), 2
                )
                info["total_gb"] = round(
                    (stat.f_frsize * stat.f_blocks) / (1024**3), 2
                )
            except Exception as e:
                logger.debug(f"Failed to stat {path}: {e}")

        return info

    async def _save_cache(self) -> None:
        """Save discovery results to cache file."""
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.context, f, indent=2)
            logger.debug(f"Saved discovery cache to {self.cache_file}")
        except Exception as e:
            logger.error(f"Failed to save discovery cache: {e}")

    async def get_encoder_info(self, encoder: str = "all") -> Dict[str, Any]:
        """Get detailed encoder information.

        Args:
            encoder: "h264", "h265", or "all"

        Returns:
            dict: Encoder properties and capabilities.
        """
        result = {}

        encoders_to_check = []
        if encoder in ("h264", "all"):
            encoders_to_check.append(("aml_h264enc", "H.264"))
        if encoder in ("h265", "all"):
            encoders_to_check.append(("aml_h265enc", "H.265"))

        for element_name, codec in encoders_to_check:
            info = await self._get_gst_element_properties(element_name)
            if info:
                result[element_name] = {
                    "codec": codec,
                    "properties": info
                }

        return result

    async def _get_gst_element_properties(
        self,
        element: str
    ) -> Optional[Dict[str, Any]]:
        """Get GStreamer element properties via gst-inspect."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gst-inspect-1.0", element,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0:
                return None

            # Parse properties from gst-inspect output
            return self._parse_gst_inspect(stdout.decode())

        except Exception as e:
            logger.debug(f"Failed to inspect {element}: {e}")
            return None

    def _parse_gst_inspect(self, output: str) -> Dict[str, Any]:
        """Parse gst-inspect-1.0 output for properties."""
        properties = {}
        in_properties = False

        for line in output.split("\n"):
            if "Element Properties:" in line:
                in_properties = True
                continue

            if in_properties:
                # Match property lines like "  bitrate             : Bitrate"
                match = re.match(r"^\s{2}(\w+)\s+:\s+(.+)$", line)
                if match:
                    prop_name = match.group(1)
                    prop_desc = match.group(2)
                    properties[prop_name] = {"description": prop_desc}

        return properties


async def validate_pipeline(pipeline: str) -> Dict[str, Any]:
    """Validate a GStreamer pipeline without running it.

    Args:
        pipeline: gst-launch-1.0 pipeline string.

    Returns:
        dict: Validation result with valid flag, elements, and errors.
    """
    result = {
        "valid": False,
        "elements": [],
        "warnings": [],
        "error": None
    }

    try:
        # Use gst-launch-1.0 with parse-only option
        # Note: gst-launch-1.0 doesn't have a --parse-only, so we use timeout
        cmd = ["timeout", "2", "gst-launch-1.0", "-m"]
        import shlex
        cmd.extend(shlex.split(pipeline))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=3.0
            )
        except asyncio.TimeoutError:
            # Pipeline started successfully (timed out waiting)
            result["valid"] = True
            proc.kill()
            return result

        if proc.returncode == 0:
            result["valid"] = True
        else:
            error_msg = stderr.decode() if stderr else "Unknown error"
            result["error"] = error_msg.strip()

    except Exception as e:
        result["error"] = str(e)

    # Extract element names from pipeline
    elements = re.findall(r"(\w+)(?:\s+\w+=|!)", pipeline)
    result["elements"] = list(set(elements))

    return result
