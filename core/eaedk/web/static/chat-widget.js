/* Shared contextual chat widget (v2.4.0). ONE component on four pages — only the context changes
 * (docs/23). A page calls mountChat({page_type, getContext, placeholder}) once; the widget owns
 * expand/collapse, send/receive, the per-page session history, and the "thinking…" indicator. It
 * posts the page's context to /api/chat; the post-filter and offline backbone are handled server-side.
 */
function mountChat(opts) {
  if (document.getElementById("chat-launch")) return;     // mount once
  const history = [];                                     // {role, content} for this page's session

  const launch = document.createElement("button");
  launch.id = "chat-launch";
  launch.textContent = "💬 Ask EAEDK";

  const panel = document.createElement("div");
  panel.id = "chat-panel";
  panel.innerHTML =
    `<div id="chat-head"><b>Ask EAEDK</b>` +
      `<span class="kv" id="chat-ctx"></span>` +
      `<button id="chat-close" class="ghost">close ✕</button></div>` +
    `<div id="chat-msgs"></div>` +
    `<div id="chat-think" style="display:none">thinking…</div>` +
    `<div id="chat-input-row">` +
      `<input id="chat-input" type="text" autocomplete="off" ` +
        `placeholder="${escapeHtml(opts.placeholder || "Ask EAEDK…")}">` +
      `<button id="chat-send">Send</button></div>`;

  document.body.appendChild(launch);
  document.body.appendChild(panel);

  const msgs = panel.querySelector("#chat-msgs");
  const input = panel.querySelector("#chat-input");
  const think = panel.querySelector("#chat-think");
  const ctxLabel = panel.querySelector("#chat-ctx");

  function open() {
    panel.classList.add("open");
    launch.style.display = "none";
    const ctx = (opts.getContext && opts.getContext()) || {};
    ctxLabel.textContent = ctx.board_name ? `· ${ctx.board_name}${ctx.project_name ? " / " + ctx.project_name : ""}` : "";
    if (!msgs.childElementCount) bubble("assistant", "Ask me about what's on this page. I'll reason from this board's real capabilities — not generic advice.");
    input.focus();
  }
  function close() { panel.classList.remove("open"); launch.style.display = ""; }
  launch.onclick = open;
  panel.querySelector("#chat-close").onclick = close;

  function bubble(role, text) {
    const d = document.createElement("div");
    d.className = "chat-msg " + role;
    d.innerHTML = `<div class="who">${role === "user" ? "you" : "EAEDK"}</div>` +
                  `<pre>${escapeHtml(text)}</pre>`;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    bubble("user", text);
    think.style.display = "block";
    const ctx = (opts.getContext && opts.getContext()) || {};
    if (!ctx.board_name) {
      think.style.display = "none";
      bubble("assistant", "Pick a board on this page first, then ask me again.");
      return;
    }
    const payload = { ...ctx, page_type: opts.page_type,
                      conversation_history: history.slice(), user_message: text };
    history.push({ role: "user", content: text });
    const r = await api("/api/chat", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    think.style.display = "none";
    if (r.error) { bubble("assistant", r.error + (r.next ? " — " + r.next : "")); return; }
    const ans = r.confidence ? `[${r.confidence}] ${r.answer}` : r.answer;
    bubble("assistant", ans);
    history.push({ role: "assistant", content: r.answer });
  }
  panel.querySelector("#chat-send").onclick = send;
  input.addEventListener("keydown", e => { if (e.key === "Enter") send(); });
}
