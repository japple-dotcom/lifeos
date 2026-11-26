/* The weekly fridge sheet (signed-in grown-ups only): filled from /api/week-sheet, printed from the browser. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
    }
    for (const c of kids.flat()) if (c !== null && c !== undefined) n.append(c);
    return n;
  };
  const list = (a) => (a.length <= 1 ? a[0] || "" : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
  const lower = (b) => (/^[A-Z]{2}/.test(b) ? b : b[0].toLowerCase() + b.slice(1));
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  let start = "";

  function day(d) {
    const items = d.events.map((e) => el("li", {}, e.time ? el("span", { class: "t", text: e.time }) : null, el("span", { text: e.title })));
    if (!items.length) items.push(el("li", { class: "none", text: "Nothing in the calendar" }));
    return el("section", { class: "week-day" + (d.date === iso(new Date()) ? " is-today" : ""), "aria-label": `${d.day} ${d.short}` },
      el("h2", {}, el("span", { text: d.day }), el("span", { class: "d", text: d.short })),
      el("ul", {}, items),
      d.birthdays.length ? el("span", { class: "tag", text: `🎂 ${list(d.birthdays)}'s birthday` }) : null,
      d.kit ? el("span", { class: "tag", text: `School: ${d.kit}` }) : null,
      d.bins_out.length ? el("span", { class: "tag", text: `Tonight: put out the ${list(d.bins_out.map(lower))}` }) : null,
      el("div", { class: "dinner" }, el("strong", { text: "Dinner: " }), el("span", { class: d.dinner ? "" : "none", text: d.dinner || "not planned" })));
  }

  async function load() {
    $("#week-status").textContent = "Loading the week...";
    const res = await fetch(`/api/week-sheet${start ? `?start=${start}` : ""}`, { credentials: "same-origin" }).catch(() => null);
    if (!res || res.status === 401) { $("#week-status").textContent = "Sign in to Life OS on this phone or computer first, then open the sheet again."; return; }
    if (!res.ok) { $("#week-status").textContent = "The sheet couldn't be loaded. Try again in a moment."; return; }
    const w = await res.json();
    start = w.week_of;
    document.title = `${w.title} · Life OS`;
    $("#week-title").textContent = w.title;
    $("#week-days").replaceChildren(...w.days.map(day));
    $("#week-rota").replaceChildren(...w.rota.map((r) => el("li", { text: `${r.who}: ${r.job}` })));
    $("#week-rota-block").hidden = !w.rota.length;
    $("#week-chores").replaceChildren(...w.chores.filter((c) => c.chores.length).map((c) => el("li", { text: `${c.kid}: ${c.chores.join(", ")}` })));
    $("#week-chores-block").hidden = !w.chores.some((c) => c.chores.length);
    $("#week-status").textContent = "";
    $("#week").hidden = false;
  }
  const move = (days) => { const d = new Date(start + "T12:00:00"); d.setDate(d.getDate() + days); start = iso(d); load(); };
  $("#week-prev").addEventListener("click", () => move(-7));
  $("#week-next").addEventListener("click", () => move(7));
  $("#week-print").addEventListener("click", () => window.print());
  load();
})();
