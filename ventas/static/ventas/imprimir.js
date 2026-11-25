// Imprimir sin abrir otra pestaña: el papel (el ticket o el turno) se carga en un marco oculto y se manda a la impresora.
// Un botón con data-imprimir="dirección [otra…]" lo imprime al dar clic. Con data-imprimir-al-abrir="nombre" se imprime
// solo al cargar la página (el ticket al cobrar, el turno al tomarlo), salvo que en este aparato se haya apagado
// (localStorage «imprimir-nombre» = «no»), que es lo que guarda una casilla con data-imprimir-recordar="nombre".
(() => {
  const uno = (url) => new Promise((listo) => {
    const marco = document.createElement("iframe");
    marco.className = "marco-imprimir";
    marco.setAttribute("aria-hidden", "true");
    marco.tabIndex = -1;
    let fin = false;
    const terminar = () => {
      if (fin) return;
      fin = true;
      setTimeout(() => { marco.remove(); listo(); }, 300);
    };
    marco.addEventListener("load", () => {
      const w = marco.contentWindow;
      if (!w) { terminar(); return; }
      w.addEventListener("afterprint", terminar, { once: true });
      const fuentes = (w.document.fonts && w.document.fonts.ready) || Promise.resolve();
      fuentes.then(() => setTimeout(() => {
        try { w.print(); } catch { terminar(); } // sin quitarle el cursor a quien está tecleando el siguiente turno
      }, 150));
      setTimeout(terminar, 120000); // por si el navegador no avisa que terminó
    });
    marco.addEventListener("error", terminar);
    marco.src = url;
    document.body.append(marco);
  });
  let cola = Promise.resolve();
  const imprimir = (urls) => {
    urls.filter(Boolean).forEach((u) => { cola = cola.then(() => uno(u)); });
    return cola;
  };
  const quiere = (nombre) => {
    try { return localStorage.getItem("imprimir-" + nombre) !== "no"; } catch { return true; }
  };
  window.erpImprimir = { imprimir, quiere };

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-imprimir]");
    if (!b) return;
    e.preventDefault();
    imprimir(b.dataset.imprimir.split(/\s+/));
  });
  // casilla «imprimir al…»: se recuerda en este aparato (data-imprimir-recordar="nombre")
  document.querySelectorAll("input[type=checkbox][data-imprimir-recordar]").forEach((casilla) => {
    const nombre = casilla.dataset.imprimirRecordar;
    casilla.checked = quiere(nombre);
    casilla.addEventListener("change", () => {
      try { localStorage.setItem("imprimir-" + nombre, casilla.checked ? "si" : "no"); } catch { /* sin almacenamiento */ }
    });
  });
  const solo = document.querySelector("[data-imprimir-al-abrir]");
  if (solo && quiere(solo.dataset.imprimirAlAbrir)) imprimir(solo.dataset.imprimir.split(/\s+/));
})();
