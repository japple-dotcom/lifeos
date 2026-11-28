/* Printable box labels: big number and name, where it lives, a few things inside, and the QR code to scan. */
(() => {
  "use strict";
  const { $, el, api } = window.LifeOSPage;
  const ids = new URLSearchParams(location.search).get("ids") || "";
  (async () => {
    try {
      const r = await api(`/api/boxes/labels${ids ? `?ids=${encodeURIComponent(ids)}` : ""}`);
      $("#labels").replaceChildren(...r.labels.map((b) => el("section", { class: "label", "aria-label": `Label for box ${b.number}` },
        el("div", { class: "label-text" },
          el("p", { class: "label-no", text: `Box ${b.number}` }), el("h2", { text: b.name }), el("p", { class: "label-where", text: b.where }),
          b.top.length ? el("p", { class: "label-top", text: b.top.join(", ") + (b.count > b.top.length ? "..." : "") }) : null,
          el("p", { class: "label-hint", text: "Scan to see everything inside" })),
        window.LifeOSQR.draw(b.qr, 150, `Code for box ${b.number}`))));
      $("#labels-status").textContent = r.labels.length ? (r.https ? "" : "These codes use this computer's address, so only work on this computer until phone access is set up.")
        : "No boxes yet: add some on the Home screen.";
    } catch (e) { $("#labels-status").textContent = e.message; }
  })();
  $("#labels-print").addEventListener("click", () => window.print());
})();
