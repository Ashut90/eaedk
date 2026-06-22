/* EAEDK Web UI — shared helpers. Vanilla JS, no dependencies, offline. */

const PAGES = [
  ["boards.html",   "Boards"],
  ["setup.html",    "New Project"],
  ["validate.html", "Validate"],
  ["export.html",   "Export"],
  ["studio.html",   "Code Studio"],
  ["ingest.html",   "Datasheet"],
  ["logs.html",     "Log Analyzer"],
  ["mentor.html",   "Mentor"],
];

/* HIGH/MEDIUM/LOW/UNKNOWN confidence -> badge class. */
function confClass(c) {
  return { HIGH: "green", MEDIUM: "yellow", LOW: "red", UNKNOWN: "grey" }[(c||"").toUpperCase()] || "grey";
}
function confBadge(c) { return `<span class="badge ${confClass(c)}">${escapeHtml(c||"?")}</span>`; }

/* Render the top nav into <nav class="top">, marking the current page active. */
function renderNav() {
  const here = location.pathname.split("/").pop() || "boards.html";
  const nav = document.querySelector("nav.top");
  if (!nav) return;
  nav.innerHTML = '<span class="brand">EAEDK</span>' +
    PAGES.map(([file, label]) =>
      `<a class="tab ${file === here ? "active" : ""}" href="${file}">${label}</a>`).join("");
}

/* The command-preview footer: shows the exact CLI command behind what you just did. */
function showCommand(cmd) {
  let bar = document.getElementById("cmdbar");
  if (!bar) { bar = document.createElement("div"); bar.id = "cmdbar"; document.body.appendChild(bar); }
  bar.innerHTML = cmd
    ? `<b>CLI equivalent:</b><code>${escapeHtml(cmd)}</code>`
    : `<b>CLI equivalent:</b><span class="kv">— do something above and the matching command appears here —</span>`;
}

/* fetch() wrapper: always returns parsed JSON; surfaces a plain-English error envelope. */
async function api(path, opts) {
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (e) {
    return { error: "Could not reach the EAEDK server.",
             next: "Make sure `eaedk web` is still running in your terminal, then reload this page." };
  }
  let data;
  try { data = await resp.json(); }
  catch (e) {
    return { error: "The server sent something this page couldn't read.",
             next: "Reload the page; if it keeps happening, restart `eaedk web`." };
  }
  return data;
}

/* Show a friendly error block (from an {error, next} envelope) into a container element. */
function showError(el, env) {
  el.innerHTML = `<div class="error"><b>${escapeHtml(env.error || "Something went wrong.")}</b>` +
    (env.next ? `<div class="next">→ ${escapeHtml(env.next)}</div>` : "") + `</div>`;
}

/* status string -> traffic-light class. */
function lightClass(status) {
  const s = (status || "").toUpperCase();
  if (["GREEN", "PASS", "HIGH", "FEASIBLE", "OK"].includes(s)) return "green";
  if (["YELLOW", "UNKNOWN", "MEDIUM", "INCOMPLETE", "NO_GEOMETRY"].includes(s)) return "yellow";
  if (["RED", "FAIL", "LOW", "BLOCKED", "NOT_FEASIBLE"].includes(s)) return "red";
  return "grey";
}
function dot(status) { return `<span class="dot ${lightClass(status)}"></span>`; }
function badge(text, status) { return `<span class="badge ${lightClass(status)}">${escapeHtml(text)}</span>`; }

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

/* Minimal markdown → safe HTML. Escapes first, then applies formatting. */
function renderMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');   // **bold**
  return s;
}

/* bytes -> human ("64 KB", "2 MB", "unknown"). */
function fmtBytes(n) {
  if (n == null) return "unknown";
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(n % (1024*1024) ? 1 : 0) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(n % 1024 ? 1 : 0) + " KB";
  return n + " B";
}

/* Translate raw engine keys to plain English for display. */
const KEY_LABELS = {
  "estimated_image_size": "Compiled program size",
  "board.flash_bytes": "Board flash size", "board.ram_bytes": "Board RAM size",
  "board.flash_base": "Flash start address", "board.ram_base": "RAM start address",
  "stack_size": "Stack size", "heap_size": "Heap size", "static_size": "Static data size",
  "vector_table_addr": "Interrupt table address", "console_uart": "Debug serial port",
  "toolchain_arch": "Compiler target", "host_os": "Your computer's OS",
  "partitions": "Storage partitions", "primary_storage_bytes": "Storage size",
  "power_rails": "Power rails", "pin_assignments": "Pin assignments",
  "soc.arch": "Chip architecture",
};
function humanizeKeys(text) {
  let out = String(text || "");
  for (const [k, v] of Object.entries(KEY_LABELS)) out = out.split(k).join(v);
  return out;
}

/* Validation rule keys -> plain-English check names (so no raw KEYS show to the user). */
const RULE_LABELS = {
  "FLASH_CAPACITY": "Program fits in flash", "RAM_BUDGET": "Memory (RAM) budget",
  "VECTOR_TABLE_PLACEMENT": "Interrupt table placement",
  "BOOTLOADER_APP_NO_OVERLAP": "Bootloader and app don't overlap",
  "PARTITION_LAYOUT_FITS": "Partitions fit in storage",
  "PARTITION_NO_OVERLAP": "Partitions don't overlap",
  "PARTITION_AB_SYMMETRY": "A/B update slots are equal size",
  "RECOVERY_PRESENT": "A recovery slot exists",
  "LOAD_ADDR_CONFLICT": "Kernel/DTB load addresses are clear",
  "DDR_TIMING_VERIFIED": "DDR memory timing verified",
  "BOOT_FLOW_CONSISTENCY": "Boot hand-off addresses are distinct",
  "CONSOLE_UART_DEFINED": "Debug serial port is set",
  "TOOLCHAIN_ARCH_MATCH": "Compiler matches the chip",
  "SDK_HOST_OS_MATCH": "SDK works on your computer's OS",
  "PINMUX_CONFLICT": "No two signals on one pin",
  "POWER_SEQUENCE": "Power-up order is valid",
  "DRIVER_COMPATIBLE_STRING": "Driver match string is set",
  "REGISTER_MAP_PRESENT": "Register map is verified",
  "SECURE_BOOT_SIGNATURE_VERIFY": "Secure boot: signature is verified",
  "SECURE_BOOT_KEY_STORAGE": "Secure boot: key stored safely",
  "SECURE_BOOT_ROLLBACK_COUNTER": "Secure boot: anti-rollback counter",
  "SECURE_BOOT_DEBUG_LOCKED": "Secure boot: debug locked in production",
  "TOOLCHAIN_COMPILER": "Build tool: compiler installed",
  "TOOLCHAIN_FLASH_TOOL": "Build tool: flasher installed",
  "TOOLCHAIN_BUILD_SYSTEM": "Build tool: build system installed",
  "TOOLCHAIN_TARGET_TRIPLE": "Compiler targets this chip",
};
function humanizeRule(key) {
  if (RULE_LABELS[key]) return RULE_LABELS[key];
  return String(key || "").toLowerCase().replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
}
/* Friendly word for a PASS/FAIL/UNKNOWN status. */
function statusWord(s) {
  return { PASS: "OK", FAIL: "Problem", UNKNOWN: "Missing info" }[(s||"").toUpperCase()] || s;
}

/* Engineering State Engine progress widget — GREEN complete / YELLOW in-progress / GREY not. */
async function renderProgress(containerId, project) {
  const el = document.getElementById(containerId);
  if (!el || !project) return;
  const s = await api("/api/progress/" + encodeURIComponent(project));
  if (s.error) { el.innerHTML = ""; return; }
  const bar = `<div style="background:var(--panel-2);border-radius:6px;height:14px;overflow:hidden">
    <div style="height:100%;width:${s.percent}%;background:var(--green)"></div></div>`;
  const rows = s.items.map(it => {
    const m = { COMPLETE: "✓", IN_PROGRESS: "•", NOT_STARTED: "✗" }[it.status] || "•";
    return `<div style="padding:6px 0;border-bottom:1px solid var(--line)">
      ${dot(it.light)} <b>${m} ${escapeHtml(it.title)}</b>
      ${it.status === "COMPLETE"
        ? `<span class="kv"> — ${escapeHtml({VALIDATION_ENGINE:"verified by the engine",USER:"confirmed by you",LOG_TRIAGE:"resolved from a log"}[it.verified_by] || "complete")}</span>`
        : `<div class="teach">Why it matters: ${escapeHtml(it.why_it_matters)}</div>`}</div>`;
  }).join("");
  el.innerHTML = `<div class="card"><b>Progress: ${s.complete}/${s.total} complete (${s.percent}%)</b>
    ${s.note ? `<div class="kv" style="margin-top:4px">${escapeHtml(s.note)}</div>` : ""}
    <div style="margin:8px 0">${bar}</div>${rows}
    ${s.next ? `<div class="note" style="margin-top:10px"><b>Next:</b> ${escapeHtml(s.next.title)}
      — ${escapeHtml(s.next.why_it_matters)}</div>` : ""}</div>`;
}

document.addEventListener("DOMContentLoaded", () => {
  renderNav();
  const here = (location.pathname.split("/").pop() || "boards.html");
  if (here !== "mentor.html") showCommand(null);
});
