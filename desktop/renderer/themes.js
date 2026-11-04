// CapWizard styles: everyone picks the look they like in Impostazioni → Stile dell'app (saved on this Mac only).
// To add a style: write themes/<id>.css (tokens over themes/skin.css) and add it to STYLES.
(function () {
  const STYLES = [
    { id: "zenus", name: "Zenus", desc: "Antracite piatto, bordi sottili, un tocco di viola.", sw: ["#111113", "#1f1f23", "#8b7cf6", "#f4f4f5"] },
    { id: "sabbia", name: "Sabbia", desc: "Chiaro e caldo, vetro smerigliato, titoli eleganti.", sw: ["#ebe5dc", "#ffffff", "#2b2724", "#b8a48c"] },
    { id: "vetro", name: "Vetro", desc: "Vetro scuro su una luce arancio e viola.", sw: ["#2a292d", "#ff7a30", "#7858b2", "#ffffff"] },
    { id: "cromo", name: "Cromo", desc: "Nero freddo, titoli in argento, metallo.", sw: ["#14161b", "#eef0f3", "#9aa0aa", "#6f7580"] },
    { id: "classico", name: "Classico", desc: "L'originale: nero e azzurro.", sw: ["#050506", "#15171c", "#8fa2ff", "#e8eaf0"] },
  ];
  const KEY = "cw-style";
  const files = (id) => (id === "classico" ? [] : ["themes/skin.css", `themes/${id}.css`]);
  const valid = (id) => STYLES.some((s) => s.id === id);
  let current = new URLSearchParams(location.search).get("style");
  if (!valid(current)) { try { current = localStorage.getItem(KEY); } catch (e) { current = null; } }
  if (!valid(current)) current = "zenus";
  document.documentElement.dataset.style = current;
  // first paint: parser-inserted links, so the page never shows in the wrong style
  document.write(files(current).map((f) => `<link rel="stylesheet" class="cw-style" href="${f}">`).join(""));

  function apply(id) {
    if (!valid(id)) return;
    document.querySelectorAll("link.cw-style").forEach((l) => l.remove());
    for (const f of files(id)) {
      const l = document.createElement("link"); l.rel = "stylesheet"; l.className = "cw-style"; l.href = f;
      document.head.appendChild(l);
    }
    current = id; document.documentElement.dataset.style = id;
    try { localStorage.setItem(KEY, id); } catch (e) { /* private storage off: it just won't be remembered */ }
    render();
  }

  function card(s) {
    const b = document.createElement("button");
    b.className = "style-card" + (s.id === current ? " on" : "");
    b.innerHTML = `<span class="sc-prev" style="--a:${s.sw[0]};--b:${s.sw[1]};--c2:${s.sw[2]};--d:${s.sw[3]}">
        <i class="sc-side"></i><i class="sc-k1"></i><i class="sc-k2"></i><i class="sc-btn"></i></span>
      <span class="sc-txt"><b>${s.name}</b><small>${s.desc}</small></span>${s.id === current ? '<em>in uso</em>' : ""}`;
    b.onclick = () => apply(s.id);
    return b;
  }
  function render() {
    const g = document.getElementById("style-grid");
    if (g) { g.replaceChildren(...STYLES.map(card)); }
    const n = document.getElementById("style-name");
    if (n) n.textContent = (STYLES.find((s) => s.id === current) || {}).name || "";
  }
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("btn-style"), modal = document.getElementById("style-modal");
    if (!btn || !modal) return;
    btn.onclick = () => { const m = document.getElementById("settings-menu"); if (m) m.hidden = true; render(); modal.hidden = false; };
    document.getElementById("style-close").onclick = () => { modal.hidden = true; };
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
    render();
  });
  window.CapWizardStyles = { list: STYLES, apply, get current() { return current; } };
})();
