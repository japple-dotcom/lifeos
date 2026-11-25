/* The babysitter sheet (signed-in grown-ups only): filled from /api/sitter, printed from the browser. */
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
  const bed = (t) => {
    if (!/^\d\d:\d\d$/.test(t || "")) return t || "";
    const [h, m] = t.split(":").map(Number);
    return `${h % 12 || 12}${m ? `:${String(m).padStart(2, "0")}` : ""}${h >= 12 ? "pm" : "am"}`;
  };
  const tel = (p) => (p ? el("a", { href: `tel:${p.replace(/[^\d+]/g, "")}`, text: p }) : "");

  async function load() {
    const res = await fetch("/api/sitter", { credentials: "same-origin" }).catch(() => null);
    if (!res || res.status === 401) { $("#sheet-status").textContent = "Sign in to Life OS on this phone first, then open the sheet again."; return; }
    if (!res.ok) { $("#sheet-status").textContent = "The sheet couldn't be loaded. Try again in a moment."; return; }
    const s = await res.json();
    $("#sheet-status").textContent = s.saved ? "" : "Tip: fill in the sheet in Life OS (Home, Babysitter sheet) for phone numbers and bedtimes.";
    $("#sheet-address").textContent = s.address ? `Address: ${s.address}` : "";
    $("#sheet-emergency").replaceChildren(...s.emergency.map((e) => el("li", { text: e })),
      ...(s.address ? [el("li", { text: `Give this address: ${s.address}` })] : []));
    const people = [...s.parents.map((p) => ({ ...p, role: p.role || "Parent" })), ...s.contacts, ...s.sitters.filter((x) => !s.contacts.some((c) => c.name === x.name))];
    $("#sheet-people").replaceChildren(...people.filter((p) => p.name).map((p) => el("tr", {}, el("td", { text: p.name }), el("td", { text: p.role ? p.role[0].toUpperCase() + p.role.slice(1) : "" }), el("td", {}, tel(p.phone)))));
    $("#sheet-kids").replaceChildren(...s.kids.map((k) => el("div", { class: "sheet-kid" },
      el("h3", { text: k.bedtime ? `${k.name}: bed at ${bed(k.bedtime)}` : k.name }), k.notes ? el("p", { class: "pre", text: k.notes }) : null)));
    $("#sheet-kids-block").hidden = !s.kids.length;
    $("#sheet-rules").textContent = s.rules || ""; $("#sheet-rules-block").hidden = !s.rules;
    $("#sheet-where").replaceChildren(...s.where.flatMap((w) => [el("dt", { text: w.title }), el("dd", { text: w.details })]));
    $("#sheet-where-block").hidden = !s.where.length;
    $("#sheet-wifi").textContent = s.wifi_name ? `Network: ${s.wifi_name}${s.wifi_password ? `   Password: ${s.wifi_password}` : ""}` : "";
    $("#sheet-wifi-block").hidden = !s.wifi_name;
    $("#sheet-notes").textContent = s.notes || ""; $("#sheet-notes-block").hidden = !s.notes;
    $("#sheet").hidden = false;
  }
  $("#print-sheet").addEventListener("click", () => window.print());
  load();
})();
