/* Shared by the small stand-alone pages (box, labels, guest sheet): $, el, and a signed-in fetch. */
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
  async function api(path, body) {
    const res = await fetch(path, body ? { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) } : { credentials: "same-origin" }).catch(() => null);
    if (!res) throw new Error("Can't reach Life OS just now.");
    if (res.status === 401) throw new Error("Sign in to Life OS on this phone first, then scan or open this again.");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work.");
    return data;
  }
  window.LifeOSPage = { $, el, api };
})();
