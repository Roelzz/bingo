/* Speerpunt Bingo frontend. Plain JS, no build step. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ME = window.ME;
  const view = $("#view");
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
  };

  let S = null;            // server state
  let tab = LS.get("tab", "card");
  let adminData = null;
  let albumData = null;
  let adminOpen = new Set(LS.get("adminOpen", ["invites"]));
  let loadTimer = null;
  let sheetHandler = null;

  /* ------------------------------------------------------------ API + offline queue */

  async function api(method, url, body, { queue = false } = {}) {
    const opts = { method, credentials: "same-origin", headers: { "X-Requested-With": "tombala" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    let res;
    try {
      res = await fetch(url, opts);
    } catch {
      if (queue) {
        const q = LS.get("queue", []);
        q.push({ method, url, body, at: Date.now() });
        if (!LS.set("queue", q)) { toast("Geen bereik en te weinig opslag. Probeer het straks opnieuw.", "err"); return { queued: false }; }
        toast("Geen bereik. Opgeslagen, wordt verstuurd zodra je online bent.");
        setOffline(true);
        return { queued: true };
      }
      setOffline(true);
      throw new Error("Geen verbinding");
    }
    if (res.status === 401) { location.href = "/login"; throw new Error("Niet ingelogd"); }
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) throw new Error((data && data.detail) || `Fout ${res.status}`);
    setOffline(false);
    return data;
  }

  let flushing = false;
  async function flushQueue() {
    if (flushing) return;
    const q = LS.get("queue", []);
    if (!q.length) return;
    flushing = true;
    try {
      while (q.length) {
        const job = q[0];
        try {
          await api(job.method, job.url, job.body);
          toast("Offline actie verstuurd ✓", "ok");
        } catch (e) {
          if (e.message === "Geen verbinding") break;
          toast(`Offline actie mislukt: ${e.message}`, "err");
        }
        q.shift();
        LS.set("queue", q);
      }
    } finally {
      flushing = false;
    }
    scheduleLoad(0);
  }

  function setOffline(off) {
    $("#offlineBar").hidden = !off;
  }

  async function load() {
    try {
      S = await api("GET", "/api/state");
      applyHeader();
      checkMyBingos();
      render();
    } catch (e) {
      if (!S) view.innerHTML = `<div class="empty-state"><div class="big">📡</div><p>${esc(e.message)}. Even geduld…</p></div>`;
    }
  }

  function scheduleLoad(ms = 250) {
    clearTimeout(loadTimer);
    loadTimer = setTimeout(async () => {
      await load();
      if (tab === "album") loadAlbum();
    }, ms);
  }

  /* ------------------------------------------------------------ realtime */

  let es = null;
  function connectStream() {
    if (es) es.close();
    es = new EventSource("/api/stream");
    es.onopen = () => { setNet("on"); scheduleLoad(0); flushQueue(); };
    es.onerror = () => setNet("off");
    es.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      switch (m.type) {
        case "refresh":
          scheduleLoad();
          break;
        case "witness_request":
          buzz([60, 40, 60]);
          toast(`🧐 ${m.nickname} heeft je nodig als getuige: “${m.text}”`);
          scheduleLoad(0);
          break;
        case "confirmed":
          buzz([40]);
          toast(`✅ ${m.witness} bevestigt: “${m.text}”`, "ok");
          break;
        case "rejected":
          buzz([200, 80, 200]);
          toast(`❌ ${m.witness} wijst af. Strafslok! 🍺`, "err");
          break;
        case "announce":
          buzz([50, 50, 50]);
          toast(`📣 ${m.nickname}: ${m.text}`);
          break;
        case "bingo":
          if (m.nickname !== ME.nickname) {
            confetti(m.full ? 260 : 90);
            fanfare(m.full);
            toast(m.full ? `🏆 VOLLE KAART voor ${m.nickname}! ${m.prize || ""}` : `🎉 BINGO voor ${m.nickname}: ${m.line}`);
            if (m.full) showBingo({ nickname: m.nickname, line: "Volle kaart", prize: m.prize, full: true });
          }
          scheduleLoad(0);
          break;
      }
    };
  }

  function setNet(s) {
    const dot = $("#netDot");
    dot.classList.toggle("on", s === "on");
    dot.classList.toggle("off", s === "off");
  }

  /* ------------------------------------------------------------ helpers */

  function initials(name) {
    const parts = String(name).trim().split(/\s+/);
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : String(name).slice(0, 2)).toUpperCase();
  }
  function avatar(name, color, cls = "") {
    return `<span class="avatar ${cls}" style="background:${esc(color || "#8e1f1f")}">${esc(initials(name))}</span>`;
  }
  function ago(iso) {
    const d = (Date.now() - Date.parse(iso)) / 1000;
    if (d < 45) return "zojuist";
    if (d < 3600) return `${Math.round(d / 60)} min geleden`;
    if (d < 5 * 3600) return `${Math.round(d / 3600)} uur geleden`;
    return new Date(iso).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  }
  function pad(n) { return String(n).padStart(2, "0"); }
  function buzz(p) { try { navigator.vibrate && navigator.vibrate(p); } catch { /* not supported */ } }

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.transition = "opacity .3s"; el.style.opacity = "0"; }, 3800);
    setTimeout(() => el.remove(), 4200);
  }

  // Small spear tip in the corner of each cell.
  const ORN = `<svg class="orn" viewBox="0 0 10 20" aria-hidden="true"><path d="M5 0 L8.6 7.2 L5 13 L1.4 7.2 Z" fill="#8e1f1f"/><rect x="4.3" y="12.5" width="1.4" height="7.5" fill="#8e1f1f"/></svg>`;

  // Wax seal with a spear tip, used as the "done" mark.
  function seal(fill, ring, line) {
    const pts = [];
    for (let i = 0; i < 32; i++) {
      const a = (i / 32) * Math.PI * 2;
      const r = i % 2 ? 17.4 : 19.2 - (i % 4 === 0 ? 0.5 : 0);
      pts.push(`${(20 + r * Math.cos(a)).toFixed(2)},${(20 + r * Math.sin(a)).toFixed(2)}`);
    }
    // One upright spear tip: the jaarclub's mark, and it can't be mistaken for a cross.
    const tip = `<path d="M20 7.2 L24.6 17.2 L20 25.2 L15.4 17.2 Z" fill="${line}"/>` +
      `<path d="M20 10.6 L20 22" stroke="${fill}" stroke-width="1.1" opacity=".55"/>` +
      `<rect x="19" y="24.4" width="2" height="8.2" rx="1" fill="${line}"/>` +
      `<rect x="16.6" y="25.4" width="6.8" height="1.6" rx=".8" fill="${line}"/>`;
    return `<svg viewBox="0 0 40 40" aria-hidden="true"><polygon points="${pts.join(" ")}" fill="${fill}"/>` +
      `<circle cx="20" cy="20" r="14" fill="none" stroke="${ring}" stroke-width="1.1"/>` + tip + `</svg>`;
  }
  const SEAL = seal("#8e1f1f", "#5e1010", "#f3dcc9");
  const SEAL_JOKER = seal("#b8913f", "#8a6a2a", "#1b1916");

  function applyHeader() {
    const st = S.settings;
    $("#kicker").textContent = st.kicker;
    $("#title").textContent = st.title;
    $("#subtitle").textContent = st.subtitle;
    document.title = st.title;
    const a = $("#meAvatar");
    a.textContent = initials(S.me.nickname);
    a.style.background = S.me.color;
  }

  function updateBadges() {
    const n = S.witness_requests.length;
    const b = $("#badgeCard");
    b.hidden = n === 0;
    b.textContent = n;
    const seen = LS.get(`feedSeen:${ME.id}`, 0);
    const newest = S.feed.length ? S.feed[0].id : 0;
    $("#badgeFeed").hidden = tab === "feed" || newest <= seen;
    if (tab === "feed") LS.set(`feedSeen:${ME.id}`, newest);
    if ("setAppBadge" in navigator) { (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {}); }
  }

  /* ------------------------------------------------------------ bingo celebrations */

  function checkMyBingos() {
    const key = `bingosSeen:${ME.id}`;
    const won = S.lines.filter((l) => l.won).map((l) => l.key);
    if (S.full_card_won) won.push("full");
    const seen = LS.get(key, null);
    if (seen === null) { LS.set(key, won); return; }  // first visit: no fireworks for old news
    const fresh = won.filter((k) => !seen.includes(k));
    LS.set(key, won);
    if (!fresh.length) return;
    const full = fresh.includes("full");
    const line = full ? null : S.lines.find((l) => l.key === fresh[0]);
    showBingo({
      nickname: S.me.nickname,
      line: full ? "Volle kaart" : (fresh.length > 1 ? `${line.label} + ${fresh.length - 1} meer` : line.label),
      prize: full ? S.settings.full_card_prize : line.prize,
      full, mine: true,
    });
  }

  function showBingo({ nickname, line, prize, full, mine }) {
    const o = $("#bingoOverlay");
    o.innerHTML = `
      <div>
        <img class="crest" src="/static/img/crest.jpg" alt="">
        <div class="word ${full ? "long" : ""}">${full ? "SPEERPUNT!" : "BINGO!"}</div>
        <div class="who">${mine ? "Jij" : esc(nickname)} · ${esc(line)}</div>
        <div class="line">${full ? "De hele kaart vol. Legendarisch." : "Volle rij, hoppa."}</div>
        ${prize ? `<div class="ribbon">🏆 ${esc(prize)}</div>` : ""}
        <div class="tap">tik om verder te gaan</div>
      </div>`;
    o.hidden = false;
    o.onclick = () => { o.hidden = true; };
    confetti(full ? 320 : 180);
    fanfare(full);
    buzz([100, 50, 100, 50, 300]);
  }

  // Confetti, hand-rolled so we don't download anything extra on roaming data.
  function confetti(count = 160) {
    const c = $("#confetti");
    const ctx = c.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    c.width = innerWidth * dpr; c.height = innerHeight * dpr;
    ctx.scale(dpr, dpr);
    const colors = ["#d8b465", "#b8913f", "#8e1f1f", "#f0e7d2", "#ffffff", "#5a2d2d"];
    const parts = Array.from({ length: count }, () => ({
      x: innerWidth / 2 + (Math.random() - 0.5) * 80, y: innerHeight * 0.35,
      vx: (Math.random() - 0.5) * 14, vy: -Math.random() * 14 - 4,
      r: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.4,
      w: 6 + Math.random() * 6, h: 8 + Math.random() * 10, c: colors[(Math.random() * colors.length) | 0],
    }));
    const start = performance.now();
    function frame(t) {
      const el = t - start;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      for (const p of parts) {
        p.vy += 0.32; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r);
        ctx.globalAlpha = Math.max(0, 1 - el / 3500);
        ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.r * 2)));
        ctx.restore();
      }
      if (el < 3500) requestAnimationFrame(frame); else ctx.clearRect(0, 0, innerWidth, innerHeight);
    }
    requestAnimationFrame(frame);
  }

  let audio = null;
  document.addEventListener("pointerdown", () => {
    if (!audio && (window.AudioContext || window.webkitAudioContext)) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio && audio.state === "suspended") audio.resume();
  }, { passive: true });

  function fanfare(big) {
    if (!audio || !LS.get("sound", true)) return;
    const t0 = audio.currentTime + 0.02;
    const notes = big ? [392, 523.25, 659.25, 783.99, 1046.5] : [523.25, 659.25, 783.99];
    notes.forEach((f, i) => {
      const o = audio.createOscillator(), g = audio.createGain();
      o.type = "triangle"; o.frequency.value = f;
      const t = t0 + i * 0.11;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.22, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + (i === notes.length - 1 ? 1.4 : 0.35));
      o.connect(g).connect(audio.destination); o.start(t); o.stop(t + 1.5);
    });
    // a low gong underneath
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = "sine"; o.frequency.value = 98;
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.3, t0 + 0.03); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 2.4);
    o.connect(g).connect(audio.destination); o.start(t0); o.stop(t0 + 2.5);
  }

  /* ------------------------------------------------------------ rendering */

  function render() {
    if (!S) return;
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    if (tab === "card") renderCard();
    else if (tab === "feed") renderFeed();
    else if (tab === "board") renderBoard();
    else if (tab === "album") renderAlbum();
    else if (tab === "admin") renderAdmin();
    updateBadges();
  }

  function wonPositions() {
    const set = new Set();
    for (const l of S.lines) if (l.won) l.cells.forEach((p) => set.add(p));
    if (S.full_card_won) S.card.forEach((c) => set.add(c.pos));
    return set;
  }

  function renderCard() {
    const st = S.settings;
    const won = wonPositions();
    const done = S.card.filter((c) => c.status === "confirmed").length;
    const bingos = S.lines.filter((l) => l.won).length;

    const requests = S.witness_requests.map((r) => `
      <div class="request">
        <div class="who">${avatar(r.claimant, colorOf(r.claimant_id), "sm")} <span>${esc(r.claimant)} vraagt jou als getuige · ${esc(ago(r.created_at))}</span></div>
        <div class="what">${esc(r.text)}</div>
        ${r.note ? `<p class="note">“${esc(r.note)}”</p>` : ""}
        ${r.photo ? `<img class="thumb" src="${esc(r.photo.thumb)}" data-act="lightbox" data-src="${esc(r.photo.full)}" alt="">` : ""}
        <div class="actions">
          <button class="btn red" data-act="reject" data-id="${r.claim_id}">Gelul, afwijzen</button>
          <button class="btn teal" data-act="confirm" data-id="${r.claim_id}">Klopt, bevestig</button>
        </div>
      </div>`).join("");

    const cells = S.card.map((c) => {
      const cls = ["cell", c.status, c.item_id ? "" : "empty", won.has(c.pos) ? "won" : "", c.claim && c.claim.joker ? "joker" : ""].join(" ");
      const mark = c.status === "confirmed" ? (c.claim.joker ? SEAL_JOKER : SEAL) : c.status === "pending" ? "⏳" : "";
      return `
        <div class="${cls}" data-act="cell" data-pos="${c.pos}" role="button" tabindex="0">
          <span class="top"><span class="num">${pad(c.pos + 1)}</span>${ORN}</span>
          <span class="txt">${c.item_id ? esc(c.text) : "Leeg vakje"}</span>
          <span class="mark">${mark}</span>
        </div>`;
    }).join("");

    const mini = (cells) => {
      const on = new Set(cells);
      return `<div class="mini-grid" style="grid-template-columns:repeat(${st.cols},1fr)">${
        Array.from({ length: st.cols * st.rows }, (_, i) => `<i class="${on.has(i) ? "on" : ""}"></i>`).join("")}</div>`;
    };
    // Group lines that share a prize, so eight identical rows become one.
    const groups = [];
    for (const l of S.lines) {
      const prize = l.prize || "Eeuwige roem";
      let g = groups.find((x) => x.prize === prize);
      if (!g) { g = { prize, lines: [] }; groups.push(g); }
      g.lines.push(l);
    }
    const prizes = groups.map((g) => {
      const won = g.lines.filter((l) => l.won).length;
      const labels = g.lines.length === S.lines.length && g.lines.length > 2 ? "Elke volle lijn" : g.lines.map((l) => l.label).join(", ");
      return `
      <div class="prize ${won ? "won" : ""}">
        ${mini(g.lines.flatMap((l) => l.cells))}
        <div class="mid"><span class="lbl">${esc(labels)}</span><span class="txt">${esc(g.prize)}</span></div>
        <span class="ok">${won ? `✅${won > 1 ? `×${won}` : ""}` : ""}</span>
      </div>`;
    }).join("");

    view.innerHTML = `
      ${st.frozen ? `<div class="frozen-banner">❄️ Het spel is bevroren. Geen nieuwe claims meer.</div>` : ""}
      ${requests ? `<div class="requests">${requests}</div>` : ""}
      <div class="stats">
        <div class="stat"><b>${done}/${S.card.length}</b><span>vakjes</span></div>
        <div class="stat ${bingos ? "gold" : ""}"><b>${bingos}${S.full_card_won ? " 🏆" : ""}</b><span>bingo's</span></div>
        <div class="stat"><b>${S.me.jokers_left ? "🃏".repeat(Math.min(3, S.me.jokers_left)) : "—"}</b><span>joker</span></div>
      </div>
      <div class="grid ${st.cols >= 4 ? "dense" : ""}" style="grid-template-columns:repeat(${st.cols},1fr)">${cells}</div>
      <div class="section-title"><h2>Prijzen</h2><span class="aside">iedereen die hem haalt</span></div>
      <div class="prizes">
        ${prizes}
        <div class="prize super ${S.full_card_won ? "won" : ""}"><div class="mid"><span class="lbl">🏆 Volle kaart</span><span class="txt">${esc(st.full_card_prize || "Superprijs")}</span></div><span class="ok">${S.full_card_won ? "✅" : ""}</span></div>
      </div>
      <p class="foot center">Şerefe!</p>
      <div class="footer-line"><span>${esc(st.footer)} · ${S.players.length} man</span><span>${esc(S.me.nickname)}</span></div>`;
  }

  function colorOf(userId) {
    const p = S.players.find((x) => x.id === userId);
    return p ? p.color : "#8e1f1f";
  }

  function feedLine(e) {
    const who = `<b>${esc(e.user || "Iemand")}</b>`;
    const item = e.item ? `<span class="item">“${esc(e.item)}”</span>` : "";
    switch (e.kind) {
      case "joined": return ["👋", `${who} doet mee. Welkom!`];
      case "claim_pending": return ["⏳", `${who} claimt ${item} · ${esc(e.witness)} moet bevestigen`];
      case "claim_confirmed": return ["✅", `${who}: ${item} · bevestigd door ${esc(e.witness)}`];
      case "claim_joker": return ["🃏", `${who} zet de joker in op ${item}`];
      case "claim_rejected": return ["🍺", `${who} claimde ${item}, maar ${esc(e.witness)} trapt er niet in. Strafslok!`];
      case "claim_vetoed": return ["🚩", `Weggestemd: ${who} met ${item}. Strafslok!`];
      case "claim_revoked": return ["↩️", `De admin draaide ${item} van ${who} terug`];
      case "bingo": return ["🎉", `<b>BINGO!</b> ${who} · ${esc(e.line)}${e.prize ? ` · 🏆 ${esc(e.prize)}` : ""}`];
      case "full_card": return ["🏆", `<b>VOLLE KAART!</b> ${who}${e.prize ? ` · ${esc(e.prize)}` : ""}`];
      case "announce": return ["📣", `${who}: ${esc(e.text)}`];
      default: return ["•", who];
    }
  }

  function renderFeed() {
    const thr = S.settings.veto_threshold;
    const items = S.feed
      .filter((e) => !(e.kind === "claim_pending" && e.status && e.status !== "pending"))
      .map((e) => {
        const [icon, html] = feedLine(e);
        const cls = e.kind === "claim_rejected" ? "rejected" : e.kind;
        let extra = "";
        if (e.status === "vetoed" && e.kind !== "claim_vetoed") extra += `<span class="pill bad">weggestemd</span>`;
        if (e.status === "revoked" && e.kind !== "claim_revoked") extra += `<span class="pill bad">teruggedraaid</span>`;
        if (thr > 0 && e.can_veto) {
          extra += `<button class="veto ${e.i_vetoed ? "on" : ""}" data-act="veto" data-id="${e.claim_id}" ${e.i_vetoed ? "disabled" : ""}>🚩 ${e.i_vetoed ? "Veto gegeven" : "Veto"} ${e.vetoes}/${thr}</button>`;
        } else if (thr > 0 && e.status === "confirmed" && e.vetoes) {
          extra += `<span class="pill bad">🚩 ${e.vetoes}/${thr}</span>`;
        }
        return `
          <div class="ev ${cls}">
            <div class="icon">${icon}</div>
            <div class="body">
              <div class="line1">${html}</div>
              <div class="meta"><span>${esc(ago(e.at))}</span>${extra}</div>
            </div>
            ${e.photo ? `<img class="pic" loading="lazy" src="${esc(e.photo.thumb)}" data-act="lightbox" data-src="${esc(e.photo.full)}" alt="">` : ""}
          </div>`;
      }).join("");
    view.innerHTML = `
      <div class="section-title"><h2>Live feed</h2><span class="aside">${thr > 0 ? `${thr} veto's = weggestemd` : ""}</span></div>
      <div class="feed">${items || `<div class="empty-state"><div class="big">🦗</div><p>Nog stil. Iemand moet beginnen.</p></div>`}</div>`;
  }

  function renderBoard() {
    const medals = ["🥇", "🥈", "🥉"];
    const rows = S.leaderboard.map((p, i) => {
      const active = p.cells > 0 || p.bingos > 0;
      const first = p.first_bingo_at && p.first_bingo_at === S.first_bingo_at;
      return `
        <div class="rank ${p.id === ME.id ? "me" : ""}">
          <div class="pos">${active && i < 3 ? medals[i] : i + 1}</div>
          ${avatar(p.nickname, p.color, "lg")}
          <div>
            <div class="name">${esc(p.nickname)} ${p.full ? "👑" : ""} ${first ? `<span class="badge-first">EERSTE BINGO</span>` : ""}</div>
            <div class="nums"><span>🎯 ${p.bingos} bingo</span><span>✅ ${p.cells}</span><span>🍺 ${p.penalties}</span><span>👀 ${p.witnessed}</span></div>
          </div>
          ${p.prizes.length ? `<div class="won-list">${p.prizes.map((z) => `<div>🏆 <b>${esc(z.label)}</b> · ${esc(z.prize || "eeuwige roem")}</div>`).join("")}</div>` : ""}
        </div>`;
    }).join("");
    view.innerHTML = `
      <div class="section-title"><h2>Stand</h2><span class="aside">🎯 bingo · ✅ vakjes · 🍺 strafslok · 👀 getuige</span></div>
      <div class="board">${rows}</div>`;
  }

  async function loadAlbum() {
    try { albumData = await api("GET", "/api/album"); if (tab === "album") renderAlbum(); } catch (e) { toast(e.message, "err"); }
  }

  function renderAlbum() {
    if (!albumData) { view.innerHTML = `<div class="loading">Foto's ophalen…</div>`; return; }
    view.innerHTML = `
      <div class="section-title"><h2>Album</h2><span class="aside">${albumData.length} foto's</span></div>
      ${albumData.length ? `<div class="album">${albumData.map((p) => `
        <figure data-act="lightbox" data-src="${esc(p.full)}">
          <img loading="lazy" src="${esc(p.thumb)}" alt="">
          <figcaption>${esc(p.nickname)} · ${esc(p.text)}</figcaption>
        </figure>`).join("")}</div>`
        : `<div class="empty-state"><div class="big">📸</div><p>Nog geen bewijsmateriaal. Voeg een foto toe bij je claim.</p></div>`}`;
  }

  /* ------------------------------------------------------------ admin */

  async function loadAdmin() {
    try { adminData = await api("GET", "/api/admin"); if (tab === "admin") renderAdmin(); } catch (e) { toast(e.message, "err"); }
  }

  function adm(id, title, inner) {
    return `<details class="adm" data-sec="${id}" ${adminOpen.has(id) ? "open" : ""}><summary>${title}</summary><div class="inner">${inner}</div></details>`;
  }

  function renderAdmin() {
    if (!ME.is_admin) { view.innerHTML = ""; return; }
    if (!adminData) { view.innerHTML = `<div class="loading">Admin laden…</div>`; return; }
    const A = adminData, s = A.settings;
    const origin = location.origin;
    const tooFew = A.items.length < A.cells;
    const G = A.grid || {};
    const gridHint = !G.suggestion ? ""
      : G.optimal
        ? `Optimaal voor ${G.items} vakjes: ${G.cols}×${G.rows}. ${G.spare_items > 0 ? `${G.spare_items} vakje(s) extra: iedereen krijgt een willekeurige selectie.` : ""}`
        : `Voor ${G.items} vakjes speelt ${G.suggestion.cols}×${G.suggestion.rows} beter dan ${G.cols}×${G.rows}.`;
    const gridRisk = (G.would_lose || []).length
      ? `<p class="warn">⚠️ Optimaliseren zou ${G.would_lose.length} bestaande bingo(s) kosten: ${G.would_lose.map((w) => `${esc(w.player)} — ${esc(w.line)}`).join(", ")}. De autoset slaat over; alleen handmatig forceren doet het toch.</p>`
      : "";

    const invites = A.invites.map((i) => `
      <div class="li">
        <div class="grow"><div class="code">${esc(i.code)}</div><div class="small">${i.uses ? `${i.uses} account${i.uses === 1 ? "" : "s"} aangemaakt` : "nog niet gebruikt"}</div></div>
        <button class="btn small teal" data-act="share-invite" data-code="${esc(i.code)}">Delen</button>
        <button class="btn small ghost" data-act="del-invite" data-code="${esc(i.code)}">✕</button>
      </div>`).join("");

    const items = A.items.map((it) => `
      <div class="li">
        <div class="grow"><input data-item="${it.id}" value="${esc(it.text)}"><div class="small">op ${it.on_cards} kaarten · ${it.confirmed}× gedaan</div></div>
        <button class="btn small" data-act="save-item" data-id="${it.id}">💾</button>
        <button class="btn small ghost" data-act="del-item" data-id="${it.id}" data-confirmed="${it.confirmed}">🗑</button>
      </div>`).join("");

    const num = (key, label) => `<label>${label}<input type="number" inputmode="numeric" data-set="${key}" value="${esc(s[key])}"></label>`;
    const txt = (key, label) => `<label>${label}<input data-set="${key}" value="${esc(s[key])}"></label>`;
    const chk = (key, label) => `<label class="toggle">${label}<input type="checkbox" data-set="${key}" ${s[key] === "1" ? "checked" : ""}></label>`;

    const lines = A.lines.map((l) => `<label>${esc(l.label)}<input data-prize="${l.key}" value="${esc(l.prize)}" placeholder="${esc(s.default_line_prize)}"></label>`).join("");

    const players = A.players.map((p) => `
      <div class="li">
        ${avatar(p.nickname, p.color, "sm")}
        <div class="grow"><b>${esc(p.nickname)}</b> ${p.is_admin ? `<span class="pill ok">admin</span>` : ""}<div class="small">${esc(p.email)}</div></div>
        <button class="btn small ghost" data-act="pw" data-id="${p.id}" data-name="${esc(p.nickname)}">🔑</button>
        <button class="btn small ghost" data-act="reset-player" data-id="${p.id}" data-name="${esc(p.nickname)}">↺</button>
        ${p.id === ME.id ? "" : `<button class="btn small ghost" data-act="del-player" data-id="${p.id}" data-name="${esc(p.nickname)}">🗑</button>`}
      </div>`).join("");

    const claims = A.claims.map((c) => `
      <div class="li">
        <div class="grow"><b>${esc(c.user)}</b> · ${esc(c.item)}<div class="small">${c.joker ? "joker" : `getuige ${esc(c.witness)}`} · ${esc(ago(c.at))}</div></div>
        <span class="pill ${c.status === "confirmed" ? "ok" : "wait"}">${c.status === "confirmed" ? "ok" : "wacht"}</span>
        <button class="btn small ghost" data-act="revoke" data-id="${c.id}">↩</button>
      </div>`).join("");

    view.innerHTML = `
      <div class="section-title"><h2>Admin</h2><span class="aside">jij bent de baas</span></div>
      ${adm("announce", "📣 Omroep", `
        <textarea id="announceText" placeholder="Iedereen over 10 minuten bij de Galatabrug!"></textarea>
        <button class="btn navy big" data-act="announce">Versturen naar iedereen</button>`)}
      ${adm("invites", `🎟️ Codes <span class="muted">één code per evenement</span>`, `
        <p class="small muted" style="margin-bottom:8px">Eén code is genoeg. Iedereen die hem heeft maakt een eigen account met nickname, e-mail en wachtwoord.</p>
        <div class="btn-row" style="margin-bottom:8px">
          <input id="customCode" placeholder="eigen code, bijv. ISTANBUL2026" autocapitalize="characters" spellcheck="false" style="flex:1;min-width:140px;margin-top:0">
          <button class="btn gold" data-act="new-custom-code">Maak code</button>
        </div>
        <div class="btn-row" style="margin-bottom:8px">
          <button class="btn" data-act="new-invites" data-n="1">+ 1 losse code</button>
          <button class="btn" data-act="new-invites" data-n="5">+ 5 codes</button>
        </div>
        <p class="small muted">Delen stuurt een link als <span class="code">${esc(origin)}/register?code=…</span></p>
        <div class="list">${invites || `<p class="muted">Nog geen codes.</p>`}</div>`)}
      ${adm("items", `🔲 Vakjes <span class="muted">${A.items.length} voor ${A.cells} cellen</span>`, `
        ${tooFew ? `<p class="warn">Je hebt minder vakjes (${A.items.length}) dan cellen (${A.cells}). Sommige kaarten krijgen lege plekken.</p>` : ""}
        <p class="small muted">Meer vakjes dan cellen? Dan krijgt iedereen een willekeurige selectie, net als echte bingo.</p>
        <label>Nieuwe vakjes (één per regel)<textarea id="newItems" placeholder="Een selfie met een kat in Cihangir"></textarea></label>
        <button class="btn teal big" data-act="add-items">Toevoegen</button>
        <div class="list" style="margin-top:12px">${items}</div>`)}
      ${adm("rules", "📐 Raster & regels", `
        <div class="row2">${num("grid_cols", "Kolommen")}${num("grid_rows", "Rijen")}</div>
        ${chk("grid_auto", "Rooster automatisch optimaal houden")}
        ${gridHint ? `<p class="small muted">${gridHint}</p>` : ""}
        ${gridRisk}
        <div class="btn-row" style="margin-bottom:8px"><button class="btn small teal" data-act="auto-grid">✨ Nu optimaal zetten</button></div>
        ${chk("line_rows", "Horizontale rijen tellen")}
        ${chk("line_diagonals", "Diagonalen tellen")}
        ${chk("line_cols", "Verticale kolommen tellen")}
        <div class="row2" style="margin-top:12px">${num("jokers_per_player", "Jokers per speler")}${num("veto_threshold", "Veto's nodig (0 = uit)")}</div>
        ${num("witness_limit", "Max getuigenissen door dezelfde vriend (0 = onbeperkt)")}
        ${txt("kicker", "Bovenregel")}${txt("title", "Titel")}${txt("subtitle", "Ondertitel")}${txt("footer", "Voetregel")}
        <p class="small muted">Raster wijzigen of optimaliseren? Geclaimde vakjes blijven staan, de rest wordt bijgevuld. Kaarten worden nooit opnieuw geschud.</p>
        <button class="btn teal big" data-act="save-settings">Opslaan</button>`)}
      ${adm("prizes", "🏆 Prijzen", `
        ${txt("default_line_prize", "Standaardprijs per rij")}
        ${txt("full_card_prize", "Superprijs volle kaart")}
        <div class="k">Per lijn (leeg = standaardprijs)</div>
        ${lines}
        <button class="btn gold big" data-act="save-prizes">Prijzen opslaan</button>`)}
      ${adm("players", `👥 Spelers <span class="muted">${A.players.length}</span>`, `<div class="list">${players}</div>
        <p class="small muted">🔑 nieuw wachtwoord · ↺ kaart en claims resetten · 🗑 verwijderen</p>`)}
      ${adm("claims", `🧾 Claims <span class="muted">${A.claims.length}</span>`, `<div class="list">${claims || `<p class="muted">Nog niks.</p>`}</div>`)}
      ${adm("danger", "🧨 Gevaarlijke knoppen", `
        <div class="list">
          <button class="btn big" data-act="reshuffle">🔀 Alle kaarten opnieuw schudden</button>
          <button class="btn navy big" data-act="freeze">${s.frozen === "1" ? "☀️ Spel weer openen" : "❄️ Spel bevriezen (einde)"}</button>
          <button class="btn red big" data-act="new-game">💥 Nieuw spel (alles wissen)</button>
        </div>`)}`;
  }

  function collectSettings(keys) {
    const out = {};
    view.querySelectorAll("[data-set]").forEach((el) => {
      if (keys && !keys.includes(el.dataset.set)) return;
      out[el.dataset.set] = el.type === "checkbox" ? el.checked : el.value;
    });
    return out;
  }

  async function adminAction(fn, okMsg) {
    try {
      await fn();
      if (okMsg) toast(okMsg, "ok");
      await loadAdmin();
      scheduleLoad(0);
    } catch (e) { toast(e.message, "err"); }
  }

  /* ------------------------------------------------------------ sheets */

  function openSheet(html, handler) {
    $("#sheet").innerHTML = html;
    sheetHandler = handler || null;
    $("#sheetWrap").hidden = false;
  }
  function closeSheet() {
    $("#sheetWrap").hidden = true;
    $("#sheet").innerHTML = "";
    sheetHandler = null;
  }
  $("#sheetWrap").addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) { closeSheet(); return; }
    if (sheetHandler) sheetHandler(e);
  });
  $("#sheetWrap").addEventListener("change", (e) => { if (sheetHandler) sheetHandler(e); });

  function openClaim(cell) {
    if (S.settings.frozen) { toast("Het spel is bevroren.", "err"); return; }
    const st = { witness: null, photo: null, joker: false };
    const limit = S.settings.witness_limit;
    const others = S.players.filter((p) => p.id !== ME.id);

    const draw = () => {
      const chips = others.map((p) => {
        const used = S.witness_load[p.id] || 0;
        const full = limit > 0 && used >= limit;
        return `<button class="chip ${st.witness === p.id ? "sel" : ""}" data-w="${p.id}" ${full || st.joker ? "disabled" : ""}>
          ${avatar(p.nickname, p.color)}<span>${esc(p.nickname)}</span>${limit > 0 ? `<small>${used}/${limit}</small>` : ""}</button>`;
      }).join("");
      const can = st.joker || st.witness;
      openSheet(`
        <h3>${esc(cell.text)}</h3>
        <p class="lead">Gelukt? Wijs een getuige aan die het zag. Die moet het bevestigen.</p>
        <div class="k">Getuige</div>
        <div class="chips">${chips || `<p class="muted">Nog geen andere spelers.</p>`}</div>
        <div class="k">Bewijs (optioneel)</div>
        <div class="btn-row">
          <label class="btn file-btn" style="margin:0">📸 ${st.photo ? "Andere foto" : "Foto toevoegen"}<input type="file" accept="image/*" id="photoInput"></label>
          ${st.photo ? `<button class="btn ghost" data-act="rm-photo">Weg ermee</button>` : ""}
        </div>
        ${st.photo ? `<img class="photo-prev" src="${st.photo}" alt="">` : ""}
        <label style="margin-top:12px">Toelichting<input id="noteInput" maxlength="200" placeholder="Hoe ging het?" value="${esc(st.note || "")}"></label>
        ${S.me.jokers_left > 0 ? `<label class="toggle">🃏 Joker inzetten (geen getuige nodig, nog ${S.me.jokers_left})<input type="checkbox" id="jokerInput" ${st.joker ? "checked" : ""}></label>` : ""}
        <div style="margin-top:14px"><button class="btn gold big" data-act="submit" ${can ? "" : "disabled"}>${st.joker ? "Joker inzetten" : "Claimen"}</button></div>`,
        async (e) => {
          const note = $("#noteInput"); if (note) st.note = note.value;
          if (e.type === "change" && e.target.id === "photoInput" && e.target.files[0]) {
            try { st.photo = await compressImage(e.target.files[0]); } catch { toast("Foto kon niet gelezen worden", "err"); }
            draw(); return;
          }
          if (e.type === "change" && e.target.id === "jokerInput") { st.joker = e.target.checked; if (st.joker) st.witness = null; draw(); return; }
          if (e.type !== "click") return;
          const chip = e.target.closest("[data-w]");
          if (chip && !chip.disabled) { st.witness = Number(chip.dataset.w); draw(); return; }
          const act = e.target.closest("[data-act]");
          if (!act) return;
          if (act.dataset.act === "rm-photo") { st.photo = null; draw(); }
          if (act.dataset.act === "submit") {
            act.disabled = true;
            try {
              const r = await api("POST", "/api/claims", { item_id: cell.item_id, witness_id: st.witness, joker: st.joker, note: st.note || "", photo: st.photo }, { queue: true });
              closeSheet();
              if (!r.queued) {
                const w = others.find((p) => p.id === st.witness);
                toast(st.joker ? "🃏 Joker ingezet!" : `Verzonden naar ${w ? w.nickname : "je getuige"} ⏳`, "ok");
              }
              scheduleLoad(0);
            } catch (err) { toast(err.message, "err"); act.disabled = false; }
          }
        });
    };
    draw();
  }

  function openCellDetail(cell) {
    const c = cell.claim;
    const thr = S.settings.veto_threshold;
    openSheet(`
      <h3>${esc(cell.text)}</h3>
      <p class="lead">${cell.status === "pending" ? `⏳ Wacht op bevestiging van <b>${esc(c.witness)}</b>.` : c.joker ? "🃏 Met joker binnengehaald." : `✅ Bevestigd door <b>${esc(c.witness)}</b>.`}
        <span class="muted">${esc(ago(c.created_at))}</span></p>
      ${c.note ? `<p>“${esc(c.note)}”</p>` : ""}
      ${c.photo ? `<img class="photo-prev" src="${esc(c.photo.full)}" alt="">` : ""}
      ${cell.status === "confirmed" && thr > 0 && c.vetoes ? `<p class="warn">🚩 ${c.vetoes}/${thr} veto's. Nog ${thr - c.vetoes} en hij is weg.</p>` : ""}
      <div style="margin-top:16px">
        ${cell.status === "pending" ? `<button class="btn red big" data-act="cancel">Claim intrekken</button>` : ""}
        <button class="btn ghost big" data-close style="margin-top:10px">Sluiten</button>
      </div>`,
      async (e) => {
        const act = e.target.closest("[data-act]");
        if (e.type !== "click" || !act) return;
        if (act.dataset.act === "cancel") {
          try { await api("POST", `/api/claims/${c.id}/cancel`); closeSheet(); toast("Claim ingetrokken"); scheduleLoad(0); }
          catch (err) { toast(err.message, "err"); }
        }
      });
  }

  function openMe() {
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    const pushOk = "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext;
    const perm = window.Notification ? Notification.permission : "unsupported";
    openSheet(`
      <div style="display:flex;gap:12px;align-items:center;margin-bottom:8px">${avatar(S.me.nickname, S.me.color, "lg")}<div><h3>${esc(S.me.nickname)}</h3><span class="muted">${S.me.is_admin ? "admin · " : ""}speler</span></div></div>
      <div class="k">Meldingen</div>
      ${pushOk ? `<div class="btn-row"><button class="btn teal" data-act="push-on">${perm === "granted" ? "🔔 Opnieuw koppelen" : "🔔 Meldingen aanzetten"}</button><button class="btn ghost" data-act="push-test">Test</button></div>`
        : `<p class="muted">${isIOS && !standalone ? "Op iPhone werken meldingen alleen als de app op je beginscherm staat. Zet hem er eerst op (zie hieronder) en open hem vanaf daar." : "Pushmeldingen werken alleen via HTTPS in een ondersteunde browser."}</p>`}
      ${!standalone ? `<div class="k">Op je beginscherm zetten</div>
        <p class="muted">${isIOS ? "Safari: tik op Deel (het vierkantje met pijl) en kies <b>Zet op beginscherm</b>." : "Chrome: menu (⋮) en kies <b>App installeren</b> of <b>Toevoegen aan startscherm</b>."}</p>` : ""}
      <label class="toggle">🔊 Geluid bij bingo<input type="checkbox" id="soundInput" ${LS.get("sound", true) ? "checked" : ""}></label>
      <form method="post" action="/logout" style="margin-top:16px"><button class="btn ghost big" type="submit">Uitloggen</button></form>`,
      async (e) => {
        if (e.type === "change" && e.target.id === "soundInput") { LS.set("sound", e.target.checked); return; }
        const act = e.target.closest("[data-act]");
        if (e.type !== "click" || !act) return;
        if (act.dataset.act === "push-on") enablePush();
        if (act.dataset.act === "push-test") { try { await api("POST", "/api/push/test"); toast("Testmelding onderweg"); } catch (err) { toast(err.message, "err"); } }
      });
  }

  async function enablePush() {
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") { toast("Geen toestemming voor meldingen", "err"); return; }
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(S.vapid_public_key) });
      await api("POST", "/api/push/subscribe", sub.toJSON());
      toast("🔔 Meldingen staan aan", "ok");
    } catch (e) { toast(`Meldingen lukt niet: ${e.message}`, "err"); }
  }

  function b64ToBytes(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  // Shrink photos on the phone before upload: saves roaming data and server space.
  function compressImage(file, maxSide = 1280, quality = 0.72) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement("canvas");
        c.width = Math.round(img.naturalWidth * scale);
        c.height = Math.round(img.naturalHeight * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/jpeg", quality));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("bad image")); };
      img.src = url;
    });
  }

  /* ------------------------------------------------------------ event wiring */

  view.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    const id = el.dataset.id;

    if (act === "cell") {
      const cell = S.card.find((c) => c.pos === Number(el.dataset.pos));
      if (!cell || !cell.item_id) return;
      if (cell.status === "open") openClaim(cell); else openCellDetail(cell);
      return;
    }
    if (act === "lightbox") {
      const lb = $("#lightbox");
      $("img", lb).src = el.dataset.src;
      lb.hidden = false;
      return;
    }
    if (act === "confirm" || act === "reject") {
      el.disabled = true;
      const r = await api("POST", `/api/claims/${id}/${act}`, undefined, { queue: true }).catch((err) => { toast(err.message, "err"); el.disabled = false; });
      if (r && !r.queued) toast(act === "confirm" ? "Bevestigd ✅" : "Afgewezen. Strafslok voor de leugenaar 🍺", act === "confirm" ? "ok" : "");
      scheduleLoad(0);
      return;
    }
    if (act === "veto") {
      el.disabled = true;
      try {
        const r = await api("POST", `/api/claims/${id}/veto`);
        toast(r.vetoed ? "🚩 Weggestemd!" : `🚩 Veto genoteerd (${r.vetoes})`);
      } catch (err) { toast(err.message, "err"); el.disabled = false; }
      scheduleLoad(0);
      return;
    }

    // admin actions
    if (act === "announce") {
      const text = $("#announceText").value.trim();
      if (!text) return;
      return adminAction(() => api("POST", "/api/admin/announce", { text }), "Verstuurd 📣");
    }
    if (act === "new-invites") return adminAction(() => api("POST", "/api/admin/invites", { count: Number(el.dataset.n) }), "Codes gemaakt");
    if (act === "new-custom-code") {
      const code = $("#customCode").value.trim().toUpperCase();
      if (!code) return toast("Typ eerst een code", "err");
      return adminAction(() => api("POST", "/api/admin/invites", { code }), `Code ${code} staat klaar 🎟️`);
    }
    if (act === "del-invite") {
      if (!confirm(`Code ${el.dataset.code} intrekken? Accounts die ermee zijn aangemaakt blijven bestaan.`)) return;
      return adminAction(() => api("DELETE", `/api/admin/invites/${encodeURIComponent(el.dataset.code)}`), "Code ingetrokken");
    }
    if (act === "share-invite") {
      const link = `${location.origin}/register?code=${encodeURIComponent(el.dataset.code)}`;
      const text = `Doe mee met ${S.settings.title}! Code: ${el.dataset.code} (iedereen krijgt ermee een eigen account).`;
      if (navigator.share) { navigator.share({ title: S.settings.title, text, url: link }).catch(() => {}); }
      else { try { await navigator.clipboard.writeText(`${text} ${link}`); toast("Link gekopieerd 📋", "ok"); } catch { prompt("Kopieer deze link:", link); } }
      return;
    }
    if (act === "add-items") {
      const text = $("#newItems").value;
      if (!text.trim()) return;
      return adminAction(() => api("POST", "/api/admin/items", { text }), "Vakjes toegevoegd");
    }
    if (act === "save-item") {
      const input = view.querySelector(`[data-item="${id}"]`);
      return adminAction(() => api("PUT", `/api/admin/items/${id}`, { text: input.value }), "Opgeslagen");
    }
    if (act === "del-item") {
      const n = Number(el.dataset.confirmed);
      if (!confirm(n ? `Dit vakje is al ${n}× gedaan. Weghalen draait die claims terug. Zeker?` : "Vakje weghalen?")) return;
      return adminAction(() => api("DELETE", `/api/admin/items/${id}`), "Weg ermee");
    }
    if (act === "auto-grid") {
      return adminAction(() => api("POST", "/api/admin/auto-grid", {}), "Raster optimaal gezet ✨");
    }
    if (act === "save-settings") {
      return adminAction(() => api("POST", "/api/admin/settings", collectSettings(
        ["grid_cols", "grid_rows", "grid_auto", "line_rows", "line_cols", "line_diagonals", "jokers_per_player", "veto_threshold", "witness_limit", "kicker", "title", "subtitle", "footer"])), "Opgeslagen");
    }
    if (act === "save-prizes") {
      const prizes = {};
      view.querySelectorAll("[data-prize]").forEach((i) => { prizes[i.dataset.prize] = i.value; });
      return adminAction(async () => {
        await api("POST", "/api/admin/settings", collectSettings(["default_line_prize", "full_card_prize"]));
        await api("POST", "/api/admin/prizes", prizes);
      }, "Prijzen opgeslagen 🏆");
    }
    if (act === "pw") {
      const pw = prompt(`Nieuw wachtwoord voor ${el.dataset.name} (minstens 8 tekens):`);
      if (!pw) return;
      return adminAction(() => api("POST", `/api/admin/players/${id}/password`, { password: pw }), "Wachtwoord gezet");
    }
    if (act === "reset-player") {
      if (!confirm(`Kaart en claims van ${el.dataset.name} wissen?`)) return;
      return adminAction(() => api("POST", `/api/admin/players/${id}/reset`), "Gereset");
    }
    if (act === "del-player") {
      if (!confirm(`${el.dataset.name} definitief verwijderen?`)) return;
      return adminAction(() => api("DELETE", `/api/admin/players/${id}`), "Verwijderd");
    }
    if (act === "revoke") {
      if (!confirm("Deze claim terugdraaien?")) return;
      return adminAction(() => api("POST", `/api/admin/claims/${id}/revoke`), "Teruggedraaid");
    }
    if (act === "reshuffle") {
      if (!confirm("Alle kaarten opnieuw schudden? Geclaimde vakjes blijven op hun plek.")) return;
      return adminAction(() => api("POST", "/api/admin/reshuffle"), "Geschud 🔀");
    }
    if (act === "freeze") {
      const frozen = adminData.settings.frozen === "1";
      return adminAction(() => api("POST", "/api/admin/settings", { frozen: !frozen }), frozen ? "Spel is weer open" : "Spel bevroren ❄️");
    }
    if (act === "new-game") {
      if (!confirm("Echt ALLES wissen? Claims, bingo's en feed gaan weg. Spelers en vakjes blijven.")) return;
      if (!confirm("Laatste kans. Zeker weten?")) return;
      return adminAction(() => api("POST", "/api/admin/new-game"), "Nieuw spel gestart 💥");
    }
  });

  view.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!d.matches || !d.matches("details.adm")) return;
    if (d.open) adminOpen.add(d.dataset.sec); else adminOpen.delete(d.dataset.sec);
    LS.set("adminOpen", [...adminOpen]);
  }, true);

  view.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-act=cell]")) { e.preventDefault(); e.target.click(); }
  });

  $("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
  $("#meBtn").addEventListener("click", () => S && openMe());

  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (!b) return;
    tab = b.dataset.tab;
    LS.set("tab", tab);
    window.scrollTo({ top: 0 });
    render();
    if (tab === "album") loadAlbum();
    if (tab === "admin") loadAdmin();
  });

  window.addEventListener("online", () => { setOffline(false); flushQueue(); connectStream(); });
  window.addEventListener("offline", () => setOffline(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { scheduleLoad(0); flushQueue(); if (!es || es.readyState === 2) connectStream(); }
  });
  setInterval(flushQueue, 20000);
  setInterval(() => { if (tab === "feed" || tab === "admin") return; render(); }, 60000); // keep "x min geleden" fresh

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  if (tab === "admin" && !ME.is_admin) tab = "card";

  load().then(() => {
    if (tab === "album") loadAlbum();
    if (tab === "admin") loadAdmin();
    flushQueue();
  });
  connectStream();
})();
