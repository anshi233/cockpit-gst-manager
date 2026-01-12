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
            html += `<div class="gst-chat-message ${cls}">`;
            html += `<div class="gst-chat-content">${this.escapeHtml(msg.content)}</div>`;

            if (msg.pipeline) {
                html += `<div class="gst-chat-pipeline">`;
                html += `<pre class="gst-code-block">${this.escapeHtml(msg.pipeline)}</pre>`;
                html += `<button class="gst-btn gst-btn-primary gst-btn-use" data-pipeline="${this.escapeAttr(msg.pipeline)}">Use Pipeline</button>`;
                html += `</div>`;
            }
            html += `</div>`;
        }

        if (this.loading) {
            html += `<div class="gst-chat-message gst-chat-assistant">`;
            html += `<div class="gst-chat-content gst-chat-loading">Thinking...</div>`;
            html += `</div>`;
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
        this.renderMessages();

        try {
            const result = await callMethod("AiGeneratePipeline", prompt, "");
            const response = JSON.parse(result);

            this.loading = false;

            if (response.error) {
                this.addMessage("assistant", `Error: ${response.error}\n${response.message || ""}`);
            } else if (response.pipeline) {
                this.addMessage("assistant", response.message || "Here's your pipeline:", response.pipeline);
            } else {
                this.addMessage("assistant", response.message || "I couldn't generate a pipeline for that request.");
            }
        } catch (error) {
            this.loading = false;
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
    }
};

// Initialize when DOM ready
document.addEventListener("DOMContentLoaded", () => AiChat.init());
