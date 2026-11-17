// Remisiones › Nueva: buscar al cliente por folio, celular o nombre, y capturar las partidas con Enter
// (Código › Cantidad › Precio › Descuento › otra partida). El servidor vuelve a revisar todo al emitir.
(() => {
  const form = document.getElementById("remision-form");
  if (!form) return;
  const num = (t) => { const n = parseFloat(String(t || "").replace(/[$,%\s]/g, "")); return Number.isFinite(n) ? n : 0; };
  const pesos = (n) => "$" + n.toLocaleString("en-US", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  const nodo = (tag, clase, texto) => { const e = document.createElement(tag); if (clase) e.className = clase; if (texto != null) e.textContent = texto; return e; };
  const clienteId = document.getElementById("rem-cliente");

  // ───────── Cliente ─────────
  // la búsqueda (folio, celular o nombre) la hace clientes.js; aquí se pinta la ficha y se ajustan condiciones
  const q = document.getElementById("rem-cliente-q");
  const ficha = document.getElementById("rem-ficha");

  const pintarFicha = (c) => {
    const nombre = nodo("p", "rem-ficha-nombre");
    nombre.append(nodo("b", "", c.nombre), " ", nodo("span", "mono", c.folio));
    ficha.replaceChildren(
      nombre,
      nodo("p", "", `RFC ${c.rfc}${c.telefono ? " · Tel. " + c.telefono : ""} · lista ${c.lista}`),
      ...(c.domicilio.length ? c.domicilio.map((d) => nodo("p", "tenue", d)) : [nodo("p", "tenue", "Sin dirección")]),
      nodo("p", "rn-credito", c.dias_credito ? `Crédito ${c.dias_credito} días · límite ${c.limite} · debe ${c.saldo} · disponible ${c.disponible_txt}` : "Solo contado"),
      ...(c.vencido_txt ? [nodo("p", "rn-vencido", `Debe ${c.vencido_txt} vencido · a crédito pide NIP de administrador`)] : []),
    );
    ficha.hidden = false;
  };
  q.addEventListener("cliente-elegido", (e) => {
    const c = e.detail;
    const cambio = clienteId.value !== String(c.pk);
    clienteId.value = c.pk;
    clienteId.dataset.dias = c.dias_credito;
    clienteId.dataset.disponible = c.disponible;
    pintarFicha(c);
    // a crédito, si debe algo vencido, el NIP es de administrador
    if (c.vencido_txt) {
      form.dataset.nipAdminSi = "condiciones:CREDITO";
      form.dataset.nipAdminPara = `Debe ${c.vencido_txt} vencido: a crédito lo autoriza un administrador`;
    } else {
      delete form.dataset.nipAdminSi;
      delete form.dataset.nipAdminPara;
    }
    const credito = form.querySelector('input[name=condiciones][value=CREDITO]');
    credito.disabled = !c.dias_credito;
    if (!c.dias_credito) form.querySelector('input[name=condiciones][value=CONTADO]').checked = true;
    if (cambio) actualizarPrecios();
    recalcular();
    (form.querySelector(".rem-fila input[name=codigo]") || form.querySelector("input[name=orden_compra]")).focus();
  });

  // ───────── Partidas ─────────
  const filas = document.getElementById("rem-filas"), cajaSug = document.getElementById("rem-sugerencias");
  const plantilla = filas.lastElementChild.cloneNode(true); // la fila vacía del final
  let sugerencias = [], sugMarcada = -1, filaSug = null, esperaSug = null, pedidoSug = null;
  let enterPendiente = null; // Enter (o el lector de código de barras) llegó antes que las sugerencias
  const campo = (fila, n) => fila.querySelector(`[name="${n}"]`);

  const nuevaFila = () => {
    const f = plantilla.cloneNode(true);
    f.querySelectorAll("input").forEach((i) => { i.value = ""; delete i.dataset.auto; });
    f.querySelectorAll(".rem-desc span, .rem-hay span, .rem-importe").forEach((s) => { s.textContent = ""; });
    filas.append(f);
    return f;
  };
  const asegurarVacia = () => {
    const ultima = filas.lastElementChild;
    if (!ultima || campo(ultima, "codigo").value.trim() || campo(ultima, "clave").value) nuevaFila();
  };
  const ponerProducto = (fila, s) => {
    campo(fila, "clave").value = s.clave;
    campo(fila, "codigo").value = s.codigo;
    campo(fila, "descripcion").value = s.nombre || s.descripcion || "";
    fila.querySelector(".rem-desc span").textContent = campo(fila, "descripcion").value;
    campo(fila, "existencia").value = fila.querySelector(".rem-hay span").textContent = s.existencia || "0";
    fila.classList.toggle("sin-existencia", !num(s.existencia));
    const precio = campo(fila, "precio");
    if (!precio.value || precio.value === precio.dataset.auto) { precio.value = s.precio || ""; precio.dataset.auto = s.precio || ""; }
    if (!campo(fila, "cantidad").value) campo(fila, "cantidad").value = "1";
  };
  const cerrarSug = () => { cajaSug.hidden = true; sugMarcada = -1; filaSug = null; };
  const marcarSug = (i) => { sugMarcada = i; [...cajaSug.querySelectorAll(".sugerencia")].forEach((el, j) => el.setAttribute("aria-selected", String(j === i))); };
  const elegirSug = (fila, s) => {
    ponerProducto(fila, s);
    cerrarSug();
    asegurarVacia();
    recalcular();
    const c = campo(fila, "cantidad");
    c.focus(); c.select();
  };
  const pintarSug = (fila) => {
    const input = campo(fila, "codigo");
    cajaSug.replaceChildren(...sugerencias.map((s) => {
      const op = nodo("div", "sugerencia");
      op.setAttribute("role", "option");
      op.append(nodo("span", "opcion-codigo mono", s.codigo), nodo("span", "sugerencia-nombre", ""),
                nodo("span", s.hay ? "estado hay" : "estado pedido", s.hay ? "Hay " + s.existencia : "Sin existencia"),
                nodo("span", "opcion-precio", s.precio_txt));
      op.children[1].append(nodo("b", "", s.marca), " " + s.nombre);
      op.addEventListener("mousedown", (e) => { e.preventDefault(); elegirSug(fila, s); });
      return op;
    }));
    if (!sugerencias.length) cajaSug.append(nodo("div", "sugerencias-nota", "No encontré ese código en el catálogo."));
    const caja = cajaSug.offsetParent || document.body, r = input.getBoundingClientRect(), rc = caja.getBoundingClientRect();
    cajaSug.style.top = (r.bottom - rc.top + 4) + "px";
    cajaSug.style.left = Math.max(0, r.left - rc.left) + "px";
    cajaSug.hidden = false;
    filaSug = fila;
    const exacto = sugerencias.findIndex((s) => s.codigo.toUpperCase().replace(/[-\s./]/g, "") === input.value.toUpperCase().replace(/[-\s./]/g, ""));
    marcarSug(sugerencias.length ? Math.max(exacto, 0) : -1);
  };
  const buscarSug = (fila, ya = false) => {
    clearTimeout(esperaSug);
    const texto = campo(fila, "codigo").value.trim();
    if (texto.length < 2) { cerrarSug(); return; }
    esperaSug = setTimeout(async () => {
      pedidoSug?.abort();
      pedidoSug = new AbortController();
      const url = form.dataset.sugerir + "?para=remision&cliente=" + encodeURIComponent(clienteId.value) + "&q=" + encodeURIComponent(texto);
      try {
        const r = await fetch(url, { signal: pedidoSug.signal });
        if (!r.ok || campo(fila, "codigo").value.trim() !== texto) return;
        sugerencias = (await r.json()).sugerencias;
        pintarSug(fila);
        if (enterPendiente === fila) {
          enterPendiente = null;
          const limpio = (t) => t.toUpperCase().replace(/[-\s./]/g, "");
          const exacto = sugerencias.find((s) => limpio(s.codigo) === limpio(texto));
          if (exacto || sugerencias.length === 1) elegirSug(fila, exacto || sugerencias[0]);
        }
      } catch (e) { /* tecleó otra cosa o no hay red */ }
    }, ya ? 0 : 160);
  };

  const recalcular = () => {
    let total = 0;
    filas.querySelectorAll(".rem-fila").forEach((f) => {
      const c = num(campo(f, "cantidad").value), p = num(campo(f, "precio").value), d = num(campo(f, "descuento").value);
      const importe = Math.round(c * p * (100 - Math.min(Math.max(d, 0), 99)) / 100 * 100) / 100;
      f.querySelector(".rem-importe").textContent = campo(f, "clave").value && c && p ? pesos(importe) : "";
      if (campo(f, "clave").value) total += importe;
    });
    const iva = Math.round((total - total / 1.16) * 100) / 100;
    document.getElementById("rem-subtotal").textContent = pesos(Math.round((total - iva) * 100) / 100);
    document.getElementById("rem-iva").textContent = pesos(iva);
    document.getElementById("rem-total").textContent = pesos(total);
    const aviso = document.getElementById("rem-aviso-credito");
    const credito = form.querySelector('input[name=condiciones]:checked')?.value === "CREDITO";
    const disponible = num(clienteId.dataset.disponible);
    aviso.hidden = !(credito && total > 0);
    aviso.classList.toggle("rojo", total > disponible);
    aviso.textContent = total > disponible
      ? `Excede el crédito disponible (${pesos(disponible)}) por ${pesos(total - disponible)}: no se podrá emitir a crédito.`
      : `Crédito disponible ${pesos(disponible)}; después de esta remisión quedan ${pesos(disponible - total)}.`;
  };

  // al cambiar de cliente, los precios que venían de la lista se recalculan con la lista del nuevo
  const actualizarPrecios = async () => {
    const conClave = [...filas.querySelectorAll(".rem-fila")].filter((f) => campo(f, "clave").value);
    if (!conClave.length || !clienteId.value) return;
    const params = new URLSearchParams({ cliente: clienteId.value });
    conClave.forEach((f) => params.append("clave", campo(f, "clave").value));
    try {
      const r = await fetch(form.dataset.precios + "?" + params);
      if (!r.ok) return;
      const datos = (await r.json()).filas;
      conClave.forEach((f) => { const d = datos[campo(f, "clave").value]; if (d) ponerProducto(f, d); });
      recalcular();
    } catch (e) { /* sin red */ }
  };

  const siguiente = { codigo: "cantidad", cantidad: "precio", precio: "descuento" };
  const anterior = { cantidad: "codigo", precio: "cantidad", descuento: "precio" };
  filas.addEventListener("input", (e) => {
    const fila = e.target.closest(".rem-fila");
    if (!fila) return;
    if (e.target.name === "codigo") {
      enterPendiente = null;
      campo(fila, "clave").value = ""; // cambió el código: hay que volver a elegirlo
      fila.querySelector(".rem-desc span").textContent = "";
      buscarSug(fila);
    }
    recalcular();
  });
  filas.addEventListener("keydown", (e) => {
    const fila = e.target.closest(".rem-fila"), nombre = e.target.name;
    if (!fila || !nombre) return;
    if (nombre === "codigo" && !cajaSug.hidden && filaSug === fila && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      if (sugerencias.length) marcarSug((sugMarcada + (e.key === "ArrowDown" ? 1 : sugerencias.length - 1)) % sugerencias.length);
      return;
    }
    if (e.key === "Escape") { cerrarSug(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault(); // aquí Enter nunca guarda
    if (e.shiftKey) {
      const a = anterior[nombre] ? campo(fila, anterior[nombre]) : fila.previousElementSibling && campo(fila.previousElementSibling, "descuento");
      if (a) { a.focus(); a.select?.(); }
      return;
    }
    if (nombre === "codigo") {
      if (!e.target.value.trim()) { document.getElementById("rem-emitir").focus(); return; } // terminé: a Emitir
      if (!cajaSug.hidden && filaSug === fila && sugerencias[sugMarcada]) { elegirSug(fila, sugerencias[sugMarcada]); return; }
      if (campo(fila, "clave").value) { const c = campo(fila, "cantidad"); c.focus(); c.select(); return; }
      enterPendiente = fila; // todavía no llegan las sugerencias: se elige en cuanto lleguen
      buscarSug(fila, true);
      return;
    }
    if (siguiente[nombre]) { const s = campo(fila, siguiente[nombre]); s.focus(); s.select(); return; }
    // en Descuento: a la siguiente partida (la vacía del final si es la última)
    asegurarVacia();
    const sig = fila.nextElementSibling || nuevaFila();
    campo(sig, "codigo").focus();
  });
  filas.addEventListener("focusout", (e) => {
    if (e.target.name === "codigo") setTimeout(() => { if (!cajaSug.contains(document.activeElement)) cerrarSug(); }, 150);
  });
  filas.addEventListener("click", (e) => {
    const b = e.target.closest(".rem-quitar");
    if (!b) return;
    b.closest(".rem-fila").remove();
    asegurarVacia();
    recalcular();
  });
  form.addEventListener("change", (e) => { if (e.target.name === "condiciones") recalcular(); });

  recalcular();
})();
