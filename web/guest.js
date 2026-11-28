/* The guest sheet: Wi-Fi with a code to scan, and the house notes. Printed for the fridge or the spare room. */
(() => {
  "use strict";
  const { $, el, api } = window.LifeOSPage;
  (async () => {
    try {
      const s = await api("/api/guests/sheet");
      if (s.visit) $("#guest-title").textContent = `Welcome, ${s.visit.name}!`;
      $("#guest-wifi").hidden = !s.wifi;
      if (s.wifi) {
        $("#gw-ssid").textContent = s.wifi.ssid;
        $("#gw-pass").textContent = s.wifi.password;
        $("#gw-pass-line").hidden = !s.wifi.password;
        $("#gw-qr").replaceChildren(window.LifeOSQR.draw(s.wifi.qr, 180, `Code to join the Wi-Fi ${s.wifi.ssid}`));
      }
      $("#guest-notes-list").replaceChildren(...s.notes.map((n) => el("li", { text: n })));
      $("#guest-notes-block").hidden = !s.notes.length;
      $("#guest-status").textContent = s.wifi || s.notes.length ? "" : "Add the Wi-Fi and some house notes on the Home screen (Guests) first.";
      $("#guest-sheet").hidden = false;
    } catch (e) { $("#guest-status").textContent = e.message; }
  })();
  $("#guest-print").addEventListener("click", () => window.print());
})();
