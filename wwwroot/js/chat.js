/*
 * chat.js — Server-Sent Events consumer for /api/chat/send.
 *
 * No EventSource — we need POST with a JSON body + auth cookie, and
 * EventSource is GET-only. Instead: fetch() + ReadableStream + manual
 * SSE frame parsing.
 *
 * Wire format (matches Python /api/query → .NET ChatController → here):
 *   event: <name>
 *   data:  <json>
 *   <blank line>
 */
(function () {
    "use strict";

    const form        = document.getElementById("chat-form");
    const input       = document.getElementById("chat-input");
    const sendBtn     = document.getElementById("chat-send");
    const messagesEl  = document.getElementById("messages");
    const welcomeEl   = document.getElementById("welcome");
    const scroller    = document.getElementById("messages-scroller");

    if (!form || !input || !messagesEl || !scroller) return;

    // -----------------------------------------------------------------
    //  Form submit
    // -----------------------------------------------------------------
    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const content = input.value.trim();
        if (!content) return;

        const conversationId = form.dataset.conversationId || null;

        // Hide welcome on first message.
        if (welcomeEl) welcomeEl.classList.add("hidden");

        // Show optimistic user bubble + an empty assistant bubble.
        appendUserBubble(content);
        const assistant = appendAssistantBubble();

        input.value = "";
        setFormLocked(true);

        try {
            const response = await fetch("/api/chat/send", {
                method:      "POST",
                headers:     {
                    "Content-Type": "application/json",
                    "Accept":       "text/event-stream",
                },
                body:        JSON.stringify({ conversationId, content }),
                credentials: "same-origin",
            });

            if (!response.ok) {
                const text = await response.text().catch(() => "");
                showAssistantError(assistant, `Lỗi máy chủ (${response.status}): ${text || "không rõ"}`);
                return;
            }

            // Stash conversation id from response headers (especially on first message).
            const returnedId = response.headers.get("X-Conversation-Id");
            if (returnedId && returnedId !== conversationId) {
                form.dataset.conversationId = returnedId;
                history.replaceState({}, "", `/Chat?id=${returnedId}`);
            }

            await consumeSse(response, {
                onSources: (docs)   => renderSources(assistant.sourcesEl, docs),
                onToken:   (chunk)  => { removeTyping(assistant); assistant.textEl.textContent += chunk; scrollToBottom(); },
                onDone:    (info)   => { removeTyping(assistant); if (info.finish_reason === "error") showAssistantError(assistant, "Có lỗi khi tạo trả lời."); },
                onError:   (msg)    => showAssistantError(assistant, msg || "Lỗi không xác định."),
            });
        } catch (err) {
            console.error(err);
            showAssistantError(assistant, "Mất kết nối với máy chủ.");
        } finally {
            setFormLocked(false);
            input.focus();
        }
    });

    // -----------------------------------------------------------------
    //  Suggestion chips → fill input + submit
    // -----------------------------------------------------------------
    document.querySelectorAll("[data-suggestion]").forEach((btn) => {
        btn.addEventListener("click", () => {
            input.value = btn.dataset.suggestion || "";
            form.requestSubmit();
        });
    });

    // -----------------------------------------------------------------
    //  SSE stream parser
    // -----------------------------------------------------------------
    async function consumeSse(response, handlers) {
        const reader  = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Split buffer on "\n\n" — each chunk is one full SSE message.
            let idx;
            while ((idx = buffer.indexOf("\n\n")) !== -1) {
                const raw = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                dispatchSse(raw, handlers);
            }
        }
    }

    function dispatchSse(raw, handlers) {
        let eventName = "message";
        const dataLines = [];

        for (const line of raw.split("\n")) {
            if (line.length === 0) continue;
            if (line.startsWith(":")) continue;                 // SSE comment
            if (line.startsWith("event:")) {
                eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
                let payload = line.slice(5);
                if (payload.startsWith(" ")) payload = payload.slice(1);
                dataLines.push(payload);
            }
        }
        if (dataLines.length === 0) return;

        let data;
        try { data = JSON.parse(dataLines.join("\n")); }
        catch { return; }

        switch (eventName) {
            case "sources": handlers.onSources?.(data.documents || []);     break;
            case "token":   handlers.onToken?.(data.content || "");         break;
            case "done":    handlers.onDone?.(data);                         break;
            case "error":   handlers.onError?.(data.message || "");          break;
            // unknown event names silently dropped (forward-compat)
        }
    }

    // -----------------------------------------------------------------
    //  DOM builders — same Tailwind classes as the server-rendered bubbles
    // -----------------------------------------------------------------

    function appendUserBubble(text) {
        const wrap = document.createElement("div");
        wrap.className = "flex gap-3 justify-end";
        wrap.innerHTML = `
            <div class="flex-1 max-w-[80%]">
                <div class="bg-blue-500 text-white rounded-xl rounded-tr-sm p-4 shadow-sm">
                    <p class="text-sm leading-relaxed whitespace-pre-wrap" data-text></p>
                </div>
                <p class="text-xs text-gray-400 mt-2 flex items-center gap-1 justify-end">
                    <i class="fa-regular fa-clock"></i> ${formatTime(new Date())}
                </p>
            </div>
            <div class="w-8 h-8 rounded-full bg-gray-200 flex items-center justify-center shrink-0 mt-1">
                <i class="fa-solid fa-user text-gray-500 text-xs"></i>
            </div>`;
        // Use textContent to avoid XSS via user input.
        wrap.querySelector("[data-text]").textContent = text;
        messagesEl.appendChild(wrap);
        scrollToBottom();
    }

    function appendAssistantBubble() {
        const wrap = document.createElement("div");
        wrap.className = "flex gap-3";
        wrap.innerHTML = `
            <div class="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center shrink-0 mt-1">
                <i class="fa-solid fa-robot text-blue-600 text-xs"></i>
            </div>
            <div class="flex-1">
                <div class="bg-white border border-gray-200 rounded-xl rounded-tl-sm p-4 shadow-sm">
                    <div data-sources class="hidden flex-wrap gap-1.5 mb-3"></div>
                    <p data-text class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap"></p>
                    <span data-typing class="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse ml-0.5 align-middle"></span>
                </div>
                <p class="text-xs text-gray-400 mt-2 flex items-center gap-1">
                    <i class="fa-regular fa-clock"></i> ${formatTime(new Date())}
                </p>
            </div>`;
        messagesEl.appendChild(wrap);
        scrollToBottom();
        return {
            sourcesEl: wrap.querySelector("[data-sources]"),
            textEl:    wrap.querySelector("[data-text]"),
            typingEl:  wrap.querySelector("[data-typing]"),
        };
    }

    function renderSources(container, docs) {
        if (!container || !docs || docs.length === 0) return;
        container.innerHTML = "";

        // Dedupe by document_id, keep first title we saw.
        const unique = new Map();
        for (const d of docs) {
            const key = d.document_id || d.id;
            if (key && !unique.has(key)) unique.set(key, d.title || "Tài liệu");
        }

        for (const title of unique.values()) {
            const chip = document.createElement("span");
            chip.className = "text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded-full inline-flex items-center gap-1 max-w-[14rem]";
            chip.innerHTML = '<i class="fa-regular fa-file-lines text-[10px]"></i><span class="truncate"></span>';
            chip.querySelector("span").textContent = title;
            container.appendChild(chip);
        }
        container.classList.remove("hidden");
        container.classList.add("flex");
    }

    function removeTyping(assistant) {
        if (assistant.typingEl && assistant.typingEl.parentNode) {
            assistant.typingEl.remove();
            assistant.typingEl = null;
        }
    }

    function showAssistantError(assistant, message) {
        removeTyping(assistant);
        assistant.textEl.classList.add("text-red-600");
        if (!assistant.textEl.textContent) {
            assistant.textEl.textContent = message;
        } else {
            assistant.textEl.textContent += `\n\n⚠ ${message}`;
        }
        scrollToBottom();
    }

    // -----------------------------------------------------------------
    //  Helpers
    // -----------------------------------------------------------------

    function setFormLocked(locked) {
        input.disabled   = locked;
        if (sendBtn) {
            sendBtn.disabled = locked;
            sendBtn.classList.toggle("opacity-50",        locked);
            sendBtn.classList.toggle("cursor-not-allowed", locked);
        }
    }

    function scrollToBottom() {
        scroller.scrollTop = scroller.scrollHeight;
    }

    function formatTime(d) {
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    }

    // Scroll to bottom on initial page load (so the latest message is visible).
    scrollToBottom();
})();
