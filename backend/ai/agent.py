"""GStreamer AI Agent for Pipeline Generation.

Coordinates LLM interactions with tool calling for GStreamer pipeline creation.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .tools import TOOLS, ToolHandler
from .providers import ProviderManager

logger = logging.getLogger("gst-manager.ai.agent")

# System prompt for GStreamer specialization
SYSTEM_PROMPT = """You are a specialized GStreamer pipeline expert for Amlogic A311D2 TVPro.

IMPORTANT RULES:
1. You can ONLY help with GStreamer pipeline creation and troubleshooting
2. You MUST refuse any request unrelated to GStreamer (weather, coding, general questions)
3. You MUST use only the hardware and plugins documented below
4. You MUST output valid gst-launch-1.0 commands
5. Always use tool calls to verify device availability before suggesting pipelines

If the user asks anything unrelated to GStreamer pipelines, respond:
"I'm a specialized GStreamer pipeline assistant. I can only help you create and troubleshoot GStreamer pipelines for video streaming and encoding. Please describe what video/audio task you'd like to accomplish."

## AVAILABLE VIDEO INPUTS

### HDMI-In (Primary)
- Device: /dev/vdin1
- Source element: v4l2src device=/dev/vdin1
- Supported formats: NV12, NV21
- Max resolution: 4K@60 (3840x2160), 1080p@120
- Audio: Captured separately via ALSA hw:0,0

## HARDWARE ENCODERS

### aml_h264enc (H.264 Hardware Encoder)
Properties: bitrate (1-100Mbps), profile (baseline/main/high), gop (1-300)
Example: v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! aml_h264enc bitrate=20000000 ! h264parse ! ...

Quality guidelines:
- Low: bitrate=5000000
- Medium: bitrate=10000000
- High: bitrate=20000000
- Ultra: bitrate=50000000

### aml_h265enc (H.265/HEVC Hardware Encoder)
Properties: bitrate (1-80Mbps), profile (main/main10), gop (1-300)
Example: v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! aml_h265enc bitrate=15000000 ! h265parse ! ...

## OUTPUT SINKS

### SRT Streaming (srtsink)
- Listener mode: uri="srt://0.0.0.0:5000?mode=listener"
- Caller mode: uri="srt://192.168.1.100:5000?mode=caller"

### RTMP Streaming (rtmpsink)
- location="rtmp://server/live/streamkey"

### File Recording (filesink, splitmuxsink)
- filesink location=/mnt/sdcard/recording.ts
- splitmuxsink for segmented recording

## AUDIO

To include audio from HDMI:
alsasrc device=hw:0,0 ! audioconvert ! voaacenc bitrate=128000 ! aacparse ! mux.

Always use mpegtsmux for MPEG-TS container when streaming.
"""


class GstAgent:
    """AI agent for GStreamer pipeline generation."""

    MAX_TOOL_ITERATIONS = 5

    def __init__(
        self,
        provider_manager: ProviderManager,
        tool_handler: ToolHandler,
        config: Dict[str, Any]
    ):
        self.provider_manager = provider_manager
        self.tool_handler = tool_handler
        self.config = config
        self.max_retries = config.get("settings", {}).get("ai_max_retries", 3)
        self.timeout = config.get("settings", {}).get("ai_timeout_seconds", 30)

    async def generate_pipeline(
        self,
        prompt: str,
        provider_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a GStreamer pipeline from natural language prompt.

        Args:
            prompt: User's natural language request.
            provider_name: Optional specific provider to use.

        Returns:
            Dict with 'pipeline' or 'error' and 'message'.
        """
        provider = self.provider_manager.get_provider(provider_name)
        if not provider:
            return {
                "error": "No AI provider configured",
                "message": "Please add an AI provider in settings."
            }

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        # Tool calling loop
        for iteration in range(self.MAX_TOOL_ITERATIONS):
            logger.debug(f"AI iteration {iteration + 1}/{self.MAX_TOOL_ITERATIONS}")

            try:
                response = await provider.chat_completion(
                    messages=messages,
                    tools=TOOLS,
                    timeout=self.timeout
                )
            except Exception as e:
                logger.error(f"Provider error: {e}")
                return {"error": f"API error: {e}"}

            if "error" in response:
                return response

            # Check for tool calls
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                # Execute tools and continue conversation
                messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"])
                            }
                        }
                        for tc in tool_calls
                    ]
                })

                for tc in tool_calls:
                    result = await self.tool_handler.execute(
                        tc["name"],
                        tc["arguments"]
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result)
                    })

                continue  # Next iteration with tool results

            # No tool calls - should have final response
            content = response.get("content", "")
            return self._extract_pipeline(content)

        return {
            "error": "Max iterations reached",
            "message": "AI could not generate pipeline in allowed iterations."
        }

    def _extract_pipeline(self, content: str) -> Dict[str, Any]:
        """Extract pipeline command from AI response.

        Args:
            content: AI response text.

        Returns:
            Dict with 'pipeline' and 'message', or 'error'.
        """
        # Look for gst-launch-1.0 command
        lines = content.split("\n")
        pipeline = None

        for i, line in enumerate(lines):
            line = line.strip()

            # Skip empty lines and markdown
            if not line or line.startswith("```"):
                continue

            # Check for gst-launch command
            if line.startswith("gst-launch"):
                # Might span multiple lines (backslash continuation)
                pipeline_parts = [line]
                j = i + 1
                while j < len(lines) and lines[j - 1].strip().endswith("\\"):
                    pipeline_parts.append(lines[j].strip())
                    j += 1
                pipeline = " ".join(pipeline_parts).replace("\\", "")
                break

            # Check for v4l2src (might be without gst-launch prefix)
            if line.startswith("v4l2src") or line.startswith("videotestsrc"):
                pipeline = line
                break

        if pipeline:
            # Clean up the pipeline
            if pipeline.startswith("gst-launch-1.0 "):
                pipeline = pipeline[len("gst-launch-1.0 "):]
            if pipeline.startswith("-e "):
                pipeline = pipeline[3:]

            return {
                "pipeline": pipeline.strip(),
                "message": content
            }

        # No pipeline found - return full message
        return {
            "message": content,
            "error": "no_pipeline" if "I can only help" in content else None
        }

    async def fix_error(
        self,
        pipeline: str,
        error: str,
        provider_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analyze error and suggest fix for a pipeline.

        Args:
            pipeline: The failing pipeline.
            error: Error message from GStreamer.
            provider_name: Optional specific provider.

        Returns:
            Dict with 'pipeline' (fixed), 'message', or 'error'.
        """
        provider = self.provider_manager.get_provider(provider_name)
        if not provider:
            return {"error": "No AI provider configured"}

        prompt = f"""The following GStreamer pipeline is failing:

Pipeline: {pipeline}

Error message:
{error}

Please analyze the error and provide a corrected pipeline. Use tool calls to verify device availability if needed."""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        try:
            response = await provider.chat_completion(
                messages=messages,
                tools=TOOLS,
                timeout=self.timeout
            )

            if "error" in response:
                return response

            content = response.get("content", "")
            return self._extract_pipeline(content)

        except Exception as e:
            logger.error(f"Fix error failed: {e}")
            return {"error": str(e)}
