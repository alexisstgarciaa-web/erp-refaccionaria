// Buscador de clientes (ticket del mostrador y Remisiones › Nueva): folio, celular o nombre; con el campo
// vacío muestra los últimos atendidos. Flechas y Enter eligen; Esc cierra; F4 lo enfoca desde cualquier parte.
// Al elegir dispara «cliente-elegido» en el campo; con data-elegir="enviar" además manda su formulario.
// Con data-vacio="id", Enter sin escribir nada deja el cliente como está y pasa a ese campo.
// Todo se escucha desde el documento porque el ticket se reemplaza sin recargar la página.
(() => {
  const estado = new WeakMap(); // por campo: {lista, marcado, espera, pedido}
  const nodo = (tag, clase, texto) => { const e = document.createElement(tag); if (clase) e.className = clase; if (texto != null) e.textContent = texto; return e; };
  const caja = (input) => document.getElementById(input.getAttribute("aria-controls"));
  const datos = (input) => { if (!estado.has(input)) estado.set(input, { lista: [], marcado: -1 }); return estado.get(input); };

  const cerrar = (input) => {
    const c = caja(input), d = datos(input);
    if (c) c.hidden = true;
    input.setAttribute("aria-expanded", "false");
    d.marcado = -1;
  };
  const marcar = (input, i) => {
    const d = datos(input);
    d.marcado = i;
    [...caja(input).querySelectorAll(".sugerencia")].forEach((el, j) => {
      el.setAttribute("aria-selected", String(j === i));
      if (j === i) el.scrollIntoView({ block: "nearest" });
    });
  };
  const elegir = (input, c) => {
    cerrar(input);
    input.value = "";
    input.dispatchEvent(new CustomEvent("cliente-elegido", { bubbles: true, detail: c }));
    if (input.dataset.elegir === "enviar" && input.form) {
      input.form.querySelector("input[name=cliente]").value = c.pk;
      document.getElementById("codigo")?.focus(); // ya con cliente, a capturar piezas
      input.form.requestSubmit ? input.form.requestSubmit() : input.form.submit();
    }
  };
  const pintar = (input, lista, titulo) => {
    const c = caja(input), d = datos(input);
    d.lista = lista;
    const opciones = lista.map((cl) => {
      const op = nodo("div", "sugerencia sug-cliente");
      op.setAttribute("role", "option");
      const nombre = nodo("span", "sugerencia-nombre");
      const datosCl = [cl.telefono, cl.rfc_generico ? "" : "RFC " + cl.rfc].filter(Boolean).join(" · ");
      nombre.append(nodo("b", "", cl.nombre), ...(datosCl ? [nodo("small", "", datosCl)] : []));
      const cond = cl.vencido_txt ? nodo("span", "sug-cliente-vencido", "Vencido " + cl.vencido_txt)
                                  : nodo("span", "sug-cliente-dato", cl.dias_credito ? "Crédito" : "Contado");
      op.append(nodo("span", "opcion-codigo mono", cl.folio), nombre, cond);
      op.addEventListener("mousedown", (e) => { e.preventDefault(); elegir(input, cl); });
      return op;
    });
    c.replaceChildren(...(titulo && lista.length ? [nodo("div", "sugerencias-nota", titulo)] : []), ...opciones);
    if (!lista.length && !titulo) c.append(nodo("div", "sugerencias-nota", "No encontré clientes con eso. Revisa el folio o el celular, o dalo de alta."));
    c.hidden = !c.children.length;
    input.setAttribute("aria-expanded", String(!c.hidden));
    marcar(input, lista.length && !titulo ? 0 : -1);
  };
  const buscar = (input, inmediato = false) => {
    const d = datos(input);
    clearTimeout(d.espera);
    const texto = input.value.trim();
    d.espera = setTimeout(async () => {
      d.pedido?.abort();
      d.pedido = new AbortController();
      try {
        const r = await fetch(input.dataset.clientes + "?q=" + encodeURIComponent(texto), { signal: d.pedido.signal });
        if (!r.ok || input.value.trim() !== texto || document.activeElement !== input) return;
        const json = await r.json();
        if (texto && d.enterPendiente && json.clientes.length === 1) { d.enterPendiente = false; elegir(input, json.clientes[0]); return; }
        d.enterPendiente = false;
        if (texto) pintar(input, json.clientes);
        else pintar(input, json.recientes || [], "Últimos atendidos");
      } catch (e) { /* tecleó otra cosa o no hay red */ }
    }, inmediato ? 0 : 160);
  };

  const campo = (e) => e.target instanceof HTMLInputElement && e.target.matches("input[data-clientes]") ? e.target : null;
  document.addEventListener("focusin", (e) => { const i = campo(e); if (i && !i.value.trim()) buscar(i, true); });
  document.addEventListener("input", (e) => { const i = campo(e); if (i) { datos(i).enterPendiente = false; buscar(i); } });
  document.addEventListener("focusout", (e) => { const i = campo(e); if (i) setTimeout(() => cerrar(i), 150); });
  // «Cambiar» en el ticket: lleva al buscador de clientes
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.("[data-enfocar]");
    const destino = a && document.getElementById(a.dataset.enfocar);
    if (destino) { e.preventDefault(); destino.scrollIntoView({ block: "center" }); destino.focus({ preventScroll: true }); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "F4" && !document.querySelector("dialog[open]")) {
      const i = [...document.querySelectorAll("input[data-clientes]")].find((x) => x.offsetParent);
      if (i) { e.preventDefault(); i.focus(); i.select(); }
      return;
    }
    const i = campo(e);
    if (!i) return;
    const d = datos(i), abierta = !caja(i).hidden;
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && abierta && d.lista.length) {
      e.preventDefault();
      const n = d.lista.length;
      marcar(i, d.marcado < 0 ? (e.key === "ArrowDown" ? 0 : n - 1) : (d.marcado + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
    } else if (e.key === "Enter") {
      e.preventDefault(); // aquí Enter elige, nunca manda el formulario tal cual
      if (abierta && d.lista[d.marcado]) elegir(i, d.lista[d.marcado]);
      else if (i.value.trim()) { d.enterPendiente = true; buscar(i, true); } // si sale uno solo, se elige
      else if (i.dataset.vacio) { cerrar(i); document.getElementById(i.dataset.vacio)?.focus(); } // sigue el mismo cliente
    } else if (e.key === "Escape" && abierta) {
      e.preventDefault();
      cerrar(i);
    }
  });
})();
