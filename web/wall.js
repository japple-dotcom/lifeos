/* Kitchen wall display: read-only, refreshes itself every minute and keeps the screen awake. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const pad = (n) => String(n).padStart(2, "0");
  const money = (v) => (v >= 1 ? `£${Number(v).toFixed(2)}` : `${Math.round(v * 100)}p`);
  const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

  function tick() {
    const now = new Date();
    $("#clock").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    $("#date").textContent = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  }

  function eventRow(e, now) {
    const li = el("li");
    const allDay = !e.start || e.start.length === 10;
    const start = allDay ? null : new Date(e.start);
    const end = e.end && e.end.length > 10 ? new Date(e.end) : null;
    li.append(el("span", "t", allDay ? "All day" : `${pad(start.getHours())}:${pad(start.getMinutes())}`));
    const what = el("span", "", e.summary || "(no title)");
    if (e.who && e.who.length) what.append(el("span", "who", e.who.join(" and ") + (e.location ? ` · ${e.location}` : "")));
    li.append(what);
    if (end && end < now) li.classList.add("past");
    return li;
  }

  function fill(list, items, emptyText) {
    list.replaceChildren(...(items.length ? items : [Object.assign(el("li"), { className: "plain" })]));
    if (!items.length) list.firstChild.append(el("span", "empty", emptyText));
  }

  // Ask the display: answered from what's on it (it can't change anything), and said out loud.
  let last = null;
  function answerFor(q) {
    const d = last || {};
    const low = q.toLowerCase();
    const list = (xs) => xs.length <= 1 ? (xs[0] || "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
    if (/dinner|tea tonight|eat(ing)? tonight/.test(low)) {
      if (/tomorrow/.test(low)) return d.dinner_tomorrow ? `Tomorrow it's ${d.dinner_tomorrow}.` : "Nothing planned for tomorrow yet.";
      return d.dinner ? `Tonight it's ${d.dinner}.` : "Nothing's planned for dinner tonight.";
    }
    const where = low.match(/where(?:'s| is| are) (?:the |our )?([a-z -]{3,30})/);
    if (where || /bin|stopcock|fuse|boiler|meter/.test(low)) {
      const word = where ? where[1].trim() : (low.match(/bin|stopcock|fuse|boiler|meter/) || [""])[0];
      const hit = (d.manual || []).find((m) => `${m.title} ${m.section}`.toLowerCase().includes(word) || word.includes(m.title.toLowerCase()));
      return hit ? `${hit.title}: ${hit.details}` : "That isn't in the home manual yet.";
    }
    if (/today|on now|calendar|what's on|whats on/.test(low)) {
      const now = new Date();
      const today = (d.events || []).filter((e) => e.start.slice(0, 10) === localIso(now) && (e.start.length === 10 || new Date(e.end || e.start) >= now));
      return today.length ? `Today: ${list(today.map((e) => e.start.length === 10 ? e.summary : `${e.summary} at ${new Date(e.start).toLocaleTimeString("en-GB", { hour: "numeric", minute: "2-digit" })}`))}.` : "Nothing else in the calendar today.";
    }
    if (/chore|pocket money/.test(low)) {
      const ks = (d.kids || []).map((k) => `${k.kid} has done ${k.done} this week`);
      return ks.length ? `${list(ks)}.` : "No chores set up.";
    }
    if (/weather|cold|rain/.test(low) && d.weather) return `It's ${d.weather.temperature}${d.weather.unit} and ${String(d.weather.condition).replace(/_/g, " ")}.`;
    if (/renew|due|coming up/.test(low)) return (d.renewals || []).length ? `Coming up: ${list(d.renewals.slice(0, 3).map((r) => r.name))}.` : "Nothing due soon.";
    return "I can tell you about dinner, today's calendar, where things are, chores and what's coming up. To change things, use the app.";
  }
  function respond(q) {
    const a = answerFor(q);
    $("#ask-answer").textContent = a;
    try {
      if ("speechSynthesis" in window) {
        const u = new SpeechSynthesisUtterance(a);
        u.lang = "en-GB";
        speechSynthesis.cancel(); speechSynthesis.speak(u);
      }
    } catch { /* no voice: the text is shown */ }
  }
  function setupAsk() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    $("#ask-btn").addEventListener("click", () => {
      if (!Rec) { $("#ask-form").hidden = false; $("#ask-text").focus(); return; }
      const r = new Rec();
      r.lang = "en-GB"; r.interimResults = false; r.maxAlternatives = 1;
      $("#ask-answer").textContent = "Listening…";
      r.onresult = (ev) => respond(ev.results[0][0].transcript);
      r.onerror = () => { $("#ask-form").hidden = false; $("#ask-answer").textContent = "Didn't catch that. Type it instead."; $("#ask-text").focus(); };
      r.start();
    });
    $("#ask-form").addEventListener("submit", (ev) => { ev.preventDefault(); respond($("#ask-text").value); $("#ask-text").value = ""; });
  }

  async function load() {
    let data;
    try {
      const res = await fetch("/api/wall", { credentials: "same-origin" });
      if (res.status === 401) return showLogin();
      data = await res.json();
      last = data;
    } catch {
      $("#updated").textContent = "Offline: showing the last update";
      return;
    }
    $("#wall-login").hidden = true;
    $("#wall").hidden = false;
    const now = new Date(), today = localIso(now);
    const tomorrow = localIso(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
    // An all-day event covers start <= day < end; a timed event belongs to the day it starts.
    const onDay = (e, iso) => (e.start || "").length === 10
      ? e.start <= iso && iso < (e.end || e.start + "~")
      : localIso(new Date(e.start)) === iso;
    fill($("#w-today"), data.events.filter((e) => onDay(e, today)).map((e) => eventRow(e, now)), "Nothing in the calendar");
    fill($("#w-tomorrow"), data.events.filter((e) => onDay(e, tomorrow)).map((e) => eventRow(e, now)), "Nothing yet");

    $("#w-dinner").textContent = data.dinner || "Not planned yet";
    $("#w-dinner-tomorrow").textContent = data.dinner_tomorrow ? `Tomorrow: ${data.dinner_tomorrow}` : "";

    const bins = data.bins;
    const binsLine = $("#w-bins");
    binsLine.hidden = !bins;
    if (bins) {
      const names = bins.bins.map((b) => (/^[A-Z]{2}/.test(b) ? b : b[0].toLowerCase() + b.slice(1)));
      const what = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
      binsLine.textContent = bins.when === "tomorrow" ? `Bins out tonight: ${what}` : `Bins ${bins.when}: ${what}`;
      binsLine.classList.toggle("urgent", bins.when === "tomorrow" || bins.when === "today");
    }
    const kit = data.school_tomorrow;
    const kitLine = $("#w-kit");
    kitLine.hidden = !(kit && kit.items && kit.items.length);
    kitLine.textContent = kitLine.hidden ? "" : `School tomorrow: ${kit.text}`;
    const xmas = data.christmas;
    $("#w-xmas").hidden = !xmas;
    $("#w-xmas").textContent = !xmas ? "" : xmas.days_to_go === 0 ? "Happy Christmas!" :
      `${xmas.days_to_go} day${xmas.days_to_go === 1 ? "" : "s"} to Christmas` + (xmas.next ? ` · next: ${xmas.next.what}` : "");
    const parcels = data.parcels || [];
    $("#w-parcels-block").hidden = !parcels.length;
    $("#w-parcels").replaceChildren(...parcels.map((p) => {
      const li = el("li"); li.className = "plain";
      li.append(el("span", "", p.name), el("span", "t", p.window || p.label));
      return li;
    }));

    const stars = {};
    (data.stars || []).forEach((s) => { stars[s.kid] = s; });
    $("#w-kids-block").hidden = !data.kids.length;
    $("#w-kids").replaceChildren(...data.kids.map((k) => {
      const li = el("li"); li.className = "plain";
      const st = stars[k.kid];
      li.append(el("span", "", `${k.kid}: ${k.done} done${st ? `, ${st.total} of ${st.target} stars` : ""}`), el("span", "t", k.owed ? money(k.owed) : ""));
      const goals = (k.goals || []).map((g) => {
        const row = el("li"); row.className = "plain goal";
        row.append(el("span", "", `${k.kid} saving for ${g.name}`), el("span", "t", `${g.pct}%`));
        const bar = document.createElement("progress"); bar.max = 100; bar.value = g.pct; bar.setAttribute("aria-hidden", "true");
        row.append(bar);
        return row;
      });
      return [li, ...goals];
    }).flat());

    $("#w-ren-block").hidden = !data.renewals.length;
    $("#w-renewals").replaceChildren(...data.renewals.map((r) => {
      const li = el("li"); li.className = "plain";
      li.append(el("span", "", r.name), el("span", "t", r.days_left <= 0 ? "today" : `${r.days_left} days`));
      return li;
    }));

    const manual = data.manual || [];
    $("#w-manual-block").hidden = !manual.length;
    $("#w-manual").replaceChildren(...manual.slice(0, 6).map((m) => {
      const li = el("li"); li.className = "plain";
      li.append(el("span", "", m.title), el("span", "t", m.details));
      return li;
    }));

    const w = $("#weather");
    w.hidden = !data.weather;
    if (data.weather) w.textContent = `${Math.round(data.weather.temperature)}${data.weather.unit || "°C"} · ${String(data.weather.condition).replace(/[-_]/g, " ")}`;
    const p = $("#pending");
    p.hidden = !data.pending;
    p.textContent = data.pending === 1 ? "1 thing needs an OK on your phone" : `${data.pending} things need an OK on your phone`;
    const b = $("#brief");
    b.hidden = !data.brief;
    b.textContent = data.brief || "";
    $("#updated").textContent = `Updated ${pad(now.getHours())}:${pad(now.getMinutes())}`;
  }

  function showLogin() {
    $("#wall").hidden = true;
    $("#wall-login").hidden = false;
  }

  $("#wall-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("#wall-error");
    err.textContent = "";
    const key = $("#wall-key").value.trim();
    if (!key) { err.textContent = "Enter the household key."; return; }
    const res = await fetch("/api/login", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key, person: "display" }) }).catch(() => null);
    if (!res || !res.ok) {
      let msg = "That didn't work.";
      try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
      err.textContent = msg; return;
    }
    $("#wall-key").value = "";
    load();
    keepAwake();
  });

  // Keep the tablet's screen on while the display is showing.
  let lock = null;
  async function keepAwake() {
    try { if ("wakeLock" in navigator && document.visibilityState === "visible") lock = await navigator.wakeLock.request("screen"); }
    catch { /* not supported or not allowed; the tablet's own screen timeout applies */ }
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { keepAwake(); load(); } });

  tick();
  setupAsk();
  setInterval(tick, 5000);
  load();
  setInterval(load, 60000);
  keepAwake();
})();
