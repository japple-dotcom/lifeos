/* Life OS phone app, v0.18 screens: parcels and bins on Today; bin days, the cleaning rota, reading/homework/stars,
   date night and the babysitter sheet on Home; car running costs; the live test in Settings.
   Uses the helpers app.js shares (window.LifeOS). All text goes in with textContent, never innerHTML. */
(() => {
  "use strict";
  const { $, el, api, toast, busy, openDialog, shortDate } = window.LifeOS;
  const DAYS = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]];
  const gbp = (n) => `£${Number(n || 0).toFixed(2)}`;
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const list = (items) => items.length <= 1 ? (items[0] || "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
  const lower = (b) => (/^[A-Z]{2}/.test(b) ? b : b[0].toLowerCase() + b.slice(1));
  const fill = (ul, items, empty) => ul.replaceChildren(...(items.length ? items : [el("li", { class: "empty", text: empty })]));
  const safeLink = (u) => (/^https:\/\/[^\s"'<>]+$/.test(u || "") ? u : "");

  // ---------- Today: bins tonight and parcels ----------
  function today(data) {
    const bins = data.bins_tomorrow || [];
    const note = $("#bins-tonight");
    note.hidden = !bins.length;
    note.textContent = bins.length ? `Bins out tonight: ${list(bins.map(lower))}.` : "";
    const p = data.parcels || {};
    const items = [...(p.today || []), ...(p.waiting || []), ...(p.soon || []).slice(0, 3)];
    $("#parcels-block").hidden = !items.length;
    fill($("#parcels-list"), items.map((x) => parcelRow(x)), "");
  }
  function parcelRow(x) {
    const when = x.status === "out_for_delivery" ? "today" : x.when;
    const meta = [x.label, x.carrier, x.window || (when ? (when === "late" ? "running late" : when) : ""), x.collect_from].filter(Boolean).join(" · ");
    const done = x.status === "ready_to_collect" ? "Collected" : "Got it";
    const track = safeLink(x.tracking_url);
    return el("li", { class: "row wrap" },
      el("span", { class: "grow" }, el("strong", { text: x.name }), el("span", { class: "meta", text: ` · ${meta}` })),
      el("span", { class: "btn-row" },
        track ? el("a", { class: "btn btn-secondary btn-small", href: track, target: "_blank", rel: "noopener noreferrer", text: "Track", "aria-label": `Track ${x.name}` }) : null,
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: done, "aria-label": `${done}: ${x.name}`,
          onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/deliveries/mark", { method: "POST", body: { id: x.id, status: "delivered" } }); toast(`${x.name}: marked done`); ev.target.closest("li").remove(); }) }),
        el("button", { type: "button", class: "linkish", text: "Hide", "aria-label": `Hide ${x.name}`,
          onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/deliveries/mark", { method: "POST", body: { id: x.id, hide: true } }); ev.target.closest("li").remove(); }) })));
  }

  // ---------- Home ----------
  function home() {
    loadBins(); loadCleaning(); loadStudy(); loadDate(); loadSitter();
  }

  // bin days
  let binsData = null;
  function rotaRow(r = {}) {
    const n = document.querySelectorAll("#bins-rota .rota-row").length + 1;
    const every = el("select", { id: `rota-every-${n}` }, [1, 2, 3, 4].map((w) => el("option", { value: String(w), text: w === 1 ? "Every week" : `Every ${w} weeks` })));
    every.value = String(r.every || 1);
    const day = el("select", { id: `rota-day-${n}` }, DAYS.map(([v, t]) => el("option", { value: v, text: t })));
    day.value = r.day || "mon";
    const from = el("input", { type: "date", id: `rota-from-${n}`, value: r.from || "" });
    const fromField = el("div", { class: "field" }, el("label", { for: `rota-from-${n}`, text: "Its next collection" }), from);
    const sync = () => { fromField.hidden = every.value === "1"; };
    every.addEventListener("change", sync); sync();
    const row = el("div", { class: "rota-row card stack" },
      el("div", { class: "field" }, el("label", { for: `rota-bin-${n}`, text: "Bin" }), el("input", { type: "text", id: `rota-bin-${n}`, value: r.bin || "", placeholder: "For example: Recycling (blue)", autocomplete: "off" })),
      el("div", { class: "field" }, el("label", { for: `rota-day-${n}`, text: "Collection day" }), day),
      el("div", { class: "field" }, el("label", { for: `rota-every-${n}`, text: "How often" }), every),
      fromField,
      el("button", { type: "button", class: "linkish", text: "Remove this bin", onclick: () => row.remove() }));
    return row;
  }
  async function loadBins() {
    let o;
    try { o = await api("/api/bins"); } catch { return; }
    binsData = o;
    $("#bins-block").hidden = false;
    $("#bins-next").textContent = o.next ? `${cap(o.next.when)}: ${list(o.next.bins.map(lower))}.` : "No bin collections yet. Set them up below.";
    fill($("#bins-upcoming"), o.upcoming.slice(1).map((c) => el("li", {}, el("strong", { text: cap(c.when) }), el("span", { class: "meta", text: ` · ${list(c.bins.map(lower))}` }))), "");
    $("#bins-upcoming").hidden = o.upcoming.length < 2;
    const sel = $("#bins-calendar");
    $("#bins-cal-field").hidden = !o.calendars.length;
    sel.replaceChildren(el("option", { value: "", text: "Find it by itself" }), ...o.calendars.map((c) => el("option", { value: c.id, text: c.name + (c.likely ? " (looks like bins)" : "") })));
    sel.value = o.calendar || "";
    $("#bins-source-hint").textContent = o.ha
      ? "With the Waste Collection Schedule integration in Home Assistant (pick your council), the dates come straight from the council. Otherwise type your bins below."
      : "Type your bins below. (With Home Assistant and its Waste Collection Schedule integration, they'd come straight from the council.)";
    $("#bins-rota").replaceChildren(...(o.rota.length ? o.rota.map(rotaRow) : []));
    if (!o.rota.length && !o.next) $("#bins-rota").append(rotaRow());
    $("#bins-remind").value = o.remind_at || "19:30";
    $("#bins-speak").checked = !!o.speak;
    $("#bins-setup").open = !o.next;
  }
  $("#bins-add-row").addEventListener("click", () => { const r = rotaRow(); $("#bins-rota").append(r); r.querySelector("input").focus(); });
  $("#bins-save").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const rota = [...document.querySelectorAll("#bins-rota .rota-row")].map((r) => {
      const [bin, from] = r.querySelectorAll("input"); const [day, every] = r.querySelectorAll("select");
      return { bin: bin.value.trim(), day: day.value, every: Number(every.value), from: from.value };
    }).filter((r) => r.bin);
    await api("/api/bins", { method: "POST", body: { calendar: $("#bins-calendar").value, rota, remind_at: $("#bins-remind").value || "19:30", speak: $("#bins-speak").checked } });
    toast("Bin days saved"); loadBins();
  }));

  // cleaning rota
  async function loadCleaning() {
    let w;
    try { w = await api("/api/cleaning"); } catch { return; }
    $("#clean-block").hidden = false;
    const shares = w.fairness.filter((f) => f.points > 0);
    $("#clean-fair").textContent = shares.length ? `Last four weeks: ${w.fairness.map((f) => `${f.name} ${f.pct}%`).join(", ")}.` : "Jobs are shared so the effort evens out over four weeks.";
    fill($("#clean-list"), w.jobs.map((j) => el("li", { class: "row wrap" + (j.done ? " is-done" : "") },
      el("span", { class: "grow" }, el("span", { text: j.name }), el("span", { class: "meta", text: ` · ${j.done ? `done by ${j.done_by_name}` : j.who_name} · ${j.points} point${j.points === 1 ? "" : "s"}` })),
      el("span", { class: "btn-row" },
        j.done ? el("button", { type: "button", class: "linkish", text: "Undo", "aria-label": `Undo: ${j.name}`,
          onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/cleaning/undo", { method: "POST", body: { id: j.id } }); loadCleaning(); }) })
          : el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Done", "aria-label": `Done: ${j.name}`,
            onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/cleaning/done", { method: "POST", body: { id: j.id } }); toast(`Done: ${j.name}`); loadCleaning(); }) }),
        j.done ? null : el("button", { type: "button", class: "linkish", text: "Swap", "aria-label": `Swap who does: ${j.name}`,
          onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/cleaning/swap", { method: "POST", body: { id: j.id } }); loadCleaning(); }) })))),
      "Nothing due this week.");
    const v = w.vacuum || {};
    const dayNames = (v.days || []).map((d) => (DAYS.find((x) => x[0] === d) || [d, d])[1].slice(0, 3));
    $("#clean-vacuum").textContent = v.set ? `Robot vacuum: ${dayNames.length ? `runs on ${list(dayNames)} while the house is empty` : "no days chosen yet"}${v.this_week ? `, ${v.this_week} time${v.this_week === 1 ? "" : "s"} this week` : ""}.` : "";
    $("#clean-todos").hidden = !w.jobs.some((j) => !j.done);
    const st = w.settings;
    $("#clean-jobs").replaceChildren(...st.jobs.map((j) => el("div", { class: "check-row" },
      el("input", { type: "checkbox", id: `cj-${j.id}`, checked: j.on, value: j.id }), el("label", { for: `cj-${j.id}`, text: `${j.name} (every ${j.every === 7 ? "week" : `${j.every} days`})` }))));
    const vs = $("#clean-vac");
    vs.replaceChildren(el("option", { value: "", text: st.vacuums.length ? "None" : "No robot vacuum in Home Assistant" }), ...st.vacuums.map((x) => el("option", { value: x.id, text: x.name })));
    vs.value = st.vacuum.entity || "";
    $("#clean-day-boxes").replaceChildren(...DAYS.map(([d, t]) => el("span", { class: "check-row" },
      el("input", { type: "checkbox", id: `cv-${d}`, value: d, checked: (st.vacuum.days || []).includes(d) }), el("label", { for: `cv-${d}`, text: t }))));
    $("#clean-latest").value = st.vacuum.latest || "16:00";
    $("#clean-even").checked = !!st.vacuum.even_if_home;
  }
  $("#clean-todos").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/cleaning/todos", { method: "POST" });
    toast(r.filed ? `${r.filed} job${r.filed === 1 ? "" : "s"} added to your to-do lists` : "They're already on your lists");
  }));
  $("#clean-save").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const off = [...document.querySelectorAll("#clean-jobs input")].filter((c) => !c.checked).map((c) => c.value);
    const days = [...document.querySelectorAll("#clean-day-boxes input")].filter((c) => c.checked).map((c) => c.value);
    await api("/api/cleaning/settings", { method: "POST", body: { off, vacuum: { entity: $("#clean-vac").value, days, latest: $("#clean-latest").value || "16:00", even_if_home: $("#clean-even").checked } } });
    toast("Saved"); loadCleaning();
  }));

  // reading, homework and stars
  async function loadStudy() {
    let b;
    try { b = await api("/api/kids/extras"); } catch { return; }
    const kids = b.kids.map((k) => k.kid);
    $("#study-block").hidden = !kids.length;
    if (!kids.length) return;
    $("#study-stars").replaceChildren(...b.stars.kids.map((k) => el("div", { class: "star-row" },
      el("p", {}, el("strong", { text: k.kid }), el("span", { class: "meta", text: ` · ${k.total} of ${k.target} stars this week${k.reading_minutes ? ` · ${k.reading_minutes} min reading` : ""}` })),
      el("progress", { max: String(k.target), value: String(Math.min(k.total, k.target)), "aria-label": `${k.kid}: ${k.total} of ${k.target} stars` }))));
    const hw = b.kids.flatMap((k) => k.homework.map((h) => ({ ...h, kid: k.kid })));
    fill($("#study-homework"), hw.map((h) => el("li", { class: "row wrap" + (h.done ? " is-done" : "") },
      el("span", { class: "grow" }, el("span", { text: `${h.kid}: ${h.title}` }), el("span", { class: "meta", text: h.done ? " · done" : h.due ? ` · due ${shortDate(h.due)}` : "" })),
      el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove homework: ${h.kid}, ${h.title}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/kids/homework/remove", { method: "POST", body: { id: h.id } }); loadStudy(); }) }))),
    "No homework on the list.");
    const sel = $("#hw-kid");
    if (sel.options.length !== kids.length) sel.replaceChildren(...kids.map((k) => el("option", { value: k, text: k })));
  }
  $("#homework-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const title = $("#hw-title").value.trim();
    if (!title) { toast("Say what the homework is.", true); $("#hw-title").focus(); return; }
    busy(ev.submitter, async () => {
      await api("/api/kids/homework", { method: "POST", body: { kid: $("#hw-kid").value, title, due: $("#hw-due").value } });
      $("#hw-title").value = ""; $("#hw-due").value = ""; toast("Homework added"); loadStudy();
    });
  });

  // date night
  let dateData = null;
  async function updateMessage() {
    const night = (dateData.evenings || []).find((e) => e.date === $("#date-pick").value);
    const sitter = (dateData.sitters || []).find((s) => String(s.id) === $("#date-sitter").value);
    $("#date-draft").hidden = !(sitter && sitter.email);
    if (!night) { $("#date-message").value = ""; return; }
    try {
      const r = await api("/api/date-night/message", { method: "POST", body: { date: night.date, name: sitter ? sitter.name : "", start: night.start, end: night.end } });
      $("#date-message").value = r.text;
    } catch (e) { toast(e.message, true); }
  }
  async function loadDate() {
    try { dateData = await api("/api/date-night"); } catch { return; }
    $("#date-block").hidden = !dateData.google;
    if (!dateData.google) return;
    const pick = $("#date-pick");
    pick.replaceChildren(...(dateData.evenings.length ? dateData.evenings.map((e) => el("option", { value: e.date, text: e.label + (dateData.booked.some((b) => b.date === e.date) ? " (booked)" : "") }))
      : [el("option", { value: "", text: "No evening free for both of you in the next four weeks" })]));
    $("#date-sitter").replaceChildren(...dateData.sitters.map((s) => el("option", { value: String(s.id), text: s.name + (s.role ? ` (${s.role})` : "") })),
      el("option", { value: "", text: "Someone else" }));
    $("#date-book").disabled = !dateData.evenings.length;
    updateMessage();
    loadIdeas(false);
  }
  async function loadIdeas(fresh) {
    try {
      const r = await api(`/api/date-night/ideas${fresh ? "?fresh=1" : ""}`);
      fill($("#date-ideas"), r.ideas.map((i) => el("li", {},
        safeLink(i.link) ? el("a", { href: i.link, target: "_blank", rel: "noopener noreferrer", text: i.name }) : el("span", { text: i.name }),
        i.what ? el("span", { class: "meta", text: ` · ${i.what}` }) : null)), "No ideas yet.");
      $("#date-more").hidden = !r.area;
    } catch { /* ideas are a bonus */ }
  }
  $("#date-pick").addEventListener("change", updateMessage);
  $("#date-sitter").addEventListener("change", updateMessage);
  $("#date-more").addEventListener("click", (ev) => busy(ev.currentTarget, () => loadIdeas(true)));
  $("#date-book").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const night = dateData.evenings.find((e) => e.date === $("#date-pick").value);
    if (!night) return;
    await api("/api/date-night/book", { method: "POST", body: { date: night.date, start: night.start, end: night.end } });
    toast(`Date night is in the calendar: ${night.label}`); loadDate();
  }));
  $("#date-draft").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const night = dateData.evenings.find((e) => e.date === $("#date-pick").value);
    await api("/api/date-night/draft", { method: "POST", body: { date: night.date, contact_id: Number($("#date-sitter").value), start: night.start, end: night.end } });
    toast("The message is in your Gmail drafts. Check it and send it from Gmail.");
  }));

  // babysitter sheet
  function personRows(box, rows, fields, count, prefix) {
    const all = [...rows];
    while (all.length < count) all.push({});
    box.replaceChildren(...all.map((r, i) => el("div", { class: "person-row" }, fields.map(([k, label, type]) => el("div", { class: "field" },
      el("label", { for: `${prefix}-${k}-${i}`, text: `${label} ${i + 1}` }),
      type === "textarea" ? el("textarea", { id: `${prefix}-${k}-${i}`, rows: "2", "data-k": k }, r[k] || "")
        : el("input", { id: `${prefix}-${k}-${i}`, type, value: r[k] || "", autocomplete: "off", "data-k": k }))))));
  }
  const readRows = (box) => [...box.querySelectorAll(".person-row")].map((row) => Object.fromEntries([...row.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value.trim()])));
  async function loadSitter() {
    let sh;
    try { sh = await api("/api/sitter"); } catch { return; }
    $("#sitter-block").hidden = false;
    $("#st-address").value = sh.address || "";
    personRows($("#st-parents"), sh.parents, [["name", "Name", "text"], ["phone", "Phone", "tel"]], 2, "stp");
    personRows($("#st-contacts"), sh.contacts, [["name", "Name", "text"], ["role", "Who they are", "text"], ["phone", "Phone", "tel"]], Math.max(2, sh.contacts.length + 1), "stc");
    personRows($("#st-kids"), sh.kids, [["name", "Child", "text"], ["bedtime", "Bedtime", "time"], ["notes", "Notes (bedtime routine, allergies, comforters)", "textarea"]], Math.max(1, sh.kids.length), "stk");
    $("#st-wifi").value = sh.wifi_name || ""; $("#st-wifi-pw").value = sh.wifi_password || "";
    $("#st-rules").value = sh.rules || ""; $("#st-notes").value = sh.notes || "";
    $("#st-manual").checked = sh.include_manual !== false;
  }
  $("#sitter-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      await api("/api/sitter", { method: "POST", body: { address: $("#st-address").value, parents: readRows($("#st-parents")), contacts: readRows($("#st-contacts")),
        kids: readRows($("#st-kids")), wifi_name: $("#st-wifi").value, wifi_password: $("#st-wifi-pw").value, rules: $("#st-rules").value,
        notes: $("#st-notes").value, include_manual: $("#st-manual").checked } });
      toast("Babysitter sheet saved"); loadSitter();
    });
  });

  // ---------- Cars: running costs ----------
  let carReg = "";
  function cars(c) {
    const items = [...document.querySelectorAll("#cars-list > li")];
    (c.cars || []).forEach((car, i) => {
      const li = items[i];
      if (!li || li.querySelector(".carcost-btn")) return;
      li.querySelector(".linkish")?.before(el("button", { type: "button", class: "btn btn-secondary btn-small carcost-btn", text: "Running costs",
        "aria-haspopup": "dialog", "aria-label": `Running costs: ${car.name}`, onclick: (ev) => openCosts(car, ev.currentTarget) }));
    });
  }
  async function openCosts(car, opener) {
    carReg = car.reg;
    $("#carcost-title").textContent = `Running costs: ${car.name}`;
    $("#cc-date").value = new Date().toISOString().slice(0, 10);
    openDialog($("#carcost-dialog"), opener);
    await showCosts();
  }
  async function showCosts(data) {
    let s = data;
    if (!s) { try { s = await api(`/api/cars/${encodeURIComponent(carReg)}/costs`); } catch (e) { toast(e.message, true); return; } }
    const kind = $("#cc-kind");
    if (s.kinds && kind.options.length !== s.kinds.length) kind.replaceChildren(...s.kinds.map((k) => el("option", { value: k, text: cap(k) })));
    const sv = s.service;
    $("#carcost-body").replaceChildren(...[
      el("p", { class: "lead", text: s.total_12m ? `Last 12 months: ${gbp(s.total_12m)} (about ${gbp(s.per_month)} a month)` : "No costs added yet." }),
      s.by_kind.length ? el("ul", { class: "list compact" }, s.by_kind.map((k) => el("li", { class: "row" }, el("span", { class: "grow", text: cap(k.kind) }), el("span", { text: gbp(k.amount) })))) : null,
      el("p", { class: "meta", text: [s.per_mile ? `${Math.round(s.per_mile * 100)}p a mile` : "", s.miles_a_year ? `about ${s.miles_a_year.toLocaleString("en-GB")} miles a year` : "",
        s.pence_per_litre ? `fuel ${s.pence_per_litre}p a litre on average` : "", s.latest_miles ? `last mileage ${s.latest_miles.toLocaleString("en-GB")}` : ""].filter(Boolean).join(" · ") }),
      el("p", { class: sv ? "" : "hint", text: sv ? `Next service: around ${shortDate(sv.date)} (${sv.why === "mileage" ? "by the miles you do" : `${sv.every_months} months since the last`}).` : "Add a service as a cost (or under Servicing) to know when the next one's due." }),
      s.costs.length ? el("details", {}, el("summary", { text: "Recent costs" }), el("ul", { class: "list compact" }, s.costs.map((c) => el("li", { class: "row" },
        el("span", { class: "grow", text: `${shortDate(c.date)}: ${cap(c.kind)} ${gbp(c.amount)}${c.miles ? ` at ${c.miles.toLocaleString("en-GB")} miles` : ""}${c.note ? ` (${c.note})` : ""}` }),
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${c.kind} ${gbp(c.amount)} on ${shortDate(c.date)}`,
          onclick: (ev) => busy(ev.currentTarget, async () => showCosts(await api(`/api/cars/${encodeURIComponent(carReg)}/costs/remove`, { method: "POST", body: { id: c.id } }))) }))))) : null].filter(Boolean));
    $("#cs-miles").value = s.service_every_miles; $("#cs-months").value = s.service_every_months;
    $("#carcost-mot").hidden = !s.mot_api;
    fill($("#carcost-adv"), s.advisories.map((a) => el("li", { class: "row wrap" },
      el("span", { class: "grow" }, el("span", { text: a.text }), el("span", { class: "meta", text: ` · ${a.from}` })),
      a.added ? el("span", { class: "meta", text: "On the list" }) : el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Add to to-dos", "aria-label": `Add to to-dos: ${a.text}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api(`/api/cars/${encodeURIComponent(carReg)}/advisories/todo`, { method: "POST", body: { id: a.id } }); toast("Added to your to-dos"); showCosts(); }) }))),
    s.mot_api ? "No advisories at the last MOT." : "None typed in yet.");
  }
  $("#cc-kind").addEventListener("change", () => { $("#cc-litres-field").hidden = $("#cc-kind").value !== "fuel"; });
  $("#carcost-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const amount = $("#cc-amount").value;
    if (!amount) { toast("Give the amount.", true); $("#cc-amount").focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api(`/api/cars/${encodeURIComponent(carReg)}/costs`, { method: "POST", body: { kind: $("#cc-kind").value, amount, date: $("#cc-date").value,
        miles: $("#cc-miles").value, litres: $("#cc-kind").value === "fuel" ? $("#cc-litres").value : "", note: $("#cc-note").value } });
      ["#cc-amount", "#cc-miles", "#cc-litres", "#cc-note"].forEach((s) => { $(s).value = ""; });
      toast("Cost added"); showCosts(r);
    });
  });
  $("#carservice-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => showCosts(await api(`/api/cars/${encodeURIComponent(carReg)}/service`, { method: "POST", body: {
      every_miles: $("#cs-miles").value, every_months: $("#cs-months").value, last_date: $("#cs-last").value, last_miles: $("#cs-last-miles").value } })));
  });
  $("#caradv-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("#ca-text").value.trim();
    if (!text) { toast("Type the advisory.", true); return; }
    busy(ev.submitter, async () => { showCosts(await api(`/api/cars/${encodeURIComponent(carReg)}/advisories`, { method: "POST", body: { text } })); $("#ca-text").value = ""; });
  });
  $("#carcost-mot").addEventListener("click", (ev) => busy(ev.currentTarget, async () => { showCosts(await api(`/api/cars/${encodeURIComponent(carReg)}/mot`, { method: "POST" })); toast("MOT history updated"); }));

  // ---------- Settings: the live test ----------
  let liveText = "";
  function showLive(r) {
    const rep = r.report;
    liveText = r.text || "";
    $("#live-copy").hidden = !rep;
    if (!rep) { $("#live-summary").textContent = "Not run yet."; $("#live-results").replaceChildren(); return; }
    const when = new Date(rep.when).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
    $("#live-summary").textContent = `${when}: ${rep.ok} worked, ${rep.failed} didn't. Nothing was sent.`;
    $("#live-results").replaceChildren(...rep.steps.map((s) => el("li", { class: "check-item " + (s.ok ? "ok" : "fail") },
      el("span", { class: "step-state", text: s.ok ? "OK" : "Problem" }),
      el("span", { class: "grow" }, el("strong", { text: `${s.area}: ` }), el("span", { text: s.step }), s.detail ? el("span", { class: "meta", text: ` · ${s.detail}` }) : null))));
  }
  function settings() {
    api("/api/system/live-test").then(showLive).catch(() => {});
  }
  $("#live-run").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    $("#live-summary").textContent = "Testing each account. This takes about half a minute...";
    showLive(await api("/api/system/live-test", { method: "POST", body: { ai: $("#live-ai").checked } }));
  }));
  $("#live-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(liveText); toast("Copied"); } catch { toast("Couldn't copy on this phone", true); }
  });

  window.LifeOSMore = { today, home, settings, cars };
})();
