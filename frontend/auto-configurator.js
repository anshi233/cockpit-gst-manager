/**
 * Auto Configurator - UI Logic
 * 
 * Manages single auto instance configuration for HDMI passthrough capture.
 * 
 * Features:
 * - Single auto instance (creates/replaces)
 * - HDMI TX state monitoring
 * - Pipeline preview with real-time updates
 * - SRT streaming always enabled
 * - Optional MPEG-TS recording
 */

class AutoConfigurator {
    constructor() {
        this.config = this.getDefaultConfig();
        this.hasExistingInstance = false;
        this.passthroughState = null;
    }

    getDefaultConfig() {
        return {
            gop_interval_seconds: 1.0,
            bitrate_kbps: 20000,
            rc_mode: 1,  // CBR
            audio_source: 'hdmi_rx',
            srt_port: 8888,
            recording_enabled: false,
            recording_path: '/mnt/sdcard/recordings/capture.ts',
            autostart_on_ready: true
        };
    }

    init() {
        this.setupEventListeners();
        this.startStatusMonitoring();
        this.loadConfig();
    }

    setupEventListeners() {
        // Form field changes - auto-preview
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
        const recEnable = document.getElementById('auto-recording-enabled');
        if (recEnable) {
            recEnable.addEventListener('change', (e) => {
                const pathGroup = document.getElementById('auto-recording-path-group');
                if (pathGroup) {
                    pathGroup.style.display = e.target.checked ? 'block' : 'none';
                }
                this.updatePreview();
            });
        }

        // Buttons
        const previewBtn = document.getElementById('btn-preview-auto');
        if (previewBtn) {
            previewBtn.addEventListener('click', () => this.updatePreview());
        }

        const saveBtn = document.getElementById('btn-save-auto');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => this.saveConfig());
        }

        const deleteBtn = document.getElementById('btn-delete-auto');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => this.deleteConfig());
        }
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
                
                const deleteBtn = document.getElementById('btn-delete-auto');
                if (deleteBtn) {
                    deleteBtn.style.display = 'inline-block';
                }
            }
            
            this.updatePreview();
        } catch (error) {
            console.error('Failed to load auto config:', error);
        }
    }

    populateForm() {
        const setValue = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.value = value;
        };

        setValue('auto-gop-interval', this.config.gop_interval_seconds);
        setValue('auto-bitrate', this.config.bitrate_kbps);
        setValue('auto-rc-mode', this.config.rc_mode);
        setValue('auto-audio-source', this.config.audio_source);
        setValue('auto-srt-port', this.config.srt_port);
        setValue('auto-recording-path', this.config.recording_path);

        const recEnable = document.getElementById('auto-recording-enabled');
        if (recEnable) {
            recEnable.checked = this.config.recording_enabled;
        }

        const autostart = document.getElementById('auto-autostart');
        if (autostart) {
            autostart.checked = this.config.autostart_on_ready;
        }

        const pathGroup = document.getElementById('auto-recording-path-group');
        if (pathGroup) {
            pathGroup.style.display = this.config.recording_enabled ? 'block' : 'none';
        }
    }

    getFormConfig() {
        const getValue = (id, defaultVal) => {
            const el = document.getElementById(id);
            return el ? el.value : defaultVal;
        };

        const getChecked = (id) => {
            const el = document.getElementById(id);
            return el ? el.checked : false;
        };

        return {
            gop_interval_seconds: parseFloat(getValue('auto-gop-interval', '1.0')),
            bitrate_kbps: parseInt(getValue('auto-bitrate', '20000')),
            rc_mode: parseInt(getValue('auto-rc-mode', '1')),
            audio_source: getValue('auto-audio-source', 'hdmi_rx'),
            srt_port: parseInt(getValue('auto-srt-port', '8888')),
            recording_enabled: getChecked('auto-recording-enabled'),
            recording_path: getValue('auto-recording-path', '/mnt/sdcard/recordings/capture.ts'),
            autostart_on_ready: getChecked('auto-autostart')
        };
    }

    async updatePreview() {
        try {
            const config = this.getFormConfig();
            const result = await callMethod('GetAutoInstancePipelinePreview', JSON.stringify(config));
            
            const previewEl = document.getElementById('auto-pipeline-preview');
            if (previewEl) {
                previewEl.textContent = result;
            }
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
                this.config = config;
                
                const deleteBtn = document.getElementById('btn-delete-auto');
                if (deleteBtn) {
                    deleteBtn.style.display = 'inline-block';
                }
                
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
                
                const deleteBtn = document.getElementById('btn-delete-auto');
                if (deleteBtn) {
                    deleteBtn.style.display = 'none';
                }
                
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
                this.passthroughState = state;
                this.updateStatusUI(state);
            } catch (error) {
                console.debug('Failed to get passthrough state:', error);
            }
        }, 2000);

        // Subscribe to D-Bus signals
        if (typeof state !== 'undefined' && state.dbus) {
            state.dbus.subscribe(
                { interface: DBUS_INTERFACE, member: 'PassthroughStateChanged' },
                (path, iface, signal, args) => {
                    const state = JSON.parse(args[1]);
                    this.passthroughState = state;
                    this.updateStatusUI(state);
                }
            );
        }
    }

    updateStatusUI(state) {
        if (!state) return;

        const setDot = (id, status) => {
            const dot = document.getElementById(id);
            if (dot) {
                dot.className = 'gst-hdmi-dot ' + status;
            }
        };

        const setText = (id, text) => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = text;
            }
        };

        // HDMI RX
        if (state.rx_connected) {
            setDot('auto-hdmi-rx-dot', state.rx_stable ? 'connected' : 'unstable');
            setText('auto-hdmi-rx-text', state.rx_stable ? 'Connected (Stable)' : 'Connected');
        } else {
            setDot('auto-hdmi-rx-dot', 'disconnected');
            setText('auto-hdmi-rx-text', 'Disconnected');
        }

        // HDMI TX
        if (state.tx_connected) {
            setDot('auto-hdmi-tx-dot', state.tx_ready ? 'connected' : 'unstable');
            setText('auto-hdmi-tx-text', state.tx_ready ? 'Ready' : 'Connected');
        } else {
            setDot('auto-hdmi-tx-dot', 'disconnected');
            setText('auto-hdmi-tx-text', 'Disconnected');
        }

        // Passthrough
        if (state.can_capture) {
            setDot('auto-passthrough-dot', 'active');
            setText('auto-passthrough-text', 'Ready');
        } else {
            setDot('auto-passthrough-dot', 'inactive');
            setText('auto-passthrough-text', 'Not Ready');
        }

        // Resolution
        if (state.width && state.height) {
            setText('auto-detected-res', `Detected: ${state.width}x${state.height}p${state.framerate || 60}`);
        } else {
            setText('auto-detected-res', 'Detected: -');
        }
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    window.autoConfigurator = new AutoConfigurator();
    window.autoConfigurator.init();
});
