/* One box's contents (opened by scanning its label): see what's inside, add things, take things out. */
(() => {
  "use strict";
  const { $, el, api } = window.LifeOSPage;
  const id = new URLSearchParams(location.search).get("id") || "";
  const status = (t) => { $("#box-status").textContent = t; };
  function show(b) {
    document.title = `${b.name} · Life OS`;
    $("#box-title").textContent = `Box ${b.number}: ${b.name}`;
    $("#box-where-line").textContent = `${b.where}. ${b.items.length} thing${b.items.length === 1 ? "" : "s"} in it.`;
    $("#box-things").replaceChildren(...(b.items.length ? b.items.map((i) => el("li", { class: "row wrap" }, el("span", { class: "grow", text: i }),
      el("button", { type: "button", class: "btn btn-secondary btn-small", text: "Taken out", "aria-label": `Took ${i} out of the box`,
        onclick: async () => { try { show(await api("/api/boxes/take-out", { id, item: i })); status(`${i}: off the list`); } catch (e) { status(e.message); } } })))
      : [el("li", { class: "empty", text: "Nothing listed yet." })]));
    if (b.places) $("#box-edit-where").replaceChildren(...b.places.map((p) => el("option", { value: p, text: p })));
    $("#box-edit-where").value = b.where; $("#box-edit-name").value = b.name;
    $("#box-label").href = `labels.html?ids=${encodeURIComponent(b.id)}`;
    $("#box").hidden = false;
  }
  async function load() {
    try { show(await api(`/api/boxes/${encodeURIComponent(id)}`)); status(""); } catch (e) { status(e.message); }
  }
  $("#box-add").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const items = $("#box-add-items").value.trim();
    if (!items) { status("Type what you're putting in."); return; }
    try { const b = await api("/api/boxes/items", { id, items }); $("#box-add-items").value = ""; show({ ...b, places: null }); status("Added"); }
    catch (e) { status(e.message); }
  });
  $("#box-edit").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await api("/api/boxes", { id, name: $("#box-edit-name").value, where: $("#box-edit-where").value }); await load(); status("Saved"); }
    catch (e) { status(e.message); }
  });
  $("#box-delete").addEventListener("click", async () => {
    if (!confirm("Delete this box from the list? (The box itself stays where it is.)")) return;
    try { await api("/api/boxes/remove", { id }); location.href = "./#home"; } catch (e) { status(e.message); }
  });
  load();
})();
