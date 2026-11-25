// Life OS first-run setup. Plain DOM, no framework; text set with textContent only.
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const status = (t) => { $("#status").textContent = t; };

  async function call(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { /* not json */ }
    if (!res.ok) throw new Error((data && data.detail) || `Something went wrong (${res.status}).`);
    return data;
  }

  // After the restart the full app answers /health with its version (the setup page answers {setup: true}).
  async function waitForApp(mode) {
    status(mode === "demo" ? "Opening the sample household…" : "Starting Life OS…");
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        const r = await fetch("/health", { cache: "no-store" });
        const h = await r.json();
        if (!h.setup) { location.href = "/app/#today"; return; }
      } catch { /* restarting */ }
    }
    status("Life OS is taking a while to start. Close this tab and open Life OS again.");
  }

  async function start(mode, button) {
    button.disabled = true;
    try {
      const r = await call("/setup/api/start", { mode });
      if (r.ok === false) { status(r.detail); button.disabled = false; return; }
      await waitForApp(mode);
    } catch (e) { status(e.message); button.disabled = false; }
  }

  async function load() {
    let s;
    try { s = await call("/setup/api/state"); } catch (e) { status(e.message); return; }
    $("#where").textContent = `Life OS ${s.version}. Settings are kept in ${s.home}`;
    if (s.adults[0]) $("#adult1").value = s.adults[0];
    if (s.adults[1]) $("#adult2").value = s.adults[1];
    $("#kids").value = (s.kids || []).join(", ");
    $("#area").value = s.area || "";
    $("#public-url").value = s.public_url || "";
    if (s.old && s.old.length) {
      const o = s.old[0];
      const who = o.names.length ? ` for ${o.names.join(" and ")}` : "";
      const g = o.google.length ? `, with ${o.google.length} Google account${o.google.length === 1 ? "" : "s"} connected` : "";
      $("#old-text").textContent = `Found Life OS settings${who} in ${o.path}${g}.`;
      $("#old-block").hidden = false;
      $("#old-bring").onclick = async (ev) => {
        ev.target.disabled = true;
        try {
          const r = await call("/setup/api/bring", { path: o.path });
          status(`Brought across${r.names.length ? ` for ${r.names.join(" and ")}` : ""}.`);
          if (r.ready) { await start("normal", ev.target); return; }
          $("#old-block").hidden = true;
          load();
        } catch (e) { status(e.message); ev.target.disabled = false; }
      };
    }
    $("#adult1").focus();
  }

  $("#setup-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("#form-error");
    err.textContent = "";
    const adults = [$("#adult1").value.trim(), $("#adult2").value.trim()].filter(Boolean);
    if (!adults.length) { err.textContent = "Type your first name."; $("#adult1").focus(); return; }
    const provider = document.querySelector("input[name=provider]:checked").value;
    const key = $("#ai-key").value.trim();
    const btn = ev.submitter;
    btn.disabled = true;
    try {
      const r = await call("/setup/api/save", {
        adults, kids: $("#kids").value.split(",").map((k) => k.trim()).filter(Boolean), area: $("#area").value.trim(),
        provider, anthropic_key: provider === "claude" ? key : "", gemini_key: provider === "gemini" ? key : "",
        public_url: $("#public-url").value.trim(), ha_url: $("#ha-url").value.trim(), ha_token: $("#ha-token").value.trim(),
      });
      $("#setup-form").hidden = true; $("#old-block").hidden = true; $("#demo-block").hidden = true; $("#intro").hidden = true;
      $("#key-show").textContent = r.household_key;
      $("#done-block").hidden = false;
      $("#done-title").focus();
    } catch (e) { err.textContent = e.message; btn.disabled = false; }
  });

  $("#key-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#key-show").textContent); status("Copied the household key."); }
    catch { status("Couldn't copy here: write it down instead."); }
  });
  $("#open-app").addEventListener("click", (ev) => start("normal", ev.currentTarget));
  $("#demo").addEventListener("click", (ev) => start("demo", ev.currentTarget));
  load();
})();
