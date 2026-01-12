/**
 * AI Chat Component for GStreamer Manager
 * 
 * Provides natural language interface for pipeline generation.
 */

const AiChat = {
    isOpen: false,
    messages: [],
    loading: false,

    init() {
        this.chatPanel = document.getElementById("ai-chat-panel");
        this.messagesContainer = document.getElementById("ai-messages");
        this.input = document.getElementById("ai-input");
        this.sendBtn = document.getElementById("btn-ai-send");

        if (!this.chatPanel) return;

        // Event listeners
        document.getElementById("btn-ai-toggle").addEventListener("click", () => this.toggle());
        document.getElementById("btn-ai-close").addEventListener("click", () => this.toggle());
        this.sendBtn.addEventListener("click", () => this.sendMessage());
        this.input.addEventListener("keypress", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Add welcome message
        this.addMessage("assistant",
            "Hi! I'm a GStreamer pipeline assistant. Tell me what you want to stream or record, " +
            "and I'll generate a pipeline for you.\n\n" +
            "Example: \"Stream HDMI at 15Mbps to SRT on port 5000\""
        );
    },

    toggle() {
        this.isOpen = !this.isOpen;
        this.chatPanel.style.display = this.isOpen ? "flex" : "none";
        if (this.isOpen) {
            this.input.focus();
        }
    },

    addMessage(role, content, pipeline = null) {
        this.messages.push({ role, content, pipeline });
        this.renderMessages();
    },

    renderMessages() {
        let html = "";
        for (const msg of this.messages) {
            const cls = msg.role === "user" ? "gst-chat-user" : "gst-chat-assistant";
            const content = msg.role === "user" ? this.escapeHtml(msg.content) : this.renderMarkdown(msg.content);
            html += `<div class="gst-chat-message ${cls}">`;
            html += `<div class="gst-chat-content">${content}</div>`;

            if (msg.pipeline) {
                html += `<div class="gst-chat-pipeline">`;
                html += `<pre class="gst-code-block">${this.escapeHtml(msg.pipeline)}</pre>`;
                html += `<button class="gst-btn gst-btn-primary gst-btn-use" data-pipeline="${this.escapeAttr(msg.pipeline)}">Use Pipeline</button>`;
                html += `</div>`;
            }
            html += `</div>`;
        }

        if (this.loading) {
            const elapsed = this.loadingStartTime ? Math.floor((Date.now() - this.loadingStartTime) / 1000) : 0;
            html += `<div class="gst-chat-message gst-chat-assistant">`;
            html += `<div class="gst-chat-content gst-chat-loading">`;
            html += `<span class="loading-dots">AI is thinking</span>`;
            if (elapsed > 5) {
                html += ` <span class="loading-time">(${elapsed}s)</span>`;
            }
            html += `</div></div>`;
        }

        this.messagesContainer.innerHTML = html;
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;

        // Add click handlers for "Use Pipeline" buttons
        this.messagesContainer.querySelectorAll(".gst-btn-use").forEach(btn => {
            btn.addEventListener("click", () => {
                const pipeline = btn.dataset.pipeline;
                this.usePipeline(pipeline);
            });
        });
    },

    async sendMessage() {
        const prompt = this.input.value.trim();
        if (!prompt || this.loading) return;

        this.input.value = "";
        this.addMessage("user", prompt);
        this.loading = true;
        this.loadingStartTime = Date.now();
        this.renderMessages();

        // Update timer every second while loading
        this.loadingTimer = setInterval(() => {
            if (this.loading) {
                this.renderMessages();
            }
        }, 1000);

        try {
            const result = await callMethod("AiGeneratePipeline", prompt, "");
            const response = JSON.parse(result);

            this.loading = false;
            clearInterval(this.loadingTimer);

            if (response.error) {
                this.addMessage("assistant", `Error: ${response.error}\n${response.message || ""}`);
            } else if (response.pipeline) {
                this.addMessage("assistant", response.message || "Here's your pipeline:", response.pipeline);
            } else {
                this.addMessage("assistant", response.message || "I couldn't generate a pipeline for that request.");
            }
        } catch (error) {
            this.loading = false;
            clearInterval(this.loadingTimer);
            console.error("AI request failed:", error);
            this.addMessage("assistant", `Request failed: ${error.message}`);
        }
    },

    usePipeline(pipeline) {
        // Open editor with this pipeline
        document.getElementById("instance-name").value = "AI Generated Pipeline";
        document.getElementById("instance-pipeline").value = pipeline;
        document.getElementById("editor-title").textContent = "New Instance";
        document.getElementById("pipeline-editor").style.display = "block";

        showToast("Pipeline loaded into editor", "success");
        this.toggle(); // Close chat panel
    },

    escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    },

    escapeAttr(text) {
        return text.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },

    renderMarkdown(text) {
        // Simple markdown rendering
        let html = this.escapeHtml(text);

        // Code blocks (```code```)
        html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre class="gst-code-block">$2</pre>');

        // Inline code (`code`)
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold (**text** or __text__)
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

        // Italic (*text* or _text_)
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

        // Headers (## Header)
        html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');

        // Lists (- item)
        html = html.replace(/^- (.+)$/gm, '• $1');

        // Line breaks
        html = html.replace(/\n/g, '<br>');

        return html;
    }
};

// Initialize when DOM ready
document.addEventListener("DOMContentLoaded", () => AiChat.init());
