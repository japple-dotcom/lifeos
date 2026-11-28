/* Draws a QR code from rows of "0"/"1" (worked out by the server) as an SVG built with DOM calls, never markup.
   Dark on white with a quiet zone, whatever the page colours. window.LifeOSQR.draw(rows, sizePx, label). */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  function draw(rows, size, label) {
    const n = rows.length + 8;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${n} ${n}`);
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("role", "img");
    if (label) svg.setAttribute("aria-label", label);
    const bg = document.createElementNS(NS, "rect");
    bg.setAttribute("width", String(n)); bg.setAttribute("height", String(n)); bg.setAttribute("fill", "#fff");
    svg.append(bg);
    let d = "";
    rows.forEach((row, r) => { for (let c = 0; c < row.length; c++) if (row[c] === "1") d += `M${c + 4},${r + 4}h1v1h-1z`; });
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d); path.setAttribute("fill", "#000");
    svg.append(path);
    return svg;
  }
  window.LifeOSQR = { draw };
})();
