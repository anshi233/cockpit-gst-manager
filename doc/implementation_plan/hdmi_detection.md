# HDMI Signal Detection

## Overview

Standalone HDMI-In signal detection using sysfs polling.

---

## Sysfs Interface

### Signal Status Path

```
/sys/class/hdmirx/hdmirx0/
├── info              # Current signal info (resolution, format)
├── cable             # Cable connected status
├── signal            # Signal lock status
└── timing            # Detailed timing info
```

### Reading Signal Status

```python
HDMIRX_PATH = "/sys/class/hdmirx/hdmirx0"

def get_hdmi_status():
    """Read HDMI-In signal status from sysfs."""
    try:
        # Check if cable is connected
        with open(f"{HDMIRX_PATH}/cable", "r") as f:
            cable = f.read().strip()
        
        # Check if signal is locked
        with open(f"{HDMIRX_PATH}/signal", "r") as f:
            signal = f.read().strip()
        
        # Get resolution info
        with open(f"{HDMIRX_PATH}/info", "r") as f:
            info = f.read().strip()
        
        return {
            "cable_connected": cable == "1",
            "signal_locked": signal == "1",
            "info": parse_hdmi_info(info)
        }
    except FileNotFoundError:
        return {"available": False, "error": "hdmirx sysfs not found"}

def parse_hdmi_info(info_str):
    """Parse HDMI info string to resolution/framerate."""
    # Example: "1920x1080p60hz"
    # Returns: {"width": 1920, "height": 1080, "fps": 60, "interlaced": False}
    ...
```

---

## Polling Strategy

### Interval

| Condition | Poll Interval |
|-----------|---------------|
| No signal | 2 seconds |
| Signal active | 5 seconds |
| After signal change | 500ms (for stability) |

### Implementation

```python
import asyncio

class HdmiMonitor:
    def __init__(self, callback):
        self.callback = callback
        self.last_status = None
        self.running = False
    
    async def start(self):
        self.running = True
        while self.running:
            status = get_hdmi_status()
            
            # Detect change
            if status != self.last_status:
                # Wait for stability
                await asyncio.sleep(0.5)
                status = get_hdmi_status()
                
                if status != self.last_status:
                    self.last_status = status
                    await self.callback(status)
            
            # Adaptive polling
            interval = 5 if status.get("signal_locked") else 2
            await asyncio.sleep(interval)
    
    def stop(self):
        self.running = False
```

---

## Event Emission

When HDMI status changes, emit D-Bus signal:

```python
def on_hdmi_change(status):
    if status["signal_locked"]:
        emit_signal("HdmiSignalChanged", True, status["info"]["resolution"])
        
        # Check for auto-start instances
        for instance in get_instances():
            if instance.trigger_event == "hdmi_signal_ready" and instance.autostart:
                start_instance(instance.id)
    else:
        emit_signal("HdmiSignalChanged", False, "")
        
        # Stop instances that depend on HDMI
        for instance in get_running_instances():
            if "/dev/vdin1" in instance.pipeline:
                stop_instance(instance.id)
```

---

## Alternative Paths

If `/sys/class/hdmirx/` is not available, check:

1. `/sys/kernel/debug/hdmirx/` (debug interface)
2. `/sys/devices/platform/hdmirx/` (platform device)
3. V4L2 device status: `v4l2-ctl -d /dev/vdin1 --query-dv-timings`

---

## Dependencies

- Kernel: hdmirx driver must be loaded
- Permissions: sysfs files readable by gst-manager daemon

---

## Testing

```bash
# Check if sysfs exists
ls -la /sys/class/hdmirx/

# Read signal status
cat /sys/class/hdmirx/hdmirx0/signal

# Monitor changes
watch -n1 cat /sys/class/hdmirx/hdmirx0/info
```
