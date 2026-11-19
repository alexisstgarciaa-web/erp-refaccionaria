// Enter pasa al siguiente campo en lugar de guardar; en el último campo, Enter guarda.
// Shift+Enter regresa al anterior y Ctrl+Enter guarda desde cualquier campo.
// Solo en formularios que guardan algo (POST) y que tienen más de un campo. Las búsquedas (GET) y los
// de un solo campo siguen como siempre, y los que ya tienen su propio Enter (captura por código, cobro)
// llevan data-enter="normal". Los campos ocultos o deshabilitados (p. ej. la dirección cerrada) se saltan.
(() => {
  const SIN_CAMPO = /^(hidden|submit|button|reset|image|file)$/;
  const TEXTO = /^(text|search|tel|url|email|password|number)$/;

  // checkVisibility también descarta lo que está dentro de un <details> cerrado
  const visible = (el) => (el.checkVisibility ? el.checkVisibility({ visibilityProperty: true })
    : el.getClientRects().length > 0 && !el.closest("details:not([open]) > :not(summary)"));

  const campos = (form) => {
    const grupos = new Set(), lista = [];
    for (const el of form.elements) {
      if (!/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) continue;
      if (el.tagName === "INPUT" && SIN_CAMPO.test(el.type)) continue;
      if (el.disabled || el.readOnly || !visible(el)) continue;
      if (el.type === "radio") { // un grupo de opciones cuenta como un campo
        if (grupos.has(el.name)) continue;
        grupos.add(el.name);
      }
      lista.push(el);
    }
    return lista;
  };

  const mismo = (a, b) => a === b || (a.type === "radio" && b.type === "radio" && a.name === b.name);

  const enfocar = (el) => {
    if (el.type === "radio") {
      const elegido = [...el.form.elements].find((r) => r.type === "radio" && r.name === el.name && r.checked);
      (elegido || el).focus();
      return;
    }
    el.focus();
    if (el.tagName === "INPUT" && TEXTO.test(el.type)) {
      try { el.select(); } catch (e) { /* number en algunos navegadores */ }
    }
  };

  const guardar = (form) => {
    const boton = [...form.elements].find((b) => b.type === "submit" && !b.disabled);
    if (form.requestSubmit) form.requestSubmit(boton);
    else form.submit();
  };

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.defaultPrevented || e.isComposing || e.altKey) return;
    const el = e.target, form = el && el.form;
    if (!form || form.method !== "post" || form.dataset.enter === "normal" || form.id === "changelist-form") return;
    if (el.tagName === "BUTTON" || (el.tagName === "INPUT" && SIN_CAMPO.test(el.type))) return;
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); guardar(form); return; }
    if (el.tagName === "TEXTAREA") return; // Enter es salto de línea
    const lista = campos(form);
    const i = lista.findIndex((c) => mismo(c, el));
    if (i < 0 || lista.length < 2) return;
    e.preventDefault();
    if (e.shiftKey) { if (i > 0) enfocar(lista[i - 1]); return; }
    if (i < lista.length - 1) enfocar(lista[i + 1]);
    else guardar(form);
  });
})();

// Que el campo con el foco no quede tapado por la barra de arriba ni por la de cobro (fija abajo): al pasar
// con Tab o Enter la página se mueve lo necesario. El navegador solo cuida que quede dentro de la ventana,
// no que se vea.
(() => {
  const MARGEN = 12;
  const fija = (el) => el && /^(fixed|sticky)$/.test(getComputedStyle(el).position);
  document.addEventListener("focusin", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLElement) || el.closest(".barra, .barra-cobro, dialog")) return;
    requestAnimationFrame(() => { // después de lo que ya movió el navegador
      if (document.activeElement !== el) return;
      const arriba = document.querySelector(".barra"), abajo = document.querySelector(".barra-cobro");
      const tope = fija(arriba) ? arriba.getBoundingClientRect().bottom : 0;
      const fondo = fija(abajo) ? abajo.getBoundingClientRect().top : window.innerHeight;
      const r = el.getBoundingClientRect();
      if (r.bottom > fondo - MARGEN) window.scrollBy(0, Math.min(r.bottom - fondo + MARGEN, r.top - tope - MARGEN));
      else if (r.top < tope + MARGEN) window.scrollBy(0, r.top - tope - MARGEN);
    });
  });
})();
