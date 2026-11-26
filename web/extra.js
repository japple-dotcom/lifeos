/* Life OS phone app, v0.19 screens: school kit and the Christmas countdown on Today; Christmas, the weekly fridge
   sheet, school kit, garden, meter readings, documents and the contents list on Home; "What can we make?" in Meals;
   setting up phone access in Settings. Uses the helpers app.js shares (window.LifeOS). Text goes in with
   textContent, never innerHTML. */
(() => {
  "use strict";
  const { $, el, api, toast, busy, attachments, loadMeals, localIso, shortDate } = window.LifeOS;
  const SCHOOL_DAYS = [["mon", "Mon", "Monday"], ["tue", "Tue", "Tuesday"], ["wed", "Wed", "Wednesday"], ["thu", "Thu", "Thursday"], ["fri", "Fri", "Friday"]];
  const gbp = (n) => `£${Number(n || 0).toLocaleString("en-GB", { minimumFractionDigits: Number(n) % 1 ? 2 : 0, maximumFractionDigits: 2 })}`;
  const fill = (ul, items, empty) => ul.replaceChildren(...(items.length ? items : [el("li", { class: "empty", text: empty })]));
  const safeLink = (u) => (/^https:\/\/[^\s"'<>]+$/.test(u || "") ? u : "");
  const dayName = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" });
  const quiet = (p) => p.catch(() => null);

  // ---------- Today ----------
  function today(data) {
    const kit = data.school_tomorrow;
    const k = $("#kit-tomorrow");
    const bits = [];
    if (kit && kit.items && kit.items.length) bits.push(`School tomorrow: ${kit.text}.`);
    if (kit && kit.wash && kit.wash.length) bits.push(`Wash ${kit.wash.map((w) => `${w}'s`).join(" and ")} PE kit tonight.`);
    k.hidden = !bits.length;
    k.textContent = bits.join(" ");
    const x = data.christmas;
    $("#xmas-today").hidden = !x;
    if (x) {
      const next = x.next ? ` Next: ${x.next.what}, ${x.next.days === 0 ? "today" : x.next.days === 1 ? "tomorrow" : shortDate(x.next.date).replace(/ \d{4}$/, "")}.` : "";
      $("#xmas-today-link").textContent = (x.days_to_go === 0 ? "Happy Christmas!" : `${x.days_to_go} day${x.days_to_go === 1 ? "" : "s"} to Christmas.`) + next;
      $("#xmas-today-link").onclick = () => setTimeout(() => $("#xmas-block").scrollIntoView({ block: "start" }), 300);
    }
  }

  // ---------- Home ----------
  function home() {
    loadXmas(); loadKit(); loadGarden(); loadMeters(); loadDocs(); loadContents();
  }

  // ===== Christmas =====
  let xmas = null;
  async function loadXmas() {
    const p = await quiet(api("/api/christmas"));
    if (!p) return;
    showXmas(p);
  }
  function showXmas(p) {
    xmas = p;
    const hasAny = p.people.length || p.cards.length || p.budget;
    $("#xmas-block").hidden = !(p.show || hasAny);
    const parts = [p.days_to_go > 0 ? `${p.days_to_go} days to go.` : p.days_to_go === 0 ? "It's Christmas Day!" : ""];
    if (p.budget) parts.push(`${gbp(p.spent)} spent of ${gbp(p.budget)}${p.spent > p.budget ? ", over budget" : ""}.`);
    else if (p.spent) parts.push(`${gbp(p.spent)} spent so far.`);
    $("#xmas-summary").textContent = parts.filter(Boolean).join(" ");
    if (document.activeElement !== $("#xmas-budget")) $("#xmas-budget").value = p.budget || "";
    $("#xmas-suggest").replaceChildren(...p.suggest_people.map((n) => el("option", { value: n })));
    $("#xmas-people").replaceChildren(...(p.people.length ? p.people.map(xmasPerson)
      : [el("p", { class: "empty", text: "Add the people you're buying for. Ideas come from Gift ideas below." })]));
    $("#xmas-cards-count").textContent = p.cards.length ? `${p.cards_sent} of ${p.cards.length} sent` : "";
    fill($("#xmas-cards"), p.cards.map((c) => el("li", { class: "row wrap" + (c.sent ? " is-done" : "") },
      el("span", { class: "grow" }, el("span", { text: c.name }), c.received ? el("span", { class: "meta", text: " · card received" }) : null),
      el("span", { class: "btn-row" },
        el("button", { type: "button", class: "btn btn-secondary btn-small", "aria-pressed": String(Boolean(c.sent)), text: c.sent ? "Sent" : "Mark sent",
          "aria-label": `Card to ${c.name} sent`, onclick: (ev) => busy(ev.currentTarget, async () => showXmas(await api("/api/christmas/card", { method: "POST", body: { cid: c.id, sent: !c.sent } }))) }),
        el("button", { type: "button", class: "btn btn-secondary btn-small", "aria-pressed": String(Boolean(c.received)), text: "Got one",
          "aria-label": `Card from ${c.name} received`, onclick: (ev) => busy(ev.currentTarget, async () => showXmas(await api("/api/christmas/card", { method: "POST", body: { cid: c.id, received: !c.received } }))) }),
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Take ${c.name} off the cards list`,
          onclick: (ev) => busy(ev.currentTarget, async () => showXmas(await api("/api/christmas/card", { method: "POST", body: { cid: c.id, remove: true } }))) })))),
    "No cards on the list yet.");
    fill($("#xmas-dates"), p.dates.map((d) => el("li", { class: "row wrap" },
      el("span", { class: "grow" }, el("strong", { text: shortDate(d.date).replace(/ \d{4}$/, "") }), el("span", { text: ` ${d.what}` }),
        el("span", { class: "meta", text: [d.days === 0 ? " · today" : d.days === 1 ? " · tomorrow" : d.days > 0 ? ` · in ${d.days} days` : "",
          d.source === "calendar" ? " · from the calendar" : "", d.filed ? " · in the to-dos" : ""].join("") })))),
    "No dates yet.");
  }
  function xmasPerson(x) {
    const id = x.id;
    const wrapped = x.bought.filter((b) => b.wrapped).length;
    const head = `${x.name} · ${gbp(x.spent)}${x.budget ? ` of ${gbp(x.budget)}` : ""}` +
      (x.bought.length ? ` · ${wrapped} of ${x.bought.length} wrapped` : "") + (x.done ? " · done" : "");
    const post = (path, body) => api(`/api/christmas/${path}`, { method: "POST", body: { id, ...body } }).then(showXmas);
    const ideaIn = el("input", { type: "text", id: `xi-${id}`, autocomplete: "off", placeholder: "For example: a Lego set" });
    const what = el("input", { type: "text", id: `xb-${id}`, autocomplete: "off" });
    const cost = el("input", { type: "number", id: `xc-${id}`, inputmode: "decimal", min: "0", step: "0.01" });
    const where = el("input", { type: "text", id: `xw-${id}`, autocomplete: "off", placeholder: "For example: Amazon" });
    const budget = el("input", { type: "number", id: `xp-${id}`, inputmode: "decimal", min: "0", step: "1", value: x.budget || "" });
    const open = document.querySelector(`details[data-xmas="${id}"]`)?.open;
    return el("details", { class: "card xmas-person", "data-xmas": id, open: open || null },
      el("summary", { text: head }),
      el("div", { class: "stack" },
        x.likes || x.sizes ? el("p", { class: "hint", text: [x.likes ? `Likes: ${x.likes}` : "", x.sizes ? `Sizes: ${x.sizes}` : ""].filter(Boolean).join(" · ") }) : null,
        x.bought.length ? el("ul", { class: "list compact", "aria-label": `Bought for ${x.name}` }, x.bought.map((b) => el("li", { class: "row wrap" },
          el("span", { class: "grow" }, el("span", { text: b.what }), el("span", { class: "meta", text: ` · ${gbp(b.cost)}${b.where ? `, ${b.where}` : ""}` })),
          el("span", { class: "btn-row" },
            el("button", { type: "button", class: "btn btn-secondary btn-small", "aria-pressed": String(Boolean(b.wrapped)), text: b.wrapped ? "Wrapped" : "Mark wrapped",
              "aria-label": `${b.what} for ${x.name} wrapped`, onclick: (ev) => busy(ev.currentTarget, () => post("present", { bid: b.id, wrapped: !b.wrapped })) }),
            el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${b.what} for ${x.name}`,
              onclick: (ev) => busy(ev.currentTarget, () => post("present", { bid: b.id, remove: true })) }))))) : null,
        x.ideas.length ? el("div", {}, el("h4", { text: "Ideas" }), el("ul", { class: "list compact", "aria-label": `Ideas for ${x.name}` }, x.ideas.map((i) => el("li", { class: "row wrap" },
          el("span", { class: "grow", text: i.text + (i.wishlist ? " (wish list)" : "") }),
          safeLink(i.link) ? el("a", { class: "btn btn-secondary btn-small", href: safeLink(i.link), target: "_blank", rel: "noopener noreferrer", text: "Look for it",
            "aria-label": `Look for ${i.text} online` }) : null)))) : null,
        el("form", { class: "inline-form", novalidate: true, onsubmit: (ev) => { ev.preventDefault(); const t = ideaIn.value.trim(); if (!t) { toast("Type an idea.", true); return; }
          busy(ev.submitter, async () => { await post("person-update", { idea: t }); toast("Idea saved"); }); } },
          el("div", { class: "field grow" }, el("label", { for: `xi-${id}`, text: `Idea for ${x.name}` }), ideaIn),
          el("button", { type: "submit", class: "btn btn-secondary", text: "Save idea", "aria-label": `Save idea for ${x.name}` })),
        el("form", { class: "stack", novalidate: true, onsubmit: (ev) => { ev.preventDefault(); if (!what.value.trim()) { toast("What did you buy?", true); return; }
          busy(ev.submitter, async () => { await post("bought", { what: what.value, cost: cost.value || 0, where: where.value }); toast(`Added for ${x.name}`); }); } },
          el("h4", { text: "Bought something" }),
          el("div", { class: "field" }, el("label", { for: `xb-${id}`, text: `Present for ${x.name}` }), what),
          el("div", { class: "field" }, el("label", { for: `xc-${id}`, text: `Cost of ${x.name}'s present (£)` }), cost),
          el("div", { class: "field" }, el("label", { for: `xw-${id}`, text: `Where ${x.name}'s present is from` }), where),
          el("button", { type: "submit", class: "btn btn-primary btn-block", text: "Add present", "aria-label": `Add present for ${x.name}` })),
        el("form", { class: "inline-form", novalidate: true, onsubmit: (ev) => { ev.preventDefault();
          busy(ev.submitter, async () => { await post("person-update", { budget: budget.value || 0 }); toast("Budget saved"); }); } },
          el("div", { class: "field grow" }, el("label", { for: `xp-${id}`, text: `Budget for ${x.name} (£)` }), budget),
          el("button", { type: "submit", class: "btn btn-secondary", text: "Save", "aria-label": `Save budget for ${x.name}` })),
        el("button", { type: "button", class: "linkish", text: `Take ${x.name} off the list`,
          onclick: (ev) => { if (!confirm(`Take ${x.name} off the Christmas list, with what's been bought?`)) return; busy(ev.currentTarget, () => post("person-update", { remove: true })); } })));
  }
  $("#xmas-budget-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => { showXmas(await api("/api/christmas/budget", { method: "POST", body: { total: $("#xmas-budget").value || 0 } })); toast("Budget saved"); });
  });
  $("#xmas-person-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("#xmas-person").value.trim();
    if (!name) { toast("Type a name.", true); $("#xmas-person").focus(); return; }
    busy(ev.submitter, async () => {
      showXmas(await api("/api/christmas/person", { method: "POST", body: { name, budget: $("#xmas-person-budget").value || 0 } }));
      $("#xmas-person").value = ""; $("#xmas-person-budget").value = ""; toast(`${name} added`);
    });
  });
  $("#xmas-card-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("#xmas-card-names").value.trim();
    if (!name) { toast("Type a name (or several, one per line).", true); return; }
    busy(ev.submitter, async () => { showXmas(await api("/api/christmas/card", { method: "POST", body: { name } })); $("#xmas-card-names").value = ""; toast("Added to the cards list"); });
  });
  $("#xmas-date-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const what = $("#xmas-date-what").value.trim(), date = $("#xmas-date-day").value;
    if (!what || !date) { toast("Say what it's for and pick the date.", true); return; }
    busy(ev.submitter, async () => { showXmas(await api("/api/christmas/date", { method: "POST", body: { what, date } })); $("#xmas-date-what").value = ""; $("#xmas-date-day").value = ""; toast("Date added"); });
  });
  $("#xmas-file").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/christmas/todos", { method: "POST" });
    toast(r.filed.length ? `Added ${r.filed.length} date${r.filed.length === 1 ? "" : "s"} to the to-dos` : "They're all in the to-dos already");
    loadXmas();
  }));

  // ===== School kit =====
  let kit = null;
  async function loadKit() {
    const r = await quiet(api("/api/schoolkit"));
    if (!r) return;
    kit = r;
    const kids = Object.keys(r.kids);
    $("#kit-block").hidden = !kids.length;
    if (!kids.length) return;
    showKitNext(r.tomorrow);
    $("#kit-lost-kid").replaceChildren(...kids.map((k) => el("option", { value: k, text: k })));
    $("#kit-kids").replaceChildren(...kids.map((k) => kitKid(k, r.kids[k])));
    if (!Object.values(r.kids).some((c) => c.pe.length || c.lunch.length || c.other.length)) $("#kit-setup").open = true;
  }
  function showKitNext(t) {
    const when = new Date(t.date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long" });
    $("#kit-next").textContent = (t.items.length ? `${when}: ${t.text}.` : `Nothing special for ${when}.`) +
      (t.wash.length ? ` Wash ${t.wash.map((w) => `${w}'s`).join(" and ")} PE kit tonight.` : "");
  }
  function dayBoxes(kid, what, chosen, slug) {
    return el("div", { class: "chip-row" }, SCHOOL_DAYS.map(([v, short, long]) => {
      const id = `kit-${slug}-${v}`;
      return el("span", { class: "check-row" },
        el("input", { type: "checkbox", id, value: v, checked: chosen.includes(v) || null, "aria-label": `${kid}: ${what} on ${long}` }),
        el("label", { for: id, text: short }));
    }));
  }
  const slugOf = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  function kitOtherRow(kid, o, n) {
    const slug = `${slugOf(kid)}-o${n}`;
    return el("div", { class: "kit-other stack" },
      el("div", { class: "field" }, el("label", { for: `kit-${slug}-what`, text: `Other kit ${n + 1} for ${kid}` }),
        el("input", { type: "text", id: `kit-${slug}-what`, value: o.what || "", autocomplete: "off", placeholder: "For example: swimming bag, library book" })),
      dayBoxes(kid, o.what || "other kit", o.days || [], slug));
  }
  function kitKid(kid, c) {
    const s = slugOf(kid);
    const others = el("div", { class: "stack kit-others" }, [...c.other, { what: "", days: [] }].map((o, i) => kitOtherRow(kid, o, i)));
    return el("fieldset", { class: "stack card kit-kid", "data-kid": kid },
      el("legend", { text: kid }),
      el("p", { class: "meta", text: "PE kit" }), dayBoxes(kid, "PE kit", c.pe, `${s}-pe`),
      el("p", { class: "meta", text: "Packed lunch" }), dayBoxes(kid, "packed lunch", c.lunch, `${s}-lunch`),
      others,
      el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Add more kit", "aria-label": `Add more kit for ${kid}`,
        onclick: () => others.append(kitOtherRow(kid, {}, others.children.length)) }));
  }
  $("#kit-save").addEventListener("click", (ev) => {
    const data = {};
    document.querySelectorAll("#kit-kids .kit-kid").forEach((fs) => {
      const kid = fs.dataset.kid, s = slugOf(kid);
      const days = (slug) => SCHOOL_DAYS.map(([v]) => v).filter((v) => $(`#kit-${slug}-${v}`)?.checked);
      const other = [...fs.querySelectorAll(".kit-other")].map((row, i) => ({
        what: row.querySelector("input[type=text]").value.trim(), days: days(`${s}-o${i}`) })).filter((o) => o.what);
      data[kid] = { pe: days(`${s}-pe`), lunch: days(`${s}-lunch`), other };
    });
    busy(ev.currentTarget, async () => { await api("/api/schoolkit", { method: "POST", body: { kids: data } }); toast("Kit days saved"); loadKit(); });
  });
  $("#kit-lost-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const item = $("#kit-lost-item").value.trim();
    if (!item) { toast("What's missing?", true); $("#kit-lost-item").focus(); return; }
    busy(ev.submitter, async () => {
      await api("/api/schoolkit/lost", { method: "POST", body: { kid: $("#kit-lost-kid").value, item } });
      $("#kit-lost-item").value = ""; toast("Added to the to-dos for the next school day");
    });
  });

  // ===== Garden =====
  async function loadGarden() {
    const g = await quiet(api("/api/garden"));
    if (!g) return;
    $("#garden-block").hidden = false;
    fill($("#garden-advice"), g.advice.map((a) => el("li", { class: "garden-" + a.kind, text: a.text })),
      g.source === "none" ? "Add your postcode (or Home Assistant weather) below for frost and watering warnings." : "No frost or watering warnings for the next few days.");
    $("#garden-forecast").textContent = g.forecast.length ? "Next days: " + g.forecast.slice(0, 4).map((d) =>
      `${new Date(d.date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short" })} ${Math.round(d.high)}° / ${Math.round(d.low)}°${d.rain >= 1 ? `, ${Math.round(d.rain)}mm rain` : ""}`).join(" · ") : "";
    fill($("#garden-jobs"), g.jobs.map((j) => el("li", { class: "row wrap" }, el("span", { class: "grow", text: j }),
      el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Add to to-dos", "aria-label": `Add to to-dos: ${j}`,
        onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/garden/todo", { method: "POST", body: { text: j } }); toast("Added to the to-dos"); ev.target.disabled = true; }) }))),
    g.have.length ? "Nothing big this month." : "Tick what the garden has below to see this month's jobs.");
    const NAMES = { lawn: "Lawn", hedge: "Hedge", pots: "Pots and tubs", veg: "Veg patch", greenhouse: "Greenhouse", trees: "Fruit trees" };
    $("#garden-have").replaceChildren(...g.all.map((h) => el("span", { class: "check-row" },
      el("input", { type: "checkbox", id: `garden-have-${h}`, value: h, checked: g.have.includes(h) || null }),
      el("label", { for: `garden-have-${h}`, text: NAMES[h] || h }))));
    $("#garden-weather-field").hidden = !g.weathers.length;
    $("#garden-weather").replaceChildren(el("option", { value: "", text: "The first one Home Assistant has" }),
      ...g.weathers.map((w) => el("option", { value: w.id, text: w.name })));
    $("#garden-weather").value = g.weather || "";
    if (document.activeElement !== $("#garden-postcode")) $("#garden-postcode").value = g.postcode || "";
    $("#garden-nudges").checked = Boolean(g.nudges);
    if (!g.have.length && g.source === "none") $("#garden-setup").open = true;
  }
  $("#garden-save").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    await api("/api/garden", { method: "POST", body: {
      have: [...document.querySelectorAll("#garden-have input:checked")].map((i) => i.value),
      weather: $("#garden-weather").value, postcode: $("#garden-postcode").value.trim(), nudges: $("#garden-nudges").checked } });
    toast("Garden saved"); loadGarden();
  }));

  // ===== Meter readings =====
  let metersData = null;
  async function loadMeters() {
    const m = await quiet(api("/api/meters"));
    if (!m) return;
    showMeters(m);
  }
  function showMeters(m) {
    metersData = m;
    $("#meters-block").hidden = false;
    const ord = (n) => n + (n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th");
    $("#meters-hint").textContent = `A reminder on the ${ord(m.day)} of each month.` + (m.ha ? " Meters with a Home Assistant sensor are read by themselves." : "");
    $("#meters-read").hidden = !m.meters.some((x) => x.sensor);
    $("#meters-list").replaceChildren(...m.meters.map(meterCard));
    $("#meters-day").replaceChildren(...Array.from({ length: 28 }, (_, i) => el("option", { value: String(i + 1), text: ord(i + 1) })));
    $("#meters-day").value = String(m.day);
    $("#meters-cfg").replaceChildren(...m.meters.map((x, i) => meterCfgRow(x, i, m.sensors)));
  }
  function meterCard(x) {
    const id = `mr-${x.id}`;
    const input = el("input", { type: "number", id, inputmode: "decimal", min: "0", step: "any" });
    const last = x.latest;
    const use = x.use.slice(-3).reverse();
    return el("div", { class: "card stack meter" + (x.due ? " is-due" : "") },
      el("div", { class: "row wrap" }, el("h3", { class: "grow", text: x.name }), x.due ? el("span", { class: "badge warn", text: "Reading due" }) : null),
      el("p", { class: "meta", text: last ? `Last: ${Number(last.value).toLocaleString("en-GB")} ${x.unit} on ${shortDate(last.date)}${last.source === "home assistant" ? " (Home Assistant)" : ""}` : "No readings yet." }),
      use.length ? el("p", { class: "meta", text: "Used: " + use.map((u) => `${new Date(u.month + "-15T12:00:00").toLocaleDateString("en-GB", { month: "short" })} ${u.use.toLocaleString("en-GB")} ${x.unit}`).join(" · ") }) : null,
      el("form", { class: "inline-form", novalidate: true, onsubmit: (ev) => { ev.preventDefault();
        if (input.value === "") { toast("Type the number on the meter.", true); input.focus(); return; }
        busy(ev.submitter, async () => { showMeters(await api("/api/meters/reading", { method: "POST", body: { meter: x.id, value: Number(input.value) } })); toast(`${x.name} reading saved`); }); } },
        el("div", { class: "field grow" }, el("label", { for: id, text: `New ${x.name.toLowerCase()} reading${x.unit ? ` (${x.unit})` : ""}` }), input),
        el("button", { type: "submit", class: "btn btn-primary", text: "Save", "aria-label": `Save ${x.name.toLowerCase()} reading` })),
      last ? el("button", { type: "button", class: "btn btn-secondary btn-block", text: "Put an email to the supplier in Gmail drafts",
        "aria-label": `Email the ${x.name.toLowerCase()} reading to the supplier (Gmail draft)`,
        onclick: (ev) => busy(ev.currentTarget, async () => {
          const r = await api("/api/meters/email", { method: "POST", body: { meter: x.id } });
          toast(r.to ? "It's in Gmail drafts. Check it and press Send there." : "It's in Gmail drafts. Add the supplier's address and send it from Gmail.");
        }) }) : null);
  }
  function meterCfgRow(x, i, sensors) {
    const sel = el("select", { id: `mc-sensor-${i}` }, el("option", { value: "", text: "None: we'll type it" }),
      ...sensors.map((s) => el("option", { value: s.id, text: `${s.name}${s.state ? ` (${s.state})` : ""}` })));
    sel.value = x.sensor || "";
    return el("fieldset", { class: "stack card meter-cfg", "data-id": x.id || "" },
      el("legend", { text: x.name || "New meter" }),
      el("div", { class: "field" }, el("label", { for: `mc-name-${i}`, text: `Meter ${i + 1} name` }), el("input", { type: "text", id: `mc-name-${i}`, value: x.name || "", autocomplete: "off", placeholder: "For example: Water" })),
      el("div", { class: "field" }, el("label", { for: `mc-unit-${i}`, text: `Meter ${i + 1} unit` }), el("input", { type: "text", id: `mc-unit-${i}`, value: x.unit || "", autocomplete: "off", placeholder: "kWh, m³" })),
      sensors.length ? el("div", { class: "field" }, el("label", { for: `mc-sensor-${i}`, text: `Meter ${i + 1} Home Assistant sensor` }), sel) : null,
      el("div", { class: "field" }, el("label", { for: `mc-supplier-${i}`, text: `Meter ${i + 1} supplier's email for readings` }), el("input", { type: "email", id: `mc-supplier-${i}`, value: x.supplier || "", autocomplete: "off" })));
  }
  $("#meters-add").addEventListener("click", () => {
    const n = document.querySelectorAll("#meters-cfg .meter-cfg").length;
    $("#meters-cfg").append(meterCfgRow({}, n, (metersData && metersData.sensors) || []));
    $(`#mc-name-${n}`).focus();
  });
  $("#meters-save").addEventListener("click", (ev) => {
    const meters = [...document.querySelectorAll("#meters-cfg .meter-cfg")].map((fs, i) => ({
      id: fs.dataset.id, name: $(`#mc-name-${i}`).value.trim(), unit: $(`#mc-unit-${i}`).value.trim(),
      sensor: $(`#mc-sensor-${i}`) ? $(`#mc-sensor-${i}`).value : "", supplier: $(`#mc-supplier-${i}`).value.trim() }));
    busy(ev.currentTarget, async () => { showMeters(await api("/api/meters/settings", { method: "POST", body: { meters, day: Number($("#meters-day").value) } })); toast("Meters saved"); });
  });
  $("#meters-read").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/meters/read-now", { method: "POST" });
    showMeters(r); toast(r.read.length ? `Read ${r.read.length} meter${r.read.length === 1 ? "" : "s"}` : "Home Assistant had nothing new to read");
  }));

  // ===== Documents =====
  let docsData = null;
  async function loadDocs() {
    const d = await quiet(api("/api/documents"));
    if (!d) return;
    showDocs(d);
  }
  function showDocs(d) {
    docsData = d;
    $("#docs-block").hidden = false;
    const kind = $("#docs-kind").value;
    $("#docs-kind").replaceChildren(...d.kinds.map((k) => el("option", { value: k, text: k })));
    if (kind) $("#docs-kind").value = kind;
    fill($("#docs-list"), d.documents.map((x) => {
      const title = `${x.whose ? `${x.whose}'s ` : ""}${x.whose ? x.kind.toLowerCase() : x.kind}`;
      const exp = x.expires ? (x.days < 0 ? `expired ${shortDate(x.expires)}` : `expires ${shortDate(x.expires)}`) : "";
      return el("li", { class: "stack-tight" },
        el("div", { class: "row wrap" },
          el("span", { class: "grow" }, el("strong", { text: title }), el("span", { class: "meta", text: ` · ${x.where}` }),
            exp ? el("span", { class: "meta" + (x.soon ? " warn" : ""), text: ` · ${exp}` }) : null,
            x.notes ? el("span", { class: "meta line", text: x.notes }) : null),
          el("span", { class: "btn-row" },
            el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Change", "aria-label": `Change ${title}`, onclick: () => editDoc(x) }),
            el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${title}`,
              onclick: (ev) => { if (!confirm(`Remove ${title} from the list (and its renewal)?`)) return; busy(ev.currentTarget, async () => showDocs(await api("/api/documents/remove", { method: "POST", body: { id: x.id } }))); } }))),
        attachments("document", x.id, x.files_list, title, loadDocs));
    }), "No documents yet. Start with passports: where they're kept and when they expire.");
  }
  function editDoc(x) {
    $("#docs-id").value = x.id; $("#docs-kind").value = x.kind; $("#docs-whose").value = x.whose;
    $("#docs-where").value = x.where; $("#docs-expires").value = x.expires || ""; $("#docs-notes").value = x.notes || "";
    $("#docs-save").textContent = "Save changes";
    $("#docs-add").open = true; $("#docs-where").focus();
  }
  function resetDoc() {
    for (const id of ["docs-id", "docs-whose", "docs-where", "docs-expires", "docs-notes"]) $(`#${id}`).value = "";
    $("#docs-save").textContent = "Save document";
  }
  $("#docs-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!$("#docs-where").value.trim()) { toast("Say where it's kept.", true); $("#docs-where").focus(); return; }
    busy(ev.submitter, async () => {
      showDocs(await api("/api/documents", { method: "POST", body: { id: $("#docs-id").value, kind: $("#docs-kind").value, whose: $("#docs-whose").value,
        where: $("#docs-where").value, expires: $("#docs-expires").value, notes: $("#docs-notes").value } }));
      resetDoc(); $("#docs-add").open = false; toast("Document saved");
    });
  });
  $("#docs-find").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = $("#docs-q").value.trim();
    if (!q) { $("#docs-answer").textContent = ""; return; }
    busy(ev.submitter, async () => { const r = await api(`/api/documents?q=${encodeURIComponent(q)}`); $("#docs-answer").textContent = r.answer; });
  });

  // ===== Contents list =====
  async function loadContents() {
    const c = await quiet(api("/api/inventory"));
    if (!c) return;
    showContents(c);
  }
  function showContents(c) {
    $("#contents-block").hidden = false;
    const room = $("#ci-room").value;
    $("#ci-room").replaceChildren(...c.room_names.map((r) => el("option", { value: r, text: r })));
    if (room) $("#ci-room").value = room;
    $("#contents-summary").textContent = c.count ? `${c.count} item${c.count === 1 ? "" : "s"}, ${gbp(c.total)} to replace` +
      (c.cover ? ` (cover ${gbp(c.cover)})` : "") + "." + (c.without_photos ? ` ${c.without_photos} without a photo or receipt.` : "") : "Nothing on the list yet.";
    $("#contents-warnings").replaceChildren(...c.warnings.map((w) => el("p", { class: "notice", text: w })));
    if (document.activeElement !== $("#contents-cover")) $("#contents-cover").value = c.cover || "";
    $("#contents-rooms").replaceChildren(...c.rooms.map((r) => el("div", { class: "contents-room" },
      el("h3", { text: `${r.room} · ${gbp(r.total)}` }),
      el("ul", { class: "list", "aria-label": r.room }, r.items.map((i) => el("li", { class: "stack-tight" },
        el("div", { class: "row wrap" },
          el("span", { class: "grow" }, el("strong", { text: i.name }), el("span", { class: "meta", text: ` · ${gbp(i.value)}` + (i.bought ? ` · bought ${shortDate(i.bought)}` : "") + (i.serial ? ` · serial ${i.serial}` : "") }),
            i.notes ? el("span", { class: "meta line", text: i.notes }) : null),
          el("span", { class: "btn-row" },
            el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Change", "aria-label": `Change ${i.name}`, onclick: () => editItem(i, r.room) }),
            el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${i.name}`,
              onclick: (ev) => { if (!confirm(`Remove ${i.name} and its photos?`)) return; busy(ev.currentTarget, async () => showContents(await api("/api/inventory/remove", { method: "POST", body: { id: i.id } }))); } }))),
        attachments("item", i.id, i.files_list, i.name, loadContents)))))));
  }
  function editItem(i, room) {
    $("#ci-id").value = i.id; $("#ci-name").value = i.name; $("#ci-room").value = room; $("#ci-value").value = i.value;
    $("#ci-bought").value = i.bought || ""; $("#ci-serial").value = i.serial || ""; $("#ci-notes").value = i.notes || "";
    $("#ci-save").textContent = "Save changes"; $("#contents-add").open = true; $("#ci-name").focus();
  }
  $("#contents-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!$("#ci-name").value.trim()) { toast("What's the item?", true); $("#ci-name").focus(); return; }
    busy(ev.submitter, async () => {
      showContents(await api("/api/inventory", { method: "POST", body: { id: $("#ci-id").value, name: $("#ci-name").value, room: $("#ci-room").value,
        value: $("#ci-value").value || 0, bought: $("#ci-bought").value, serial: $("#ci-serial").value, notes: $("#ci-notes").value } }));
      for (const id of ["ci-id", "ci-name", "ci-value", "ci-bought", "ci-serial", "ci-notes"]) $(`#${id}`).value = "";
      $("#ci-save").textContent = "Save item"; toast("Saved. Attach a photo or receipt to it below.");
    });
  });
  $("#contents-cover-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => { showContents(await api("/api/inventory/cover", { method: "POST", body: { amount: $("#contents-cover").value || 0 } })); toast("Cover saved"); });
  });

  // ---------- Meals: what can we make? ----------
  async function shrink(file) {
    // Phone photos are big; a 1600px JPEG is plenty to read and keeps the assistant's cost down. Also turns iPhone
    // HEIC photos into JPEG where the browser can open them.
    try {
      const bmp = await createImageBitmap(file);
      const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.85));
      return blob || file;
    } catch { return file; }
  }
  function showDinners(r) {
    const box = $("#fridge-result");
    const tomorrow = localIso(new Date(Date.now() + 86400000));
    const items = [];
    if (r.seen && r.seen.length && r.from === "photo") items.push(el("p", { class: "meta", text: `Spotted: ${r.seen.join(", ")}.` }));
    if (!r.dinners.length) items.push(el("p", { text: r.from === "photo" ? "No dinner ideas from that photo. Try typing what's there." : "None of your past dinners match that. Try the photo, or add more things." }));
    for (const d of r.dinners) {
      const missing = d.missing || [];
      items.push(el("div", { class: "card stack dinner-idea" },
        el("h4", { text: d.name + (d.minutes ? ` · about ${d.minutes} minutes` : "") }),
        d.uses.length ? el("p", { class: "meta", text: `Uses: ${d.uses.join(", ")}` }) : null,
        el("p", { class: "meta", text: missing.length ? `Still need: ${missing.join(", ")}` : "Nothing else to buy." }),
        el("div", { class: "btn-row" },
          el("button", { type: "button", class: "btn btn-primary btn-small", text: "Have it tonight", "aria-label": `Have ${d.name} tonight`,
            onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/fridge/have", { method: "POST", body: { name: d.name, uses: d.uses, missing, day: localIso() } }); toast(`${d.name} is tonight's dinner`); loadMeals(); }) }),
          el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Tomorrow", "aria-label": `Have ${d.name} tomorrow`,
            onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/fridge/have", { method: "POST", body: { name: d.name, uses: d.uses, missing, day: tomorrow } }); toast(`${d.name} is tomorrow's dinner`); loadMeals(); }) }),
          missing.length ? el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Add to Shopping", "aria-label": `Add what ${d.name} still needs to Shopping`,
            onclick: (ev) => busy(ev.currentTarget, async () => {
              const s = await api("/api/fridge/shopping", { method: "POST", body: { items: missing } });
              toast(s.added.length ? `Added: ${s.added.join(", ")}` : "Already on the list or in the cupboard");
            }) }) : null)));
    }
    box.replaceChildren(...items);
  }
  $("#fridge-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const file = $("#fridge-photo").files[0];
    const text = $("#fridge-text").value.trim();
    if (!file && !text) { toast("Take a photo or type what's in the fridge.", true); $("#fridge-photo").focus(); return; }
    busy(ev.submitter, async () => {
      $("#fridge-result").replaceChildren(el("p", { class: "meta", text: file ? "Looking at the photo..." : "Matching your dinners..." }));
      try {
        let r;
        if (file) {
          const blob = await shrink(file);
          r = await api(`/api/fridge/photo?note=${encodeURIComponent(text.slice(0, 200))}`, { method: "POST", body: blob, headers: { "Content-Type": blob.type || "application/octet-stream" } });
          $("#fridge-photo").value = "";
        } else {
          r = await api("/api/fridge/text", { method: "POST", body: { text } });
        }
        showDinners(r);
      } catch (e) { $("#fridge-result").replaceChildren(); throw e; }
    });
  });

  // ---------- Settings: the Google sign-in file ----------
  async function googleClient() {
    const acc = await quiet(api("/api/accounts"));
    if (!acc) return;
    $("#google-client-summary").textContent = acc.client_ready ? "Replace the Google sign-in file" : "Add the Google sign-in file";
    if (!acc.client_ready) $("#google-client").open = true;
  }
  $("#gc-save").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const f = $("#gc-file").files[0];
    const body = { client_id: $("#gc-id").value.trim(), client_secret: $("#gc-secret").value.trim() };
    if (f) {
      if (f.size > 20000) { toast("That file is too big to be Google's sign-in file.", true); return; }
      body.file = await f.text();
    } else if (!body.client_id || !body.client_secret) {
      toast("Choose the downloaded file, or paste the client ID and secret.", true); return;
    }
    const r = await api("/api/accounts/client", { method: "POST", body });
    $("#gc-file").value = ""; $("#gc-secret").value = ""; $("#gc-id").value = "";
    $("#gc-result").textContent = `Saved (${r.client_id.split("-")[0]}...). Now press Connect for each person below.`;
    $("#google-client").open = false;
    toast("Google sign-in saved");
    setTimeout(() => window.dispatchEvent(new HashChangeEvent("hashchange")), 400);
  }));

  // ---------- Settings: phone access ----------
  async function settings() {
    googleClient();
    const r = await quiet(api("/api/system/phone-access"));
    if (!r) return;
    const st = $("#phone-access-status");
    st.textContent = r.https ? `Phones use ${r.url}` : "Not set up yet: phones can't reach Life OS away from this computer.";
    $("#phone-access-run").hidden = !r.here;
    if (!r.here) st.textContent += " To change it, do this on the Life OS computer.";
    else if (!r.cli) st.textContent += " Tailscale isn't installed on this computer yet (tailscale.com).";
  }
  $("#phone-access-run").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const st = $("#phone-access-status"), link = $("#phone-access-link");
    st.textContent = "Setting up Tailscale on this computer...";
    const r = await api("/api/system/phone-access", { method: "POST" });
    link.hidden = !r.link;
    if (r.link && safeLink(r.link)) link.href = safeLink(r.link);
    if (r.ok) {
      st.textContent = `Done. Phones use ${r.url}${r.restart ? ". Life OS is restarting to use it; this page comes back in a few seconds." : ""}`;
      if (r.restart) setTimeout(() => location.reload(), 6000);
    } else st.textContent = r.problem;
  }));

  window.LifeOSExtra = { today, home, settings };
})();
