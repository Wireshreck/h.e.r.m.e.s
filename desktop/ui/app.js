/* H.E.R.M.E.S. desktop canvas — all state comes from /api/snapshot. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const stage = $("stage"), viewport = $("viewport"), svg = $("edges"), nodesEl = $("nodes");
  const ascii = $("ascii"), tooltip = $("tooltip"), inspector = $("inspector");
  const chatInput = $("chatInput"), chatResponse = $("chatResponse"), sendBtn = $("sendBtn"), cancelBtn = $("cancelBtn");
  const historyDrawer = $("historyDrawer"), commsDrawer = $("commsDrawer");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const LAYOUT_KEY = "hermes.canvas.layout.v1";

  const state = {
    snap: null, agents: [], transform: { x: 0, y: 0, k: 0.78 },
    settings: null, models: null, pollTimer: 0, saveTimer: 0, ctxAgent: null,
    positions: loadLayout(), selected: null, highlight: null,
    filter: "all", filterAgent: "", search: "",
    drawers: { history: false, comms: false }, hoverTimer: 0, leaveTimer: 0,
    sending: false, activeJob: null, drag: null, moved: false, nodeEls: new Map(), edgeEls: new Map(),
  };

  function loadLayout() {
    try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}"); } catch { return {}; }
  }
  function saveLayout() {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.positions));
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => {
      if (state.settings) {
        state.settings.canvas.positions = state.positions;
        state.settings.canvas.preset = "custom";
        post("/api/settings", {
          canvas: { positions: state.positions, preset: "custom" },
        }).catch(() => {});
      }
    }, 500);
  }
  function toast(text) {
    const el = $("toast");
    el.textContent = text; el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 2600);
  }
  function applyTheme(colors) {
    if (!colors) return;
    const map = { bg: "--bg", bg2: "--bg-2", line: "--line", ink: "--ink", dim: "--dim",
                  accent: "--cyan", teal: "--teal", amber: "--amber", red: "--red", violet: "--violet" };
    for (const [key, cssVar] of Object.entries(map)) {
      if (colors[key]) document.documentElement.style.setProperty(cssVar, colors[key]);
    }
    document.documentElement.style.setProperty("--line-2", colors.line || "#2a303c");
  }
  function now() { return Date.now(); }
  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function short(s, n = 90) { s = String(s || ""); return s.length > n ? s.slice(0, n) + "…" : s; }
  function ago(ms) {
    if (!ms) return "—";
    const sec = Math.max(0, Math.round((Date.now() - (ms > 1e12 ? ms : ms * 1000)) / 1000));
    if (sec < 60) return sec + "s";
    if (sec < 3600) return Math.floor(sec / 60) + "m";
    if (sec < 86400) return Math.floor(sec / 3600) + "h";
    return Math.floor(sec / 86400) + "d";
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let payload;
    try { payload = await response.json(); } catch { payload = { error: `HTTP ${response.status}` }; }
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }
  function post(path, body) {
    return api(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function connection(ok, text) {
    $("conn").className = "conn " + (ok ? "ok" : "bad");
    $("connText").textContent = text;
    $("banner").hidden = ok;
    if (!ok) $("banner").textContent = "RUNTIME DISCONNECTED — retrying…";
  }

  // -- layout ---------------------------------------------------------------
  function defaultPositions(agents, preset) {
    const center = agents.find(a => a.orchestrator);
    const others = agents.filter(a => !a.orchestrator);
    const p = {};
    if (center) p[center.id] = { x: 0, y: 0 };
    others.forEach((a, i) => {
      if (preset === "grid") {
        p[a.id] = { x: ((i % 4) - 1.5) * 460, y: (Math.floor(i / 4) - 1) * 240 };
      } else if (preset === "radial") {
        const angle = (i / others.length) * Math.PI * 2 - Math.PI / 2;
        p[a.id] = { x: Math.cos(angle) * 620, y: Math.sin(angle) * 430 };
      } else {
        const ring = i < 6 ? 330 : i < 10 ? 520 : 700;
        const slot = i < 6 ? i : i < 10 ? i - 6 : i - 10;
        const per = i < 6 ? 6 : i < 10 ? 4 : 3;
        const angle = (slot / per) * Math.PI * 2 - Math.PI / 2 + (ring === 520 ? 0.35 : ring === 700 ? -0.2 : 0);
        p[a.id] = { x: Math.cos(angle) * ring * 1.5, y: Math.sin(angle) * ring };
      }
    });
    return p;
  }
  function canvasPreset() {
    return state.settings?.canvas?.preset || "algorithm";
  }
  function applyTransform() {
    const t = state.transform;
    viewport.style.transform = `translate(${t.x}px, ${t.y}px) scale(${t.k})`;
    drawAscii();
  }
  function fit() {
    const agents = state.agents;
    if (!agents.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const a of agents) {
      const p = state.positions[a.id]; if (!p) continue;
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    if (!isFinite(minX)) return;
    const w = maxX - minX + 320, h = maxY - minY + 220;
    const k = Math.max(0.22, Math.min(1.1, Math.min(stage.clientWidth / w, stage.clientHeight / h)));
    state.transform = { k, x: stage.clientWidth / 2 - ((minX + maxX) / 2) * k,
                        y: stage.clientHeight / 2 - ((minY + maxY) / 2) * k };
    applyTransform();
  }

  // -- ASCII background ------------------------------------------------------
  function drawAscii() {
    const ctx = ascii.getContext("2d");
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const w = stage.clientWidth, h = stage.clientHeight;
    if (ascii.width !== Math.round(w * ratio) || ascii.height !== Math.round(h * ratio)) {
      ascii.width = Math.round(w * ratio); ascii.height = Math.round(h * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const glyphs = ["·", "·", "·", "+", ":", "/", "\\", "0", "1", "#", "=", "-"];
    const step = Math.max(22, Math.round(26 * state.transform.k));
    const t = state.transform;
    ctx.font = "9px " + getComputedStyle(document.body).getPropertyValue("--mono");
    ctx.textBaseline = "top";
    for (let gy = 0; gy < h + step; gy += step) {
      for (let gx = 0; gx < w + step; gx += step) {
        // world coordinate of this cell -> stable hashed density
        const wx = Math.round((gx - t.x) / t.k / step), wy = Math.round((gy - t.y) / t.k / step);
        let hash = Math.imul(wx * 374761393 + wy * 668265263, 2654435761) >>> 0;
        hash ^= hash >>> 13;
        if ((hash & 7) !== 0) continue; // ~1/8 of cells carry a glyph
        const g = glyphs[hash % glyphs.length];
        ctx.fillStyle = (hash & 16) ? "rgba(0,229,255,0.075)" : "rgba(130,150,175,0.055)";
        ctx.fillText(g, gx + (hash % 5), gy + ((hash >>> 4) % 5));
      }
    }
  }

  // -- nodes / edges ---------------------------------------------------------
  function makeNode(agent) {
    const el = document.createElement("div");
    el.className = "node" + (agent.orchestrator ? " orchestrator" : "");
    el.dataset.id = agent.id;
    el.tabIndex = 0;
    el.innerHTML = `<div class="node-head"><span class="status-dot"></span><span class="name"></span><span class="node-signals"></span></div>
      <div class="node-body"><div class="role"></div><div class="task"></div><div class="model"></div></div>`;
    el.addEventListener("pointerdown", onNodeDown);
    el.addEventListener("click", (e) => { if (!state.moved) inspect(agent.id); e.stopPropagation(); });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inspect(agent.id); } });
    el.addEventListener("mouseenter", (e) => showTip(agent, e));
    el.addEventListener("mousemove", (e) => moveTip(e));
    el.addEventListener("mouseleave", hideTip);
    nodesEl.appendChild(el);
    return el;
  }
  function renderNodes() {
    const ids = new Set(state.agents.map(a => a.id));
    for (const [id, el] of state.nodeEls) if (!ids.has(id)) { el.remove(); state.nodeEls.delete(id); }
    const preset = canvasPreset();
    const saved = preset === "custom" ? (state.settings?.canvas?.positions || {}) : {};
    const defaults = defaultPositions(state.agents, preset);
    for (const a of state.agents) {
      let el = state.nodeEls.get(a.id);
      if (!el) { el = makeNode(a); state.nodeEls.set(a.id, el); }
      if (!state.positions[a.id]) {
        state.positions[a.id] = saved[a.id] || defaults[a.id] || { x: 0, y: 120 };
      }
      const p = state.positions[a.id];
      el.style.left = p.x + "px"; el.style.top = p.y + "px";
      el.style.transform = "translate(-50%, -50%)";
      el.dataset.status = a.status;
      el.querySelector(".name").textContent = a.id.toUpperCase();
      el.querySelector(".role").textContent = (a.role || "").toUpperCase();
      el.querySelector(".task").textContent = a.current_task ? `▸ ${short(a.current_task, 34)}` : "";
      el.querySelector(".model").textContent = short(a.model || a.provider || "", 34);
      const signals = [];
      if (a.comms?.pending) signals.push(`${a.comms.pending}↘`);
      if (a.tasks?.some(t => t.status === "running")) signals.push("●");
      el.querySelector(".node-signals").textContent = signals.join(" ");
      el.classList.toggle("selected", state.selected === a.id);
      const linked = state.highlight && (state.highlight.id === a.id || state.highlight.peers?.has(a.id));
      el.classList.toggle("related", !!linked);
      el.classList.toggle("dimmed", !!(state.highlight && !linked));
    }
  }
  function renderEdges() {
    const t = now();
    const pairs = new Map();
    const hubId = state.agents.find(a => a.orchestrator)?.id;
    for (const m of (state.snap?.messages || []).slice(0, 80)) {
      const key = `${m.sender}->${(m.recipients || [])[0] || hubId}`;
      if (!pairs.has(key)) pairs.set(key, { from: m.sender, to: (m.recipients || [])[0] || hubId, ts: m.timestamp_ms });
    }
    const wanted = new Set();
    const draw = (from, to, cls, key) => {
      const a = state.positions[from], b = state.positions[to];
      if (!a || !b) return;
      wanted.add(key);
      let line = state.edgeEls.get(key);
      if (!line) {
        line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        svg.appendChild(line); state.edgeEls.set(key, line);
      }
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      const active = cls === "comm" && (t - (pairs.get(key)?.ts || 0) < (state.settings?.canvas?.commActivityMs || 15000));
      const hi = state.highlight && (state.highlight.id === from || state.highlight.id === to);
      line.setAttribute("class", `edge ${cls}${active ? " active" : ""}${hi ? " highlight" : ""}`);
    };
    if (state.settings?.canvas?.showHubEdges !== false) {
      for (const a of state.agents) if (!a.orchestrator && hubId) draw(hubId, a.id, "hub", `hub:${a.id}`);
    }
    if (state.settings?.canvas?.showCommEdges !== false) {
      for (const [key, p] of pairs) if (p.from !== p.to) draw(p.from, p.to, "comm", `comm:${key}`);
    }
    for (const [key, el] of state.edgeEls) if (!wanted.has(key)) { el.remove(); state.edgeEls.delete(key); }
  }

  // -- pointer interactions --------------------------------------------------
  function onNodeDown(e) {
    if (e.button !== 0) return;
    e.stopPropagation();
    const id = e.currentTarget.dataset.id;
    state.drag = { type: "node", id, startX: e.clientX, startY: e.clientY,
                   origin: { ...state.positions[id] } };
    state.moved = false;
    e.currentTarget.setPointerCapture(e.pointerId);
    hideTip();
  }
  stage.addEventListener("pointerdown", (e) => {
    // Never start a canvas pan on interactive UI: pointer capture on #stage
    // would swallow the subsequent click on menu/modal buttons.
    if (e.target.closest(".node, #chat, #inspector, .drawer, #topbar, #contextMenu, #settingsModal, #toast")) return;
    if (e.button !== 0 && e.button !== 1) return;
    state.drag = { type: "pan", startX: e.clientX, startY: e.clientY,
                   origin: { ...state.transform } };
    state.moved = false;
    stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener("pointermove", (e) => {
    if (state.drag) {
      const dx = e.clientX - state.drag.startX, dy = e.clientY - state.drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) state.moved = true;
      if (state.drag.type === "pan") {
        state.transform.x = state.drag.origin.x + dx;
        state.transform.y = state.drag.origin.y + dy;
        applyTransform();
      } else {
        const p = state.positions[state.drag.id];
        p.x = state.drag.origin.x + dx / state.transform.k;
        p.y = state.drag.origin.y + dy / state.transform.k;
        renderNodes(); renderEdges();
      }
    }
    edgeProximity(e);
  });
  stage.addEventListener("pointerup", (e) => {
    if (state.drag?.type === "node") saveLayout();
    state.drag = null;
    setTimeout(() => { state.moved = false; }, 0);
  });
  stage.addEventListener("wheel", (e) => {
    if (e.target.closest("#chat, .drawer, #inspector, #contextMenu, #settingsModal, #toast")) return;
    e.preventDefault();
    const rect = stage.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const t = state.transform;
    const k = Math.max(0.22, Math.min(2.6, t.k * (e.deltaY > 0 ? 0.9 : 1.1)));
    t.x = mx - (mx - t.x) * (k / t.k);
    t.y = my - (my - t.y) * (k / t.k);
    t.k = k; applyTransform();
  }, { passive: false });

  function showTip(agent, e) {
    const a = state.agents.find(x => x.id === agent.id) || agent;
    tooltip.innerHTML = `<b>${esc(a.id)}</b> · ${esc(a.role)}<br>status: ${esc(a.status)}<br>` +
      `model: ${esc(a.model || "—")}<br>task: ${esc(short(a.current_task, 80))}<br>` +
      `comms: ${a.comms?.total || 0} total / ${a.comms?.pending || 0} pending`;
    tooltip.hidden = false; moveTip(e);
  }
  function moveTip(e) {
    const rect = stage.getBoundingClientRect();
    tooltip.style.left = Math.min(rect.width - 330, e.clientX - rect.left + 14) + "px";
    tooltip.style.top = Math.min(rect.height - 110, e.clientY - rect.top + 14) + "px";
  }
  function hideTip() { tooltip.hidden = true; }

  // -- inspector ------------------------------------------------------------
  function inspect(id) {
    const a = state.agents.find(x => x.id === id);
    if (!a) return;
    state.selected = id; renderNodes();
    $("inspName").textContent = a.id.toUpperCase();
    const convs = (state.snap?.conversations || []).filter(c => (c.participants || []).includes(id));
    $("inspBody").innerHTML =
      `<b>role</b> ${esc(a.role)}<br><b>description</b> ${esc(a.description)}<br>` +
      `<b>status</b> ${esc(a.status)}<br><b>model</b> ${esc(a.model || "—")} (${esc(a.provider || "—")})<br>` +
      `<b>tools</b> ${esc(a.tools.slice(0, 8).join(", "))}${a.tools.length > 8 ? "…" : ""}<br>` +
      `<b>tasks</b> ${a.tasks.length ? a.tasks.map(t => `${esc(t.id)} [${esc(t.status)}] ${esc(short(t.title, 50))}`).join("<br>") : "none"}<br>` +
      `<b>comms</b> ${a.comms.total} messages, ${a.comms.pending} pending<br>` +
      `<b>conversations</b> ${convs.length}`;
    inspector.hidden = false;
    $("inspInput").hidden = true; $("inspSend").hidden = true; $("inspResult").textContent = "";
  }
  function closeInspector() { inspector.hidden = true; state.selected = null; renderNodes(); }
  $("inspClose").addEventListener("click", closeInspector);
  let inspMode = null;
  $("inspMessage").addEventListener("click", () => { inspMode = "message"; $("inspInput").hidden = false; $("inspSend").hidden = false; $("inspInput").focus(); });
  $("inspAsk").addEventListener("click", () => { inspMode = "ask"; $("inspInput").hidden = false; $("inspSend").hidden = false; $("inspInput").focus(); });
  $("inspTasks").addEventListener("click", () => { openDrawer("history"); $("historySearch").value = state.selected || ""; renderHistory(); });
  $("inspSend").addEventListener("click", async () => {
    if (!state.selected) return;
    const text = $("inspInput").value.trim();
    if (!text) return;
    $("inspSend").disabled = true;
    $("inspResult").textContent = "sending…";
    try {
      const result = inspMode === "ask"
        ? await post("/api/ask", { sender: "hermes-orchestrator", agent: state.selected, question: text })
        : await post("/api/messages", { sender: "hermes-orchestrator", recipient: state.selected, content: text });
      $("inspResult").textContent = inspMode === "ask"
        ? (result.answered ? `${result.from}: ${result.content}` : `no reply yet (${result.request_id})`)
        : `queued ${result.message_id} · ${result.status}`;
      $("inspInput").value = "";
      await poll();
    } catch (err) { $("inspResult").textContent = err.message; }
    $("inspSend").disabled = false;
  });

  // -- drawers --------------------------------------------------------------
  function openDrawer(which) {
    state.drawers[which] = true;
    const el = which === "history" ? historyDrawer : commsDrawer;
    el.classList.add("open"); el.setAttribute("aria-hidden", "false");
    $(which + "Btn").setAttribute("aria-expanded", "true");
  }
  function closeDrawer(which) {
    state.drawers[which] = false;
    const el = which === "history" ? historyDrawer : commsDrawer;
    el.classList.remove("open"); el.setAttribute("aria-hidden", "true");
    $(which + "Btn").setAttribute("aria-expanded", "false");
  }
  function toggleDrawer(which) { state.drawers[which] ? closeDrawer(which) : openDrawer(which); }
  $("historyBtn").addEventListener("click", () => toggleDrawer("history"));
  $("commsBtn").addEventListener("click", () => toggleDrawer("comms"));
  document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => closeDrawer(b.dataset.close)));
  function edgeProximity(e) {
    if (state.drag || e.target.closest(".drawer, #settingsModal, #contextMenu, #toast")) return;
    const rect = stage.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const px = e.clientX, py = e.clientY;
    const edge = 14, activeTop = 50, activeBottom = rect.height - 170;
    const reveal = reduced ? 0 : (state.settings?.behavior?.revealDelayMs ?? 110);
    const retract = reduced ? 0 : (state.settings?.behavior?.retractDelayMs ?? 550);
    clearTimeout(state.hoverTimer); clearTimeout(state.leaveTimer);
    const left = x < edge && y > activeTop && y < activeBottom;
    const right = x > rect.width - edge && y > activeTop && y < activeBottom;
    if (left || right) {
      const which = left ? "history" : "comms";
      state.hoverTimer = setTimeout(() => openDrawer(which), reveal);
    } else {
      state.leaveTimer = setTimeout(() => {
        if (state.drag) return;
        for (const which of ["history", "comms"]) {
          if (!state.drawers[which]) continue;
          const r = (which === "history" ? historyDrawer : commsDrawer).getBoundingClientRect();
          const inside = px >= r.left && px <= r.right && py >= r.top && py <= r.bottom;
          const inEdge = which === "history" ? px <= 22 : px >= window.innerWidth - 22;
          if (!inside && !inEdge) closeDrawer(which);
        }
      }, retract);
    }
  }
  window.addEventListener("keydown", (e) => {
    if (e.altKey && e.key.toLowerCase() === "h") { e.preventDefault(); toggleDrawer("history"); }
    if (e.altKey && e.key.toLowerCase() === "c") { e.preventDefault(); toggleDrawer("comms"); }
    if (e.key === "Escape") {
      closeInspector(); closeDrawer("history"); closeDrawer("comms");
      closeSettings(); $("contextMenu").hidden = true;
    }
  });

  function renderHistory() {
    const jobs = state.snap?.jobs || [], tasks = state.snap?.tasks || [];
    const q = state.search.toLowerCase();
    const items = [];
    for (const j of jobs) {
      if (q && !(j.text + j.status).toLowerCase().includes(q)) continue;
      items.push(`<div class="item" data-job="${esc(j.id)}"><div class="title">${esc(short(j.text, 78))}</div>
        <div class="meta"><span class="tag ${esc(j.status)}">${esc(j.status)}</span><span>${ago(j.created_at)}</span><span>${esc(j.id)}</span></div>
        <div class="preview">${esc(short(j.response || j.error || "no response yet", 130))}</div></div>`);
    }
    for (const t of tasks) {
      if (q && !(t.title + t.status + (t.assignee || "")).toLowerCase().includes(q)) continue;
      items.push(`<div class="item" data-task="${esc(t.id)}"><div class="title">${esc(short(t.title, 78))}</div>
        <div class="meta"><span class="tag ${esc(t.status)}">${esc(t.status)}</span><span>${ago(t.created_at)}</span><span>${esc(t.assignee || "unassigned")}</span></div>
        <div class="preview">${esc(short(t.body || "", 130))}</div></div>`);
    }
    $("historyList").innerHTML = items.join("") || `<div class="small">No tasks yet. Send one to the orchestrator.</div>`;
    $("historyList").querySelectorAll("[data-job]").forEach(el => el.addEventListener("click", () => {
      const job = jobs.find(j => j.id === el.dataset.job);
      chatInput.value = job.text;
      showChat(job.response || job.error || `job ${job.status}`, job.status === "error");
      chatInput.focus();
    }));
    $("historyList").querySelectorAll("[data-task]").forEach(el => el.addEventListener("click", () => {
      const t = tasks.find(x => x.id === el.dataset.task);
      showChat(`${t.id} · ${t.status} · ${t.assignee || "unassigned"}\n${t.body || ""}`, false);
    }));
  }
  $("historySearch").addEventListener("input", (e) => { state.search = e.target.value; renderHistory(); });

  function renderComms() {
    const sel = $("commsAgent");
    for (const a of state.agents) {
      if (![...sel.options].some(o => o.value === a.id)) {
        const o = document.createElement("option"); o.value = a.id; o.textContent = a.id; sel.appendChild(o);
      }
    }
    let msgs = state.snap?.messages || [];
    if (state.filter === "task") msgs = msgs.filter(m => m.task_id);
    if (state.filter === "agent") msgs = msgs.filter(m => state.filterAgent && (m.sender === state.filterAgent || (m.recipients || []).includes(state.filterAgent)));
    if (state.filter === "conversation") msgs = msgs.filter(m => m.conversation_id);
    $("commsList").innerHTML = msgs.slice(0, 120).map(m =>
      `<div class="item" data-msg="${esc(m.id)}"><div class="title">${esc(m.sender)} → ${esc((m.recipients || []).join(", ") || "—")}</div>
       <div class="meta"><span class="tag ${esc(m.type)}">${esc(m.type)}</span><span>${ago(m.timestamp_ms)}</span><span>${esc(short(m.conversation_id, 16))}</span>${m.task_id ? `<span>${esc(m.task_id)}</span>` : ""}</div>
       <div class="preview">${esc(short(m.content, 160))}</div></div>`).join("") ||
      `<div class="small">No communications recorded yet.</div>`;
    $("commsList").querySelectorAll("[data-msg]").forEach(el => el.addEventListener("click", () => {
      const m = msgs.find(x => x.id === el.dataset.msg);
      state.highlight = { id: m.sender, peers: new Set(m.recipients || []) };
      renderNodes(); renderEdges();
      showChat(`${m.sender} → ${(m.recipients || []).join(", ")}\n[${m.type}${m.task_id ? " · " + m.task_id : ""} · ${ago(m.timestamp_ms)}]\n${m.content}`, false);
    }));
  }
  document.querySelectorAll(".filters button").forEach(b => b.addEventListener("click", () => {
    state.filter = b.dataset.filter;
    document.querySelectorAll(".filters button").forEach(x => x.classList.toggle("active", x === b));
    renderComms();
  }));
  $("commsAgent").addEventListener("change", (e) => { state.filterAgent = e.target.value; if (state.filter === "agent") renderComms(); });

  // -- chat -----------------------------------------------------------------
  function showChat(text, isError) {
    chatResponse.textContent = text;
    chatResponse.className = "mono show" + (isError ? " error" : "");
  }
  async function submitTask() {
    const text = chatInput.value.trim();
    if (!text || state.sending || state.activeJob) return;
    state.sending = true; sendBtn.disabled = true; cancelBtn.disabled = false;
    showChat("submitting to orchestrator…", false);
    try {
      const job = await post("/api/tasks", { text });
      state.activeJob = job.id;
      showChat(`job ${job.id} · ${job.status}\nwaiting for orchestrator…`, false);
      chatInput.value = "";
      await poll();
    } catch (err) { showChat(err.message, true); state.sending = false; sendBtn.disabled = false; cancelBtn.disabled = true; }
  }
  sendBtn.addEventListener("click", submitTask);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitTask(); }
  });
  cancelBtn.addEventListener("click", async () => {
    if (!state.activeJob) return;
    try { await post(`/api/jobs/${state.activeJob}/cancel`, {}); showChat("job cancelled", false); }
    catch (err) { showChat(err.message, true); }
    state.activeJob = null; state.sending = false; sendBtn.disabled = false; cancelBtn.disabled = true;
    await poll();
  });
  $("fitBtn").addEventListener("click", fit);
  $("resetBtn").addEventListener("click", () => {
    clearTimeout(state.saveTimer);
    state.positions = defaultPositions(state.agents, canvasPreset());
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.positions));
    renderNodes(); renderEdges(); fit();
  });

  // -- polling --------------------------------------------------------------
  async function poll() {
    try {
      const snap = await api("/api/snapshot");
      const first = !state.snap;
      state.snap = snap; state.agents = snap.agents || [];
      if (snap.settings) {
        const changedPoll = !state.settings || state.settings.behavior?.pollMs !== snap.settings.behavior?.pollMs;
        const editing = !$("settingsModal").hidden;
        state.settings = snap.settings;
        if (!editing) applyTheme(state.settings.colors); // don't undo unsaved edits
        if (changedPoll) schedulePoll();
      }
      connection(snap.health?.connected, snap.health?.connected ? `runtime online · ${state.agents.length} agents` : "runtime offline");
      renderNodes(); renderEdges();
      if (first) fit();
      if (state.drawers.history) renderHistory();
      if (state.drawers.comms) renderComms();
      const job = (snap.jobs || []).find(j => j.id === state.activeJob);
      if (state.activeJob && job && job.status !== "running" && job.status !== "queued") {
        state.activeJob = null; state.sending = false; sendBtn.disabled = false; cancelBtn.disabled = true;
        showChat(job.response || job.error || `job ${job.status}`, job.status === "error");
      }
    } catch (err) {
      connection(false, err.message);
    }
  }

  // -- settings & model switching -------------------------------------------
  async function ensureModels() {
    if (!state.models) state.models = await api("/api/models");
    return state.models;
  }
  function fillModelSelects(providerSel, modelSel, current) {
    const models = state.models || { options: {}, current: {} };
    const providers = Object.keys(models.options || {});
    providerSel.innerHTML = providers.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
    const drawModels = () => {
      const list = models.options[providerSel.value] || [];
      modelSel.innerHTML = list.map(m => `<option value="${esc(m)}">${esc(short(m, 60))}</option>`).join("");
      if (current && current.model && list.includes(current.model)) modelSel.value = current.model;
    };
    providerSel.onchange = drawModels;
    drawModels();
    if (current && current.provider && providers.includes(current.provider)) providerSel.value = current.provider;
    drawModels();
  }

  function openSettings() {
    $("settingsModal").hidden = false;
    const colors = state.settings?.colors || {};
    $("colorRows").innerHTML = Object.entries(colors).map(([key, value]) =>
      `<label class="color-row"><input type="color" data-color="${esc(key)}" value="${esc(value)}"> ${esc(key)}</label>`).join("");
    const c = state.settings?.canvas || {}, b = state.settings?.behavior || {};
    $("canvasPreset").value = c.preset || "algorithm";
    $("showHubEdges").checked = c.showHubEdges !== false;
    $("showCommEdges").checked = c.showCommEdges !== false;
    $("commActivity").value = Math.round((c.commActivityMs || 15000) / 1000);
    $("pollMs").value = b.pollMs || 2500;
    $("revealMs").value = b.revealDelayMs ?? 110;
    $("retractMs").value = b.retractDelayMs ?? 550;
    $("autoFit").checked = b.autoFit !== false;
    ensureModels().then(() => {
      $("settingsAgent").innerHTML = state.agents.map(a =>
        `<option value="${esc(a.id)}">${esc(a.id)} — ${esc(a.role)}</option>`).join("");
      const refreshSettingsModel = () => {
        const id = $("settingsAgent").value;
        fillModelSelects($("settingsProvider"), $("settingsModel"), state.models.current[id]);
        $("settingsModelStatus").textContent = "";
      };
      $("settingsAgent").onchange = refreshSettingsModel;
      refreshSettingsModel();
    }).catch(err => { $("settingsModelStatus").textContent = err.message; });
  }
  function closeSettings() { $("settingsModal").hidden = true; }
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsClose").addEventListener("click", closeSettings);
  $("settingsModal").addEventListener("click", (e) => { if (e.target === $("settingsModal")) closeSettings(); });
  document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab-body").forEach(s => { s.hidden = s.id !== `tab-${b.dataset.tab}`; });
  }));
  document.querySelectorAll("[data-theme]").forEach(b => b.addEventListener("click", () => {
    const themes = {
      obsidian: { bg: "#08090c", bg2: "#0c0e13", line: "#1c2029", ink: "#d9dee8", dim: "#798291", accent: "#00e5ff", teal: "#00d4aa", amber: "#e8b04b", red: "#ff5f56", violet: "#a78bfa" },
      mono: { bg: "#0a0a0a", bg2: "#101010", line: "#292929", ink: "#e6e6e6", dim: "#909090", accent: "#d7d7d7", teal: "#a6a6a6", amber: "#c8c8c8", red: "#e0e0e0", violet: "#bfbfbf" },
      amber: { bg: "#0c0a08", bg2: "#140f0b", line: "#30241a", ink: "#f0e3d2", dim: "#9a8770", accent: "#ffb454", teal: "#c8a465", amber: "#ff9d2e", red: "#ff6b4a", violet: "#d3a8ff" },
    };
    const theme = themes[b.dataset.theme];
    if (!theme) return;
    Object.entries(theme).forEach(([k, v]) => {
      const input = document.querySelector(`[data-color="${k}"]`);
      if (input) input.value = v;
    });
    applyTheme(theme);
    state.settings.colors = { ...theme };
    post("/api/settings", { colors: theme }).catch(() => {});
    toast(`theme: ${b.dataset.theme}`);
  }));
  $("settingsSave").addEventListener("click", async () => {
    const colors = {};
    document.querySelectorAll("[data-color]").forEach(i => { colors[i.dataset.color] = i.value; });
    const preset = $("canvasPreset").value;
    if (preset !== "custom") {
      // A generated layout must not be overridden by stale saved positions.
      state.positions = defaultPositions(state.agents, preset);
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.positions));
    }
    const payload = {
      colors,
      canvas: {
        preset,
        positions: preset === "custom" ? state.positions : {},
        showHubEdges: $("showHubEdges").checked,
        showCommEdges: $("showCommEdges").checked,
        commActivityMs: Math.max(1, Number($("commActivity").value) || 15) * 1000,
      },
      behavior: {
        pollMs: Number($("pollMs").value) || 2500,
        revealDelayMs: Number($("revealMs").value) || 0,
        retractDelayMs: Number($("retractMs").value) || 0,
        autoFit: $("autoFit").checked,
      },
    };
    try {
      state.settings = await post("/api/settings", payload);
      applyTheme(state.settings.colors); schedulePoll(); renderEdges();
      toast("settings saved"); closeSettings();
    } catch (err) { toast("save failed: " + err.message); }
  });
  $("settingsReset").addEventListener("click", async () => {
    try {
      state.settings = await post("/api/settings/reset", {});
      applyTheme(state.settings.colors);
      clearTimeout(state.saveTimer);
      state.positions = defaultPositions(state.agents, "algorithm");
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.positions));
      renderNodes(); renderEdges(); fit(); schedulePoll();
      closeSettings(); toast("settings reset");
    } catch (err) { toast("reset failed: " + err.message); }
  });
  $("canvasPreset").addEventListener("change", async (e) => {
    clearTimeout(state.saveTimer);
    state.positions = defaultPositions(state.agents, e.target.value);
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.positions));
    renderNodes(); renderEdges(); fit();
    try {
      state.settings = await post("/api/settings", { canvas: { positions: {}, preset: e.target.value } });
    } catch (err) { toast(err.message); }
  });
  $("savePositions").addEventListener("click", async () => {
    try {
      state.settings = await post("/api/settings", { canvas: { positions: state.positions, preset: "custom" } });
      toast("current layout saved as default");
    } catch (err) { toast(err.message); }
  });
  $("clearPositions").addEventListener("click", async () => {
    try {
      state.settings = await post("/api/settings", { canvas: { positions: {}, preset: "algorithm" } });
      clearTimeout(state.saveTimer);
      state.positions = defaultPositions(state.agents, "algorithm");
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.positions));
      renderNodes(); renderEdges(); fit();
      toast("saved positions cleared");
    } catch (err) { toast(err.message); }
  });
  async function applyModelFrom(agentId, provider, model, statusEl) {
    if (!model || !provider) return;
    statusEl.textContent = "applying…";
    try {
      const result = await post(`/api/agents/${encodeURIComponent(agentId)}/model`, { provider, model });
      statusEl.textContent = `${result.agent}: ${result.provider} / ${short(result.model, 50)}`;
      toast(`model set: ${agentId}`);
      state.models = null;
      await poll();
    } catch (err) { statusEl.textContent = err.message; toast("model change failed"); }
  }
  $("settingsApplyModel").addEventListener("click", () => {
    const custom = $("settingsCustomModel").value.trim();
    applyModelFrom($("settingsAgent").value, $("settingsProvider").value,
                   custom || $("settingsModel").value, $("settingsModelStatus"));
  });

  // -- node right-click menu -------------------------------------------------
  stage.addEventListener("contextmenu", (e) => {
    e.preventDefault();  // suppress the native menu everywhere on the canvas
    const el = e.target.closest(".node");
    if (!el) { $("contextMenu").hidden = true; return; }
    e.preventDefault();
    const id = el.dataset.id;
    state.ctxAgent = id;
    const agent = state.agents.find(a => a.id === id);
    $("ctxTitle").textContent = (id || "").toUpperCase();
    $("contextMenu").hidden = false;
    const rect = stage.getBoundingClientRect();
    $("contextMenu").style.left = Math.min(e.clientX - rect.left, rect.width - 240) + "px";
    $("contextMenu").style.top = Math.min(e.clientY - rect.top, rect.height - 250) + "px";
    ensureModels().then(() => fillModelSelects($("ctxProvider"), $("ctxModel"), state.models.current[id]))
      .catch(() => { $("ctxProvider").innerHTML = ""; $("ctxModel").innerHTML = ""; });
  });
  document.addEventListener("pointerdown", (e) => {
    if (!e.target.closest("#contextMenu")) $("contextMenu").hidden = true;
  });
  $("ctxInspect").addEventListener("click", () => { inspect(state.ctxAgent); $("contextMenu").hidden = true; });
  $("ctxFocus").addEventListener("click", () => {
    const p = state.positions[state.ctxAgent];
    if (p) {
      state.transform.x = stage.clientWidth / 2 - p.x * state.transform.k;
      state.transform.y = stage.clientHeight / 2 - p.y * state.transform.k;
      applyTransform();
    }
    $("contextMenu").hidden = true;
  });
  $("ctxApplyModel").addEventListener("click", () => {
    applyModelFrom(state.ctxAgent, $("ctxProvider").value, $("ctxModel").value, $("toast"));
    $("contextMenu").hidden = true;
  });
  $("ctxResetPos").addEventListener("click", () => {
    delete state.positions[state.ctxAgent];
    state.positions[state.ctxAgent] = defaultPositions(state.agents, canvasPreset())[state.ctxAgent] || { x: 0, y: 120 };
    saveLayout(); renderNodes(); renderEdges(); fit();
    $("contextMenu").hidden = true;
  });

  window.addEventListener("keydown", (e) => {
    if (e.altKey && e.key.toLowerCase() === "s") { e.preventDefault(); openSettings(); }
  });

  function schedulePoll() {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(poll, Math.max(500, state.settings?.behavior?.pollMs || 2500));
  }

  applyTransform();
  poll();
  schedulePoll();
  window.addEventListener("resize", () => { drawAscii(); });
})();
