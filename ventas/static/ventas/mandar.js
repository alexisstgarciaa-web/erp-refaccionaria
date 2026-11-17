// Mandar una cotización o una remisión al cliente en PDF sin salir de la página: se abre una ventana encima con
// WhatsApp (el menú de compartir del aparato, con el PDF ya adjunto) y correo (desde el servidor si tiene cuenta; si
// no, también por el menú de compartir). Cualquier botón con data-mandar="<dirección de la ventana>" la abre, y el
// botón «Mandar…» de Cotizar guarda la cotización y la abre ahí mismo.
(() => {
  if (!window.fetch || !window.HTMLDialogElement) return;
  let dialogo = null;
  let regreso = null; // a dónde vuelve el cursor al cerrar
  let enlacePdf = null; // el PDF ya bajado, para descargarlo sin volver a pedirlo

  const crear = () => {
    dialogo = document.createElement("dialog");
    dialogo.className = "mandar-dialogo";
    dialogo.setAttribute("aria-labelledby", "mandar-titulo");
    document.body.append(dialogo);
    dialogo.addEventListener("click", (e) => {
      if (e.target === dialogo || e.target.closest("[data-cerrar]")) dialogo.close();
    });
    dialogo.addEventListener("close", () => {
      if (enlacePdf) URL.revokeObjectURL(enlacePdf);
      enlacePdf = null;
      dialogo.replaceChildren();
      // si el botón quedó dentro de un «Cotizar» ya cerrado, el cursor vuelve a «Cotizar»
      const destino = regreso?.closest("details:not([open])")?.querySelector("summary") || regreso;
      if (destino && document.contains(destino)) destino.focus({ preventScroll: true });
    });
  };

  const fallo = (texto) => {
    dialogo.innerHTML = '<div class="mandar"><p class="mandar-cargando"></p>' +
      '<p class="mandar-pie"><button type="button" class="btn-sec" data-cerrar>Cerrar</button></p></div>';
    dialogo.querySelector(".mandar-cargando").textContent = texto;
  };

  const abrir = async (direccion, desde) => {
    if (!dialogo) crear();
    if (!dialogo.open) regreso = desde || document.activeElement;
    dialogo.innerHTML = '<p class="mandar-cargando">Preparando…</p>';
    if (!dialogo.open) dialogo.showModal();
    let r = null, html = "";
    try {
      r = await fetch(direccion, { credentials: "same-origin" });
      html = await r.text();
    } catch { /* sin conexión */ }
    if (r && r.redirected) { window.location.assign(r.url); return; } // la sesión venció: a entrar otra vez
    if (!r || !r.ok || !html.includes('class="mandar"')) {
      fallo("No se pudo abrir. Revisa la conexión e intenta otra vez.");
      return;
    }
    dialogo.innerHTML = html;
    preparar(dialogo.querySelector(".mandar"));
  };

  const decir = (nodo, texto, tono = "") => {
    if (!nodo) return;
    nodo.textContent = texto;
    if (tono) nodo.dataset.tono = tono; else delete nodo.dataset.tono;
  };
  const clic = (href, descarga) => { // descargar o abrir WhatsApp / el correo sin salir de la página
    const a = Object.assign(document.createElement("a"), { href });
    if (descarga) a.download = descarga;
    document.body.append(a);
    a.click();
    a.remove();
  };

  const preparar = (caja) => {
    const datos = caja.dataset;
    const $ = (sel) => caja.querySelector(sel);
    const estado = $("[data-pdf-estado]"), descargar = $("[data-descargar]");
    const wa = $('[data-medio="whatsapp"]'), correo = $('[data-medio="correo"]');
    const notaWa = $('[data-nota="whatsapp"]'), notaCorreo = $('[data-nota="correo"]');
    let pdf = null;
    const compartible = () => Boolean(pdf && navigator.canShare && navigator.canShare({ files: [pdf] }));

    // El PDF se baja en cuanto abre la ventana: así, al dar clic, el menú de compartir sale de inmediato
    fetch(datos.pdf, { credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.blob(); })
      .then((b) => {
        if (!caja.isConnected) return;
        pdf = new File([b], datos.archivo, { type: "application/pdf" });
        enlacePdf = URL.createObjectURL(pdf);
        descargar.href = enlacePdf;
        decir(estado, `Listo · ${Math.max(1, Math.round(b.size / 1024))} KB`);
        [wa, correo].forEach((b) => { if (b) b.disabled = false; });
        if (!compartible()) { // sin menú de compartir con archivos (p. ej. Firefox en la compu)
          wa.textContent = "Descargar el PDF y abrir WhatsApp";
          decir(notaWa, "Se descarga el PDF y se abre WhatsApp" + (datos.numero ? " en el chat del cliente" : "") +
                        ": ahí lo adjuntas desde Descargas.");
          if (correo) {
            correo.textContent = "Descargar el PDF y abrir mi correo";
            decir(notaCorreo, "Se descarga el PDF y se abre tu correo con el mensaje escrito: lo adjuntas desde Descargas.");
          }
        }
        const a = document.activeElement;
        if (!a || !caja.contains(a) || a.matches("[data-cerrar]")) wa.focus();
      })
      .catch(() => decir(estado, "No se pudo preparar el PDF. Cierra la ventana y vuelve a intentar.", "error"));

    const compartir = async (nota, datosCompartir, siNo) => {
      if (!pdf) return;
      if (!compartible()) { siNo(); return; }
      try {
        await navigator.share({ files: [pdf], ...datosCompartir });
        decir(nota, "Listo: el PDF pasó a la app que elegiste.", "ok");
      } catch (e) {
        if (e.name !== "AbortError") decir(nota, "No se abrió el menú de compartir. Usa «Descargar» y adjunta el PDF.", "error");
      }
    };

    wa.addEventListener("click", () => compartir(notaWa, { text: datos.texto }, () => {
      clic(enlacePdf, datos.archivo);
      clic("whatsapp://send?" + (datos.numero ? `phone=${datos.numero}&` : "") + "text=" + encodeURIComponent(datos.texto));
      decir(notaWa, `Se descargó ${datos.archivo}. Adjúntalo en el chat de WhatsApp.`, "ok");
    }));

    correo?.addEventListener("click", () => {
      if (datos.correo) navigator.clipboard?.writeText(datos.correo).catch(() => {});
      compartir(notaCorreo, { title: datos.asunto, text: datos.cuerpo }, () => {
        clic(enlacePdf, datos.archivo);
        clic("mailto:" + encodeURIComponent(datos.correo).replace(/%40/g, "@") + "?subject=" +
             encodeURIComponent(datos.asunto) + "&body=" + encodeURIComponent(datos.cuerpo));
        decir(notaCorreo, `Se descargó ${datos.archivo}. Adjúntalo en el correo.`, "ok");
      });
    });

    // Con cuenta de correo en el servidor: sale de ahí con el PDF adjunto, sin abrir nada
    const forma = $("[data-correo-servidor]");
    forma?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const boton = forma.querySelector("button[type=submit]");
      boton.disabled = true;
      decir(notaCorreo, "Mandando…");
      try {
        const r = await fetch(forma.action, { method: "POST", body: new FormData(forma), credentials: "same-origin" });
        const j = await r.json();
        decir(notaCorreo, j.ok ? j.mensaje : j.error, j.ok ? "ok" : "error");
      } catch {
        decir(notaCorreo, "No salió el correo: revisa la conexión e intenta otra vez.", "error");
      }
      boton.disabled = false;
    });
  };

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mandar]");
    if (!b) return;
    e.preventDefault();
    abrir(b.dataset.mandar, b);
  });

  // Cotizar › «Mandar por WhatsApp o correo»: guarda la cotización y abre la ventana ahí mismo
  document.addEventListener("submit", async (e) => {
    const f = e.target, boton = e.submitter;
    if (!(f instanceof HTMLFormElement) || !f.hasAttribute("data-mandar-form") || !boton || boton.value !== "enviar") return;
    e.preventDefault();
    const datos = new FormData(f);
    datos.set(boton.name, boton.value);
    boton.disabled = true;
    f.querySelector(".cotizar-error")?.remove();
    let error = "No se pudo guardar la cotización. Intenta otra vez.";
    try {
      const r = await fetch(f.action, { method: "POST", body: datos, credentials: "same-origin", headers: { "X-Mandar": "1" } });
      const j = await r.json().catch(() => ({}));
      if (j.mandar) {
        f.closest("details")?.removeAttribute("open");
        abrir(j.mandar, boton);
        return;
      }
      if (j.error) error = j.error;
    } catch { /* sin conexión */ } finally {
      boton.disabled = false;
    }
    f.append(Object.assign(document.createElement("p"), { className: "cotizar-error", role: "alert", textContent: error }));
  });

  const sola = document.querySelector("[data-mandar-abrir]");
  if (sola) abrir(sola.dataset.mandarAbrir);
})();
