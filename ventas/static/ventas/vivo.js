// Partes de la pantalla que se actualizan solas: lo que lleve data-vivo="<dirección>" se vuelve a pedir cada 5 segundos
// y se reemplaza si cambió (la fila de turnos, lo que hay por cobrar en caja). No se toca mientras la persona tiene algo
// abierto o está escribiendo ahí. Para no tener despierta la base de datos con una pantalla olvidada, descansa cuando
// la pestaña no se ve o si pasan 30 minutos sin movimiento; al volver se actualiza al instante.
(() => {
  if (!window.fetch) return;
  const CADA = 5000, DESCANSO = 30 * 60 * 1000;
  const visto = new WeakMap(); // lo último que mandó el servidor, para saber si cambió
  let senal = Date.now();
  const limpio = (html) => html.replace(/name="csrfmiddlewaretoken" value="[^"]*"/g, "");
  const despierta = () => document.visibilityState === "visible" && Date.now() - senal < DESCANSO;
  const ocupado = (el) => Boolean(document.querySelector("dialog[open]") || el.querySelector("details[open]") ||
    (el.contains(document.activeElement) && document.activeElement.matches("input, select, textarea")));

  const pedir = async (el) => {
    if (el.dataset.vivoPidiendo) return;
    el.dataset.vivoPidiendo = "1";
    try {
      const r = await fetch(el.dataset.vivo, { credentials: "same-origin" });
      if (!r.ok || r.redirected) return; // sin conexión o la sesión venció: se queda como está
      const html = await r.text();
      if (!visto.has(el)) visto.set(el, limpio(el.innerHTML.trim()) === limpio(html.trim()) ? limpio(html) : "");
      if (limpio(html) === visto.get(el) || ocupado(el)) return;
      const foco = el.contains(document.activeElement);
      el.innerHTML = html;
      visto.set(el, limpio(html));
      senal = Date.now(); // hubo movimiento en el negocio: sigue despierta
      if (foco) el.querySelector("button:not([disabled]), a[href]")?.focus({ preventScroll: true });
      el.dispatchEvent(new CustomEvent("vivo:cambio", { bubbles: true }));
    } catch { /* sin conexión: se intenta en la siguiente vuelta */ } finally {
      delete el.dataset.vivoPidiendo;
    }
  };
  const todos = () => document.querySelectorAll("[data-vivo]").forEach(pedir);

  setInterval(() => { if (despierta()) todos(); }, CADA);
  const volver = () => {
    const dormia = !despierta();
    senal = Date.now();
    if (dormia && document.visibilityState === "visible") todos();
  };
  ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"].forEach((t) => document.addEventListener(t, volver, { passive: true }));
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { senal = Date.now(); todos(); } });
  // al cerrar lo que estaba abierto, se pone al día
  document.addEventListener("toggle", (e) => {
    const el = e.target.closest && e.target.closest("[data-vivo]");
    if (el && !e.target.open) pedir(el);
  }, true);
  window.erpVivo = todos;
})();
