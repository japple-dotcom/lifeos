/* Life OS phone app, v0.20 screens: brain dump and snap a letter (a dialog from Today and the Add dialog); the
   freezer, loft and garage boxes, guests, something's broken and the kids' routines on Home; the Google set-up check,
   email to-dos and the evening "Tomorrow" message in Settings. Uses window.LifeOS; text goes in with textContent. */
(() => {
  "use strict";
  const { $, el, api, toast, busy, openDialog, shortDate } = window.LifeOS;
  const quiet = (p) => p.catch(() => null);
  const fill = (ul, items, empty) => ul.replaceChildren(...(items.length ? items : [el("li", { class: "empty", text: empty })]));
  const safeLink = (u) => (/^https:\/\/[^\s"'<>]+$/.test(u || "") ? u : "");
  const KIND_NAMES = { todo: "To-do", shopping: "Shopping", event: "Calendar" };

  // ---------- brain dump and snap a letter ----------
  let review = { items: [], origin: "brain dump" };
  function openDump(opener, letter) {
    $("#dump-review").replaceChildren();
    openDialog($("#dump-dialog"), opener);
    (letter ? $("#letter-photo") : $("#dump-text")).focus();
  }
  $("#today-dump").addEventListener("click", (ev) => openDump(ev.currentTarget, false));
  $("#today-letter").addEventListener("click", (ev) => openDump(ev.currentTarget, true));
  $("#add-to-dump").addEventListener("click", () => {
    const text = $("#add-text").value.trim();
    $("#add-dialog").close();
    if (text) $("#dump-text").value = text;
    openDump($("#today-dump"), false);
  });

  function showReview(items, origin, heading) {
    review = { items, origin };
    const box = $("#dump-review");
    if (!items.length) { box.replaceChildren(el("p", { text: "Nothing to add was found. Try a few more words." })); return; }
    const rows = items.map((it, i) => {
      const kind = el("select", { id: `dr-kind-${i}`, "aria-label": `Kind for: ${it.text}` },
        ...Object.entries(KIND_NAMES).map(([v, t]) => el("option", { value: v, text: t, selected: v === it.kind || null })));
      const day = el("input", { type: "date", id: `dr-date-${i}`, value: it.date || "", "aria-label": `Date for: ${it.text}` });
      const time = el("input", { type: "time", id: `dr-time-${i}`, value: it.time || "", "aria-label": `Time for: ${it.text}` });
      const sync = () => { time.hidden = kind.value !== "event"; day.hidden = kind.value === "shopping"; };
      kind.addEventListener("change", sync);
      sync();
      return el("li", { class: "dump-row" },
        el("span", { class: "check-row" }, el("input", { type: "checkbox", id: `dr-on-${i}`, checked: true }),
          el("label", { for: `dr-on-${i}`, text: it.text })),
        el("span", { class: "dump-opts" }, kind, day, time));
    });
    box.replaceChildren(
      el("h3", { text: heading }),
      el("ul", { class: "list dump-list", "aria-label": "Check before adding" }, rows),
      el("button", { type: "button", class: "btn btn-primary btn-block", text: "Add these", onclick: (ev) => busy(ev.currentTarget, saveReview) }));
  }
  async function saveReview() {
    const items = review.items.map((it, i) => ({ ...it, kind: $(`#dr-kind-${i}`).value, date: $(`#dr-date-${i}`).value,
      time: $(`#dr-time-${i}`).value, on: $(`#dr-on-${i}`).checked })).filter((it) => it.on);
    if (!items.length) { toast("Tick at least one thing to add.", true); return; }
    if (items.some((it) => it.kind === "event" && !it.date)) { toast("Calendar dates need a day. Pick one, or make it a to-do.", true); return; }
    const r = await api("/api/braindump/save", { method: "POST", body: { items, origin: review.origin } });
    const n = r.added.length;
    $("#dump-review").replaceChildren(el("p", { text: `Added ${n} thing${n === 1 ? "" : "s"}${r.skipped ? ` (${r.skipped} already on the list or in the cupboard)` : ""}.` }));
    $("#dump-text").value = "";
    toast(`Added ${n}`);
  }
  $("#dump-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("#dump-text").value.trim();
    if (!text) { toast("Type or paste what's on your mind first.", true); $("#dump-text").focus(); return; }
    busy(ev.submitter, async () => {
      $("#dump-review").replaceChildren(el("p", { class: "meta", text: "Sorting it out..." }));
      const r = await api("/api/braindump/split", { method: "POST", body: { text } });
      showReview(r.items, "brain dump", `Found ${r.items.length}: untick anything you don't want`);
    });
  });
  $("#letter-photo").addEventListener("change", async () => {
    const f = $("#letter-photo").files[0];
    if (!f) return;
    $("#dump-review").replaceChildren(el("p", { class: "meta", text: "Reading the letter..." }));
    try {
      const shrink = window.LifeOSExtra && window.LifeOSExtra.shrink;
      const blob = shrink ? await shrink(f, 2000) : f;
      const r = await api("/api/letters/read", { method: "POST", body: blob, headers: { "Content-Type": blob.type || "application/octet-stream" } });
      showReview(r.items, "letter", `${r.sender || "The letter"}${r.about ? `: ${r.about}` : ""}`);
    } catch (e) {
      $("#dump-review").replaceChildren(el("p", { class: "error", text: e.message }));
    } finally { $("#letter-photo").value = ""; }
  });

  // ---------- Home ----------
  function home() { loadFreezer(); loadBoxes(); loadGuests(); loadClaims(); loadRoutines(); }

  // freezer
  async function loadFreezer() { const f = await quiet(api("/api/freezer")); if (f) showFreezer(f); }
  function showFreezer(f) {
    $("#freezer-block").hidden = false;
    $("#freezer-summary").textContent = f.items.length ? `${f.count} portion${f.count === 1 ? "" : "s"} in the freezer` +
      (f.old.length ? `. Use up soon: ${f.old.map((i) => i.name).join(", ")}.` : ".") : "Nothing on the freezer list yet.";
    $("#fz-wheres").replaceChildren(...f.wheres.map((w) => el("option", { value: w })));
    fill($("#freezer-list"), f.items.map((i) => el("li", { class: "row wrap" + (i.old ? " is-old" : "") },
      el("span", { class: "grow" }, el("strong", { text: i.name }),
        el("span", { class: "meta", text: ` · ${i.portions} portion${i.portions === 1 ? "" : "s"}${i.where ? ` · ${i.where}` : ""} · in ${i.days === 0 ? "today" : i.days < 14 ? `${i.days} days` : i.days < 60 ? `${Math.floor(i.days / 7)} weeks` : `${Math.floor(i.days / 30)} months`}` })),
      el("span", { class: "btn-row" },
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Used one", "aria-label": `Used one portion of ${i.name}`,
          onclick: (ev) => busy(ev.currentTarget, async () => { const r = await api("/api/freezer/use", { method: "POST", body: { id: i.id } }); showFreezer(r); toast(r.used.gone ? `${i.name}: all gone` : `${i.name}: ${r.used.portions} left`); }) }),
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Take ${i.name} off the freezer list`,
          onclick: (ev) => busy(ev.currentTarget, async () => showFreezer(await api("/api/freezer/remove", { method: "POST", body: { id: i.id } }))) })))),
    "Add what you batch cook or buy frozen, and \"What can we make?\" counts it.");
  }
  $("#freezer-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("#fz-name").value.trim();
    if (!name) { toast("What's going in the freezer?", true); $("#fz-name").focus(); return; }
    busy(ev.submitter, async () => {
      showFreezer(await api("/api/freezer", { method: "POST", body: { name, portions: Number($("#fz-portions").value || 1), where: $("#fz-where").value } }));
      $("#fz-name").value = ""; $("#fz-portions").value = "1"; toast(`${name} is on the freezer list`);
    });
  });

  // boxes
  async function loadBoxes() { const b = await quiet(api("/api/boxes")); if (b) showBoxes(b); }
  function showBoxes(b) {
    $("#boxes-block").hidden = false;
    const where = $("#box-where").value;
    $("#box-where").replaceChildren(...b.places.map((p) => el("option", { value: p, text: p })));
    if (where) $("#box-where").value = where;
    fill($("#boxes-list"), b.boxes.map((x) => el("li", { class: "row wrap" },
      el("span", { class: "grow" }, el("strong", { text: `${x.number}. ${x.name}` }), el("span", { class: "meta", text: ` · ${x.where} · ${x.items.length} thing${x.items.length === 1 ? "" : "s"}` }),
        x.items.length ? el("span", { class: "meta line", text: x.items.slice(0, 6).join(", ") + (x.items.length > 6 ? "..." : "") }) : null),
      el("span", { class: "btn-row" },
        el("a", { class: "btn btn-secondary btn-small", href: `box.html?id=${encodeURIComponent(x.id)}`, text: "Open", "aria-label": `Open box ${x.number}, ${x.name}` }),
        el("a", { class: "btn btn-secondary btn-small", href: `labels.html?ids=${encodeURIComponent(x.id)}`, target: "_blank", rel: "noopener", text: "Label", "aria-label": `Print the label for box ${x.number}` })))),
    "No boxes yet. Start with the Christmas decorations.");
    $("#boxes-print").hidden = !b.boxes.length;
  }
  $("#box-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      const r = await api("/api/boxes", { method: "POST", body: { name: $("#box-name").value, where: $("#box-where").value, items: $("#box-items").value } });
      $("#box-name").value = ""; $("#box-items").value = ""; $("#boxes-add").open = false;
      toast(`Box ${r.number} saved. Print its label below.`); loadBoxes();
    });
  });
  $("#boxes-find").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = $("#boxes-q").value.trim();
    if (!q) return;
    busy(ev.submitter, async () => { $("#boxes-answer").textContent = (await api(`/api/boxes?q=${encodeURIComponent(q)}`)).answer; });
  });

  // guests
  async function loadGuests() { const g = await quiet(api("/api/guests")); if (g) showGuests(g); }
  function showGuests(g) {
    $("#guests-block").hidden = false;
    const next = g.visits[0];
    $("#guests-summary").textContent = next ? (next.now ? `${next.name} is staying until ${shortDate(next.leave)}.` :
      `${next.name} arrives ${next.days_to_go === 1 ? "tomorrow" : `in ${next.days_to_go} days`}: ${next.done} of ${next.checklist.length} ready.`) : "No visits planned.";
    if (document.activeElement !== $("#wifi-ssid")) $("#wifi-ssid").value = g.wifi.ssid;
    $("#wifi-security").value = g.wifi.security;
    if (document.activeElement !== $("#guest-notes")) $("#guest-notes").value = g.notes;
    $("#wifi-password").placeholder = g.wifi.has_password ? "Saved. Leave blank to keep it" : "";
    $("#guest-mode").setAttribute("aria-pressed", String(Boolean(g.mode_until)));
    $("#guest-mode").textContent = g.mode_until ? "Stop showing Wi-Fi on the display" : "Show Wi-Fi on the display";
    $("#guests-visits").replaceChildren(...g.visits.map((v) => el("div", { class: "card stack" },
      el("h3", { text: `${v.name}: ${shortDate(v.arrive)} to ${shortDate(v.leave)}${v.room ? ` · ${v.room}` : ""}` }),
      v.notes ? el("p", { class: "hint", text: v.notes }) : null,
      el("ul", { class: "list compact", "aria-label": `Ready for ${v.name}` }, v.checklist.map((c) => el("li", { class: "check-row" },
        el("input", { type: "checkbox", id: `gv-${v.id}-${c.id}`, checked: c.done || null,
          onchange: (ev) => quiet(api(`/api/guests/tick`, { method: "POST", body: { id: v.id, cid: c.id, done: ev.target.checked } }).then(showGuests)) }),
        el("label", { for: `gv-${v.id}-${c.id}`, text: c.text })))),
      el("div", { class: "btn-row" },
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: v.filed && v.calendar ? "In the to-dos and calendar" : "Put it in the to-dos and calendar",
          disabled: (v.filed && v.calendar) || null, "aria-label": `Put ${v.name}'s visit in the to-dos and calendar`,
          onclick: (ev) => busy(ev.currentTarget, async () => { const r = await api("/api/guests/file", { method: "POST", body: { id: v.id } }); showGuests(r); toast(`Added ${r.result.filed} job${r.result.filed === 1 ? "" : "s"}${r.result.calendar ? " and the calendar entry" : ""}`); }) }),
        el("button", { type: "button", class: "linkish", text: "Remove", "aria-label": `Remove ${v.name}'s visit`,
          onclick: (ev) => { if (!confirm(`Remove ${v.name}'s visit?`)) return; busy(ev.currentTarget, async () => showGuests(await api("/api/guests/remove", { method: "POST", body: { id: v.id } }))); } })))));
  }
  $("#visit-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      showGuests(await api("/api/guests/visit", { method: "POST", body: { name: $("#visit-name").value, arrive: $("#visit-arrive").value,
        leave: $("#visit-leave").value || $("#visit-arrive").value, room: $("#visit-room").value, notes: $("#visit-notes").value } }));
      for (const id of ["visit-name", "visit-arrive", "visit-leave", "visit-room", "visit-notes"]) $(`#${id}`).value = "";
      toast("Visit added, with a checklist");
    });
  });
  $("#wifi-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const body = { ssid: $("#wifi-ssid").value, security: $("#wifi-security").value, notes: $("#guest-notes").value };
    if ($("#wifi-password").value) body.password = $("#wifi-password").value;
    busy(ev.submitter, async () => { showGuests(await api("/api/guests/wifi", { method: "POST", body })); $("#wifi-password").value = ""; toast("Saved"); });
  });
  $("#guest-mode").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const on = ev.currentTarget.getAttribute("aria-pressed") === "true";
    showGuests(await api("/api/guests/mode", { method: "POST", body: { days: on ? 0 : 3 } }));
    toast(on ? "Wi-Fi code off the display" : "The display shows the Wi-Fi code for 3 days");
  }));

  // something's broken
  async function loadClaims() {
    const r = await quiet(api("/api/broken"));
    if (!r) return;
    const STATUS = { drafted: "In Gmail drafts", sent: "Sent", replied: "They replied", sorted: "Sorted", dropped: "Dropped" };
    fill($("#broken-claims"), r.claims.filter((c) => !["sorted", "dropped"].includes(c.status)).map((c) => el("li", { class: "row wrap" },
      el("span", { class: "grow", text: `${c.name}${c.shop ? ` (${c.shop})` : ""}: ${STATUS[c.status] || c.status}` }),
      el("label", { class: "visually-hidden", for: `cl-${c.id}`, text: `Claim for ${c.name}` }),
      el("select", { id: `cl-${c.id}`, onchange: (ev) => quiet(api("/api/broken/status", { method: "POST", body: { id: c.id, status: ev.target.value } }).then(loadClaims)) },
        ...Object.entries(STATUS).map(([v, t]) => el("option", { value: v, text: t, selected: v === c.status || null }))))), "");
    if (!$("#broken-claims").querySelector("li:not(.empty)")) $("#broken-claims").replaceChildren();
  }
  function claimForm(f, i) {
    const fault = el("input", { type: "text", id: `bk-fault-${i}`, autocomplete: "off", placeholder: "For example: it won't switch on" });
    const to = el("input", { type: "email", id: `bk-to-${i}`, autocomplete: "off" });
    return el("div", { class: "stack" },
      el("div", { class: "field" }, el("label", { for: `bk-fault-${i}`, text: `What's wrong with the ${f.name}?` }), fault),
      el("div", { class: "field" }, el("label", { for: `bk-to-${i}`, text: `${f.shop || "The shop"}'s email (optional)` }), to),
      el("div", { class: "btn-row" },
        el("button", { type: "button", class: "btn btn-primary btn-small", text: "Write to the shop (Gmail draft)", "aria-label": `Write to the shop about the ${f.name}`,
          onclick: (ev) => { if (!fault.value.trim()) { toast("Say what's wrong first.", true); fault.focus(); return; }
            busy(ev.currentTarget, async () => { await api("/api/broken/claim", { method: "POST", body: { name: f.name, fault: fault.value, bought: f.bought, shop: f.shop, to: to.value.trim(), link: f.link } });
              toast("It's in your Gmail drafts. Check it and send it from Gmail."); loadClaims(); }); } }),
        el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Get it repaired", "aria-label": `Get the ${f.name} repaired`,
          onclick: (ev) => busy(ev.currentTarget, async () => { await api("/api/broken/repair", { method: "POST", body: { name: f.name, fault: fault.value } }); toast("Added to the to-dos: open it to find a repairer"); }) })));
  }
  $("#broken-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = $("#broken-q").value.trim();
    if (!q) { toast("What's broken?", true); $("#broken-q").focus(); return; }
    busy(ev.submitter, async () => {
      const r = await api(`/api/broken?q=${encodeURIComponent(q)}`);
      const SRC = { warranty: "Warranty", receipt: "Receipt", contents: "Contents list" };
      if (!r.found.length) {
        const f = { name: q, shop: "", bought: "", link: "" };
        $("#broken-result").replaceChildren(el("p", { text: `No receipt or warranty found for "${q}". ${r.general}` }), claimForm(f, 0));
        return;
      }
      $("#broken-result").replaceChildren(...r.found.slice(0, 3).map((f, i) => el("div", { class: "card stack" },
        el("h3", { text: f.name }),
        el("p", { class: "meta", text: [SRC[f.source], f.shop ? `from ${f.shop}` : "", f.bought ? `bought ${shortDate(f.bought)}` : "",
          f.warranty_ends ? `guarantee to ${shortDate(f.warranty_ends)}` : "", f.files ? `${f.files} photo or receipt` : ""].filter(Boolean).join(" · ") }),
        el("p", { text: f.rights.text }),
        safeLink(f.link) ? el("a", { href: safeLink(f.link), target: "_blank", rel: "noopener noreferrer", text: "Open the receipt email", class: "linkish" }) : null,
        claimForm(f, i))));
    });
  });

  // kids' routines (grown-ups set the steps and times)
  async function loadRoutines() {
    const r = await quiet(api("/api/routines/settings"));
    if (!r) return;
    const kids = Object.keys(r.kids);
    $("#routines-block").hidden = !kids.length;
    $("#routines-kids").replaceChildren(...kids.map((kid) => {
      const c = r.kids[kid];
      const slug = kid.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const area = (when) => el("div", { class: "field" }, el("label", { for: `rt-${slug}-${when}`, text: `${kid}'s ${when} steps (one per line)` }),
        el("textarea", { id: `rt-${slug}-${when}`, rows: "5", text: c[when].map((s) => `${s.icon} ${s.text}`).join("\n") }));
      return el("details", { class: "card" }, el("summary", { text: `${kid}: leave at ${c.leave_at}, bed at ${c.bed_at}` }),
        el("div", { class: "stack" }, area("morning"), area("bedtime"),
          el("div", { class: "field" }, el("label", { for: `rt-${slug}-leave`, text: `${kid} leaves the house at` }), el("input", { type: "time", id: `rt-${slug}-leave`, value: c.leave_at })),
          el("div", { class: "field" }, el("label", { for: `rt-${slug}-bed`, text: `${kid}'s bedtime` }), el("input", { type: "time", id: `rt-${slug}-bed`, value: c.bed_at })),
          el("button", { type: "button", class: "btn btn-primary btn-block", text: "Save routines", "aria-label": `Save ${kid}'s routines`,
            onclick: (ev) => busy(ev.currentTarget, async () => {
              const steps = (when) => $(`#rt-${slug}-${when}`).value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
                const m = l.match(/^(\p{Extended_Pictographic}️?)\s*(.*)$/u);
                return m ? { icon: m[1], text: m[2] } : { text: l };
              });
              await api("/api/routines/settings", { method: "POST", body: { kid, morning: steps("morning"), bedtime: steps("bedtime"),
                leave_at: $(`#rt-${slug}-leave`).value, bed_at: $(`#rt-${slug}-bed`).value } });
              toast(`${kid}'s routines saved`); loadRoutines();
            }) })));
    }));
  }

  // ---------- Settings ----------
  function settings() { loadTomorrow(); }
  $("#gsetup-run").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/google/setup-check");
    $("#gsetup-summary").textContent = r.summary;
    const ICON = { ok: "✓", todo: "○", problem: "✗", unknown: "?" };
    const STATE = { ok: "done", todo: "still to do", problem: "needs fixing", unknown: "check this yourself" };
    const ol = $("#gsetup-steps");
    ol.hidden = !r.steps.length;
    ol.replaceChildren(...r.steps.map((s) => el("li", { class: `gs-${s.status}` },
      el("span", { class: "gs-icon", "aria-hidden": "true", text: ICON[s.status] }),
      el("span", { class: "grow" }, el("strong", { text: s.title }), el("span", { class: "visually-hidden", text: ` (${STATE[s.status]})` }),
        el("span", { class: "meta line", text: s.detail }),
        s.copy ? el("span", { class: "row wrap" }, el("code", { text: s.copy }),
          el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Copy", "aria-label": `Copy ${s.copy}`,
            onclick: async () => { try { await navigator.clipboard.writeText(s.copy); toast("Copied"); } catch { toast("Couldn't copy: select it and copy instead.", true); } } })) : null,
        safeLink(s.link) ? el("a", { href: safeLink(s.link), target: "_blank", rel: "noopener noreferrer", class: "btn btn-secondary btn-small", text: s.link_text || "Open Google Cloud" }) : null))));
  }));
  $("#mailtodo-run").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
    const r = await api("/api/mailtodo/run", { method: "POST" });
    toast(r.added.length ? `Added: ${r.added.join("; ")}` : "No emails with the Life OS label just now");
  }));
  async function loadTomorrow() {
    const r = await quiet(api("/api/tomorrow"));
    if (!r) return;
    $("#tm-on").checked = r.settings.on; $("#tm-at").value = r.settings.at; $("#tm-speak").checked = r.settings.speak;
    $("#tm-preview").textContent = r.preview.text ? `Tomorrow's would say: ${r.preview.text}` : "Nothing for tomorrow's message yet.";
  }
  $("#tomorrow-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(ev.submitter, async () => {
      const r = await api("/api/tomorrow", { method: "POST", body: { on: $("#tm-on").checked, at: $("#tm-at").value, speak: $("#tm-speak").checked } });
      $("#tm-preview").textContent = r.preview.text ? `Tomorrow's would say: ${r.preview.text}` : "Nothing for tomorrow's message yet.";
      toast("Saved");
    });
  });

  window.LifeOSNext = { home, settings };
})();
