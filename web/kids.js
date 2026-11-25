/* Kids' tablet: tick a chore, log reading, tick homework, see the star chart, and undo your own tick for 10
   minutes. Nothing else (no money, no changing the lists). */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat()) if (c !== null && c !== undefined) n.append(c);
    return n;
  };
  const money = (v) => (v >= 1 ? `£${Number(v).toFixed(2)}` : `${Math.round(v * 100)}p`);
  const CHEERS = ["Brilliant!", "Nice one!", "Well done!", "Star!", "Great job!"];
  let board = null, extras = null, current = localStorage.getItem("lifeos.kid") || "", mode = "chores";
  const HEADINGS = { chores: "Who's done a chore?", reading: "Who's been reading?", homework: "Homework", stars: "This week's stars" };

  async function api(path, body) {
    const res = await fetch(path, body ? { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) } : { credentials: "same-origin" });
    if (res.status === 401) { showLogin(); throw new Error("Please sign in"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "That didn't work.");
    return data;
  }
  function showLogin() { $("#kids-app").hidden = true; $("#kids-login").hidden = false; $("#kids-key").focus(); }

  function cheer(text, sad = false) {
    const c = $("#kids-cheer");
    c.textContent = text; c.classList.toggle("sad", sad); c.classList.add("show");
    clearTimeout(cheer.t); cheer.t = setTimeout(() => c.classList.remove("show"), 4000);
  }

  function render() {
    const kids = board.kids;
    $("#kids-heading").textContent = HEADINGS[mode];
    document.querySelectorAll(".kid-mode").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    if (!kids.length) { $("#kid-panel").replaceChildren(el("p", { text: "No children are set up yet." })); return; }
    if (!kids.some((k) => k.kid === current)) current = kids[0].kid;
    $("#kids-tabs").hidden = mode === "stars";
    $("#kids-tabs").replaceChildren(...kids.map((k) => el("button", { type: "button", class: "kid-tab", "aria-pressed": String(k.kid === current),
      text: k.kid, onclick: () => { current = k.kid; try { localStorage.setItem("lifeos.kid", current); } catch { /* fine */ } render(); } })));
    if (mode !== "chores") { renderExtras(); return; }
    const k = kids.find((x) => x.kid === current);
    $("#kid-panel").replaceChildren(...[
      el("h2", { text: `${k.kid}: ${k.owed ? `${money(k.owed)} to come` : "nothing owed yet"}` }),
      el("div", { class: "kid-chores", role: "group", "aria-label": `${k.kid}'s chores` }, k.chores.map((c) => el("button", { type: "button", class: "kid-chore",
        "aria-label": `I did ${c.name}${c.value ? `, ${money(c.value)}` : ""}`,
        onclick: async (ev) => {
          ev.currentTarget.disabled = true;
          try { await api("/api/kids/done", { kid: k.kid, chore_id: c.id }); cheer(`${CHEERS[Math.floor(Math.random() * CHEERS.length)]} ${c.name}${c.value ? `: +${money(c.value)}` : ""}`); await load(); }
          catch (e) { cheer(e.message, true); ev.currentTarget.disabled = false; }
        } }, el("span", { text: c.name }), c.value ? el("small", { text: `+${money(c.value)}` }) : null))),
      k.goals.length ? el("div", { class: "kid-goals" }, el("h2", { text: "Saving for" }), k.goals.map((g) => el("div", { class: "kid-goal" },
        el("span", { text: `${g.name}: ${g.pct}%` }), el("progress", { max: "100", value: String(g.pct), "aria-label": `${g.name}: ${g.pct}% saved` })))) : null,
      k.done.length ? el("div", { class: "kid-done" }, el("h2", { text: "Done this week" }),
        el("ul", { class: "wlist" }, k.done.slice().reverse().map((d) => el("li", { class: "plain" }, el("span", { text: d.chore }),
          d.undo ? el("button", { type: "button", class: "ask-btn small", text: "Oops, undo", "aria-label": `Undo ${d.chore}`,
            onclick: async () => { try { await api("/api/kids/undo", { id: d.id }); cheer("Undone"); await load(); } catch (e) { cheer(e.message, true); } } })
            : el("span", { class: "t", text: new Date(d.at).toLocaleDateString("en-GB", { weekday: "short" }) }))))) : null].filter(Boolean));
  }

  const day = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short" });
  async function act(path, body, message) {
    try { await api(path, body); cheer(message); await load(); } catch (e) { cheer(e.message, true); }
  }
  function renderExtras() {
    if (!extras) { $("#kid-panel").replaceChildren(el("p", { text: "Loading..." })); return; }
    const k = extras.kids.find((x) => x.kid === current) || extras.kids[0];
    if (mode === "reading") {
      const bookId = "kid-book";
      $("#kid-panel").replaceChildren(...[
        el("h2", { text: `${k.kid}: how long did you read?` }),
        el("div", { class: "field kid-book" }, el("label", { for: bookId, text: "Which book? (you can leave this)" }),
          el("input", { id: bookId, type: "text", list: "kid-books", autocomplete: "off", value: k.books[0] || "" }),
          el("datalist", { id: "kid-books" }, k.books.map((b) => el("option", { value: b })))),
        el("div", { class: "kid-chores", role: "group", "aria-label": "Minutes read" }, [10, 15, 20, 30].map((m) => el("button", { type: "button", class: "kid-chore",
          "aria-label": `I read for ${m} minutes`, onclick: (ev) => { ev.currentTarget.disabled = true;
            act("/api/kids/reading", { kid: k.kid, minutes: m, book: $("#" + bookId).value.trim() }, `${CHEERS[Math.floor(Math.random() * CHEERS.length)]} ${m} minutes of reading`); } },
          el("span", { text: `${m} minutes` })))),
        k.reading.length ? el("div", { class: "kid-done" }, el("h2", { text: "Read this week" }),
          el("ul", { class: "wlist" }, k.reading.slice().reverse().map((r) => el("li", { class: "plain" },
            el("span", { text: `${day(r.date)}: ${r.minutes} min${r.book ? ` of ${r.book}` : ""}` }),
            r.undo ? el("button", { type: "button", class: "ask-btn small", text: "Oops, undo", "aria-label": `Undo ${r.minutes} minutes reading`,
              onclick: () => act("/api/kids/extras/undo", { kind: "reading", id: r.id }, "Undone") }) : null)))) : null].filter(Boolean));
    } else if (mode === "homework") {
      const todo = k.homework.filter((h) => !h.done), done = k.homework.filter((h) => h.done);
      $("#kid-panel").replaceChildren(...[
        el("h2", { text: todo.length ? `${k.kid}: tap it when it's done` : `${k.kid}: no homework to do!` }),
        todo.length ? el("div", { class: "kid-chores", role: "group", "aria-label": `${k.kid}'s homework` }, todo.map((h) => el("button", { type: "button", class: "kid-chore",
          "aria-label": `I've done: ${h.title}`, onclick: (ev) => { ev.currentTarget.disabled = true; act("/api/kids/homework/done", { id: h.id }, `${CHEERS[Math.floor(Math.random() * CHEERS.length)]} ${h.title}`); } },
          el("span", { text: h.title }), h.due ? el("small", { text: `for ${day(h.due)}` }) : null))) : null,
        done.length ? el("div", { class: "kid-done" }, el("h2", { text: "Done" }), el("ul", { class: "wlist" }, done.map((h) => el("li", { class: "plain" },
          el("span", { text: h.title }),
          h.undo ? el("button", { type: "button", class: "ask-btn small", text: "Oops, undo", "aria-label": `Undo: ${h.title}`,
            onclick: () => act("/api/kids/extras/undo", { kind: "homework", id: h.id }, "Undone") }) : el("span", { class: "t", text: day(h.done) }))))) : null].filter(Boolean));
    } else {
      const st = extras.stars;
      $("#kid-panel").replaceChildren(...st.kids.map((s) => el("section", { class: "star-chart", "aria-label": `${s.kid}'s stars` },
        el("h2", { text: `${s.kid}: ${s.total} of ${s.target} stars${s.reached ? ". Target reached!" : ""}` }),
        el("progress", { max: String(s.target), value: String(Math.min(s.total, s.target)), "aria-label": `${s.kid}: ${s.total} of ${s.target} stars` }),
        el("ol", { class: "star-days" }, s.days.map((d) => el("li", { "aria-label": `${d.day}: ${d.stars} star${d.stars === 1 ? "" : "s"}` },
          el("span", { class: "d", text: d.day }), el("span", { class: "s", "aria-hidden": "true", text: d.stars ? "★".repeat(Math.min(d.stars, 6)) : "·" })))))),
        el("p", { class: "hint", text: "A star for each chore, for 10 minutes of reading in a day, and for homework (two if it's done early)." }));
    }
  }

  async function load() {
    try { board = await api("/api/kids/board"); } catch { return; }
    try { extras = await api("/api/kids/extras"); } catch { extras = null; }
    $("#kids-login").hidden = true; $("#kids-app").hidden = false;
    render();
  }
  document.querySelectorAll(".kid-mode").forEach((b) => b.addEventListener("click", () => { mode = b.dataset.mode; render(); }));

  $("#kids-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("#kids-error"); err.textContent = "";
    const key = $("#kids-key").value.trim();
    if (!key) { err.textContent = "Enter the household key."; return; }
    const res = await fetch("/api/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, person: "kids" }) }).catch(() => null);
    if (!res || !res.ok) { let m = "That didn't work."; try { m = (await res.json()).detail || m; } catch { /* ignore */ } err.textContent = m; return; }
    $("#kids-key").value = "";
    load();
  });
  load();
  setInterval(() => { if (!document.hidden && !$("#kids-app").hidden) load(); }, 60000);
})();
