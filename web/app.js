/* Life OS phone app. Plain JavaScript, no build step. All text is inserted with textContent (never innerHTML). */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
    return node;
  };
  const prefs = {
    get(k, d) { try { const v = localStorage.getItem("lifeos." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("lifeos." + k, JSON.stringify(v)); } catch { /* storage may be off */ } },
  };
  const CHECK = () => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", "m5 12.5 4.5 4.5L19 7.5"); s.append(p); return s;
  };

  // v0.18 screens live in more.js and v0.19 ones in extra.js; each is told when a screen has loaded (and never stops
  // this one if it fails)
  function hook(name, data) {
    for (const [file, m] of [["more.js", window.LifeOSMore], ["extra.js", window.LifeOSExtra]]) {
      try { if (m && typeof m[name] === "function") m[name](data); }
      catch (e) { console.warn(file, name, e); }
    }
  }

  let toastTimer;
  function toast(message, isError = false) {
    const t = $("#toast");
    t.textContent = message;
    t.classList.remove("has-action");
    t.classList.toggle("error", isError);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), isError ? 7000 : 4000);
  }

  // A toast with one button (Undo). Keyboard users get focus moved to it, so it can be reached in time.
  function toastAction(message, label, onAction, ms = 6000, focus = false) {
    const t = $("#toast");
    const b = el("button", { type: "button", class: "toast-btn", text: label });
    t.replaceChildren(el("span", { text: message }), b);
    t.classList.remove("error"); t.classList.add("show", "has-action");
    clearTimeout(toastTimer);
    const hide = () => t.classList.remove("show", "has-action");
    b.addEventListener("click", () => { clearTimeout(toastTimer); hide(); onAction(); });
    toastTimer = setTimeout(hide, ms);
    if (focus) b.focus();
  }

  class AuthError extends Error {}
  class OfflineError extends Error {}
  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", ...opts, headers: { ...(opts.headers || {}) } };
    if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof Blob)) {
      init.body = JSON.stringify(opts.body);
      init.headers["Content-Type"] = "application/json";
    }
    let res;
    try { res = await fetch(path, init); }
    catch { throw new OfflineError("Can't reach Life OS. Check you're on home Wi-Fi or Tailscale is on."); }
    if (res.status === 401 && !opts.signingIn) throw new AuthError("Please sign in");
    let data = null;
    try { data = await res.json(); } catch { /* not json */ }
    if (!res.ok) {
      const d = data && data.detail;
      throw new Error(typeof d === "string" ? d : d ? "Something in that form isn't right. Check it and try again." : `Something went wrong (${res.status}).`);
    }
    return data;
  }
  async function busy(button, fn) {
    if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
    try { return await fn(); }
    catch (e) { if (e instanceof AuthError) showLogin(); else toast(e.message, true); }
    finally { if (button) { button.disabled = false; button.removeAttribute("aria-busy"); } }
  }

  // ---------- offline: jobs added and things ticked wait on the phone until Life OS is reachable ----------
  const OUTBOX = "lifeos.outbox";
  const outbox = {
    all() { try { return JSON.parse(localStorage.getItem(OUTBOX) || "[]"); } catch { return []; } },
    save(items) { try { localStorage.setItem(OUTBOX, JSON.stringify(items)); } catch { /* storage full or blocked */ } showOutbox(); },
    add(path, body, label) { const items = outbox.all(); items.push({ path, body, label, at: Date.now() }); outbox.save(items); },
  };
  function showOutbox() {
    const n = outbox.all().length, b = $("#outbox-banner");
    if (!b) return;
    b.hidden = n === 0;
    b.textContent = n ? `Offline: ${n} change${n === 1 ? "" : "s"} saved on this phone. They'll go through when Life OS is reachable.` : "";
  }
  let flushing = false;
  async function flushOutbox() {
    if (flushing) return;
    let items = outbox.all();
    if (!items.length) return;
    flushing = true;
    let sent = 0;
    try {
      while (items.length) {
        const it = items[0];
        try { await api(it.path, { method: "POST", body: it.body }); sent++; }
        catch (e) {
          if (e instanceof OfflineError || e instanceof AuthError) break;
          toast(`Couldn't save "${it.label}": ${e.message}`, true);  // e.g. already ticked elsewhere: drop it
        }
        items = items.slice(1); outbox.save(items);
      }
    } finally { flushing = false; }
    if (sent) {
      toast(`Back online: ${sent} saved change${sent === 1 ? "" : "s"} sent`);
      if (state.me) route();
    }
  }
  window.addEventListener("online", flushOutbox);

  const localIso = (d = new Date()) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const money = (v) => (v >= 1 ? `£${Number(v).toFixed(2)}` : `${Math.round(v * 100)}p`);
  const shortDate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

  // Photos and PDFs kept with a renewal, a home manual entry, a bill or a trip. They stay on the home computer.
  function attachments(kind, id, files, label, reload) {
    const inputId = `att-${kind}-${id}`;
    const input = el("input", { type: "file", id: inputId, class: "visually-hidden", accept: "image/*,application/pdf",
      onchange: async (ev) => {
        const f = ev.currentTarget.files[0];
        if (!f) return;
        if (f.size > 15 * 1024 * 1024) { toast("That file is bigger than 15 MB.", true); return; }
        const lab = ev.currentTarget.nextElementSibling;
        lab.setAttribute("aria-busy", "true");
        try {
          await api(`/api/files?for_kind=${kind}&for_id=${encodeURIComponent(id)}&name=${encodeURIComponent(f.name)}`,
            { method: "POST", body: f, headers: { "Content-Type": f.type || "application/octet-stream" } });
          toast(`Attached to ${label}`); reload();
        } catch (e) { if (e instanceof AuthError) showLogin(); else toast(e.message, true); }
        finally { lab.removeAttribute("aria-busy"); }
      } });
    return el("div", { class: "attachments" },
      files && files.length ? el("ul", { class: "att-list", "aria-label": `Attached to ${label}` }, files.map((f) => el("li", {},
        el("a", { href: `/api/files/${f.id}`, target: "_blank", rel: "noopener", text: f.type === "application/pdf" ? `📄 ${f.name}` : `🖼 ${f.name}`,
          "aria-label": `Open ${f.name}` }),
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${f.name} from ${label}`,
          onclick: (ev) => { if (!confirm(`Remove ${f.name}?`)) return; busy(ev.currentTarget, async () => { await api(`/api/files/${f.id}/delete`, { method: "POST" }); reload(); }); } })))) : null,
      input, el("label", { for: inputId, class: "linkish att-add", text: "Attach a photo or PDF", "aria-label": `Attach a photo or PDF to ${label}` }));
  }
  const when = (iso) => new Date(iso).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const ukDate = (d) => d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  function eventTime(e) {
    if (!e.start) return "";
    if (e.start.length === 10) return "All day";
    const s = new Date(e.start), f = new Date(e.end || e.start);
    const t = (x) => x.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    return `${t(s)} to ${t(f)}`;
  }

  // ---------- display preferences ----------
  function applyDisplay() {
    const size = prefs.get("textsize", "normal");
    document.documentElement.classList.toggle("text-large", size === "large");
    document.documentElement.classList.toggle("text-xl", size === "xl");
    document.documentElement.classList.toggle("high-contrast", prefs.get("contrast", false));
  }
  applyDisplay();

  function speak(text) {
    if (!prefs.get("speak", false) || !("speechSynthesis" in window) || !text) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-GB";
    window.speechSynthesis.speak(u);
  }

  // ---------- state & routing ----------
  const state = { me: null, people: [], lists: [], currentList: prefs.get("list", "Shopping"),
    listOwner: prefs.get("listOwner", "shared"), chatLoaded: false, sec: prefs.get("sec", "todo"),
    todos: [], todoFilter: prefs.get("todoFilter", "all") };
  const views = ["login", "today", "ask", "tasks", "home", "settings"];
  const loaders = { today: loadToday, ask: loadAsk, tasks: () => showSec(state.sec), home: loadHome, settings: loadSettings };

  function route() {
    const raw = location.hash.replace(/^#/, "");
    const [path, query] = raw.split("?");
    const [name, sub] = path.split("/");
    const view = views.includes(name) && name !== "login" ? name : "today";
    if (!state.me) return showLogin();
    // A link inside a dialog (like "Open it") moves on: close it without sending focus back
    document.querySelectorAll("dialog[open]").forEach((d) => { dialogOpener = null; d.close(); });
    if (view === "tasks" && sub) { state.focusTodo = decodeURIComponent(sub); state.sec = "todo"; }
    const q = new URLSearchParams(query || "");
    if (view === "tasks" && q.get("sec")) state.sec = q.get("sec");
    if (view !== "tasks") setShopMode(false);
    show(view);
    if (query && view === "settings") handleGoogleReturn(new URLSearchParams(query));
    if (view === "tasks" && q.get("agenda")) setTimeout(() => openAgenda(null), 400);
    if (view === "settings" && q.get("sec")) setTimeout(() => { const t = document.getElementById(q.get("sec")); if (t) t.scrollIntoView({ block: "start" }); }, 300);
    if (loaders[view]) loaders[view]();
  }

  function show(view) {
    for (const v of views) $("#view-" + v).hidden = v !== view;
    const signedIn = view !== "login";
    $("#tabbar").hidden = !signedIn;
    $("#topbar").hidden = !signedIn;
    $("#fab-add").hidden = !signedIn || view === "ask" || view === "settings";
    document.querySelectorAll("#tabbar a").forEach((a) => {
      if (a.dataset.tab === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    const h1 = $("#view-" + view + " h1");
    document.title = `${h1 ? h1.textContent : "Life OS"} · Life OS`;
    // Move focus to the new screen's heading so screen readers announce it.
    if (h1 && document.activeElement && document.activeElement.closest("#tabbar")) {
      h1.setAttribute("tabindex", "-1"); h1.focus({ preventScroll: false });
    }
    window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", route);
  // In-page jump links (Lists screen) scroll without changing the screen.
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-jump]");
    if (!a) return;
    ev.preventDefault();
    const target = document.querySelector(a.getAttribute("href"));
    if (target) { target.setAttribute("tabindex", "-1"); target.scrollIntoView({ block: "start" }); target.focus({ preventScroll: true }); }
  });

  // ---------- sign in ----------
  let chosenPerson = null;
  async function showLogin() {
    state.me = null; state.chatLoaded = false; chat.replaceChildren();
    show("login");
    const box = $("#login-people");
    box.replaceChildren();
    try {
      state.people = await api("/api/people");
    } catch (e) { $("#login-error").textContent = e.message; return; }
    chosenPerson = prefs.get("person", state.people[0] && state.people[0].id);
    for (const p of state.people) {
      const b = el("button", { type: "button", class: "btn btn-secondary", "aria-pressed": String(p.id === chosenPerson),
        text: p.name, onclick: () => {
          chosenPerson = p.id;
          box.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        } });
      box.append(b);
    }
  }

  $("#login-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const key = $("#login-key").value.trim();
    const err = $("#login-error");
    err.textContent = "";
    if (!chosenPerson) { err.textContent = "Choose who is using this phone."; return; }
    if (!key) { err.textContent = "Enter the household key."; $("#login-key").focus(); return; }
    busy(ev.submitter, async () => {
      try {
        await api("/api/login", { method: "POST", body: { key, person: chosenPerson }, signingIn: true });
      } catch (e) { err.textContent = e.message; $("#login-key").focus(); return; }
      prefs.set("person", chosenPerson);
      $("#login-key").value = "";
      await boot();
      toast(`Signed in as ${state.me.name}`);
    });
  });

  // ---------- Today ----------
  function renderRate(r) {
    const box = $("#rate-block");
    box.hidden = !r;
    if (!r) return;
    $("#rate-text").textContent = `${r.dinner}, ${r.when}. It helps Life OS suggest dinners you'll like.`;
    const rate = (n, msg) => (ev) => busy(ev.currentTarget, async () => {
      await api("/api/meals/rate", { method: "POST", body: { day: r.day, rating: n } });
      box.hidden = true; if (msg) toast(msg);
    });
    $("#rate-up").onclick = rate(1, `Noted: you loved ${r.dinner}`);
    $("#rate-down").onclick = rate(-1, `Noted: ${r.dinner} won't be suggested`);
    $("#rate-skip").onclick = rate(0, "");
  }
  async function loadToday(fresh = false) {
    $("#today-date").textContent = ukDate(new Date());
    const warnings = $("#today-warnings");
    let data;
    try { data = await api(`/api/today${fresh === true ? "?fresh=1" : ""}`); }
    catch (e) { if (e instanceof AuthError) return showLogin(); warnings.replaceChildren(el("p", { class: "notice", text: e.message })); return; }
    const hour = new Date().getHours();
    const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    $("#today-title").textContent = `${greet}, ${data.name}`;
    const notices = data.warnings.map((w) => el("p", { class: "notice", text: w }));
    if (state.me && state.me.problems && state.me.problems.length) {
      notices.unshift(el("p", { class: "notice" }, "Life OS setup isn't finished, so some things won't work yet. ",
        el("a", { href: "#settings", text: "See what's needed in Settings" }), "."));
    }
    warnings.replaceChildren(...notices);

    renderChecklist();

    // latest briefing from the scheduled routines
    const bb = $("#brief-block");
    bb.hidden = !data.brief;
    if (data.brief) {
      const label = { morning_brief: "Morning brief", evening_reset: "Evening reset", weekly_review: "Weekly review" }[data.brief.name] || "Briefing";
      $("#brief-title").textContent = label;
      $("#brief-meta").textContent = when(data.brief.at);
      $("#brief-text").textContent = data.brief.text;
    }

    // dinner tonight
    const dl = $("#dinner-line");
    dl.replaceChildren(data.dinner
      ? document.createTextNode(`Dinner tonight: ${data.dinner}`)
      : el("a", { href: "#tasks", text: "No dinner planned tonight. Plan this week's dinners" }));

    // Next up: the few jobs worth doing next, each with its main action
    const nu = (data.next_up || []).filter((n) => !pendingDone.has(n.id));
    $("#nextup-block").hidden = nu.length === 0;
    $("#nextup-list").replaceChildren(...nu.map((n) => {
      const li = el("li", { class: `nextup p${n.priority}`, "data-id": n.id });
      const due = dueWords(n);
      li.append(el("div", { class: "row" },
        el("button", { type: "button", class: "done-btn", "aria-label": `Mark done: ${n.title}`, title: `Mark done: ${n.title}`,
          onclick: (ev) => completeWithUndo({ id: n.id, list: n.list, title: n.title }, li, loadToday, ev.detail === 0) }, CHECK()),
        el("div", { class: "grow" }, el("strong", { text: n.title }),
          n.next_action ? el("div", { class: "next", text: `Next: ${n.next_action}` }) : null,
          due ? el("div", { class: n.days < 0 ? "tag-overdue" : "meta", text: due }) : null)),
        el("a", { class: "btn btn-secondary btn-small nextup-go", href: `#tasks/${encodeURIComponent(n.id)}`, text: n.action, "aria-label": `${n.action}: ${n.title}` }));
      return li;
    }));

    // open loops with tradespeople and appointments: the ones needing you first
    const waiting = data.waiting || [];
    $("#waiting-block").hidden = waiting.length === 0;
    $("#waiting-list").replaceChildren(...waiting.map((w) => el("li", { class: `row waiting${w.needs_you ? " needs-you" : ""}` },
      el("div", { class: "grow" }, el("strong", { text: w.title }), el("div", { class: w.needs_you ? "" : "meta", text: w.line })),
      el("a", { class: "linkish", href: w.task_id ? `#tasks/${encodeURIComponent(w.task_id)}` : "#tasks", text: w.needs_you ? "Sort it" : "View", "aria-label": `${w.needs_you ? "Sort out" : "View"}: ${w.title}` }))));

    // renewals in the next 60 days
    renderRate(data.rate_dinner);
    const cc = data.childcare || [];
    $("#cc-today-block").hidden = !cc.length;
    $("#cc-today").replaceChildren(...cc.map((g) => el("li", { text: `${g.label}: ${g.closed}. You're both busy.` })));
    const rb = $("#renewals-soon-block");
    rb.hidden = !data.renewals || data.renewals.length === 0;
    $("#renewals-soon").replaceChildren(...(data.renewals || []).map(renewalItem));

    // approvals
    const ab = $("#approvals-block"), al = $("#approvals-list");
    ab.hidden = data.approvals.length === 0;
    al.replaceChildren(...data.approvals.map((a) => {
      const full = a.reason || (a.args && a.args.summary) || a.tool.replace(/_/g, " ");
      const [what, ...rest] = full.split("\n");
      const details = rest.join("\n").trim();
      const li = el("li", { class: "approval" },
        el("strong", { text: what }),
        details ? el("div", { class: "approval-details", text: details, tabindex: "0", role: "region", "aria-label": `Details: ${what}` }) : null,
        el("div", { class: "meta", text: `Asked ${new Date(a.created_at).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" })}` +
          (a.expires_at ? ` · expires ${new Date(a.expires_at).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : "") }));
      const decide = (decision) => (ev) => busy(ev.currentTarget, async () => {
        const r = await api(`/api/approvals/${a.id}/${decision}`, { method: "POST" });
        if (r.ok) toast(decision === "approve" ? `Approved: ${what}` : `Cancelled: ${what}`);
        else toast(r.error || "That didn't work.", true);
        loadToday();
      });
      li.append(el("div", { class: "btns" },
        el("button", { type: "button", class: "btn btn-primary", "aria-label": `Approve: ${what}`, text: "Approve", onclick: decide("approve") }),
        el("button", { type: "button", class: "btn btn-danger", "aria-label": `Reject: ${what}`, text: "Reject", onclick: decide("reject") })));
      return li;
    }));

    // events
    const evs = data.events;
    $("#events-list").replaceChildren(...(evs.length ? evs.map((e) => el("li", {},
      el("span", { class: "event-time", text: eventTime(e) }),
      el("span", { text: e.summary || "(no title)" }),
      el("div", { class: "meta", text: [e.location, (e.who || []).join(" and ")].filter(Boolean).join(" · ") })))
      : [el("li", { class: "empty", text: "Nothing in the calendar today." })]));

    // anything else due (Next up already shows the top few)
    const dueTasks = data.tasks.filter((t) => !pendingDone.has(t.id));
    $("#due-title").textContent = nu.length ? "Also due" : "Due today";
    $("#due-block").hidden = !dueTasks.length && nu.length > 0;
    $("#due-list").replaceChildren(...(dueTasks.length ? dueTasks.map((t) => todoItem(t, loadToday))
      : [el("li", { class: "empty", text: "Nothing due. Enjoy it." })]));

    // chores
    $("#chores-list").replaceChildren(...(data.chores.length ? data.chores.map((c) => el("li", {},
      el("span", { text: c.chore }), el("span", { class: "meta", text: ` · ${c.who || "someone"}, ${new Date(c.at).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" })}` })))
      : [el("li", { class: "empty", text: "No chores logged yet." })]));
    hook("today", data);
  }
  $("#today-refresh").addEventListener("click", (ev) => busy(ev.currentTarget, async () => { await loadToday(true); toast("Up to date"); }));

  // The first real day: the server works out what's done; this phone adds "installed".
  async function checklistData() {
    const c = await api("/api/checklist");
    const installed = window.matchMedia("(display-mode: standalone)").matches;
    c.items.unshift({ group: "essentials", id: "install", text: "Install Life OS on this phone", done: installed,
      how: "Settings > Install on this phone", link: "#settings?sec=install" });
    c.done = c.items.filter((x) => x.done).length; c.total = c.items.length;
    c.essentials = c.items.filter((x) => x.group === "essentials");
    return c;
  }
  async function renderChecklist() {
    const card = $("#setup-card");
    if (!state.me || state.me.demo || prefs.get("setupHidden", false)) { card.hidden = true; return; }
    let c;
    try { c = await checklistData(); } catch { card.hidden = true; return; }
    const ess = c.essentials, done = ess.filter((x) => x.done).length;
    // Today shows the essentials until they're done; the rest lives in Settings > Getting set up.
    card.hidden = done === ess.length;
    $("#setup-progress").textContent = `${done} of ${ess.length} essentials done. ${c.done} of ${c.total} altogether.`;
    $("#setup-steps").replaceChildren(...ess.map((x) => el("li", { class: x.done ? "done" : "" },
      el("span", { class: "tick", "aria-hidden": "true", text: x.done ? "✓" : "" }),
      el("span", { class: "what grow", text: x.text }),
      x.done ? el("span", { class: "visually-hidden", text: "Done" }) : el("a", { href: x.link, "aria-label": `How: ${x.text}`, text: "How" }))));
  }
  const GROUPS = { essentials: "The essentials", household: "Your household", extras: "Extras" };
  async function renderSetupAll() {
    const c = await checklistData();
    $("#setup-all-count").textContent = c.done === c.total ? "All done. Life OS knows everything it needs." : `${c.done} of ${c.total} done`;
    $("#setup-all-groups").replaceChildren(...Object.entries(GROUPS).map(([g, title]) => {
      const its = c.items.filter((x) => x.group === g);
      return el("div", {}, el("h3", { text: `${title} (${its.filter((x) => x.done).length} of ${its.length})` }),
        el("ol", { class: "checklist" }, its.map((x) => el("li", { class: x.done ? "done" : "" },
          el("span", { class: "tick", "aria-hidden": "true", text: x.done ? "✓" : "" }),
          el("span", { class: "what grow" }, el("span", { text: x.text }), x.done ? null : el("span", { class: "meta block", text: x.how })),
          x.done ? el("span", { class: "visually-hidden", text: "Done" }) : el("a", { href: x.link, "aria-label": `Go: ${x.text}`, text: "Go" })))));
    }));
  }
  $("#setup-hide").addEventListener("click", () => { prefs.set("setupHidden", true); $("#setup-card").hidden = true; toast("Hidden. Everything is still in Settings."); });

  // ---------- To-do screen sections ----------
  const SECTIONS = { todo: () => loadTodos(), predicted: () => loadPredicted(), week: () => loadWeek(),
    projects: () => loadProjects(), lists: () => loadLists(), meals: () => loadMeals(), money: () => loadMoney(),
    renewals: () => { loadRenewals(); loadCars(); } };
  function showSec(name) {
    if (!SECTIONS[name]) name = "todo";
    if (name !== "lists") setShopMode(false);
    state.sec = name; prefs.set("sec", name);
    document.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sec === name)));
    document.querySelectorAll("#view-tasks .sec").forEach((d) => { d.hidden = d.id !== `sec-${name}`; });
    SECTIONS[name]();
  }
  document.querySelectorAll(".segmented button").forEach((b) => b.addEventListener("click", () => showSec(b.dataset.sec)));

  const PRIORITY = { 1: "Urgent", 2: "High", 3: "", 4: "Low" };
  const effortWords = (m) => (m < 60 ? `${m} min` : m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`);
  const repeatWords = (r) => ({ weekly: "Every week", fortnightly: "Every 2 weeks", monthly: "Every month",
    quarterly: "Every 3 months", yearly: "Every year" }[r] || (r.match(/^every_(\d+)_(weeks|months)$/) ? r.replace(/^every_(\d+)_(weeks|months)$/, "Every $1 $2") : ""));
  function dueWords(t) {
    if (!t.due) return "";
    if (t.days < 0) return `Overdue (${shortDate(t.due)})`;
    if (t.days === 0) return "Due today";
    if (t.days === 1) return "Due tomorrow";
    if (t.days < 7) return `Due ${new Date(t.due + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long" })}`;
    return `Due ${shortDate(t.due)}`;
  }
  function whoName(w) {
    if (!w || w === "anyone") return "";
    const p = (state.people || []).find((x) => x.id === w);
    return `For ${p ? p.name : w}`;
  }

  function todoItem(t, reload) {
    const label = `Mark done: ${t.title}`;
    const pr = PRIORITY[t.priority];
    const bits = [t.project ? `Project: ${t.project}` : t.category, effortWords(t.effort_min), repeatWords(t.recurrence || ""), whoName(t.who)].filter(Boolean);
    const due = dueWords(t);
    const li = el("li", { class: `todo p${t.priority}`, "data-id": t.id });
    li.append(el("div", { class: "row" },
      el("button", { type: "button", class: "done-btn", "aria-label": label, title: label,
        onclick: (ev) => completeWithUndo(t, li, reload, ev.detail === 0) }, CHECK()),
      el("div", { class: "grow" },
        el("div", { class: "todo-title" }, pr ? el("span", { class: `badge b${t.priority}`, text: pr }) : null, el("span", { text: t.title }),
          t.quick ? el("span", { class: "badge quick", text: "Quick" }) : null),
        t.next_action ? el("div", { class: "next", text: `Next: ${t.next_action}` }) : null,
        due ? el("div", { class: t.days < 0 ? "tag-overdue" : "meta", text: due }) : null,
        el("div", { class: "meta", text: bits.join(" · ") }))),
      solutionBlock(t, reload), moreMenu(t, reload));
    return li;
  }

  // Tick with a few seconds to undo: nothing changes in Google until the time is up.
  const pendingDone = new Map();
  function completeWithUndo(t, li, reload, viaKeyboard = false) {
    li.hidden = true;
    const commit = async () => {
      pendingDone.delete(t.id);
      try {
        const r = await api("/api/todos/complete", { method: "POST", body: { list_name: t.list, task_id: t.id }, keepalive: true });
        if (r.next_due) toast(`Done. It's back on ${shortDate(r.next_due)}`);
        else if (r.project && r.project.next) toast(`Done. Next: ${r.project.next.title}`);
        reload();
      } catch (e) {
        if (e instanceof OfflineError) { outbox.add("/api/todos/complete", { list_name: t.list, task_id: t.id }, t.title); toast(`Saved offline: ${t.title} will be ticked when you're back online`); return; }
        li.hidden = false; toast(e instanceof AuthError ? "Please sign in again" : e.message, true);
      }
    };
    const timer = setTimeout(commit, viaKeyboard ? 9000 : 6000);
    pendingDone.set(t.id, { timer, commit });
    toastAction(`Done: ${t.title}`, "Undo", () => {
      clearTimeout(timer); pendingDone.delete(t.id); li.hidden = false;
      toast(`Kept: ${t.title}`);
      const btn = li.querySelector(".done-btn"); if (btn && viaKeyboard) btn.focus();
    }, viaKeyboard ? 9000 : 6000, viaKeyboard);
  }
  const flushDone = () => { for (const p of [...pendingDone.values()]) { clearTimeout(p.timer); p.commit(); } };
  window.addEventListener("pagehide", flushDone);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushDone(); });

  function moreMenu(t, reload) {
    const snooze = (until, text) => el("button", { type: "button", class: "btn btn-secondary btn-small", text, "aria-label": `Snooze until ${text.toLowerCase()}: ${t.title}`,
      onclick: (ev) => busy(ev.currentTarget, async () => {
        const r = await api("/api/todos/snooze", { method: "POST", body: { list_name: t.list, task_id: t.id, until } });
        toast(`Moved to ${new Date(r.due + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" })}: ${t.title}`);
        reload();
      }) });
    return el("details", { class: "todo-more" },
      el("summary", { "aria-label": `More options: ${t.title}` }, el("span", { text: "More" })),
      el("div", { class: "more-actions" },
        el("p", { class: "meta", text: "Not now? Move it to:" }),
        el("div", { class: "opt-actions" }, snooze("tomorrow", "Tomorrow"), snooze("weekend", "This weekend"), snooze("next_week", "Next week")),
        el("div", { class: "opt-actions" },
          el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Change", "aria-haspopup": "dialog", "aria-label": `Change: ${t.title}`,
            onclick: (ev) => openEdit(t, reload, ev.currentTarget) }))));
  }

  // ---------- dialogs: quick add, change a job, plan my week ----------
  let dialogOpener = null;
  function openDialog(d, opener) {
    dialogOpener = opener || document.activeElement;
    d.showModal();
  }
  document.querySelectorAll("dialog").forEach((d) => {
    d.addEventListener("close", () => { if (dialogOpener && document.contains(dialogOpener)) dialogOpener.focus(); dialogOpener = null; });
    d.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => d.close()));
  });

  function openEdit(t, reload, opener) {
    const d = $("#edit-dialog");
    $("#edit-name").value = t.title;
    $("#edit-due").value = t.due || "";
    const cats = (lastTodoData && lastTodoData.categories) || [t.category];
    const listSel = $("#edit-list");
    listSel.replaceChildren(...(t.project ? [el("option", { value: t.list, text: `Project: ${t.project}` })]
      : cats.map((c) => el("option", { value: c, text: c }))));
    listSel.value = t.project ? t.list : t.list;
    listSel.disabled = Boolean(t.project);
    $("#edit-who").replaceChildren(el("option", { value: "anyone", text: "Anyone" }),
      ...(state.people || []).map((p) => el("option", { value: p.id, text: p.name })));
    $("#edit-who").value = (state.people || []).some((p) => p.id === t.who) ? t.who : "anyone";
    $("#edit-priority").value = String(t.priority || 3);
    $("#edit-next").value = t.next_action || "";
    const form = $("#edit-form");
    form.onsubmit = (ev) => {
      ev.preventDefault();
      const title = $("#edit-name").value.trim();
      if (!title) { toast("Give it a name.", true); $("#edit-name").focus(); return; }
      const body = { list_name: t.list, task_id: t.id };
      if (title !== t.title) body.title = title;
      if (($("#edit-due").value || "") !== (t.due || "")) body.due = $("#edit-due").value || "";
      if (!t.project && listSel.value !== t.list) body.category = listSel.value;
      if ($("#edit-who").value !== (t.who || "anyone")) body.who = $("#edit-who").value;
      if (Number($("#edit-priority").value) !== (t.priority || 3)) body.priority = Number($("#edit-priority").value);
      if ($("#edit-next").value.trim() !== (t.next_action || "")) body.next_action = $("#edit-next").value.trim();
      busy(ev.submitter, async () => {
        if (Object.keys(body).length > 2) await api("/api/todos/edit", { method: "POST", body });
        d.close(); toast(`Saved: ${title}`); reload();
      });
    };
    $("#edit-delete").onclick = (ev) => {
      if (!confirm(`Delete "${t.title}"? It won't come back, even if it repeats.`)) return;
      busy(ev.currentTarget, async () => {
        await api("/api/todos/delete", { method: "POST", body: { list_name: t.list, task_id: t.id } });
        d.close(); toast(`Deleted: ${t.title}`); reload();
      });
    };
    openDialog(d, opener);
    $("#edit-name").focus();
  }

  // ---------- "Sort it for me": suggestions you approve with one tap ----------
  const money2 = (v) => (v == null ? "" : `about £${Number(v).toFixed(v % 1 ? 2 : 0)}`);
  async function solveTodo(t, reload, extra = {}) {
    const url = extra.answer ? "/api/todos/answer" : "/api/todos/solve";
    const r = await api(url, { method: "POST", body: { list_name: t.list, task_id: t.id, ...extra } });
    if (r.status === "error") toast("Couldn't find options just now. Try again in a minute.", true);
    reload();
  }
  function solutionBlock(t, reload) {
    const sol = t.solution;
    if (t.quotes && t.quotes.length > 1) {
      const replied = t.quotes.filter((q) => q.status === "replied").length;
      return el("details", { class: "sol-wrap quotes", open: true },
        el("summary", { text: `Quotes from ${t.quotes.length} firms${replied ? ` (${replied} replied)` : ""}` }),
        el("p", { class: "hint", text: "Book the one you want: the others stop being chased." }),
        ...t.quotes.map((q) => el("div", { class: "quote" }, el("h3", { class: "quote-firm", text: q.contact_name }), requestBlock({ ...t, request: q }, reload))));
    }
    if (t.request && !["cancelled", "drafted"].includes(t.request.status)) return requestBlock(t, reload);
    if (!sol || sol.status === "error") {
      return el("div", { class: "sort-row" },
        el("button", { type: "button", class: "btn btn-secondary btn-small", "aria-label": `Sort it for me: ${t.title}`,
          text: "Sort it for me", onclick: (ev) => busy(ev.currentTarget, async () => { ev.currentTarget.textContent = "Working it out…"; await solveTodo(t, reload); }) }),
        findTimeButton(t, reload));
    }
    if (sol.status === "working") return el("p", { class: "meta solving", text: "Working out options…" });
    if (sol.kind === "arrange") return arrangeBlock(t, sol, reload);
    const box = el("div", { class: `solution k-${sol.kind}` });
    box.append(el("p", { class: "sol-summary" }, el("strong", { text: "Suggestion: " }), document.createTextNode(sol.summary || "")));
    if (sol.kind === "buy" && sol.options) {
      const ul = el("ul", { class: "options" });
      sol.options.forEach((o, i) => {
        const ordered = el("button", { type: "button", class: "btn btn-primary btn-small", hidden: true, text: "I've ordered it",
          "aria-label": `I've ordered ${o.title}`,
          onclick: (ev) => busy(ev.currentTarget, async () => {
            await api("/api/todos/ordered", { method: "POST", body: { list_name: t.list, task_id: t.id, option: i } });
            toast(`Ordered: ${o.title}. Ticked off and remembered for next time.`); reload();
          }) });
        const go = o.basket_url
          ? el("a", { class: "btn btn-primary btn-small", href: o.basket_url, target: "_blank", rel: "noopener", text: "Add to Amazon basket",
              "aria-label": `Add ${o.title} to Amazon basket (opens Amazon)`, onclick: () => { ordered.hidden = false; } })
          : el("a", { class: "btn btn-secondary btn-small", href: o.search_url, target: "_blank", rel: "noopener", text: "Find on Amazon",
              "aria-label": `Find ${o.title} on Amazon (opens Amazon)`, onclick: () => { ordered.hidden = false; } });
        const see = (o.product_url || o.url) ? el("a", { class: "linkish", href: o.product_url || o.url, target: "_blank", rel: "noopener",
          text: "Details", "aria-label": `Details for ${o.title} (opens a new tab)` }) : null;
        ul.append(el("li", {},
          el("div", { class: "opt-title", text: o.title }),
          el("div", { class: "meta", text: [o.why, money2(o.price_gbp)].filter(Boolean).join(" · ") }),
          el("div", { class: "opt-actions" }, go, see, ordered)));
      });
      box.append(ul, el("p", { class: "hint", text: "Amazon opens with it in your basket. Nothing is bought until you press Buy there." }));
    }
    if (sol.kind === "question" && sol.question) {
      const q = sol.question, otherId = `other-${t.id}`;
      box.append(el("p", { class: "question", text: q.text }),
        el("div", { class: "choices", role: "group", "aria-label": q.text }, ...q.choices.map((c) => el("button", { type: "button", class: "chip", text: c,
          onclick: (ev) => busy(ev.currentTarget, async () => { await solveTodo(t, reload, { answer: c }); toast("Thanks, remembered for next time"); }) }))));
      const other = el("form", { class: "add-step", novalidate: true },
        el("label", { for: otherId, class: "visually-hidden", text: "Another answer" }),
        el("input", { id: otherId, type: "text", placeholder: "Something else", autocomplete: "off" }),
        el("button", { type: "submit", class: "btn btn-secondary", text: "Answer" }));
      other.addEventListener("submit", (ev) => { ev.preventDefault(); const v = other.querySelector("input").value.trim();
        if (v) busy(ev.submitter, () => solveTodo(t, reload, { answer: v })); });
      box.append(other);
    }
    if (sol.steps && sol.steps.length) box.append(el("ol", { class: "sol-steps" }, ...sol.steps.map((s) => el("li", { text: s }))));
    if (sol.kind === "do" || sol.kind === "info") box.append(findTimeButton(t, reload));
    if (sol.links && sol.links.length) {
      box.append(el("div", { class: "opt-actions" }, ...sol.links.map((lk) => el("a", { class: "btn btn-secondary btn-small",
        href: lk.url, target: "_blank", rel: "noopener", text: lk.label, "aria-label": `${lk.label} (opens a new tab)` }))));
    }
    const hintId = `hint-${t.id}`;
    const retry = el("form", { class: "add-step retry", novalidate: true },
      el("label", { for: hintId, class: "visually-hidden", text: "Tell Life OS more, then try again" }),
      el("input", { id: hintId, type: "text", placeholder: "Not quite? Say more (e.g. under £30)", autocomplete: "off" }),
      el("button", { type: "submit", class: "btn btn-secondary", text: "Try again" }));
    retry.addEventListener("submit", (ev) => { ev.preventDefault();
      busy(ev.submitter, () => solveTodo(t, reload, { hint: retry.querySelector("input").value.trim() })); });
    box.append(retry);
    return el("details", { class: "sol-wrap", open: sol.kind === "buy" || sol.kind === "question" },
      el("summary", { text: sol.kind === "question" ? "Quick question" : sol.kind === "buy" ? "Options to buy" : sol.kind === "book" ? "How to book it" : "How to do it" }), box);
  }
  // ---------- Arranging a tradesperson ----------
  const gmailThread = (id) => `https://mail.google.com/mail/u/0/#all/${encodeURIComponent(id)}`;
  function arrangeBlock(t, sol, reload) {
    const box = el("div", { class: "solution k-arrange" });
    box.append(el("p", { class: "sol-summary" }, el("strong", { text: "Suggestion: " }), document.createTextNode(sol.summary || "")));
    const drafted = t.request && t.request.status === "drafted" ? t.request : null;
    if (drafted) box.append(el("p", { class: "drafted-note" }, el("strong", { text: "Ready in Gmail: " }),
      document.createTextNode(`the email to ${drafted.contact_name} is in ${drafted.person === (state.me && state.me.person) ? "your" : `${drafted.sender_name || "the household"}'s`} Gmail drafts. Send it from Gmail, or edit and send it here. Life OS notices either way.`)));
    const contacts = sol.contacts || [];
    let chosen = 0;
    const subjId = `subj-${t.id}`, bodyId = `body-${t.id}`, groupName = `who-${t.id}`;
    const subject = el("input", { id: subjId, type: "text", value: (sol.email && sol.email.subject) || "" });
    const body = el("textarea", { id: bodyId, rows: "11" });
    body.value = (sol.email && sol.email.body) || "";
    const sendBtn = el("button", { type: "button", class: "btn btn-primary btn-block" });
    const contactActions = el("div", { class: "opt-actions" });
    const alsoAsk = new Set();
    const quoteBox = el("fieldset", { class: "who quotes-pick", hidden: true }, el("legend", { text: "Also ask for a quote from" }));
    function refreshQuotes() {
      const others = contacts.map((c, i) => [c, i]).filter(([c, i]) => i !== chosen && c.email);
      quoteBox.hidden = !others.length || !contacts[chosen] || !contacts[chosen].email;
      [...alsoAsk].forEach((i) => { if (i === chosen) alsoAsk.delete(i); });
      quoteBox.replaceChildren(quoteBox.firstChild, ...others.map(([c, i]) => {
        const id = `q-${t.id}-${i}`;
        const cb = el("input", { type: "checkbox", id, checked: alsoAsk.has(i) || null });
        cb.addEventListener("change", () => { if (cb.checked) alsoAsk.add(i); else alsoAsk.delete(i); refresh(); });
        return el("div", { class: "check-row" }, cb, el("label", { for: id, text: c.name }));
      }));
    }
    function refresh() {
      const c = contacts[chosen];
      if (!c) { sendBtn.hidden = true; return; }
      body.value = body.value.replace(/^Hello [^\n]*,/, `Hello ${c.greet || c.name},`);
      sendBtn.hidden = !c.email;
      sendBtn.textContent = alsoAsk.size ? `Ask ${alsoAsk.size + 1} firms for quotes` : `Send email to ${c.name.split(" (")[0]}`;
      contactActions.replaceChildren(
        c.tel ? el("a", { class: "btn btn-secondary btn-small", href: c.tel, text: "Call", "aria-label": `Call ${c.name}` }) : null,
        c.sms ? el("a", { class: "btn btn-secondary btn-small", href: c.sms, text: "Text", "aria-label": `Text ${c.name}` }) : null,
        c.website ? el("a", { class: "btn btn-secondary btn-small", href: c.website, target: "_blank", rel: "noopener", text: "Website", "aria-label": `${c.name} website (opens a new tab)` }) : null);
      if (!c.email) contactActions.prepend(el("p", { class: "hint", text: "No email saved for them: call or text instead (the text is written for you)." }));
    }
    if (contacts.length) {
      const fs = el("fieldset", { class: "who" }, el("legend", { text: "Who to ask" }));
      contacts.forEach((c, i) => {
        const id = `${groupName}-${i}`;
        const input = el("input", { type: "radio", name: groupName, id, checked: i === 0 });
        input.addEventListener("change", () => { chosen = i; refreshQuotes(); refresh(); });
        fs.append(el("div", { class: "radio-row contact-row" }, input,
          el("label", { for: id },
            el("span", { class: "opt-title", text: c.name }), c.favourite ? el("span", { class: "badge quick", text: "Go-to" }) : null,
            el("span", { class: "meta block", text: [c.why, c.phone, c.email].filter(Boolean).join(" · ") }))));
      });
      box.append(fs, quoteBox);
      refreshQuotes();
    }
    if (sol.slots && sol.slots.length) {
      box.append(el("p", { class: "meta", text: "Times offered: " + sol.slots.map((s) => s.in ? `${s.label} (${s.in} in)` : s.label).join("; ") }));
    }
    box.append(
      el("div", { class: "field" }, el("label", { for: subjId, text: "Subject" }), subject),
      el("div", { class: "field" }, el("label", { for: bodyId, text: "Message (edit anything)" }), body),
      sendBtn, contactActions,
      el("p", { class: "meta" }, el("a", { href: sol.search_url, target: "_blank", rel: "noopener", text: `Find other ${sol.trade}s${sol.area ? ` near ${sol.area}` : ""}` }),
        document.createTextNode(" · "), el("a", { href: "#home", text: "Save a tradesperson" })));
    sendBtn.addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
      const c = contacts[chosen];
      const all = [chosen, ...alsoAsk];
      if (all.length > 1) {
        const names = all.map((i) => contacts[i].name).join(", ");
        if (!confirm(`Send this email to ${names} from your Gmail? Each is greeted by name.`)) return;
        const r = await api("/api/todos/arrange", { method: "POST", body: { list_name: t.list, task_id: t.id, contacts: all, subject: subject.value, body: body.value } });
        toast(`Asked ${r.sent.length} firms. Their replies will show side by side.`); reload();
        return;
      }
      if (!confirm(`Send this email to ${c.name} (${c.email}) from your Gmail?`)) return;
      await api("/api/todos/arrange", { method: "POST", body: { list_name: t.list, task_id: t.id, contact: chosen, subject: subject.value, body: body.value } });
      toast(`Sent to ${c.name}. Life OS will tell you when they reply.`); reload();
    }));
    refresh();
    if (!contacts.length) box.append(el("p", { class: "notice", text: `No ${sol.trade} saved yet. Use the search link, then save them under Home, Trades and contacts.` }));
    return el("details", { class: "sol-wrap", open: true }, el("summary", { text: sol.appointment ? `Book the ${sol.trade}` : `Get ${sol.trade && /^[aeiou]/.test(sol.trade) ? "an" : "a"} ${sol.trade || "tradesperson"}` }), box);
  }

  function niceWhen(dateStr, timeStr) {
    if (!dateStr) return "That time";
    const d = new Date(`${dateStr}T${timeStr || "09:00"}:00`);
    const day = d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    const h = d.getHours(), m = d.getMinutes();
    const time = `${h % 12 || 12}${m ? ":" + String(m).padStart(2, "0") : ""}${h < 12 ? "am" : "pm"}`;
    return `${day} at ${time}`;
  }

  function requestBlock(t, reload) {
    const r = t.request;
    const box = el("div", { class: `solution request s-${r.status}` });
    const sentOn = new Date(r.sent_at).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
    const actions = el("div", { class: "opt-actions" });
    const remind = el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Send a reminder",
      onclick: (ev) => busy(ev.currentTarget, async () => {
        const d = await api(`/api/requests/${r.id}/reminder`);
        const bId = `rm-${r.id}`;
        const bodyIn = el("textarea", { id: bId, rows: "8" }); bodyIn.value = d.body;
        const form = el("form", { class: "stack reminder-form", novalidate: true },
          el("div", { class: "field" }, el("label", { for: bId, text: `Reminder to ${r.greet || r.contact_name} (edit anything)` }), bodyIn),
          el("div", { class: "opt-actions" },
            el("button", { type: "submit", class: "btn btn-primary btn-small", text: `Send reminder to ${r.greet || r.contact_name}` }),
            el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Not now", onclick: () => { form.remove(); actions.hidden = false; } })));
        form.addEventListener("submit", (e2) => { e2.preventDefault(); busy(e2.submitter, async () => {
          await api(`/api/requests/${r.id}/remind`, { method: "POST", body: { subject: d.subject, body: bodyIn.value } });
          toast(`Reminder sent to ${r.contact_name}`); reload(); }); });
        actions.hidden = true; box.append(form); bodyIn.focus();
      }) });
    const other = el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Ask someone else",
      onclick: (ev) => busy(ev.currentTarget, async () => { await api(`/api/requests/${r.id}/cancel`, { method: "POST" }); await solveTodo(t, reload); }) });
    if (r.status === "sent") {
      box.append(el("p", {}, el("strong", { text: "Emailed " }), document.createTextNode(`${r.contact_name} on ${sentOn}. Waiting for a reply; you'll get a notification, and Life OS reminds you if they go quiet.`)));
      actions.append(remind, other);
    } else if (r.status === "chased") {
      box.append(el("p", {}, el("strong", { text: "No reply yet " }), document.createTextNode(`from ${r.contact_name} (emailed ${sentOn}).`)));
      remind.className = "btn btn-primary btn-small"; actions.append(remind, other);
    } else if (r.status === "replied") {
      box.append(el("p", {}, el("strong", { text: `${r.contact_name} replied: ` }), document.createTextNode(`"${r.reply_snippet}"`)));
      if (r.thread_id) actions.append(el("a", { class: "btn btn-secondary btn-small", href: gmailThread(r.thread_id), target: "_blank", rel: "noopener", text: "Open email", "aria-label": `Open the email from ${r.contact_name} (opens Gmail)` }));
      // Pre-fill the visit with the offered time that the reply seems to agree to
      const reply = (r.reply_snippet || "").toLowerCase();
      const guess = (r.slots || []).find((s) => reply.includes(new Date(s.start).toLocaleDateString("en-GB", { weekday: "long" }).toLowerCase())) || (r.slots || [])[0];
      const start = guess ? new Date(guess.start) : new Date(Date.now() + 2 * 864e5);
      const dId = `bd-${r.id}`, tId = `bt-${r.id}`, lId = `bl-${r.id}`;
      const dIn = el("input", { id: dId, type: "date", value: localIso(start) });
      const tIn = el("input", { id: tId, type: "time", value: `${String(start.getHours()).padStart(2, "0")}:${String(start.getMinutes()).padStart(2, "0")}` });
      const lIn = el("select", { id: lId }, ...[[60, "1 hour"], [120, "2 hours"], [240, "Half a day"], [480, "All day"]].map(([v, l]) => el("option", { value: String(v), text: l, selected: v === 120 })));
      const cId = `bc-${r.id}`, cbId = `bcb-${r.id}`;
      const confirmOn = el("input", { type: "checkbox", id: cbId, checked: true });
      const confirmText = el("textarea", { id: cId, rows: "4" });
      let edited = false;
      const fillConfirm = () => { if (!edited) confirmText.value = `Hello ${r.greet || r.contact_name},\n\n${niceWhen(dIn.value, tIn.value)} is great, thank you. See you then.\n\n${r.sender_name || ""}`.trim(); };
      confirmText.addEventListener("input", () => { edited = true; });
      dIn.addEventListener("change", fillConfirm); tIn.addEventListener("change", fillConfirm); fillConfirm();
      const confirmField = el("div", { class: "field" }, el("label", { for: cId, text: "Confirmation email (edit anything)" }), confirmText);
      // Who'll be in? Checked against the calendars whenever the time changes (busy or free only).
      const whoIn = el("p", { class: "meta", "aria-live": "polite" });
      const checkWho = async () => {
        try {
          const h = await api(`/api/who-home?start=${encodeURIComponent(`${dIn.value}T${tIn.value}:00`)}&duration_min=${lIn.value}`);
          whoIn.textContent = h.in ? `${h.in} will be in${h.busy.length ? ` (${h.busy.join(" and ")} busy)` : ""}.` : "Nobody's free then in the calendars: check who can be in.";
          whoIn.className = h.in ? "meta" : "notice";
        } catch { whoIn.textContent = ""; }
      };
      dIn.addEventListener("change", checkWho); tIn.addEventListener("change", checkWho); lIn.addEventListener("change", checkWho);
      checkWho();
      confirmOn.addEventListener("change", () => { confirmField.hidden = !confirmOn.checked; });
      const form = el("form", { class: "book-form stack", novalidate: true },
        el("p", { class: "meta", text: "When are they coming?" }),
        el("div", { class: "row two" }, el("div", { class: "field" }, el("label", { for: dId, text: "Date" }), dIn),
          el("div", { class: "field" }, el("label", { for: tId, text: "Time" }), tIn)),
        el("div", { class: "field" }, el("label", { for: lId, text: "How long" }), lIn),
        whoIn,
        el("div", { class: "check-row" }, confirmOn, el("label", { for: cbId, text: `Also email ${r.greet || r.contact_name} to confirm` })),
        confirmField,
        el("button", { type: "submit", class: "btn btn-primary btn-block", text: "Add to calendar" }));
      form.addEventListener("submit", (ev) => { ev.preventDefault(); busy(ev.submitter, async () => {
        const b = await api(`/api/requests/${r.id}/book`, { method: "POST", body: { start: `${dIn.value}T${tIn.value}:00`, duration_min: Number(lIn.value),
          confirm_body: confirmOn.checked ? confirmText.value : "" } });
        toast(b.appointment.confirm_error ? `Booked, but the confirmation email didn't send: ${b.appointment.confirm_error}` : `Booked: ${r.contact_name}, ${b.appointment.label}${b.appointment.confirmed ? ". Confirmation sent" : ""}`, Boolean(b.appointment.confirm_error));
        reload(); }); });
      box.append(form);
      actions.append(other);
    } else if (r.status === "booked" && r.appointment) {
      box.append(el("p", {}, el("strong", { text: "Appointment: " }), document.createTextNode(`${r.contact_name}, ${r.appointment.label}${r.appointment.in ? ` (${r.appointment.in} in)` : ""}. It's in the calendar${r.appointment.confirmed ? " and they've been sent a confirmation" : ""}. Tick this off once the job's done.`)));
    } else if (r.status === "visited") {
      box.append(el("p", {}, el("strong", { text: `Did ${r.contact_name} sort it?` })));
      actions.append(el("button", { type: "button", class: "btn btn-primary btn-small", text: "Yes, it's done", "aria-label": `Yes, it's done: ${t.title}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/todos/complete", { method: "POST", body: { list_name: t.list, task_id: t.id } }); toast(`Done: ${t.title}`); reload(); }) }));
      if (r.contact_id && !r.contact_favourite && r.trade) actions.append(el("button", { type: "button", class: "btn btn-secondary btn-small", text: `Make ${r.greet || r.contact_name} our go-to`,
        "aria-label": `Make ${r.contact_name} our go-to ${r.trade}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api(`/api/contacts/${r.contact_id}/favourite`, { method: "POST" }); toast(`${r.contact_name} is now your go-to ${r.trade}`); ev.currentTarget.remove(); }) }));
      other.textContent = "No, ask again";
      actions.append(other);
    }
    box.append(actions);
    return el("details", { class: "sol-wrap", open: true }, el("summary", { text: { sent: "Waiting for a reply", chased: "No reply yet", replied: "They replied", booked: "Booked", visited: "How did it go?" }[r.status] || "Request" }), box);
  }

  // ---------- Find time in the calendar for a job you'll do yourself ----------
  function findTimeButton(t, reload) {
    const wrap = el("div", { class: "find-time" });
    const btn = el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Find time", "aria-label": `Find time to do: ${t.title}` });
    btn.addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
      const { slots } = await api(`/api/slots?duration_min=${t.effort_min || 60}&diy=true`);
      if (!slots.length) { toast("No free time found in the next two weeks.", true); return; }
      wrap.replaceChildren(el("p", { class: "meta", text: "Free times (tap one to add it to your calendar):" }),
        el("div", { class: "choices" }, ...slots.map((s) => el("button", { type: "button", class: "chip", text: s.label,
          onclick: (e2) => busy(e2.currentTarget, async () => {
            const r = await api("/api/todos/schedule", { method: "POST", body: { list_name: t.list, task_id: t.id, start: s.start, duration_min: t.effort_min || 60 } });
            toast(`In your calendar: ${r.label}`); reload();
          }) }))));
    }));
    wrap.append(btn);
    return wrap;
  }

  let solvePoll = null;
  function pollIfSolving(items, reload) {
    clearTimeout(solvePoll);
    if (items.some((t) => t.solution && t.solution.status === "working")) { solvePoll = setTimeout(reload, 4000); return; }
    // Waiting for a tradesperson: look again every 30 seconds while the list is on screen and nobody is typing
    if (items.some((t) => t.request && (t.request.status === "sent" || t.request.status === "chased"))) {
      const state = (list) => list.filter((t) => t.request).map((t) => `${t.id}:${t.request.status}`).join();
      const before = state(items);
      solvePoll = setTimeout(async () => {
        if ($("#view-tasks").hidden) return;
        const typing = document.activeElement && document.activeElement.closest && document.activeElement.closest("form");
        if (typing || document.querySelector(".reminder-form")) { pollIfSolving(items, reload); return; }
        let fresh;
        try { fresh = await api("/api/todos"); } catch { pollIfSolving(items, reload); return; }
        if (state(fresh.todos) === before) { pollIfSolving(items, reload); return; }
        const replied = fresh.todos.find((t) => t.request && t.request.status === "replied" && !before.includes(`${t.id}:replied`));
        reload();
        toast(replied ? `${replied.request.contact_name} replied` : "There's an update on a job you're waiting on");
      }, 30000);
    }
  }

  function filterOptions(cats) {
    const sel = $("#todo-filter");
    const opts = [["all", "Everything"], ["mine", "For me"],
      ...(state.people || []).filter((p) => !state.me || p.id !== state.me.person).map((p) => [`who:${p.id}`, `For ${p.name}`]),
      ["quick", "Quick jobs (15 min or less)"], ["urgent", "Urgent and high"],
      ...cats.map((c) => [`cat:${c}`, c])];
    if (sel.options.length !== opts.length) sel.replaceChildren(...opts.map(([v, l]) => el("option", { value: v, text: l })));
    sel.value = opts.some(([v]) => v === state.todoFilter) ? state.todoFilter : "all";
  }
  function passes(t) {
    if (pendingDone.has(t.id)) return false;
    const q = (state.todoSearch || "").trim().toLowerCase();
    if (q && !q.split(/\s+/).every((w) => [t.title, t.next_action, t.category, t.project].join(" ").toLowerCase().includes(w))) return false;
    const f = state.todoFilter;
    if (f.startsWith("who:")) return t.who === f.slice(4);
    if (f === "mine") return state.me && (t.who === state.me.person || t.who === "anyone");
    if (f === "quick") return t.quick;
    if (f === "urgent") return t.priority <= 2;
    if (f.startsWith("cat:")) return t.category === f.slice(4);
    return true;
  }
  function renderTodos(data) {
    const groups = $("#todo-groups");
    const items = state.todos.filter(passes);
    const filtered = items.length !== state.todos.filter((t) => !pendingDone.has(t.id)).length;
    $("#todo-count").textContent = filtered ? `Showing ${items.length} of ${state.todos.length}` : "";
    if (!items.length) {
      groups.replaceChildren(el("p", { class: "empty-state", text: state.todos.length ? "Nothing matches. Try other words or Show: Everything." : "Nothing to do. Add anything above." }));
      return;
    }
    const order = ["now", "this_week", "this_month", "later", "someday"];
    groups.replaceChildren(...order.filter((b) => items.some((t) => t.bucket === b)).map((b) => {
      const mine = items.filter((t) => t.bucket === b);
      const id = `bucket-${b}`;
      return el("section", { class: "block", "aria-labelledby": id },
        el("h2", { id, text: `${data.buckets[b]} (${mine.length})` }),
        el("ul", { class: "list" }, ...mine.map((t) => todoItem(t, loadTodos))));
    }));
  }
  let lastTodoData = null;
  async function loadTodos() {
    let data;
    try { data = await api("/api/todos"); }
    catch (e) {
      if (e instanceof AuthError) return showLogin();
      $("#todo-groups").replaceChildren(el("p", { class: "notice", text: e.message })); return;
    }
    lastTodoData = data; state.todos = data.todos;
    filterOptions(data.categories);
    renderTodos(data);
    focusAskedTodo(data);
    pollIfSolving(data.todos, loadTodos);
  }
  // Opened from a notification or a link (#tasks/<id>): scroll to that job and highlight it
  function focusAskedTodo(data) {
    const id = state.focusTodo;
    if (!id) return;
    state.focusTodo = null;
    let li = document.querySelector(`#todo-groups li[data-id="${CSS.escape(id)}"]`);
    if (!li && state.todos.some((t) => t.id === id)) {  // hidden by a search or filter: show everything
      state.todoSearch = ""; $("#todo-search").value = ""; state.todoFilter = "all"; $("#todo-filter").value = "all";
      renderTodos(data);
      li = document.querySelector(`#todo-groups li[data-id="${CSS.escape(id)}"]`);
    }
    if (!li) { toast("That job has been ticked off or moved."); return; }
    li.classList.add("highlight");
    li.setAttribute("tabindex", "-1");
    li.scrollIntoView({ block: "center" });
    li.focus({ preventScroll: true });
    setTimeout(() => li.classList.remove("highlight"), 4000);
  }
  let searchTimer;
  $("#todo-search").addEventListener("input", (ev) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.todoSearch = ev.target.value; if (lastTodoData) renderTodos(lastTodoData); }, 150);
  });
  $("#todo-filter").addEventListener("change", (ev) => { state.todoFilter = ev.target.value; prefs.set("todoFilter", ev.target.value); if (lastTodoData) renderTodos(lastTodoData); });

  // Adding a job: the To-do box and the + Add button on every screen work the same way.
  function addJob(input, out, button, after) {
    const text = input.value.trim();
    if (!text) { toast("Say what needs doing first.", true); input.focus(); return; }
    out.replaceChildren(el("p", { class: "meta", text: "Sorting it…" }));
    busy(button, async () => {
      try {
        const r = await api("/api/todos", { method: "POST", body: { text } });
        input.value = "";
        if (r.duplicate) { out.replaceChildren(el("p", { class: "meta", text: `"${r.title}" is already on ${r.list}.` })); return; }
        const parts = r.category === "Shopping" ? ["Added to Shopping"]
          : [`Filed under ${r.project_name ? `project ${r.project_name}` : r.category}`, PRIORITY[r.priority] || "Normal priority",
             r.due ? dueWords({ due: r.due, days: Math.round((new Date(r.due + "T12:00:00") - new Date(localIso() + "T12:00:00")) / 864e5) }) : ({ today: "Today", this_week: "This week", this_month: "This month", later: "Later", someday: "Someday" }[r.timescale]),
             r.effort_min ? `about ${effortWords(r.effort_min)}` : "", repeatWords(r.recurrence || "")].filter(Boolean);
        const box = [el("p", { class: "filed" }, el("strong", { text: r.title }), document.createTextNode(`: ${parts.join(" · ")}`))];
        if (r.next_action) box.push(el("p", { class: "next", text: `Next: ${r.next_action}` }));
        if (r.solving) box.push(el("p", { class: "meta", text: "Working out options for you; they'll appear on the to-do in a moment." }));
        if (r.is_project) {
          box.push(el("p", { class: "hint", text: "This sounds like a bigger job." }),
            el("button", { type: "button", class: "btn btn-secondary btn-block", text: `Make "${r.title}" a project`,
              onclick: (e2) => busy(e2.currentTarget, async () => {
                await api("/api/todos/complete", { method: "POST", body: { list_name: r.list, task_id: r.id } });
                const pj = await api("/api/projects", { method: "POST", body: { name: r.title, details: text } });
                out.replaceChildren(); toast(`Project planned: ${pj.total} steps`); showSec("projects");
              }) }));
        }
        if (!r.is_project && r.id && r.category !== "Shopping") box.push(el("a", { href: `#tasks/${encodeURIComponent(r.id)}`, text: "Open it" }));
        out.replaceChildren(...box);
        toast(`Added: ${r.title}`);
        if (after) after(r);
      } catch (e) {
        out.replaceChildren();
        if (e instanceof OfflineError) {
          outbox.add("/api/todos", { text }, text); input.value = "";
          out.replaceChildren(el("p", { class: "meta", text: `Saved offline: "${text}" will be added and sorted when you're back online.` }));
          toast("Saved offline");
          return;
        }
        throw e;
      }
    });
  }
  $("#todo-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    addJob($("#todo-text"), $("#todo-result"), ev.submitter, () => loadTodos());
  });
  $("#fab-add").addEventListener("click", (ev) => {
    $("#add-result").replaceChildren();
    openDialog($("#add-dialog"), ev.currentTarget);
    $("#add-text").focus();
  });
  $("#add-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    addJob($("#add-text"), $("#add-result"), ev.submitter, () => {
      if (!$("#view-tasks").hidden && state.sec === "todo") loadTodos();
      if (!$("#view-today").hidden) loadToday();
      $("#add-text").focus();
    });
  });

  // Dictation (Chrome on Android): speak, and it's added when you stop
  function setupMic(btn, input, form) {
    const R = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!R) { btn.hidden = true; return; }
    let r = null;
    btn.addEventListener("click", () => {
      if (r) { r.stop(); return; }
      r = new R(); r.lang = "en-GB"; r.interimResults = true;
      btn.setAttribute("aria-pressed", "true");
      r.onresult = (e) => { input.value = [...e.results].map((x) => x[0].transcript).join(""); };
      r.onerror = (e) => toast(e.error === "not-allowed" ? "Allow microphone access to use voice." : "Didn't catch that. Try again.", true);
      r.onend = () => { btn.setAttribute("aria-pressed", "false"); r = null; if (input.value.trim()) form.requestSubmit(); };
      r.start();
    });
  }
  setupMic($("#todo-mic"), $("#todo-text"), $("#todo-form"));
  setupMic($("#add-mic"), $("#add-text"), $("#add-form"));

  // Plan my week: free slots for the jobs you'll do yourself, confirmed in one go
  $("#plan-week-btn").addEventListener("click", (ev) => {
    const d = $("#plan-dialog"), body = $("#plan-body"), confirmBtn = $("#plan-confirm");
    body.replaceChildren(el("p", { class: "meta", text: "Finding free times…" }));
    confirmBtn.hidden = true;
    openDialog(d, ev.currentTarget);
    api("/api/plan-week").then((p) => {
      if (!p.plan.length) {
        body.replaceChildren(el("p", { text: p.skipped.length ? "No free evenings or weekend time left this week." : "Nothing this week that you'd do yourself. Nice." }));
        return;
      }
      const boxes = p.plan.map((it, i) => {
        const id = `plan-${i}`;
        const cb = el("input", { type: "checkbox", id, checked: true });
        cb._item = it;
        return el("li", { class: "check-row plan-item" }, cb, el("label", { for: id },
          el("strong", { text: it.title }), el("span", { class: "meta", text: ` ${it.label}` })));
      });
      body.replaceChildren(el("ul", { class: "plain-list" }, ...boxes),
        p.skipped.length ? el("p", { class: "meta", text: `No room this week for: ${p.skipped.join(", ")}.` }) : "");
      confirmBtn.hidden = false;
      confirmBtn.onclick = (e2) => busy(e2.currentTarget, async () => {
        const items = boxes.map((li) => li.querySelector("input")).filter((cb) => cb.checked)
          .map((cb) => ({ list_name: cb._item.list, task_id: cb._item.task_id, start: cb._item.start, duration_min: cb._item.duration_min }));
        if (!items.length) { d.close(); return; }
        const r = await api("/api/plan-week", { method: "POST", body: { items } });
        d.close();
        toast(`${r.planned.length} job${r.planned.length === 1 ? "" : "s"} added to your calendar`);
        loadTodos();
      });
    }).catch((e) => body.replaceChildren(el("p", { class: "notice", text: e.message })));
  });

  // Projects
  async function loadProjects() {
    let data;
    try { data = await api("/api/projects"); }
    catch (e) {
      if (e instanceof AuthError) return showLogin();
      $("#project-cards").replaceChildren(el("p", { class: "notice", text: e.message })); return;
    }
    const active = data.projects.filter((p) => p.status === "active"), done = data.projects.filter((p) => p.status === "done");
    $("#project-cards").replaceChildren(...(active.length ? [el("h2", { text: "Your projects" }), ...active.map(projectCard)]
      : [el("p", { class: "empty-state", text: "No projects yet. Start one above: Life OS plans the steps." })]),
      ...(done.length ? [el("h2", { text: "Finished" }), ...done.map(projectCard)] : []));
    loadTrips();
    loadParties();
  }
  async function loadTrips() {
    let t;
    try { t = await api("/api/trips"); } catch { return; }
    $("#trips-list").replaceChildren(...(t.trips.length ? t.trips.map((x) => el("li", { class: "card stack" },
      el("div", { class: "row" }, el("strong", { class: "grow", text: x.name }),
        el("span", { class: "badge", text: x.on ? "Away now" : x.days_to_go === 1 ? "Tomorrow" : x.days_to_go > 0 ? `${x.days_to_go} days to go` : "Back" })),
      el("p", { class: "meta", text: `${shortDate(x.start)} to ${shortDate(x.end)}${x.abroad ? " · abroad" : ""}` }),
      x.to_pack ? el("div", {},
        el("p", { text: `Packed ${x.packed} of ${x.to_pack}. The list is "${x.list}" in Google Tasks.` }),
        el("progress", { max: String(x.to_pack), value: String(x.packed), "aria-label": `Packing for ${x.name}` })) : null,
      ...(x.warnings || []).map((w) => el("p", { class: "notice", text: w })),
      awayToggle(x),
      attachments("trip", x.id, x.files, x.name, loadTrips),
      el("button", { type: "button", class: "linkish", text: "Remove trip", "aria-label": `Remove the ${x.name} trip`,
        onclick: (ev) => {
          if (!confirm(`Remove the ${x.name} trip and its Away entry in the calendar? The packing list and to-dos stay.`)) return;
          busy(ev.currentTarget, async () => { await api("/api/trips/delete", { method: "POST", body: { id: x.id } }); toast("Trip removed"); loadTrips(); });
        } })))
      : [el("li", { class: "empty", text: "No trips planned. Add one below and Life OS makes the packing list and the jobs before you go." })]));
  }
  $("#trip-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const dest = $("#trip-dest").value.trim(), start = $("#trip-start").value, end = $("#trip-end").value;
    if (!dest || !start || !end) { toast("Say where you're going and the dates.", true); (dest ? (start ? $("#trip-end") : $("#trip-start")) : $("#trip-dest")).focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api("/api/trips", { method: "POST", body: { name: dest, destination: dest, start, end, travel: $("#trip-travel").value,
        abroad: $("#trip-abroad").checked, kids: $("#trip-kids").checked, pets: $("#trip-pets").checked } });
      toast(`Planned. Packing list "${r.list}" is in Google Tasks.`);
      ev.target.reset(); $("#trip-add").open = false; loadTrips();
    });
  });
  function projectCard(p) {
    const stepsId = `steps-${p.id}`, inputId = `step-${p.id}`;
    const card = el("article", { class: "project card", "aria-label": p.name },
      el("div", { class: "row" }, el("h3", { class: "grow", text: p.name }), el("span", { class: "meta", text: `${p.done} of ${p.total} done` })),
      el("progress", { max: String(p.total || 1), value: String(p.done), "aria-label": `${p.name}: ${p.done} of ${p.total} steps done` }));
    if (p.goal) card.append(el("p", { class: "meta", text: p.goal }));
    if (p.next) {
      card.append(el("div", { class: "row next-step" },
        el("button", { type: "button", class: "done-btn", "aria-label": `Mark done: ${p.next.title}`, title: `Mark done: ${p.next.title}`,
          onclick: (ev) => busy(ev.currentTarget, async () => {
            const r = await api("/api/todos/complete", { method: "POST", body: { list_name: p.list, task_id: p.next.id } });
            toast(r.project && r.project.next ? `Done. Next: ${r.project.next.title}` : `${p.name} is finished!`);
            loadProjects();
          }) }, CHECK()),
        el("div", { class: "grow" }, el("div", { class: "meta", text: "Next step" }), el("strong", { text: p.next.title }))));
    }
    const list = el("ol", { class: "steps-list", id: stepsId }, ...p.steps.map((st) => el("li", { class: st.done ? "done" : "" },
      el("span", { text: st.title }), st.done ? el("span", { class: "visually-hidden", text: " (done)" }) : null)));
    const add = el("form", { class: "add-step", novalidate: true },
      el("label", { for: inputId, class: "visually-hidden", text: `Add a step to ${p.name}` }),
      el("input", { id: inputId, type: "text", placeholder: "Add a step", autocomplete: "off" }),
      el("button", { type: "submit", class: "btn btn-secondary", text: "Add step" }));
    add.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const v = add.querySelector("input").value.trim();
      if (!v) return;
      busy(ev.submitter, async () => { await api(`/api/projects/${p.id}/steps`, { method: "POST", body: { text: v } }); toast("Step added"); loadProjects(); });
    });
    card.append(el("details", {}, el("summary", { text: `All ${p.total} steps` }), list, add));
    return card;
  }
  $("#project-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("#project-name").value.trim(), details = $("#project-details").value.trim();
    if (!name) { toast("Give the project a name first.", true); $("#project-name").focus(); return; }
    busy(ev.submitter, async () => {
      toast("Planning the steps…");
      const p = await api("/api/projects", { method: "POST", body: { name, details } });
      $("#project-name").value = ""; $("#project-details").value = "";
      toast(`${p.name}: ${p.total} steps planned. First: ${p.next ? p.next.title : ""}`);
      loadProjects();
    });
  });

  function renewalItem(r) {
    const left = r.days_left;
    const words = left < 0 ? `${-left} day${left === -1 ? "" : "s"} ago` : left === 0 ? "today" : `in ${left} day${left === 1 ? "" : "s"}`;
    return el("li", { class: "row" },
      el("span", { class: "grow" }, el("span", { text: r.name }), el("span", { class: "meta", text: ` · ${shortDate(r.due_date)}` })),
      el("span", { class: `days-left${left <= 14 ? " soon" : ""}`, text: words }));
  }

  function taskItem(t, listName, reload) {
    const label = `Mark done: ${t.title}`;
    const todayIso = localIso();
    const dueWord = t.due === todayIso ? "Due today" : `Due ${(t.due || "").split("-").reverse().join("/")}`;
    const due = t.due ? (t.overdue ? el("span", { class: "tag-overdue", text: `Overdue (${t.due.split("-").reverse().join("/")})` })
      : el("span", { class: "meta", text: dueWord })) : null;
    return el("li", {}, el("div", { class: "row" },
      el("button", { type: "button", class: "done-btn", "aria-label": label, title: label,
        onclick: (ev) => busy(ev.currentTarget, async () => {
          const owner = reload === loadLists ? state.listOwner : "shared";
          const path = `/api/tasks/complete?owner=${owner}`, body = { list_name: listName, task_id: t.id };
          try { await api(path, { method: "POST", body }); }
          catch (e) {
            if (!(e instanceof OfflineError)) throw e;
            outbox.add(path, body, t.title); ev.target.closest("li").hidden = true;
            toast(`Saved offline: ${t.title} will be ticked when you're back online`); return;
          }
          toast(`Done: ${t.title}`);
          reload();
        }) }, CHECK()),
      el("div", { class: "grow" }, el("div", { text: t.title }), t.notes ? el("div", { class: "meta", text: t.notes }) : null,
        due, listName && reload === loadToday ? el("div", { class: "meta", text: listName }) : null)));
  }

  // ---------- Ask ----------
  const chat = $("#chat");
  function addMsg(who, text, cls) {
    const m = el("div", { class: `msg ${cls}` }, el("span", { class: "who", text: who }), document.createTextNode(text));
    chat.append(m);
    m.scrollIntoView({ block: "end", behavior: "smooth" });
    return m;
  }
  async function loadAsk() {
    if (state.chatLoaded) return;
    state.chatLoaded = true;
    try {
      const { turns } = await api("/api/conversation");
      chat.replaceChildren();
      for (const t of turns) addMsg(t.role === "user" ? "You" : "Life OS", t.text, t.role === "user" ? "me" : "ai");
      $("#suggestions").hidden = turns.length > 0;
      $("#new-chat").hidden = turns.length === 0;
    } catch (e) { if (e instanceof AuthError) showLogin(); }
  }
  $("#new-chat").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/conversation/reset", { method: "POST" });
    chat.replaceChildren();
    $("#suggestions").hidden = false; $("#new-chat").hidden = true;
    toast("Started a new conversation");
    $("#ask-input").focus();
  }));

  async function send(text) {
    text = text.trim();
    if (!text) { toast("Type or say something first.", true); $("#ask-input").focus(); return; }
    addMsg("You", text, "me");
    $("#suggestions").hidden = true;  // keep the screen uncluttered once the conversation starts
    $("#new-chat").hidden = false;
    $("#ask-input").value = "";
    const thinking = addMsg("Life OS", "Working on it…", "ai thinking");
    chat.setAttribute("aria-busy", "true");
    await busy($("#send-btn"), async () => {
      try {
        const r = await api("/api/ask", { method: "POST", body: { text } });
        thinking.remove();
        addMsg(`Life OS (${cap(r.ai)})`, r.reply, "ai");
        speak(r.reply);
      } catch (e) { thinking.remove(); throw e; }
    });
    chat.removeAttribute("aria-busy");
  }
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
  $("#ask-form").addEventListener("submit", (ev) => { ev.preventDefault(); send($("#ask-input").value); });
  $("#ask-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(ev.target.value); }
  });
  $("#suggestions").addEventListener("click", (ev) => { if (ev.target.matches(".chip")) send(ev.target.textContent); });

  // Voice input (Chrome on Android supports this)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const micBtn = $("#mic-btn");
  let rec = null;
  if (!SR) {
    micBtn.hidden = true;
  } else {
    micBtn.addEventListener("click", () => {
      if (rec) { rec.stop(); return; }
      rec = new SR();
      rec.lang = "en-GB"; rec.interimResults = true; rec.continuous = false;
      const input = $("#ask-input");
      const before = input.value;
      micBtn.setAttribute("aria-pressed", "true"); $("#mic-label").textContent = "Stop";
      toast("Listening…");
      rec.onresult = (e) => {
        let text = "";
        for (const r of e.results) text += r[0].transcript;
        input.value = (before ? before + " " : "") + text;
        if (e.results[e.results.length - 1].isFinal) { rec.stop(); }
      };
      rec.onerror = (e) => toast(e.error === "not-allowed" ? "Allow microphone access to use voice." : "Didn't catch that. Try again.", true);
      rec.onend = () => {
        micBtn.setAttribute("aria-pressed", "false"); $("#mic-label").textContent = "Speak";
        rec = null;
        if (input.value.trim() && input.value !== before) send(input.value);
      };
      rec.start();
    });
  }

  // ---------- Lists ----------
  async function loadLists() {
    const select = $("#list-select");
    const ul = $("#task-list");
    let data;
    try { data = await api(`/api/tasks?list_name=${encodeURIComponent(state.currentList)}&owner=${state.listOwner}`); }
    catch (e) {
      if (e instanceof AuthError) return showLogin();
      if (state.listOwner !== "shared") { state.listOwner = "shared"; prefs.set("listOwner", "shared"); return loadLists(); }
      ul.replaceChildren(el("li", { class: "empty", text: e.message })); return;
    }
    const groups = data.groups || [{ owner: "shared", label: "Shared lists", lists: data.lists }];
    select.replaceChildren(...(groups.length > 1
      ? groups.map((g) => el("optgroup", { label: g.label }, ...g.lists.map((l) => el("option", { value: `${g.owner}|${l}`, text: l }))))
      : groups[0].lists.map((l) => el("option", { value: `shared|${l}`, text: l }))));
    select.value = `${data.owner}|${data.list}`;
    $("#list-heading").textContent = `Items in ${data.list}`;
    ul.replaceChildren(...(data.tasks.length ? data.tasks.map((t) => taskItem(t, data.list, loadLists))
      : [el("li", { class: "empty", text: `${data.list} is empty.` })]));
    state.currentList = data.list; state.listOwner = data.owner;
    loadShopOrder();
  }

  // ---------- Predicted: needs Life OS sees coming ----------
  const KIND_WORDS = { shopping: "Shopping", job: "Job", checkup: "Check-up", gift: "Present", care: "Appliance care" };
  const CONF_WORDS = { high: "Very likely", medium: "Likely", low: "Maybe" };
  function whenWords(p) {
    const d = new Date(p.due + "T12:00:00");
    const day = d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    if (p.days_until < 0) return `Probably needed now (was due ${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })})`;
    if (p.days_until === 0) return "Probably needed today";
    if (p.days_until === 1) return "Probably needed tomorrow";
    if (p.days_until < 14) return `Probably needed around ${day}`;
    return `In about ${Math.round(p.days_until / 7)} weeks (around ${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })})`;
  }
  function predictionItem(p, reload) {
    const act = (action, extra = {}) => (ev) => busy(ev.currentTarget, async () => {
      const r = await api("/api/predictions/act", { method: "POST", body: { pid: p.pid, action, ...extra } });
      toast({ add: `Added: ${p.title}${r.list ? ` (${r.list})` : ""}`, snooze: `Not yet: ${p.title}. Back in a week.`,
        dismiss: `Not needed this time: ${p.title}`, never: `Won't predict ${p.title} again`, last: "Saved" }[action]);
      reload();
    });
    const addLabel = (p.kind === "shopping" || p.to === "shopping") ? "Add to Shopping" : p.kind === "gift" ? "Sort a present" : p.kind === "checkup" ? "Book it" : "Add to to-do";
    const li = el("li", { class: `prediction c-${p.confidence}` },
      el("div", { class: "pred-head" }, el("strong", { text: p.title }), el("span", { class: "badge", text: KIND_WORDS[p.kind] || p.kind }),
        p.status === "added" ? el("span", { class: "badge quick", text: "On your list" }) : null),
      el("div", { text: p.needs_last ? "Due date not known yet" : whenWords(p) }),
      el("div", { class: "meta", text: `${CONF_WORDS[p.confidence]}. ${p.why}` }));
    if (p.needs_last) {
      const id = `last-${p.pid.replace(/[^a-z0-9]/gi, "-")}`;
      const input = el("input", { type: "date", id, max: localIso() });
      li.append(el("form", { class: "row two last-form", novalidate: true, onsubmit: (ev) => { ev.preventDefault();
          if (!input.value) { toast("Pick the date of the last one.", true); input.focus(); return; }
          act("last", { last: input.value })(ev); } },
        el("div", { class: "field" }, el("label", { for: id, text: "When was the last one?" }), input),
        el("button", { type: "submit", class: "btn btn-secondary btn-small", text: "Save" })));
    }
    if (p.status !== "added") li.append(el("div", { class: "opt-actions" },
      el("button", { type: "button", class: "btn btn-primary btn-small", text: addLabel, "aria-label": `${addLabel}: ${p.title}`, onclick: act("add") }),
      p.needs_last ? null : el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Not yet", "aria-label": `Not yet: ${p.title}`, onclick: act("snooze", { days: 7 }) }),
      p.needs_last ? null : el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Not needed", "aria-label": `Not needed this time: ${p.title}`, onclick: act("dismiss") }),
      el("button", { type: "button", class: "linkish", text: "Stop predicting", "aria-label": `Stop predicting: ${p.title}`, onclick: (ev) => {
        if (confirm(`Stop predicting ${p.title}?`)) act("never")(ev); } })));
    return li;
  }
  async function loadPredicted() {
    let data;
    try { data = await api("/api/predictions"); }
    catch (e) { if (e instanceof AuthError) return showLogin(); $("#pred-groups").replaceChildren(el("p", { class: "notice", text: e.message })); return; }
    $("#pred-learned").textContent = data.learned_from
      ? `Learned from ${data.learned_from} things bought and done. ${data.auto_add === "off" ? "Nothing moves across by itself." : "Very likely needs move across by themselves."}`
      : "Nothing learned yet. As you tick things off and shop, predictions appear here.";
    const groups = [["Due now", (p) => p.days_until <= 2], ["This month", (p) => p.days_until > 2 && p.days_until <= 31], ["Later", (p) => p.days_until > 31]];
    const ps = data.predictions;
    $("#pred-groups").replaceChildren(...(ps.length ? groups.filter(([, f]) => ps.some(f)).map(([label, f], i) =>
      el("section", { class: "block", "aria-labelledby": `pred-g${i}` }, el("h3", { id: `pred-g${i}`, text: `${label} (${ps.filter(f).length})` }),
        el("ul", { class: "list" }, ...ps.filter(f).map((p) => predictionItem(p, loadPredicted)))))
      : [el("p", { class: "empty-state", text: "No predictions yet." })]));
  }

  // ---------- The week ----------
  async function loadWeek() {
    let w;
    try { w = await api("/api/week"); }
    catch (e) { if (e instanceof AuthError) return showLogin(); toast(e.message, true); return; }
    const empty = (t) => [el("li", { class: "empty", text: t })];
    $("#week-done").replaceChildren(...(w.done.length ? w.done.map((t) => el("li", { text: t })) : empty("Nothing ticked off yet this week.")));
    $("#week-bought").textContent = w.bought.length ? `Bought: ${w.bought.join(", ")}` : "";
    $("#week-waiting").replaceChildren(...(w.waiting.length ? w.waiting.map((x) => el("li", {},
      el("a", { href: x.task_id ? `#tasks/${encodeURIComponent(x.task_id)}` : "#tasks", text: x.title }), el("div", { class: "meta", text: x.line })))
      : empty("Not waiting on anyone.")));
    $("#week-events").replaceChildren(...(w.events.length ? w.events.map((e) => el("li", {},
      el("span", { class: "event-time", text: `${new Date((e.start.length > 10 ? e.start : e.start + "T12:00:00")).toLocaleDateString("en-GB", { weekday: "short", day: "numeric" })} ${e.start.length > 10 ? eventTime(e) : "All day"}` }),
      el("span", { text: ` ${e.summary || "(no title)"}` }))) : empty("Nothing in the calendar.")));
    $("#week-predicted").replaceChildren(...(w.predicted.length ? w.predicted.map((p) => el("li", {},
      el("strong", { text: p.title }), el("span", { class: "meta", text: ` · ${whenWords(p).replace("Probably needed ", "")}` })))
      : empty("Nothing predicted this week.")));
    loadChildcare();
    loadAdminHour();
    loadSchoolRun();
  }
  let adminSkip = 0;
  async function loadAdminHour() {
    let a;
    try { a = await api(`/api/admin-hour?skip=${adminSkip}`); } catch { $("#admin-block").hidden = true; return; }
    $("#admin-block").hidden = !a.jobs.length;
    if (!a.jobs.length) return;
    $("#admin-jobs").replaceChildren(...a.jobs.map((j) => el("li", { text: j.title })));
    const booked = a.booked && a.booked >= new Date().toISOString().slice(0, 10);
    $("#admin-slot").textContent = booked ? `Booked for ${new Date(a.booked).toLocaleString("en-GB", { weekday: "long", hour: "2-digit", minute: "2-digit" })}.`
      : a.slot ? `${a.slot.label}, ${a.slot.in} is free.` : "No free hour this week. Try next week.";
    $("#admin-book").hidden = booked || !a.slot;
    $("#admin-other").hidden = booked || !a.slot;
    $("#admin-book").onclick = (ev) => busy(ev.currentTarget, async () => {
      const r = await api("/api/admin-hour/book", { method: "POST", body: { start: a.slot.start, jobs: a.jobs.map((j) => j.title), who: a.slot.in } });
      toast(`"${r.title}" is in the calendar`); adminSkip = 0; loadAdminHour();
    });
    $("#admin-other").onclick = () => { adminSkip = (adminSkip + 1) % 5; loadAdminHour(); };
  }
  async function loadSchoolRun() {
    let r;
    try { r = await api("/api/school-run"); } catch { $("#run-block").hidden = true; return; }
    $("#run-block").hidden = !r.days.length;
    $("#run-days").replaceChildren(...r.days.map((d) => el("li", { class: "stack" },
      el("strong", { text: d.label }),
      el("ul", { class: "list compact" }, d.runs.map((x) => el("li", { class: "row" },
        el("span", { class: "grow" }, el("span", { text: `${x.kind} ${x.at}: ` }),
          x.gap ? el("strong", { class: "warn", text: "Nobody free" }) : el("span", { text: `${x.who}${x.claimed ? " ✓" : " (suggested)"}` }),
          x.claimed ? el("span", { class: "visually-hidden", text: " (in the calendar)" }) : null),
        x.claimed || x.gap ? null : el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Claim it",
          "aria-label": `${x.who} does ${x.kind} on ${d.label}`,
          onclick: (ev) => busy(ev.currentTarget, async () => {
            await api("/api/school-run/claim", { method: "POST", body: { date: d.date, kind: x.kind, at: x.at, who: x.who } });
            toast(`${x.who}: ${x.kind} on ${d.label}`); loadSchoolRun();
          }) })))))));
  }
  $("#week-plan").addEventListener("click", () => $("#plan-week-btn").click());

  // ---------- School holidays and childcare ----------
  function childcareGap(g) {
    const enc = encodeURIComponent;
    const contacts = (state.ccContacts || []).filter((c) => c.phone || c.email);
    const who = el("select", { "aria-label": `Who's covering ${g.label}` },
      el("option", { value: "", text: "Who's covering?" }), ...["Grandparents", "Holiday club", "Childminder", "Friend", "One of us is off"].map((w) => el("option", { value: w, text: w })));
    const asks = contacts.slice(0, 3).map((c) => el("a", { class: "btn btn-secondary btn-small",
      href: c.phone ? `sms:${c.phone}?&body=${enc(g.ask_text)}` : `mailto:${c.email}?subject=${enc("Could you help with childcare?")}&body=${enc(g.ask_text)}`,
      text: `Ask ${c.name}` }));
    return el("li", { class: "card stack cc-gap" },
      el("p", {}, el("strong", { text: g.label }), document.createTextNode(` · ${g.closed} · ${g.days} day${g.days === 1 ? "" : "s"}`)),
      el("p", { class: "meta", text: `Busy: ${g.busy.join(" and ")}` }),
      el("div", { class: "btn-row" },
        el("a", { class: "btn btn-secondary btn-small", href: `sms:?&body=${enc(g.ask_text)}`, text: "Ask someone by text" }), ...asks,
        el("a", { class: "btn btn-secondary btn-small", target: "_blank", rel: "noopener",
          href: `https://www.google.com/search?q=${enc(`holiday club near ${state.area || "me"} ${new Date(g.dates[0]).toLocaleDateString("en-GB", { month: "long" })}`)}`, text: "Find a holiday club" }),
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Ask for time off",
          onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/childcare/leave", { method: "POST", body: { label: g.label } }); toast("Added to your to-dos"); }) })),
      el("div", { class: "row" }, who, el("button", { type: "button", class: "btn btn-primary btn-small", text: "It's sorted",
        onclick: (ev) => busy(ev.currentTarget, async () => {
          await api("/api/childcare/covered", { method: "POST", body: { dates: g.dates, who: who.value || "Sorted" } });
          toast(`Sorted: ${g.label}, in the family calendar`); loadWeek();
        }) })));
  }
  async function loadChildcare() {
    let c;
    try { c = await api("/api/childcare"); } catch { return; }
    state.ccContacts = c.contacts;
    $("#cc-gaps").replaceChildren(...(c.gaps.length ? c.gaps.map(childcareGap)
      : [el("li", { class: "empty", text: c.kids.length ? (c.closed.length ? "No gaps: someone's free (or it's sorted) every day the school's closed in the next 8 weeks." : "Add the school's term dates below to check.") : "Add the children's names in settings to use this." })]));
    $("#cc-closed").replaceChildren(...(c.closed.length ? c.closed.map((x) => el("li", { class: "row" },
      el("span", { class: "grow", text: `${x.name}: ${shortDate(x.start)}${x.end !== x.start ? ` to ${shortDate(x.end)}` : ""}` }),
      x.source === "settings" ? null : el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${x.name}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/childcare/closed/delete", { method: "POST", body: { id: x.id } }); loadChildcare(); }) })))
      : [el("li", { class: "empty", text: "No school holidays added yet." })]));
  }
  $("#cc-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("#cc-text").value.trim();
    if (!text) { toast("Paste the term dates first.", true); $("#cc-text").focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api("/api/childcare/closed", { method: "POST", body: { text } });
      $("#cc-text").value = "";
      toast(`Added ${r.added.length} school closure${r.added.length === 1 ? "" : "s"}`);
      loadChildcare();
    });
  });

  // ---------- Money: subscriptions and spending ----------
  const gbp = (n) => `£${Number(n).toFixed(2)}`;
  async function loadMoney() {
    let m;
    try { m = await api("/api/money"); } catch (e) { if (e instanceof AuthError) return showLogin(); toast(e.message, true); return; }
    const subs = m.subscriptions;
    $("#subs-total").textContent = subs.items.length ? `${gbp(subs.per_month)} a month (${gbp(subs.per_year)} a year)` : "";
    const per = { weekly: "week", monthly: "month", yearly: "year" };
    $("#subs-list").replaceChildren(...(subs.items.length ? subs.items.map((x) => {
      const decide = (choice, label) => el("button", { type: "button", class: `btn ${choice === "keep" ? "btn-secondary" : "btn-secondary"} btn-small`, text: label,
        "aria-label": `${label}: ${x.service}`,
        onclick: (ev) => busy(ev.currentTarget, async () => {
          await api("/api/money/subscription", { method: "POST", body: { id: x.id, choice } });
          toast(choice === "cancel" ? `Added "Cancel ${x.service}" to your to-dos` : choice === "keep" ? `Keeping ${x.service}` : "Removed");
          loadMoney();
        }) });
      return el("li", { class: "card stack" },
        el("p", {}, el("strong", { text: x.service }), document.createTextNode(` · ${gbp(x.amount)} a ${per[x.period]}`),
          x.was ? " " : null, x.was ? el("span", { class: "badge", text: `${x.amount > x.was ? "up" : "down"} from ${gbp(x.was)}` }) : null),
        el("p", { class: "meta", text: [x.next_date ? `Renews ${shortDate(x.next_date)}` : "", x.status === "keep" ? "Keeping" : x.status === "cancelling" ? "Cancelling" : ""].filter(Boolean).join(" · ") }),
        x.status === "cancelling" ? null : el("div", { class: "btn-row" }, decide("keep", "Keep"), decide("cancel", "Cancel it"), decide("not", "Not a subscription")));
    }) : [el("li", { class: "empty", text: "None found yet. They appear as payment emails arrive, or add one below." })]));
    const sp = m.spending;
    $("#spend-total").textContent = sp.count ? `${sp.month}: ${gbp(sp.total)} so far (${gbp(sp.last_month_so_far)} by this point last month)` : `${sp.month}: no receipts yet.`;
    $("#spend-changes").replaceChildren(...sp.biggest_changes.map((c) => el("li", {
      text: `${c.category[0].toUpperCase()}${c.category.slice(1)}: ${c.change > 0 ? "up" : "down"} ${gbp(Math.abs(c.change))} on last month` })));
    const top = Math.max(1, ...sp.by_category.map((c) => c.amount));
    $("#spend-cats").replaceChildren(...sp.by_category.filter((c) => c.amount > 0).map((c) => el("li", { class: "bar-row" },
      el("span", { class: "bar-label", text: c.category[0].toUpperCase() + c.category.slice(1) }),
      el("span", { class: "bar", style: `--w:${Math.round(100 * c.amount / top)}%`, "aria-hidden": "true" }),
      el("span", { class: "bar-value", text: gbp(c.amount) }))));
    $("#spend-months").textContent = `Last six months: ${sp.months.map((x) => `${x.month} ${gbp(x.total)}`).join(" · ")}`;
    loadBills();
  }
  async function loadBills() {
    let b;
    try { b = await api("/api/bills"); } catch { return; }
    const per = { monthly: "a month", quarterly: "a quarter", yearly: "a year" };
    $("#bills-total").textContent = b.items.length ? `${gbp(b.per_month)} a month altogether (${gbp(b.per_year)} a year)` : "";
    $("#bills-list").replaceChildren(...(b.items.length ? b.items.map((x) => el("li", { class: "card stack" },
      el("p", {}, el("strong", { text: x.provider }), document.createTextNode(` · ${x.kind} · ${gbp(x.amount)} ${per[x.period]}`),
        x.recent_rise ? " " : null, x.recent_rise ? el("span", { class: "badge warn", text: `up ${x.rise.pct}% from ${gbp(x.rise.from)}` }) : null),
      x.next_date ? el("p", { class: "meta", text: `Next payment ${shortDate(x.next_date)}${x.direct_debit ? " by direct debit" : ""}` }) : null,
      x.recent_rise ? el("p", { class: "hint", text: "Worth a look: a switch or a call to ask for a better price often helps." }) : null,
      ["water", "tv licence"].includes(x.kind) ? null : el("button", { type: "button", class: `btn ${x.recent_rise ? "btn-primary" : "btn-secondary"} btn-small`,
        text: "Ask for a better price", "aria-label": `Ask ${x.provider} for a better price`, "aria-haspopup": "dialog",
        onclick: (ev) => openHaggle({ for: "bill", id: x.id }, ev.currentTarget) }),
      attachments("bill", x.id, x.files, x.provider, loadBills),
      el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${x.provider}`,
        onclick: (ev) => { if (!confirm(`Stop tracking ${x.provider}?`)) return;
          busy(ev.currentTarget, async () => { await api("/api/bills/delete", { method: "POST", body: { id: x.id } }); loadBills(); }); } })))
      : [el("li", { class: "empty", text: "None yet. Bills appear as bill and direct debit emails arrive, or add one below." })]));
  }
  $("#bill-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const provider = $("#bill-provider").value.trim(), amount = parseFloat($("#bill-amount").value);
    if (!provider || !(amount > 0)) { toast("Give who it's to and how much.", true); (provider ? $("#bill-amount") : $("#bill-provider")).focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api("/api/bills", { method: "POST", body: { provider, amount, kind: $("#bill-kind").value, period: $("#bill-period").value,
        next_date: $("#bill-next").value } });
      toast(r.rise && r.rise.when === new Date().toISOString().slice(0, 10) ? `Added. That's up ${r.rise.pct}% on before.` : `Added ${provider}`);
      ev.target.reset(); loadBills();
    });
  });
  $("#subs-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const service = $("#subs-name").value.trim(), amount = parseFloat($("#subs-amount").value);
    if (!service || !(amount > 0)) { toast("Give the name and what it costs.", true); return; }
    busy(ev.submitter, async () => {
      await api("/api/money/subscription/add", { method: "POST", body: { service, amount, period: $("#subs-period").value, next_date: $("#subs-next").value } });
      ev.target.reset(); toast(`Added ${service}`); loadMoney();
    });
  });

  // ---------- Order the Shopping list online ----------
  let shopPlan = null;
  async function loadShopOrder() {
    const box = $("#shop-order");
    if (state.currentList.toLowerCase() !== "shopping" || state.listOwner !== "shared") { box.hidden = true; return; }
    try { shopPlan = await api(`/api/shop?supermarket=${encodeURIComponent(prefs.get("supermarket", ""))}`); }
    catch { box.hidden = true; return; }
    box.hidden = !(shopPlan.amazon.length + shopPlan.other.length);
    const sel = $("#shop-where");
    sel.replaceChildren(...shopPlan.retailers.filter((r) => r.id !== "amazon").map((r) => el("option", { value: r.id, text: r.name })));
    sel.value = shopPlan.supermarket.id;
    const ordered = (ids, where) => (ev) => busy(ev.currentTarget, async () => {
      const r = await api("/api/shop/ordered", { method: "POST", body: { task_ids: ids, where } });
      toast(`Ticked off ${r.done.length} item${r.done.length === 1 ? "" : "s"} and remembered for next time`);
      loadLists();
    });
    const amazon = shopPlan.amazon;
    $("#shop-amazon").replaceChildren(...(amazon.length ? [
      el("p", {}, el("strong", { text: `Amazon, one tap: ` }), document.createTextNode(amazon.map((a) => a.title).join(", "))),
      el("a", { class: "btn btn-primary btn-block", href: shopPlan.amazon_basket, target: "_blank", rel: "noopener",
        text: `Add ${amazon.length} to Amazon basket`, "aria-label": `Add ${amazon.length} items to your Amazon basket (opens Amazon)` }),
      el("button", { type: "button", class: "btn btn-secondary btn-block", text: "I've ordered these on Amazon", onclick: ordered(amazon.map((a) => a.id), "amazon") }),
    ] : []));
    const name = shopPlan.supermarket.name;
    $("#shop-other").replaceChildren(...(shopPlan.other.length ? [el("ul", { class: "plain-list shop-links" }, ...shopPlan.other.map((o) => el("li", { class: "row" },
      el("span", { class: "grow", text: o.title }),
      el("a", { href: o.search_url, target: "_blank", rel: "noopener", text: `Find at ${name}`, "aria-label": `Find ${o.title} at ${name} (opens ${name})` }),
      el("button", { type: "button", class: "linkish", text: "Usual product", "aria-haspopup": "dialog", "aria-label": `Set the usual Amazon product for ${o.title}`,
        onclick: (ev) => openProduct(o.title, ev.currentTarget) }))))] : []));
    $("#shop-all-done").onclick = ordered([...amazon, ...shopPlan.other].map((x) => x.id), shopPlan.supermarket.id);
  }
  $("#shop-where").addEventListener("change", (ev) => { prefs.set("supermarket", ev.target.value); loadShopOrder(); });
  $("#shop-copy").addEventListener("click", async () => {
    if (!shopPlan) return;
    try { await navigator.clipboard.writeText(shopPlan.copy_text); toast(`Copied ${shopPlan.copy_text.split("\n").length} items. Paste them into ${shopPlan.supermarket.name}'s list search.`); }
    catch { toast("Couldn't copy here. Long-press the items to copy them.", true); }
  });
  function openProduct(item, opener) {
    $("#product-item").value = item; $("#product-link").value = ""; $("#product-name").value = "";
    openDialog($("#product-dialog"), opener);
    $("#product-link").focus();
  }
  $("#product-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      await api("/api/products", { method: "POST", body: { item: $("#product-item").value, link: $("#product-link").value.trim(), title: $("#product-name").value } });
      $("#product-dialog").close(); toast(`Saved. ${$("#product-item").value} now goes in the Amazon basket in one tap.`); loadShopOrder();
    });
  });

  // Shopping mode: big ticks and the screen stays on while you're in the shop
  let wakeLock = null;
  async function keepAwake(on) {
    try {
      if (on && "wakeLock" in navigator && document.visibilityState === "visible") wakeLock = await navigator.wakeLock.request("screen");
      else if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
    } catch { /* not allowed here: big ticks still work */ }
  }
  function setShopMode(on) {
    const btn = $("#shop-mode");
    if (!on && !document.body.classList.contains("shop-mode")) return;
    document.body.classList.toggle("shop-mode", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "Finished shopping" : "Shopping mode: big ticks, screen stays on";
    keepAwake(on);
  }
  $("#shop-mode").addEventListener("click", () => {
    const on = !document.body.classList.contains("shop-mode");
    setShopMode(on);
    toast(on ? "Shopping mode on. The screen stays on until you tap Finished shopping." : "Shopping mode off");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && document.body.classList.contains("shop-mode")) keepAwake(true);
  });

  async function loadMeals() {
    let data;
    try { data = await api("/api/meals"); } catch (e) { return; }
    $("#favourite-meals").replaceChildren(...data.favourites.map((f) => el("option", { value: f })));
    $("#meal-fields").replaceChildren(...data.days.map((d, i) => el("div", { class: "field" },
      el("label", { for: `meal-${i}`, text: i === 0 ? `Tonight (${d.label})` : d.label }),
      el("input", { id: `meal-${i}`, type: "text", list: "favourite-meals", value: d.dinner, autocomplete: "off",
        "data-day": d.day, placeholder: "Not planned" }),
      d.ingredients.length ? el("p", { class: "hint", text: `Needs: ${d.ingredients.join(", ")}` }) : null)));
    const firstFree = data.days.find((d) => !d.dinner);
    $("#recipe-day").replaceChildren(...data.days.map((d, i) => el("option", { value: d.day,
      text: `${i === 0 ? "Tonight" : d.label}${d.dinner ? ` (now: ${d.dinner})` : ""}` })));
    if (firstFree) $("#recipe-day").value = firstFree.day;
  }
  $("#recipe-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const url = $("#recipe-url").value.trim();
    if (!/^https?:\/\//.test(url)) { toast("Paste the recipe's web link (starting https://).", true); $("#recipe-url").focus(); return; }
    busy(ev.submitter, async () => {
      $("#recipe-result").textContent = "Reading the recipe...";
      try {
        const r = await api("/api/meals/recipe", { method: "POST", body: { url, day: $("#recipe-day").value } });
        const bits = [`${r.dinner} is on ${new Date(r.day + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long" })}.`];
        bits.push(r.added.length ? `Added to Shopping: ${r.added.join(", ")}.` : "Nothing new to buy.");
        if (r.in_cupboard.length) bits.push(`In the cupboard already: ${r.in_cupboard.join(", ")}.`);
        $("#recipe-result").textContent = bits.join(" ");
        $("#recipe-url").value = "";
        loadMeals();
      } catch (e) { $("#recipe-result").textContent = ""; throw e; }
    });
  });
  $("#meals-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const meals = [...document.querySelectorAll("#meal-fields input")].map((i) => ({ day: i.dataset.day, dinner: i.value.trim() }));
    busy(ev.submitter, async () => {
      const r = await api("/api/meals", { method: "POST", body: { meals } });
      toast(r.changed ? "Dinners saved" : "No changes to save");
      loadMeals();
    });
  });
  $("#suggest-meals").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/meals/suggest");
    const inputs = [...document.querySelectorAll("#meal-fields input")];
    let n = 0;
    for (const sg of r.suggestions) {
      const input = inputs.find((i) => i.dataset.day === sg.day && !i.value.trim());
      if (!input) continue;
      input.value = sg.dinner; n++;
      let why = input.parentElement.querySelector(".why");
      if (!why) { why = el("p", { class: "hint why" }); input.after(why); }
      why.textContent = `Suggested: ${sg.why}`;
    }
    $("#suggest-result").textContent = n ? `Suggested ${n} dinner${n === 1 ? "" : "s"}. Change any you like, then Save dinners.` : "Every evening already has a dinner, or there's not enough history yet to suggest from.";
  }));
  $("#meals-shopping").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/meals/shopping", { method: "POST" });
    const bits = [r.added.length ? `Added ${r.added.length} to Shopping: ${r.added.join(", ")}.` : "Nothing new to add."];
    if (r.already_on_list.length) bits.push(`Already on the list: ${r.already_on_list.join(", ")}.`);
    if (r.unknown.length) bits.push(`Couldn't work out the ingredients for ${r.unknown.join(", ")}, so add those yourself.`);
    $("#meals-shopping-result").textContent = bits.join(" ");
    toast(r.added.length ? `Added ${r.added.length} ingredients to Shopping` : "Nothing new to add");
  }));

  // ---------- Cheapest time to run (Octopus) ----------
  async function loadEnergy() {
    let e;
    try { e = await api("/api/energy"); } catch { return; }
    const box = $("#energy-block");
    box.hidden = !e.connected;
    if (!e.connected) return;
    if (e.error) { $("#energy-now").textContent = e.error; $("#energy-list").replaceChildren(); return; }
    $("#energy-now").textContent = e.flat ? "Your tariff costs the same all day, so any time is fine." : `Now: ${e.now_p}p per kWh.`;
    $("#energy-list").replaceChildren(...e.appliances.map((a) => el("li", { class: "row" },
      el("span", { class: "grow" }, el("strong", { text: a.name }), el("span", { class: "meta", text: ` · ${a.label} · ${a.avg}p (${a.now_avg}p if now)` })),
      !a.entity ? null : a.scheduled ? el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Cancel", "aria-label": `Cancel ${a.name}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/energy/cancel", { method: "POST", body: { id: a.scheduled.id } }); loadEnergy(); }) })
        : el("button", { type: "button", class: "btn btn-primary btn-small", text: "Run it then", "aria-label": `Switch ${a.name} on at ${a.label}`,
          onclick: (ev) => busy(ev.currentTarget, async () => {
            await api("/api/energy/run", { method: "POST", body: { entity: a.entity, start: a.start } });
            toast(`${a.name} switches on at ${a.label}. Set it going with the plug off.`); loadEnergy();
          }) }))));
  }

  // ---------- Home manual ----------
  async function loadManual() {
    let m;
    try { m = await api("/api/manual"); } catch { return; }
    const sel = $("#manual-section");
    if (!sel.options.length) sel.replaceChildren(...m.sections.map((x) => el("option", { value: x, text: x })));
    const edit = (e) => {
      $("#manual-id").value = e.id || ""; $("#manual-section").value = e.section; $("#manual-name").value = e.title;
      $("#manual-details").value = e.details || ""; $("#manual-private").checked = !!e.private;
      $("#manual-add").open = true; $("#manual-details").focus();
    };
    $("#manual-list").replaceChildren(...(m.entries.length ? m.entries.map((e) => el("li", { class: "row" },
      el("span", { class: "grow" }, el("strong", { text: e.title }), el("span", { class: "meta", text: ` · ${e.section}${e.private ? " · private" : ""}` }),
        el("div", { text: e.details }), attachments("manual", e.id, e.files, e.title, loadManual)),
      el("button", { type: "button", class: "linkish", text: "Change", "aria-label": `Change ${e.title}`, onclick: () => edit(e) }),
      el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${e.title}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/manual/delete", { method: "POST", body: { id: e.id } }); loadManual(); }) })))
      : [el("li", { class: "empty", text: "Nothing yet. Start with the stopcock and the fuse box: the things someone needs in a hurry." })]));
    $("#manual-starters").replaceChildren(...m.starters.map((x) => el("button", { type: "button", class: "chip", text: `+ ${x.title}`,
      onclick: () => edit({ section: x.section, title: x.title }) })));
  }
  $("#manual-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const title = $("#manual-name").value.trim(), details = $("#manual-details").value.trim();
    if (!title || !details) { toast("Say what it is and where.", true); return; }
    busy(ev.submitter, async () => {
      const r = await api("/api/manual", { method: "POST", body: { id: $("#manual-id").value, title, details,
        section: $("#manual-section").value, private: $("#manual-private").checked } });
      ev.target.reset(); $("#manual-id").value = ""; $("#manual-add").open = false;
      toast(r.private ? `Saved ${title} (private)` : `Saved ${title}`); loadManual();
    });
  });

  // ---------- People and presents ----------
  async function loadGifts() {
    let g;
    try { g = await api("/api/people-gifts"); } catch { return; }
    $("#gifts-list").replaceChildren(...(g.people.length ? g.people.map((p) => el("li", { class: "card stack" },
      el("p", {}, el("strong", { text: p.name }), document.createTextNode(p.relation ? ` · ${p.relation}` : ""),
        p.next_birthday ? el("span", { class: "meta", text: ` · birthday ${shortDate(p.next_birthday)}` }) : null),
      p.likes ? el("p", { class: "meta", text: `Likes: ${p.likes}` }) : null,
      p.sizes ? el("p", { class: "meta", text: `Sizes: ${p.sizes}` }) : null,
      p.wishlist.length ? el("ul", { class: "list compact" }, ...p.wishlist.map((w) => el("li", {},
        w.url ? el("a", { href: w.url, target: "_blank", rel: "noopener", text: w.title }) : el("span", { text: w.title })))) : null,
      p.given.length ? el("p", { class: "meta", text: `Given: ${p.given.slice(-3).map((x) => `${x.what} (${x.year})`).join(", ")}` }) : null,
      el("div", { class: "btn-row" },
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Change", "aria-label": `Change ${p.name}`,
          onclick: () => {
            $("#gift-name").value = p.name; $("#gift-relation").value = p.relation || "";
            $("#gift-birthday").value = /^\d{4}/.test(p.birthday || "") ? p.birthday : "";
            $("#gift-likes").value = p.likes || ""; $("#gift-sizes").value = p.sizes || "";
            $("#gift-wishlist").value = p.wishlist.map((w) => `${w.title}${w.url ? " " + w.url : ""}`).join("\n");
            $("#gift-add").open = true; $("#gift-likes").focus();
          } }))))
      : [el("li", { class: "empty", text: "Add the people you buy presents for." })]));
  }
  $("#gift-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("#gift-name").value.trim();
    if (!name) { toast("Type their name.", true); $("#gift-name").focus(); return; }
    busy(ev.submitter, async () => {
      await api("/api/people-gifts", { method: "POST", body: { name, relation: $("#gift-relation").value, birthday: $("#gift-birthday").value,
        likes: $("#gift-likes").value, sizes: $("#gift-sizes").value, wishlist: $("#gift-wishlist").value } });
      ev.target.reset(); $("#gift-add").open = false; toast(`Saved ${name}`); loadGifts();
    });
  });

  // Leaving home check
  async function renderLeaving() {
    const out = $("#leaving-result");
    const r = await api("/api/home/leaving");
    if (!r.connected) { out.replaceChildren(el("p", { class: "notice", text: "Home Assistant isn't connected yet, so there's nothing to check." })); return; }
    if (r.error) { out.replaceChildren(el("p", { class: "notice", text: r.error })); return; }
    if (r.ok) { out.replaceChildren(el("p", { class: "status-ok", text: "All good: nothing open, on or unlocked. Off you go." })); return; }
    out.replaceChildren(el("ul", { class: "list compact" }, ...r.issues.map((i) => el("li", { class: "row" },
      el("span", { class: "grow", text: i.text }),
      i.fix ? el("button", { type: "button", class: "btn btn-secondary btn-small", text: i.fix.label, "aria-label": `${i.fix.label}: ${i.name}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { const f = await api("/api/home/leaving/fix", { method: "POST", body: { entity_id: i.entity_id } }); toast(f.done || "Already sorted"); renderLeaving(); }) })
        : el("span", { class: "meta", text: i.hint || "" })))));
  }
  $("#leaving-check").addEventListener("click", (ev) => busy(ev.currentTarget, renderLeaving));

  $("#plan-meals").addEventListener("click", () => {
    location.hash = "#ask";
    setTimeout(() => send("Plan dinners for the next 7 days. Keep any dinners already planned, fill the gaps around busy evenings in the calendar using our favourite meals, and add the ingredients to Shopping."), 150);
  });
  async function loadCars() {
    let c;
    try { c = await api("/api/cars"); } catch { return; }
    $("#cars-hint").textContent = c.dvla ? "MOT and tax dates come from the DVLA and are checked every week."
      : "Type the MOT and tax dates once and they go into Renewals. (With a free DVLA key in Settings, they're looked up for you.)";
    $("#car-dates").hidden = c.dvla;
    $("#cars-list").replaceChildren(...(c.cars.length ? c.cars.map((x) => el("li", { class: "row" },
      el("span", { class: "grow" }, el("strong", { text: x.name }), el("span", { class: "meta", text:
        ` · ${x.reg}${x.mot_due ? ` · MOT ${shortDate(x.mot_due)}` : ""}${x.tax_due ? ` · tax ${shortDate(x.tax_due)}` : ""}` })),
      el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${x.name}`,
        onclick: (ev) => { if (!confirm(`Remove ${x.name} and its MOT and tax reminders?`)) return;
          busy(ev.currentTarget, async () => { await api("/api/cars/delete", { method: "POST", body: { reg: x.reg } }); loadCars(); loadRenewals(); }); } })))
      : [el("li", { class: "empty", text: "No cars yet." })]));
    hook("cars", c);
  }
  $("#car-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const reg = $("#car-reg").value.trim();
    if (!reg) { toast("Type the registration.", true); $("#car-reg").focus(); return; }
    busy(ev.submitter, async () => {
      const car = await api("/api/cars", { method: "POST", body: { reg, name: $("#car-name").value, mot_due: $("#car-mot").value, tax_due: $("#car-tax").value } });
      ev.target.reset(); toast(`Added ${car.name}`); loadCars(); loadRenewals();
    });
  });
  async function loadRenewals() {
    let data;
    try { data = await api("/api/renewals"); } catch (e) { return; }
    const ul = $("#renewals-list");
    ul.replaceChildren(...(data.renewals.length ? data.renewals.map((r) => el("li", { class: "row" },
      el("span", { class: "grow" }, el("span", { text: r.name }),
        el("span", { class: "meta", text: ` · ${shortDate(r.due_date)}${r.repeat_months ? " · yearly" : ""}` }),
        attachments("renewal", r.id, r.files, r.name, loadRenewals),
        HAGGLE_WORDS.test(r.name) ? el("button", { type: "button", class: "linkish", text: "Ask for a better price", "aria-haspopup": "dialog",
          "aria-label": `Ask for a better price on ${r.name}`, onclick: (ev) => openHaggle({ for: "renewal", id: r.id, name: r.name }, ev.currentTarget) }) : null),
      el("button", { type: "button", class: "linkish", "aria-label": `Remove ${r.name}`, text: "Remove",
        onclick: (ev) => {
          if (!confirm(`Stop tracking ${r.name}?`)) return;
          busy(ev.currentTarget, async () => { await api(`/api/renewals/${r.id}/delete`, { method: "POST" }); toast(`Removed ${r.name}`); loadRenewals(); });
        } })))
      : [el("li", { class: "empty", text: "Nothing tracked yet. Add your car insurance, MOT or home insurance below." })]));
  }
  $("#renewal-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("#renewal-name").value.trim(), due = $("#renewal-date").value;
    if (!name) { toast("Say what renews, for example Car insurance.", true); $("#renewal-name").focus(); return; }
    if (!due) { toast("Choose the renewal date.", true); $("#renewal-date").focus(); return; }
    busy(ev.submitter, async () => {
      await api("/api/renewals", { method: "POST", body: { name, due_date: due, repeat_months: $("#renewal-yearly").checked ? 12 : 0 } });
      $("#renewal-name").value = ""; $("#renewal-date").value = "";
      toast(`Tracking ${name}`);
      loadRenewals();
    });
  });

  $("#list-select").addEventListener("change", (ev) => {
    const [owner, ...rest] = ev.target.value.split("|");
    state.listOwner = owner; state.currentList = rest.join("|");
    prefs.set("list", state.currentList); prefs.set("listOwner", owner);
    loadLists();
  });
  $("#task-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const title = $("#task-title").value.trim();
    if (!title) { toast("Type what to add first.", true); $("#task-title").focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api(`/api/tasks?owner=${state.listOwner}`, { method: "POST", body: { list_name: state.currentList, title, due: $("#task-due").value || null } });
      $("#task-title").value = ""; $("#task-due").value = "";
      toast(r.note || `Added ${title} to ${state.currentList}`);
      $("#task-title").focus();
      loadLists();
    });
  });

  // ---------- Home ----------
  async function loadEnergyReport() {
    let r;
    try { r = await api("/api/energy/report"); } catch { return; }
    $("#report-block").hidden = !r.available;
    if (!r.available) return;
    const vs = (a, b, words, near) => (b == null ? "" : Math.abs(a - b) < near ? " (about the same as last week)"
      : ` (${a > b ? "up" : "down"} ${words(Math.abs(a - b))} on last week)`);
    const rows = [];
    if (r.kwh != null) rows.push(`Electricity: ${r.kwh.toFixed(0)} kWh, about ${gbp(r.cost)}${vs(r.cost, r.cost_last, gbp, 0.5)}`);
    if (r.heating_hours != null) rows.push(`Heating on: ${r.heating_hours.toFixed(1)} hours${vs(r.heating_hours, r.heating_hours_last, (h) => `${h.toFixed(1)} hours`, 0.5)}`);
    $("#report-list").replaceChildren(...rows.map((t) => el("li", { text: t })));
    $("#report-tip").textContent = r.suggestion || "";
  }
  async function loadHome() {
    let data;
    try { data = await api("/api/home"); }
    catch (e) { if (e instanceof AuthError) return showLogin(); toast(e.message, true); return; }
    const warn = $("#home-warning");
    warn.hidden = !data.warning; warn.textContent = data.warning || "";
    $("#action-grid").replaceChildren(...data.actions.map((a) => el("button", { type: "button", class: "btn btn-secondary", text: a.label,
      onclick: (ev) => busy(ev.currentTarget, async () => { const r = await api(`/api/home/action/${a.id}`, { method: "POST" }); toast(`Done: ${r.label}`); loadHome(); }) })));
    loadChores();
    loadHomeDetails();
    loadEnergy();
    loadEnergyReport();
    loadSeasonal();
    loadManual();
    loadGifts();
    loadContacts();
    hook("home");
    $("#status-list").replaceChildren(...(data.states.length ? data.states.map((s) => el("li", { class: "row" },
      el("span", { class: "grow", text: s.name }), el("span", { class: "state", text: humanState(s.state) })))
      : [el("li", { class: "empty", text: "No devices to show yet." })]));
  }
  // Saving goals: money moves from what they're owed into the goal (and back if the goal is removed).
  function goalsBlock(k, pm) {
    const box = el("div", { class: "goals stack" });
    for (const g of pm.goals) {
      box.append(el("div", { class: "goal" },
        el("div", { class: "row" }, el("span", { class: "grow" }, el("strong", { text: g.name }),
          el("span", { class: "meta", text: g.done ? " · reached!" : ` · ${gbp(g.saved)} of ${gbp(g.target)}` })),
          el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${k.kid}'s goal: ${g.name}`,
            onclick: (ev) => { if (!confirm(g.saved > 0 && !g.done ? `Remove ${g.name}? The ${gbp(g.saved)} saved goes back to what ${k.kid} is owed.` : `Remove ${g.name}?`)) return;
              busy(ev.currentTarget, async () => { await api("/api/pocket-money/goal/delete", { method: "POST", body: { id: g.id } }); loadChores(); }); } })),
        el("progress", { max: "100", value: String(g.pct), "aria-label": `${g.name}: ${g.pct}% saved` }),
        !g.done && k.owed > 0 ? el("button", { type: "button", class: "btn btn-secondary btn-small",
          text: `Put ${money(Math.min(k.owed, g.left))} towards it`,
          onclick: (ev) => busy(ev.currentTarget, async () => {
            const r = await api("/api/pocket-money/save", { method: "POST", body: { id: g.id, amount: Math.min(k.owed, g.left) } });
            toast(r.done ? `${k.kid} has saved enough for ${g.name}!` : `${money(r.moved)} saved towards ${g.name}`); loadChores();
          }) }) : null));
    }
    const slug = k.kid.replace(/[^A-Za-z0-9]+/g, "-"), nameId = `goal-name-${slug}`, amtId = `goal-amt-${slug}`, alId = `allow-${slug}`;
    box.append(el("details", {}, el("summary", { text: `Pocket money and saving for ${k.kid}` }),
      el("form", { class: "stack", novalidate: true, onsubmit: (ev) => {
        ev.preventDefault();
        busy(ev.submitter, async () => {
          await api("/api/pocket-money/allowance", { method: "POST", body: { kid: k.kid, amount: parseFloat(ev.target.querySelector(`#${alId}`).value) || 0 } });
          toast("Saved. It's added every Saturday morning."); loadChores();
        });
      } }, el("div", { class: "field" }, el("label", { for: alId, text: "Weekly pocket money (£), added every Saturday" }),
        el("input", { id: alId, type: "number", min: "0", max: "50", step: "0.5", inputmode: "decimal", value: String(pm.allowance || 0) })),
        el("button", { type: "submit", class: "btn btn-secondary", text: "Save pocket money" })),
      el("form", { class: "stack", novalidate: true, onsubmit: (ev) => {
        ev.preventDefault();
        const name = ev.target.querySelector(`#${nameId}`).value.trim(), target = parseFloat(ev.target.querySelector(`#${amtId}`).value);
        if (!name || !(target > 0)) { toast("Say what it's for and how much it costs.", true); return; }
        busy(ev.submitter, async () => {
          await api("/api/pocket-money/goal", { method: "POST", body: { kid: k.kid, name, target } });
          toast(`${k.kid} is saving for ${name}`); loadChores();
        });
      } }, el("div", { class: "field" }, el("label", { for: nameId, text: "Saving up for" }), el("input", { id: nameId, type: "text", autocomplete: "off", placeholder: "For example: Lego set" })),
        el("div", { class: "field" }, el("label", { for: amtId, text: "It costs (£)" }), el("input", { id: amtId, type: "number", min: "1", step: "0.01", inputmode: "decimal" })),
        el("button", { type: "submit", class: "btn btn-secondary", text: "Add saving goal" }))));
    return box;
  }
  async function loadChores() {
    let data;
    try { data = await api("/api/chores"); } catch (e) { return; }
    const pocket = {};
    try { for (const p of (await api("/api/pocket-money")).kids) pocket[p.kid] = p; } catch { /* older server */ }
    $("#kids-block").hidden = data.kids.length === 0;
    $("#kids").replaceChildren(...data.kids.map((k) => {
      const card = el("div", { class: "kid" },
        el("div", { class: "row" }, el("h3", { class: "grow", text: k.kid }),
          el("span", { class: "owed", text: k.owed ? `${money(k.owed)} owed` : "Nothing owed" })));
      card.append(el("div", { class: "chore-grid", role: "group", "aria-label": `Chores for ${k.kid}` },
        ...k.chores.map((c) => el("button", { type: "button", class: "btn btn-secondary", "aria-label": `${k.kid}: ${c.name} done, ${money(c.value)}`,
          onclick: (ev) => busy(ev.currentTarget, async () => {
            await api("/api/chores/done", { method: "POST", body: { kid: k.kid, chore_id: c.id } });
            toast(`${k.kid}: ${c.name}${c.value ? `, ${money(c.value)} added` : ""}`);
            loadChores();
          }) }, el("span", { text: c.name }), c.value ? el("small", { text: `+${money(c.value)}` }) : null))));
      if (k.done_this_week.length) {
        card.append(el("p", { class: "meta", text: "This week" }), el("ul", { class: "done-week" },
          ...k.done_this_week.slice(-8).reverse().map((d) => el("li", {},
            el("span", { text: `${d.chore} · ${when(d.at)}${d.tablet ? " (on the tablet)" : ""}` }),
            el("button", { type: "button", class: "linkish", "aria-label": `Undo ${d.chore} for ${k.kid}`, text: "Undo",
              onclick: (ev) => busy(ev.currentTarget, async () => { await api(`/api/chores/${d.id}/undo`, { method: "POST" }); toast("Undone"); loadChores(); }) })))));
      }
      const pm = pocket[k.kid] || { goals: [], allowance: 0 };
      card.append(goalsBlock(k, pm));
      if (k.owed > 0) {
        card.append(el("button", { type: "button", class: "btn btn-primary btn-block", text: `Mark ${money(k.owed)} as paid to ${k.kid}`,
          onclick: (ev) => {
            if (!confirm(`Have you paid ${k.kid} ${money(k.owed)}?`)) return;
            busy(ev.currentTarget, async () => { await api("/api/chores/paid", { method: "POST", body: { kid: k.kid } }); toast(`Paid ${k.kid}`); loadChores(); });
          } }));
      }
      return card;
    }));
  }

  async function loadContacts() {
    let data;
    try { data = await api("/api/contacts"); } catch { return; }
    const title = (c) => (c.company ? `${c.company} (${c.name})` : c.name);
    $("#ct-list").replaceChildren(...(data.contacts.length ? data.contacts.map((c) => el("li", { class: "contact-card" },
      el("div", { class: "contact-head" }, el("strong", { text: title(c) }), c.favourite ? el("span", { class: "badge quick", text: "Go-to" }) : null),
      el("div", { class: "meta", text: [c.trade ? c.trade[0].toUpperCase() + c.trade.slice(1) : "", c.phone, c.email].filter(Boolean).join(" · ") }),
      c.last_used ? el("div", { class: "meta", text: `Last used ${new Date(c.last_used).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}` }) : null,
      el("div", { class: "contact-actions" },
        c.phone ? el("a", { class: "linkish", href: `tel:${c.phone.replace(/[^0-9+]/g, "")}`, text: "Call", "aria-label": `Call ${title(c)}` }) : null,
        c.email ? el("a", { class: "linkish", href: `mailto:${c.email}`, text: "Email", "aria-label": `Email ${title(c)}` }) : null,
        !c.favourite && c.trade ? el("button", { type: "button", class: "linkish", text: "Make go-to", "aria-label": `Make ${title(c)} your go-to ${c.trade}`,
          onclick: (ev) => busy(ev.currentTarget, async () => { await api(`/api/contacts/${c.id}/favourite`, { method: "POST" }); toast(`${title(c)} is now your go-to ${c.trade}`); loadContacts(); }) }) : null,
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${title(c)}`,
          onclick: (ev) => { if (!confirm(`Remove ${title(c)}?`)) return;
            busy(ev.currentTarget, async () => { await api(`/api/contacts/${c.id}/delete`, { method: "POST" }); loadContacts(); }); } }))))
      : [el("li", { class: "empty", text: "None yet. Add your go-to electrician, plumber and gas engineer, or Life OS saves them as you use them." })]));
  }
  $("#ct-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const v = (id) => $(id).value.trim();
    if (!v("#ct-name")) { toast("Give them a name.", true); $("#ct-name").focus(); return; }
    busy(ev.submitter, async () => {
      await api("/api/contacts", { method: "POST", body: { name: v("#ct-name"), company: v("#ct-company"), trade: v("#ct-trade"),
        email: v("#ct-email"), phone: v("#ct-phone"), favourite: $("#ct-fav").checked } });
      ["#ct-name", "#ct-company", "#ct-trade", "#ct-email", "#ct-phone"].forEach((id) => { $(id).value = ""; });
      $("#ct-fav").checked = false; toast("Saved"); loadContacts();
    });
  });

  async function loadHomeDetails() {
    let data;
    try { data = await api("/api/home-details"); } catch { return; }
    const rows = data.details;
    $("#hd-areas").replaceChildren(...[...new Set(rows.map((r) => r.area))].map((a) => el("option", { value: a })));
    $("#hd-list").replaceChildren(...(rows.length ? rows.map((r) => el("li", { class: "row" },
      el("div", { class: "grow" }, el("strong", { text: `${r.area} · ${r.item}` }), el("div", { class: "meta", text: r.details })),
      el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${r.area} ${r.item}`,
        onclick: (ev) => { if (!confirm(`Remove ${r.area} ${r.item}?`)) return;
          busy(ev.currentTarget, async () => { await api(`/api/home-details/${r.id}/delete`, { method: "POST" }); loadHomeDetails(); }); } })))
      : [el("li", { class: "empty", text: "Nothing yet. Life OS fills this in as you answer its questions, or add things below." })]));
  }
  $("#hd-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const area = $("#hd-area").value.trim(), item = $("#hd-item").value.trim(), details = $("#hd-details").value.trim();
    if (!area || !item || !details) { toast("Fill in where, what and the details.", true); return; }
    busy(ev.submitter, async () => {
      await api("/api/home-details", { method: "POST", body: { area, item, details } });
      $("#hd-item").value = ""; $("#hd-details").value = "";
      toast(`Saved: ${area} · ${item}`); loadHomeDetails();
    });
  });

  const humanState = (s) => ({ on: "On", off: "Off", locked: "Locked", unlocked: "Unlocked", open: "Open", closed: "Closed",
    docked: "Docked", cleaning: "Cleaning", unavailable: "Not responding", home: "Home", not_home: "Away" }[s] || s);

  // ---------- Settings ----------
  async function loadSettings() {
    $("#signed-in-as").textContent = `Signed in as ${state.me.name}.`;
    $("#app-version").textContent = state.me.version ? `Life OS version ${state.me.version}` : "";
    const probs = (state.me && state.me.problems) || [];
    $("#setup-problems").hidden = probs.length === 0;
    $("#setup-list").replaceChildren(...probs.map((p) => el("li", { text: p })));
    const size = prefs.get("textsize", "normal");
    $(`#ts-${size === "xl" ? "xl" : size === "large" ? "large" : "normal"}`).checked = true;
    $("#opt-contrast").checked = prefs.get("contrast", false);
    $("#opt-speak").checked = prefs.get("speak", false);
    renderNotify().catch(() => {});
    renderBridge().catch(() => {});
    renderSystem().catch(() => {});
    renderVoice().catch(() => {});
    renderSetupAll().catch(() => { $("#setup-all").hidden = true; });
    renderBackups().catch(() => {});
    hook("settings");
    try {
      const p = await api("/api/provider");
      const radio = $(`#ai-${p.provider}`);
      if (radio) radio.checked = true;  // the demo's stand-in assistant has no switch
      setBadge(p.provider);
      const acc = await api("/api/accounts");
      renderAccounts(acc);
    } catch (e) { if (e instanceof AuthError) return showLogin(); toast(e.message, true); }
  }

  // ---------- v0.17: haggling ----------
  const HAGGLE_WORDS = /insurance|broadband|mobile|phone|energy|breakdown|contents|buildings/i;
  let haggleReq = null;
  function showHaggle(h) {
    const body = $("#haggle-body");
    body.replaceChildren(...[
      el("p", { class: "lead", text: h.price_line + (h.yearly_extra ? `. That's about £${h.yearly_extra} more a year.` : ".") }),
      h.note ? el("p", { class: "notice", text: h.note }) : null,
      h.script.length ? el("h3", { text: "On the phone" }) : null,
      h.script.length ? el("ol", { class: "steps-plain" }, h.script.map((line) => el("li", { text: line }))) : null,
      h.compare.length ? el("h3", { text: "Compare first" }) : null,
      h.compare.length ? el("ul", { class: "list compact" }, h.compare.map((c) => el("li", {},
        el("a", { href: c.url, target: "_blank", rel: "noopener", text: c.name })))) : null].filter(Boolean));
    $("#haggle-actions").hidden = !h.email && !h.script.length;
    $("#haggle-draft").hidden = !h.email;
    $("#haggle-todo").hidden = !h.script.length;
  }
  async function openHaggle(req, opener) {
    haggleReq = req;
    $("#haggle-title").textContent = req.name ? `Ask for a better price: ${req.name}` : "Ask for a better price";
    $("#haggle-body").replaceChildren();
    $("#haggle-actions").hidden = true;
    $("#haggle-ask").hidden = req.for !== "renewal";
    openDialog($("#haggle-dialog"), opener);
    if (req.for === "renewal") { $("#haggle-quoted").focus(); return; }
    try { showHaggle(await api("/api/haggle", { method: "POST", body: req })); } catch (e) { toast(e.message, true); }
  }
  $("#haggle-ask").addEventListener("submit", (ev) => {
    ev.preventDefault();
    haggleReq = { ...haggleReq, quoted: parseFloat($("#haggle-quoted").value) || 0, last_year: parseFloat($("#haggle-last").value) || 0 };
    busy(ev.submitter, async () => showHaggle(await api("/api/haggle", { method: "POST", body: haggleReq })));
  });
  $("#haggle-draft").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/haggle/draft", { method: "POST", body: haggleReq });
    toast("The email is in your Gmail drafts. Add their address and send it from Gmail.");
  }));
  $("#haggle-todo").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/haggle/todo", { method: "POST", body: haggleReq });
    toast(`Added "${r.title}" with what to say`);
  }));

  // ---------- v0.17: away mode on a trip ----------
  function awayToggle(x) {
    const id = `away-${x.id}`;
    return el("div", { class: "stack away" },
      el("div", { class: "check-row" },
        el("input", { type: "checkbox", id, checked: !!x.away, "aria-describedby": `${id}-hint`,
          onchange: (ev) => {
            const on = ev.currentTarget.checked, box = ev.currentTarget;
            api("/api/trips/away", { method: "POST", body: { id: x.id, on } })
              .then(() => toast(on ? "Away mode is on for this trip" : "Away mode is off"))
              .catch((e) => { box.checked = !on; if (e instanceof AuthError) showLogin(); else toast(e.message, true); });
          } }),
        el("label", { for: id, text: "Away mode while we're gone" })),
      el("p", { class: "hint", id: `${id}-hint`, text: "Heating down to frost protection, chosen lights on at dusk, a house check each evening, and the heating back up on the day you come home." }));
  }

  // ---------- v0.17: seasonal jobs ----------
  async function loadSeasonal() {
    let o;
    try { o = await api("/api/seasonal"); } catch { return; }
    $("#seasonal-block").hidden = false;
    $("#seasonal-month").textContent = `${o.month}'s house jobs`;
    $("#seasonal-now").replaceChildren(...(o.now.length ? o.now.map((j) => el("li", { class: "row" },
      el("span", { class: "grow", text: j.title + (j.off ? " (not for us)" : "") }),
      j.off ? el("button", { type: "button", class: "linkish", text: "It does apply", "aria-label": `${j.title}: it does apply`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/seasonal/off", { method: "POST", body: { id: j.id, off: false } }); loadSeasonal(); }) })
        : j.added ? el("span", { class: "meta", text: "On the list" })
          : el("span", { class: "btn-row" },
            el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Add", "aria-label": `Add to to-dos: ${j.title}`,
              onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/seasonal/add", { method: "POST", body: { id: j.id } }); toast(`Added: ${j.title}`); loadSeasonal(); }) }),
            el("button", { type: "button", class: "linkish", text: "Not for us", "aria-label": `${j.title}: doesn't apply to us`,
              onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/seasonal/off", { method: "POST", body: { id: j.id, off: true } }); loadSeasonal(); }) }))))
      : [el("li", { class: "empty", text: "Nothing this month." })]));
    $("#seasonal-next").textContent = o.next.length ? `Coming in ${o.next_month}: ${o.next.map((j) => j.title.toLowerCase()).join("; ")}.` : "";
    $("#seasonal-things").replaceChildren(...o.things.map((t) => {
      const id = `have-${t.name.replace(/\W+/g, "-")}`;
      return el("div", { class: "check-row" }, el("input", { type: "checkbox", id, checked: t.have,
        onchange: (ev) => api("/api/seasonal/have", { method: "POST", body: { thing: t.name, have: ev.currentTarget.checked } }).then(loadSeasonal).catch((e) => toast(e.message, true)) }),
        el("label", { for: id, text: t.name[0].toUpperCase() + t.name.slice(1) }));
    }));
  }

  // ---------- v0.17: the Sunday agenda ----------
  let agendaText = "";
  async function openAgenda(opener) {
    const d = $("#agenda-dialog");
    $("#agenda-body").replaceChildren(el("p", { class: "meta", text: "Putting it together..." }));
    openDialog(d, opener);
    try {
      const a = await api("/api/agenda");
      agendaText = a.text;
      $("#agenda-title").textContent = `Sunday agenda: ${a.title}`;
      $("#agenda-made").textContent = `Made ${a.made}`;
      $("#agenda-body").replaceChildren(...(a.sections.length ? a.sections.map((sec) => el("section", { class: "agenda-sec" },
        el("h3", { text: sec.title }),
        el("ul", { class: "list compact" }, sec.items.map((i) => el("li", { class: i.strong ? "strong" : "" },
          i.link ? el("a", { href: i.link, text: i.text }) : el("span", { text: i.text }))))))
        : [el("p", { text: "A quiet week: nothing to decide." })]));
    } catch (e) { $("#agenda-body").replaceChildren(el("p", { class: "notice", text: e.message })); }
  }
  $("#agenda-open").addEventListener("click", (ev) => openAgenda(ev.currentTarget));
  $("#agenda-print").addEventListener("click", () => window.print());
  $("#agenda-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(agendaText); toast("Copied"); } catch { toast("Couldn't copy on this phone.", true); }
  });

  // ---------- v0.17: parties ----------
  async function loadParties() {
    let r;
    try { r = await api("/api/parties"); } catch { return; }
    $("#party-kids").replaceChildren(...r.kids.map((k) => el("option", { value: k })));
    $("#parties-list").replaceChildren(...(r.parties.length ? r.parties.map(partyCard)
      : [el("li", { class: "empty", text: "No parties planned. Add one below for the jobs, invites and who's coming." })]));
  }
  const ANSWERS = [["yes", "Coming"], ["maybe", "Maybe"], ["no", "Can't come"]];
  function partyCard(p) {
    const call = (path, body, done) => (ev) => busy(ev.currentTarget, async () => { const res = await api(path, { method: "POST", body }); if (done) done(res); loadParties(); });
    const gname = `guest-name-${p.id}`, gmail = `guest-email-${p.id}`;
    return el("li", { class: "card stack party" },
      el("div", { class: "row" }, el("strong", { class: "grow", text: `${p.name} party` }),
        el("span", { class: "badge", text: p.days_to_go > 0 ? `${p.days_to_go} days to go` : p.days_to_go === 0 ? "Today!" : "Done" })),
      el("p", { class: "meta", text: [new Date(p.date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }),
        p.start && `${p.start}${p.end ? ` to ${p.end}` : ""}`, p.venue, p.theme && `Theme: ${p.theme}`].filter(Boolean).join(" · ") }),
      el("p", { text: `${p.yes} coming, ${p.maybe} maybe, ${p.no} can't, ${p.no_reply} not answered` }),
      el("ul", { class: "list compact", "aria-label": `Guests for ${p.name}` }, p.guests.map((g) => el("li", { class: "row guest" },
        el("span", { class: "grow" }, el("span", { text: g.name }), el("span", { class: "meta", text: g.email ? ` · ${g.email}` : "" }),
          g.rsvp ? el("span", { class: "meta", text: ` · ${ANSWERS.find((a) => a[0] === g.rsvp)[1]}` }) : null),
        el("span", { class: "btn-row", role: "group", "aria-label": `${g.name}'s answer` }, ANSWERS.map(([v, label]) =>
          el("button", { type: "button", class: "btn btn-secondary btn-small", "aria-pressed": String(g.rsvp === v), text: label,
            "aria-label": `${g.name}: ${label}`, onclick: call("/api/parties/rsvp", { id: p.id, name: g.name, answer: g.rsvp === v ? "" : v }) }))),
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${g.name}`,
          onclick: call("/api/parties/guest/remove", { id: p.id, name: g.name }) })))),
      el("form", { class: "stack", novalidate: true, onsubmit: (ev) => {
        ev.preventDefault();
        const name = ev.target.querySelector(`#${gname}`).value.trim(), email = ev.target.querySelector(`#${gmail}`).value.trim();
        if (!name) { toast("Give the guest's name.", true); return; }
        busy(ev.submitter, async () => { await api("/api/parties/guest", { method: "POST", body: { id: p.id, name, email } }); loadParties(); });
      } }, el("div", { class: "row two" },
        el("div", { class: "field" }, el("label", { for: gname, text: "Guest" }), el("input", { id: gname, type: "text", autocomplete: "off" })),
        el("div", { class: "field" }, el("label", { for: gmail, text: "Parent's email (optional)" }), el("input", { id: gmail, type: "email", autocomplete: "off" }))),
        el("button", { type: "submit", class: "btn btn-secondary", text: "Add guest" })),
      el("div", { class: "btn-row" },
        el("button", { type: "button", class: "btn btn-primary", text: "Make the invites", "aria-label": `Make the invites for ${p.name}`,
          onclick: call("/api/parties/invites", { id: p.id }, (res) => toast(res.made.length ? `${res.made.length} invite${res.made.length === 1 ? " is" : "s are"} in your Gmail drafts. Send them from Gmail.`
            : res.without_email.length ? "No email addresses: copy the invite text below for WhatsApp." : "Invites already made.")) }),
        el("button", { type: "button", class: "btn btn-secondary", text: "Food to Shopping", "aria-label": `Add party food for ${p.name} to Shopping`,
          onclick: call("/api/parties/food", { id: p.id }, (res) => toast(`Added for ${res.for}: ${res.added.join(", ") || "already on the list"}`)) })),
      el("details", {}, el("summary", { text: "Invite text for WhatsApp" }),
        el("pre", { class: "invite", text: p.invite }),
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Copy",
          onclick: async () => { try { await navigator.clipboard.writeText(p.invite); toast("Copied"); } catch { toast("Couldn't copy on this phone.", true); } } })),
      el("button", { type: "button", class: "linkish", text: "Remove party", "aria-label": `Remove ${p.name} party`,
        onclick: (ev) => { if (!confirm(`Remove ${p.name} party and its calendar entry? The to-dos stay.`)) return; call("/api/parties/delete", { id: p.id })(ev); } }));
  }
  $("#party-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const child = $("#party-child").value.trim(), date = $("#party-date").value;
    if (!child || !date) { toast("Say whose party it is and the date.", true); (child ? $("#party-date") : $("#party-child")).focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api("/api/parties", { method: "POST", body: { child, date, age: $("#party-age").value, start: $("#party-start").value,
        end: $("#party-end").value, venue: $("#party-venue").value, theme: $("#party-theme").value } });
      toast(`Planned. The party-day checklist is "${r.list}" in Google Tasks.`);
      ev.target.reset(); $("#party-add").open = false; loadParties();
    });
  });

  // ---------- v0.17: check everything and backups ----------
  const STATUS_WORD = { ok: "OK", warn: "Worth a look", fail: "Fix this" };
  $("#check-run").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    $("#check-summary").textContent = "Checking...";
    const r = await api("/api/system/check", { method: "POST", body: { ai: $("#check-ai").checked } });
    $("#check-summary").textContent = r.summary;
    const order = { fail: 0, warn: 1, ok: 2 };
    $("#check-results").replaceChildren(...[...r.results].sort((a, b) => order[a.status] - order[b.status]).map((x) => el("li", { class: `check ${x.status}` },
      el("span", { class: "badge", text: STATUS_WORD[x.status] }), " ",
      el("strong", { text: x.section }), el("span", { text: `: ${x.text} ` }),
      x.status === "ok" ? null : el("a", { href: x.link, text: "Go there", "aria-label": `Go to fix: ${x.section}` }))));
  }));
  async function renderBackups() {
    const b = await api("/api/system/backup");
    const last = b.last || {};
    $("#backup-folder").value = b.folder || "";
    $("#backup-status").textContent = !b.folder ? "Not set up yet."
      : !b.has_passphrase ? "Set a passphrase to start."
        : !last.when ? "Set up. The first copy is made tonight, or tap Back up now."
          : last.ok ? `Last copy ${last.when}: ${last.detail}.` : `The last copy didn't work (${last.when}): ${last.detail}`;
    $("#backup-status").classList.toggle("notice", !!last.when && !last.ok);
    $("#backup-now").disabled = !b.folder || !b.has_passphrase;
  }
  $("#backup-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      await api("/api/system/backup", { method: "POST", body: { folder: $("#backup-folder").value.trim(), passphrase: $("#backup-pass").value } });
      $("#backup-pass").value = "";
      toast("Saved"); renderBackups();
    });
  });
  $("#backup-now").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    $("#backup-status").textContent = "Backing up and checking...";
    const r = await api("/api/system/backup/run", { method: "POST" });
    toast(r.ok ? "Backed up and checked" : `Backup didn't work: ${r.detail}`, !r.ok);
    renderBackups();
  }));
  $("#restore-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const f = $("#restore-file").files[0], pass = $("#restore-pass").value;
    if (!f || !pass) { toast("Choose the backup file and type its passphrase.", true); return; }
    if (!confirm("Replace everything in Life OS with this backup? What's here now is kept in a before-restore folder.")) return;
    busy(ev.submitter, async () => {
      await api("/api/system/backup/restore", { method: "POST", body: f, headers: { "Content-Type": "application/octet-stream", "X-Backup-Passphrase": pass } });
      $("#restore-pass").value = "";
      toast("Backup checked. Restarting Life OS to put it in place...");
      await api("/api/system/restart", { method: "POST" }).catch(() => {});
    });
  });

  // ---------- Voice and speakers ----------
  async function renderVoice() {
    const v = await api("/api/voice");
    $("#voice-ha").hidden = v.ha;
    const sel = $("#voice-speaker");
    sel.replaceChildren(el("option", { value: "", text: v.speakers.length ? "Choose a speaker" : "No speakers found yet" }),
      ...v.speakers.map((x) => el("option", { value: x.id, text: x.name })));
    if (v.speaker && !v.speakers.some((x) => x.id === v.speaker)) sel.append(el("option", { value: v.speaker, text: v.speaker }));
    sel.value = v.speaker || "";
    $("#voice-tts").value = v.tts_entity || "";
    $("#voice-routines").replaceChildren(...v.routines.map((r) => el("li", {},
      el("strong", { text: `"Hey Google, ${r.phrase}"` }), el("span", { class: "meta", text: ` · scene: ${r.scene}` }))));
    $("#voice-package").value = v.package;
    $("#voice-sentences").value = v.sentences;
    $("#voice-sentences-all").value = v.sentences_all || "";
  }
  $("#voice-save").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/voice/settings", { method: "POST", body: { speaker: $("#voice-speaker").value, tts_entity: $("#voice-tts").value.trim() } });
    toast("Saved"); renderVoice();
  }));
  $("#voice-test").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/voice/test", { method: "POST" }); toast("Listen to the speaker");
  }));
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const t = document.getElementById(b.dataset.copy);
    try { await navigator.clipboard.writeText(t.value); toast("Copied"); } catch { t.select(); toast("Selected: copy it with your keyboard"); }
  }));

  // ---------- Updates and this computer ----------
  function dateTime(secs) {
    return new Date(secs * 1000).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  }
  // After an update or a settings change Life OS restarts: wait for it, then load the new version.
  async function waitForRestart(fromVersion, out) {
    out.replaceChildren(el("p", { class: "meta", text: "Restarting Life OS. This takes a few seconds…" }));
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      try {
        const h = await (await fetch("/health", { cache: "no-store" })).json();
        if (h.version && (h.version !== fromVersion || i > 6)) { location.reload(); return; }
      } catch { /* still restarting */ }
    }
    out.replaceChildren(el("p", { class: "notice", text: "Life OS is taking a while to come back. Check the computer it runs on." }));
  }
  function renderUpdate(u) {
    const out = $("#sys-update");
    const rows = [];
    if (u.rolled_back && u.rolled_back.length && prefs.get("ackRollback", "") !== u.rolled_back[0]) {
      rows.push(el("p", { class: "notice", text: `Life OS ${u.rolled_back[0]} didn't start properly, so it went back to ${u.current} by itself.` }));
      prefs.set("ackRollback", u.rolled_back[0]);
    }
    if (u.available) {
      rows.push(el("p", {}, el("strong", { text: `Life OS ${u.latest} is ready.` })));
      if (u.notes) rows.push(el("p", { class: "meta", text: u.notes }));
      rows.push(el("button", { type: "button", class: "btn btn-primary btn-block", text: `Update to ${u.latest}`,
        onclick: (ev) => busy(ev.currentTarget, async () => {
          const r = await api("/api/system/update/install", { method: "POST" });
          if (r.restart) await waitForRestart(u.current, out);
          else out.replaceChildren(el("p", { class: "notice", text: `Installed ${r.version}. ${r.detail || ""}` }));
        }) }));
    } else if (u.needs_new_app) {
      rows.push(el("p", { text: `Life OS ${u.latest} needs the new app download. Your settings and history carry over.` }));
      if (u.app_url) rows.push(el("a", { class: "btn btn-primary btn-block", href: u.app_url, target: "_blank", rel: "noopener", text: "Download the new app" }));
    } else if (u.feed) {
      rows.push(el("p", { class: "meta", text: `You have the latest version.${u.checked_at ? ` Checked ${dateTime(u.checked_at)}.` : ""}` }));
    } else {
      rows.push(el("p", { class: "meta", text: "Updates come as a file for now: install it below when you get one." }));
    }
    if (u.error) rows.push(el("p", { class: "meta", text: u.error }));
    if (u.feed && !u.available) rows.push(el("button", { type: "button", class: "btn btn-secondary", text: "Check for updates",
      onclick: (ev) => busy(ev.currentTarget, async () => { renderUpdate(await api("/api/system/update/check", { method: "POST" })); }) }));
    out.replaceChildren(...rows);
    $("#sys-auto").checked = !!u.auto;
    $("#sys-auto").closest(".check-row").hidden = !u.feed;
  }
  async function renderSystem() {
    const sys = await api("/api/system");
    $("#sys-version").replaceChildren(el("strong", { text: `Life OS ${sys.version}` }),
      document.createTextNode(sys.demo ? " (sample household)" : sys.app_download ? "" : " (running from a source folder)"));
    renderUpdate(sys.updates);
    $("#sys-home").textContent = `Settings and history are kept in ${sys.home}`;
    $("#sys-autostart-row").hidden = !(sys.autostart.supported && sys.local);
    $("#sys-autostart").checked = sys.autostart.on;
    $("#sys-quit").hidden = !(sys.local && sys.managed);
    $("#sys-restart").hidden = !sys.managed;
    $("#sys-leave-demo").hidden = !sys.demo;
    const said = (on, what) => on ? `${what} is saved.` : `No ${what} yet.`;
    $("#k-anthropic-state").textContent = said(sys.keys.anthropic, "Claude key");
    $("#k-gemini-state").textContent = said(sys.keys.gemini, "Gemini key");
    $("#k-ha-state").textContent = said(sys.keys.ha, "Home Assistant token");
    $("#k-url-state").textContent = `Now: ${sys.public_url}`;
    $("#k-gemini-model-state").textContent = sys.gemini_model ? `Now: ${sys.gemini_model}. If Gemini says the model isn't free or doesn't exist, choose another.` : "";
    $("#gemini-models").replaceChildren(...(sys.gemini_models || []).map((m) => el("option", { value: m })));
    $("#k-mot-state").textContent = sys.keys.mot ? "The DVSA keys are saved. MOT histories come in by themselves."
      : "For MOT advisories and the mileage at each MOT. Apply free at documentation.history.mot.api.gov.uk; DVSA emails all four.";
    state.sysVersion = sys.version;
  }
  $("#sys-auto").addEventListener("change", (ev) => busy(null, async () => {
    const r = await api("/api/system/update/auto", { method: "POST", body: { on: ev.target.checked } });
    toast(r.auto ? "Updates will install overnight by themselves" : "Updates will wait for you to tap Update");
  }));
  $("#sys-autostart").addEventListener("change", (ev) => busy(null, async () => {
    const r = await api("/api/system/autostart", { method: "POST", body: { on: ev.target.checked } });
    ev.target.checked = r.on;
    toast(r.on ? "Life OS will start with this computer" : "Life OS won't start by itself");
  }));
  $("#sys-file").addEventListener("change", (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    busy(null, async () => {
      const out = $("#sys-update");
      out.replaceChildren(el("p", { class: "meta", text: `Checking ${f.name}…` }));
      try {
        const r = await api("/api/system/update/file", { method: "POST", body: f, headers: { "Content-Type": "application/zip" } });
        toast(`Installing Life OS ${r.version}`);
        if (r.restart) await waitForRestart(state.sysVersion, out);
        else out.replaceChildren(el("p", { class: "notice", text: `Installed ${r.version}. ${r.detail || ""}` }));
      } catch (e) { out.replaceChildren(el("p", { class: "notice", text: e.message })); throw e; }
      finally { ev.target.value = ""; }
    });
  });
  $("#sys-keys-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      const r = await api("/api/system/keys", { method: "POST", body: {
        anthropic_api_key: $("#k-anthropic").value, gemini_api_key: $("#k-gemini").value, public_url: $("#k-url").value,
        ha_url: $("#k-ha-url").value, ha_token: $("#k-ha-token").value, dvla_api_key: $("#k-dvla").value, octopus_tariff: $("#k-octopus").value,
        gemini_model: $("#k-gemini-model").value.trim(), mot_client_id: $("#k-mot-id").value.trim(), mot_client_secret: $("#k-mot-secret").value,
        mot_api_key: $("#k-mot-key").value, mot_token_url: $("#k-mot-url").value.trim() } });
      ev.target.reset();
      toast(`Saved: ${r.changed.length} change${r.changed.length === 1 ? "" : "s"}`);
      if (r.restart) await waitForRestart(state.sysVersion, $("#sys-update"));
    });
  });
  $("#sys-restart").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/system/restart", { method: "POST" });
    if (r.restart) await waitForRestart(state.sysVersion, $("#sys-update")); else toast(r.detail);
  }));
  $("#sys-quit").addEventListener("click", (ev) => {
    if (!confirm("Quit Life OS on this computer? Phones can't reach it until it's opened again.")) return;
    busy(ev.currentTarget, async () => {
      await api("/api/system/quit", { method: "POST" });
      document.body.replaceChildren(el("main", { class: "view" }, el("h1", { text: "Life OS has stopped" }),
        el("p", { text: "Open it again from your Applications or Start menu." })));
    });
  });
  $("#sys-leave-demo").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/system/leave-demo", { method: "POST" });
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { const h = await (await fetch("/health", { cache: "no-store" })).json(); if (h.setup) { location.href = "/setup"; return; } } catch { /* restarting */ }
    }
  }));

  function ago(iso) {
    if (!iso) return "never";
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
    const hrs = Math.round(mins / 60);
    return hrs < 24 ? `${hrs} hour${hrs === 1 ? "" : "s"} ago` : new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  }
  function showBridge(b) {
    const parts = [];
    if (!b.connected) parts.push("Not linked yet: connect a Google account below.");
    else {
      parts.push(b.pick_up_todos ? "On." : "Picking up Gemini to-dos is off (gemini.pick_up_todos in settings).");
      parts.push(`Last checked ${ago(b.last && b.last.at)}.`);
      if (b.last && b.last.errors && b.last.errors.length) parts.push(`Problem last time: ${b.last.errors[0]}`);
    }
    $("#gemini-status").textContent = parts.join(" ");
    $("#gemini-recent").replaceChildren(...(b.recent || []).slice(0, 5).map((r) =>
      el("li", { class: "row" }, el("span", { class: "grow", text: r.text }), el("span", { class: "meta", text: ago(r.at) }))));
    $("#gemini-run").hidden = !b.connected;
  }
  async function renderBridge() { showBridge(await api("/api/bridge")); }

  const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const b64ToBytes = (b64) => {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  };
  async function currentSub() {
    if (!pushSupported) return null;
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }
  async function renderNotify() {
    const status = $("#notify-status"), on = $("#notify-on"), test = $("#notify-test"), off = $("#notify-off");
    if (!pushSupported) {
      status.textContent = "This browser can't show notifications. Install the app from Chrome first.";
      on.hidden = test.hidden = off.hidden = true; return;
    }
    if (Notification.permission === "denied") {
      status.textContent = "Notifications are blocked for Life OS. Allow them in Android Settings > Apps > Life OS > Notifications.";
      on.hidden = test.hidden = off.hidden = true; return;
    }
    const sub = await currentSub();
    status.textContent = sub ? "On for this phone." : "Off for this phone.";
    on.hidden = !!sub; test.hidden = !sub; off.hidden = !sub;
  }
  $("#gemini-run").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const b = await api("/api/bridge/run", { method: "POST" });
    showBridge(b);
    const r = b.result || {}, n = (r.picked_up || 0) + (r.drafts || 0) + (r.ticks || 0);
    toast(n ? `Checked: ${r.picked_up || 0} new to-do${r.picked_up === 1 ? "" : "s"}, ${(r.drafts || 0) + (r.ticks || 0)} other update${(r.drafts || 0) + (r.ticks || 0) === 1 ? "" : "s"}` : "Checked: nothing new");
  }));
  $("#notify-on").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { toast("Notifications weren't allowed.", true); return renderNotify(); }
    const { key } = await api("/api/push/key");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
    await api("/api/push/subscribe", { method: "POST", body: sub.toJSON() });
    toast("Notifications are on");
    renderNotify();
  }));
  $("#notify-test").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/push/test", { method: "POST" }); toast("Test sent. It should arrive in a few seconds.");
  }));
  $("#notify-off").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const sub = await currentSub();
    if (sub) { await api("/api/push/unsubscribe", { method: "POST", body: sub.toJSON() }); await sub.unsubscribe(); }
    toast("Notifications are off for this phone"); renderNotify();
  }));

  function renderGoogleChecklist(acc) {
    const steps = [];
    const step = (done, text, extra) => steps.push(el("li", { class: done === null ? "tip" : done ? "is-done" : "is-todo" },
      el("span", { class: "step-state", text: done === null ? "Tip" : done ? "Done" : "To do" }),
      el("div", { class: "grow" }, el("span", { text }), extra || null)));
    step(acc.client_ready, "Google sign-in file saved on the Life OS computer as secrets/client_secret.json",
      acc.client_ready ? null : el("p", { class: "hint", text: "Create it in Google Cloud (README, step 4): a Web application client." }));
    const local = acc.redirect_uri.startsWith("http://localhost");
    step(null, "In Google Cloud, this address must be listed under Authorised redirect URIs:", el("div", { class: "copy-row" },
      el("code", { text: acc.redirect_uri }),
      el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Copy", "aria-label": "Copy the redirect address",
        onclick: async () => { try { await navigator.clipboard.writeText(acc.redirect_uri); toast("Copied"); } catch { toast("Couldn't copy. Select the address and copy it.", true); } } }),
      local ? el("p", { class: "hint", text: "This address only works on this computer. Phones need the Tailscale https address too." }) : null));
    step(null, "In Google Cloud, switch on the Google Tasks API, Google Calendar API and Gmail API (APIs and Services, Library, then Enable on each), and add each person's Gmail under Google Auth Platform, Audience, Test users.");
    for (const a of acc.accounts) step(a.connected, `${a.name}'s Google account connected${a.connected ? (a.gmail ? " (with email)" : " (calendar and tasks)") : ""}`);
    if (acc.accounts.some((a) => a.connected)) step(null, "Once you're both connected, publish the app in Google Auth Platform (Audience, Publish app), then connect once more each. Google then stops asking every 7 days.");
    $("#google-checklist").replaceChildren(...steps);
  }

  function renderAccounts(acc) {
    renderGoogleChecklist(acc);
    const box = $("#accounts");
    if (!acc.client_ready) {
      box.replaceChildren();
      return;
    }
    box.replaceChildren(...acc.accounts.map((a) => {
      const isMe = state.me && a.person === state.me.person;
      const gmailId = `gmail-${a.person}`;
      const status = a.connected
        ? el("p", {}, el("span", { class: "status-ok", text: "Connected" }), document.createTextNode(a.gmail ? " · calendar, tasks and email" : " · calendar and tasks (no email)"))
        : el("p", {}, el("span", { class: "status-no", text: "Not connected" }));
      const gmailBox = el("div", { class: "check-row" },
        el("input", { type: "checkbox", id: gmailId, checked: a.connected ? a.gmail : true }),
        el("label", { for: gmailId, text: "Also allow email (read and draft only)" }));
      const connect = el("a", { class: "btn btn-primary btn-block", href: "#", text: a.connected ? `Reconnect ${a.name}` : `Connect ${a.name}'s Google account`,
        onclick: (ev) => {
          ev.preventDefault();
          if (!isMe && !confirm(`${a.name} will need to sign in with their own Google account on the next screen. Continue?`)) return;
          location.href = `/auth/google/start?person=${encodeURIComponent(a.person)}&gmail=${$("#" + gmailId).checked}`;
        } });
      const actions = el("div", { class: "actions" }, gmailBox, connect);
      if (a.connected) {
        actions.append(el("button", { type: "button", class: "btn btn-danger btn-block", text: `Disconnect ${a.name}`,
          onclick: (ev) => {
            if (!confirm(`Disconnect ${a.name}'s Google account from Life OS?`)) return;
            busy(ev.currentTarget, async () => { await api(`/api/accounts/${a.person}/disconnect`, { method: "POST" }); toast(`${a.name} disconnected`); loadSettings(); });
          } }));
      }
      if (!isMe && !a.connected) actions.prepend(el("p", { class: "hint", text: `Best done on ${a.name}'s own phone, signed in to Life OS as ${a.name}.` }));
      return el("div", { class: "account" }, el("h3", { text: a.name + (isMe ? " (you)" : "") }), status, actions);
    }));
  }

  function handleGoogleReturn(q) {
    const result = q.get("google");
    if (!result) return;
    const who = (state.people.find((p) => p.id === q.get("person")) || {}).name || "Account";
    if (result === "connected") toast(`${who}'s Google account is connected`);
    else if (result === "cancelled") toast("Google sign-in was cancelled.", true);
    else toast("Google sign-in didn't work. Try again.", true);
    history.replaceState(null, "", "#settings");
  }

  document.querySelectorAll("input[name=ai]").forEach((r) => r.addEventListener("change", async (ev) => {
    const name = ev.target.value;
    try { await api("/api/provider", { method: "POST", body: { provider: name } }); setBadge(name); toast(`Now using ${cap(name)}`); }
    catch (e) { toast(e.message, true); loadSettings(); }
  }));
  document.querySelectorAll("input[name=textsize]").forEach((r) => r.addEventListener("change", (ev) => {
    prefs.set("textsize", ev.target.value); applyDisplay(); toast("Text size changed");
  }));
  $("#opt-contrast").addEventListener("change", (ev) => { prefs.set("contrast", ev.target.checked); applyDisplay(); });
  $("#opt-speak").addEventListener("change", (ev) => {
    prefs.set("speak", ev.target.checked);
    if (ev.target.checked) speak("Replies will be read aloud.");
  });
  $("#logout-btn").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ }
    showLogin(); toast("Signed out");
  });

  function setBadge(provider) {
    $("#ai-badge").textContent = state.me && state.me.demo ? "Demo: sample data" : `Using ${cap(provider)}`;
  }

  // ---------- install (Android Chrome) ----------
  let installEvent = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault(); installEvent = e; $("#install-btn").hidden = false;
  });
  $("#install-btn").addEventListener("click", async () => {
    if (!installEvent) return;
    installEvent.prompt();
    const choice = await installEvent.userChoice;
    if (choice.outcome === "accepted") { toast("Life OS installed"); $("#install-btn").hidden = true; }
    installEvent = null;
  });
  window.addEventListener("appinstalled", () => { $("#install-hint").textContent = "Installed on this phone."; $("#install-btn").hidden = true; });
  if (window.matchMedia("(display-mode: standalone)").matches) $("#install-hint").textContent = "Installed on this phone.";

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }

  // ---------- start ----------
  // Shared from another app (Android share sheet) or the "Add a job" shortcut: open Add with it filled in.
  function takeShared() {
    const q = new URLSearchParams(location.search);
    const bits = [q.get("share_title"), q.get("share_text"), q.get("share_url")].map((x) => (x || "").trim()).filter(Boolean);
    const words = bits.filter((b, i) => !bits.some((o, j) => j !== i && o.includes(b) && o !== b));  // apps often repeat the link
    const wantsAdd = q.has("add") || words.length > 0 || location.hash === "#add";
    if (!wantsAdd) return;
    try { sessionStorage.setItem("lifeos.share", JSON.stringify({ text: [...new Set(words)].join(" ") })); } catch { /* ignore */ }
    history.replaceState(null, "", "/app/" + (location.hash === "#add" ? "#today" : location.hash || "#today"));
  }
  function openShared() {
    let s = null;
    try { s = JSON.parse(sessionStorage.getItem("lifeos.share") || "null"); sessionStorage.removeItem("lifeos.share"); } catch { /* ignore */ }
    if (!s) return;
    $("#add-result").replaceChildren();
    $("#add-text").value = s.text || "";
    openDialog($("#add-dialog"), $("#fab-add"));
    $("#add-text").focus();
  }
  window.addEventListener("hashchange", () => { if (location.hash === "#add") { takeShared(); openShared(); } });

  window.LifeOS = { $, el, api, toast, busy, openDialog, AuthError, shortDate, prefs, attachments, loadMeals, localIso };

  // A one-time sign-in link from the QR code on the Life OS computer: sign this phone in, then tidy the address
  async function takeJoin() {
    const params = new URLSearchParams(location.search);
    const code = params.get("join");
    if (!code) return;
    params.delete("join");
    history.replaceState(null, "", location.pathname + (params.toString() ? `?${params}` : "") + location.hash);
    try {
      const r = await api("/api/login", { method: "POST", body: { join: code }, signingIn: true });
      toast(`Signed in as ${r.name}`);
    } catch (e) {
      joinProblem = e.message;
    }
  }
  let joinProblem = "";

  async function boot() {
    takeShared();
    try { await takeJoin(); } catch { /* the normal sign-in below takes over */ }
    try {
      const me = await api("/api/me");
      state.me = me; state.people = me.people;
      setBadge(me.ai);
      route();
      openShared();
      const seen = prefs.get("lastVersion", "");
      if (me.version && seen && seen !== me.version) toast(`Life OS updated to ${me.version}`);
      if (me.version) prefs.set("lastVersion", me.version);
      flushOutbox();
    } catch (e) {
      if (e instanceof AuthError) { showLogin(); if (joinProblem) $("#login-error").textContent = joinProblem; }
      else {
        // Offline with something shared in: keep it on the phone rather than lose it
        try {
          const s = JSON.parse(sessionStorage.getItem("lifeos.share") || "null");
          if (e instanceof OfflineError && s && s.text) { outbox.add("/api/todos", { text: s.text }, s.text); sessionStorage.removeItem("lifeos.share"); toast("Saved offline"); }
        } catch { /* ignore */ }
        showLogin(); $("#login-error").textContent = e.message;
      }
    }
    showOutbox();
  }
  boot();
})();
