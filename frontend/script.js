document.addEventListener("DOMContentLoaded", () => {
  /* ========= DOM ========= */
  const inputMessage = document.getElementById("inputMessage");
  const sendBtn = document.getElementById("sendBtn");
  const chatbox = document.getElementById("chatbox");

  const rewriteShorterBtn = document.getElementById("rewriteShorter");
  const rewritePoliterBtn = document.getElementById("rewritePoliter");
  const rewriteConfidentBtn = document.getElementById("rewriteConfident");

  const starterPills = document.getElementById("starterPills");
  const toggleBtn = document.getElementById("toggleSettings");
  const settingsPanel = document.getElementById("settingsPanel");

  /* ========= Pill state ========= */
  let selectedTone = "Calm";
  let selectedScenario = "general";

  function setupPills(containerId, onChange) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.addEventListener("click", (e) => {
      const btn = e.target.closest(".pill");
      if (!btn) return;

      container.querySelectorAll(".pill").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      onChange(btn.dataset.value);
    });
  }

  setupPills("tonePills", (v) => (selectedTone = v));
  setupPills("scenarioPills", (v) => (selectedScenario = v));

  /* ========= UI helpers ========= */
  function appendMessage(text, sender) {
    const msgDiv = document.createElement("div");
    msgDiv.classList.add("message", sender);

    const textBubble = document.createElement("span");
    textBubble.classList.add("text-bubble");
    textBubble.textContent = text;

    if (sender === "bot") {
      const iconImg = document.createElement("img");
      iconImg.src = "/static/logo.jpg";
      iconImg.classList.add("bot-chat-logo");
      iconImg.alt = "bot logo";
      msgDiv.appendChild(iconImg);
    }

    msgDiv.appendChild(textBubble);
    chatbox.appendChild(msgDiv);
    chatbox.scrollTop = chatbox.scrollHeight;
  }

// ✅ render clickable "draft replies" (NOT bot messages)
function appendOptions(options) {
  if (!options || !Array.isArray(options)) return;

  const cleaned = options
    .filter((t) => typeof t === "string" && t.trim().length > 0)
    .slice(0, 3);

  if (cleaned.length === 0) return;

  const section = document.createElement("div");
  section.classList.add("options-section");

  const label = document.createElement("div");
  label.className = "options-label";
  label.textContent = "You could reply with:";
  section.appendChild(label);

  const wrap = document.createElement("div");
  wrap.classList.add("option-row");

  cleaned.forEach((text) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "option-chip";
    btn.textContent = text;

    btn.addEventListener("click", () => {
      inputMessage.value = text; // 放进输入框（用户自己决定要不要发送）
      inputMessage.focus();
    });

    wrap.appendChild(btn);
  });

  section.appendChild(wrap);
  chatbox.appendChild(section);
  chatbox.scrollTop = chatbox.scrollHeight;
}


  /* ========= Welcome ========= */
  appendMessage("Hi 👋 I’m Echo. Paste what they said to you, and I’ll draft a reply you can send. (Use Rewrite if you typed your own draft.)", "bot");


  /* ========= API ========= */
  // ✅ IMPORTANT: Render backend base URL
  const API_BASE = "";


  async function callEchoAPI({ message, mode = "chat" }) {
    // Timeout so UI won't hang forever
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 70000); // 70s

    try {
      const response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          tone: selectedTone,
          scenario: selectedScenario,
          mode,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errText}`);
      }

      return await response.json(); // chat: {reply, options?}  rewrite: {reply}
    } catch (err) {
      if (err.name === "AbortError") {
        throw new Error("Request timed out. Please try again.");
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /* ========= Chat ========= */
  async function sendMessage() {
    const message = inputMessage.value.trim();
    if (!message) return;

    appendMessage(message, "user");
    inputMessage.value = "";
    sendBtn.disabled = true;

    try {
      const data = await callEchoAPI({ message, mode: "chat" });
      appendMessage(data.reply, "bot");
      appendOptions(data.options); // ✅ show options if backend provides them
    } catch (error) {
      appendMessage(`Error: ${error.message}`, "bot");
      console.error(error);
    } finally {
      sendBtn.disabled = false;
      inputMessage.focus();
    }
  }

  sendBtn?.addEventListener("click", sendMessage);

  inputMessage?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      sendMessage();
    }
  });

  /* ========= Rewrite ========= */
  async function rewriteMessage(mode) {
    const message = inputMessage.value.trim();
    if (!message) {
      appendMessage("Type something first, then choose a rewrite option.", "bot");
      return;
    }

    sendBtn.disabled = true;

    try {
      const data = await callEchoAPI({ message, mode });
      appendMessage(data.reply, "bot");
    } catch (error) {
      appendMessage(`Error: ${error.message}`, "bot");
      console.error(error);
    } finally {
      sendBtn.disabled = false;
      inputMessage.focus();
    }
  }

  rewriteShorterBtn?.addEventListener("click", () => rewriteMessage("rewrite_shorter"));
  rewritePoliterBtn?.addEventListener("click", () => rewriteMessage("rewrite_politer"));
  rewriteConfidentBtn?.addEventListener("click", () => rewriteMessage("rewrite_confident"));

  /* ========= Quick starters ========= */
  function getStarterText(type, scenario, tone) {
    const isProf = scenario === "talking to a professor";
    const isFriend = scenario === "messaging a friend";
    const polite = (tone || "").toLowerCase().includes("polite");

    if (type === "ask") {
      if (isProf)
        return polite
          ? "Hi Professor, could I please ask a quick question about the deadline?"
          : "Hi Professor, can I ask a quick question about the deadline?";
      if (isFriend) return "Hey, quick question — are you free later?";
      return "Hi, quick question — could you help me with something?";
    }

    if (type === "followup") {
      if (isProf) return "Just following up on my message — thank you in advance.";
      if (isFriend) return "Just checking in — no rush to reply.";
      return "Just following up — let me know when you get a moment.";
    }

    if (type === "no") {
      if (isProf)
        return "Thanks for asking — I can’t do that, but I can suggest an alternative if helpful.";
      if (isFriend) return "I can’t this time, sorry — could we do another day?";
      return "Thanks for thinking of me, but I’ll have to pass this time.";
    }

    if (type === "clarify") {
      if (isProf) return "Sorry, I may have misunderstood — could you clarify what you mean by this part?";
      if (isFriend) return "Sorry, I explained that badly — what I meant was…";
      return "Sorry if I wasn’t clear — what I mean is…";
    }

    if (type === "friendly") {
      if (isProf) return "Hi Professor, hope you’re doing well — I wanted to ask about…";
      if (isFriend) return "Hey! Hope your week’s going okay 😊";
      return "Hi! Hope you’re having a good day.";
    }

    return "Hi — I wanted to say…";
  }

  starterPills?.addEventListener("click", (e) => {
    const btn = e.target.closest(".qs-pill");
    if (!btn) return;

    const type = btn.dataset.starter;
    const draft = getStarterText(type, selectedScenario, selectedTone);

    inputMessage.value = draft;
    inputMessage.focus();
  });

  /* ========= Collapsible settings panel ========= */
  function openPanel() {
    if (!settingsPanel) return;
    settingsPanel.hidden = false;
    toggleBtn?.setAttribute("aria-expanded", "true");
  }

  function closePanel() {
    if (!settingsPanel) return;
    settingsPanel.hidden = true;
    toggleBtn?.setAttribute("aria-expanded", "false");
  }

  toggleBtn?.addEventListener("click", () => {
    const expanded = toggleBtn.getAttribute("aria-expanded") === "true";
    expanded ? closePanel() : openPanel();
  });

  inputMessage?.addEventListener("focus", closePanel);
});
