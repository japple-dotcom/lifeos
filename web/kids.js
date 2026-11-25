/* Kids' chores on the kitchen tablet: tick a chore, undo your own tick for 10 minutes. Nothing else. */
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
  let board = null, current = localStorage.getItem("lifeos.kid") || "";

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
    if (!kids.length) { $("#kid-panel").replaceChildren(el("p", { text: "No children are set up yet." })); return; }
    if (!kids.some((k) => k.kid === current)) current = kids[0].kid;
    $("#kids-tabs").replaceChildren(...kids.map((k) => el("button", { type: "button", class: "kid-tab", "aria-pressed": String(k.kid === current),
      text: k.kid, onclick: () => { current = k.kid; try { localStorage.setItem("lifeos.kid", current); } catch { /* fine */ } render(); } })));
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

  async function load() {
    try { board = await api("/api/kids/board"); } catch { return; }
    $("#kids-login").hidden = true; $("#kids-app").hidden = false;
    render();
  }

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
