// Listas del mostrador: marca › año › modelo › motor. Al cambiar una, se llenan las siguientes
// sin recargar la página. Sin JavaScript también funciona: el botón «Ver piezas» manda el formulario.
(() => {
  const f = document.querySelector("form.selector");
  if (!f || !window.fetch) return;
  const campos = ["armadora", "anio", "modelo", "litros"];
  const lista = (n) => f.elements[n];
  const vacio = { anio: "Año", modelo: "Modelo", litros: "Motor" };

  const llenar = (n, valores, etiqueta = (v) => v, extra = []) => {
    const s = lista(n);
    s.replaceChildren(new Option(vacio[n], ""));
    valores.forEach((v) => s.add(new Option(etiqueta(v), v)));
    extra.forEach(([v, t]) => s.add(new Option(t, v)));
    s.disabled = valores.length + extra.length === 0;
  };

  // Resalta la primera lista que falta, para que el de mostrador sepa dónde sigue.
  const marcarSiguiente = () => {
    let listo = false;
    campos.forEach((n) => {
      const toca = !listo && !lista(n).disabled && !lista(n).value;
      lista(n).closest(".lista").classList.toggle("siguiente", toca);
      if (toca) listo = true;
    });
  };

  f.addEventListener("change", async (e) => {
    const i = campos.indexOf(e.target.name);
    if (i < 0) return;
    document.body.classList.add("cambiando");
    if (e.target.name === "litros") {
      if (e.target.value) f.submit();
      return;
    }
    campos.slice(i + 1).forEach((n) => llenar(n, []));
    const q = new URLSearchParams();
    campos.slice(0, i + 1).forEach((n) => lista(n).value && q.set(n, lista(n).value));
    let d;
    try {
      const r = await fetch(`${f.dataset.listas}?${q}`, { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(r.status);
      d = await r.json();
    } catch {
      f.submit();
      return;
    }
    if (i === 0 && d.sel.armadora) { llenar("anio", d.anios); lista("anio").focus(); }
    if (i === 1 && d.sel.anio) { llenar("modelo", d.modelos); lista("modelo").focus(); }
    if (i === 2 && d.sel.modelo) {
      if (d.sel.litros) {
        // un solo motor: no hay nada que preguntar, directo a las piezas
        llenar("litros", [d.sel.litros]);
        lista("litros").value = d.sel.litros;
        f.submit();
        return;
      }
      llenar("litros", d.motores, (v) => `${v} L`, [["todos", "Todos los motores"]]);
      lista("litros").focus();
    }
    marcarSiguiente();
  });
  marcarSiguiente();
})();

// F2 regresa al campo de código desde cualquier parte del mostrador (lectores de código de barras incluidos).
document.addEventListener("keydown", (e) => {
  if (e.key !== "F2") return;
  const c = document.getElementById("codigo");
  if (c) { e.preventDefault(); c.focus(); c.select(); }
});

// Campo de código como buscador: sugiere códigos mientras se teclea. ↑ ↓ para moverse, Enter o clic
// para elegir; al elegir se llenan el código y el precio de lista, y el foco pasa a Cantidad, donde
// Enter agrega al ticket. Un lector de código de barras teclea y da Enter sin elegir: entra directo.
(() => {
  const f = document.querySelector("form.captura");
  const c = document.getElementById("codigo");
  if (!f || !c || !window.fetch) return;
  const caja = document.getElementById("sugerencias");
  const clave = document.getElementById("codigo-clave");
  const cant = document.getElementById("captura-cantidad");
  const precio = document.getElementById("captura-precio");
  const CANTIDAD = /^\s*(\d+(?:\.\d+)?)\s*\*\s*/; // «4*GP-37»
  let items = [], activo = -1, pedido = null, espera = null;

  const cerrar = () => {
    caja.hidden = true;
    c.setAttribute("aria-expanded", "false");
    c.removeAttribute("aria-activedescendant");
    activo = -1;
  };
  const marcar = (i) => {
    activo = i;
    caja.querySelectorAll(".sugerencia").forEach((el, j) => el.setAttribute("aria-selected", String(j === i)));
    const el = i >= 0 && document.getElementById(`sug-${i}`);
    if (el) {
      c.setAttribute("aria-activedescendant", el.id);
      el.scrollIntoView({ block: "nearest" });
    } else c.removeAttribute("aria-activedescendant");
  };
  // resalta en el código lo que se tecleó, sin importar guiones, puntos o espacios
  const resaltar = (el, codigo, q) => {
    const limpio = q.replace(/[^a-z0-9]/gi, "");
    const re = limpio && new RegExp(limpio.split("").join("[-./ ]*"), "i");
    const m = re && codigo.match(re);
    if (!m) { el.textContent = codigo; return; }
    const mk = document.createElement("mark");
    mk.textContent = m[0];
    el.append(codigo.slice(0, m.index), mk, codigo.slice(m.index + m[0].length));
  };
  const nodo = (tag, clase, texto) => {
    const el = document.createElement(tag);
    if (clase) el.className = clase;
    if (texto !== undefined) el.textContent = texto;
    return el;
  };
  const pintar = (q, lista) => {
    items = lista;
    caja.replaceChildren();
    lista.forEach((s, i) => {
      const el = nodo("div", "sugerencia");
      el.id = `sug-${i}`;
      el.setAttribute("role", "option");
      const cod = nodo("span", "opcion-codigo");
      resaltar(cod, s.codigo, q);
      const nom = nodo("span", "sugerencia-nombre");
      nom.append(nodo("b", "", s.marca), ` ${s.nombre}`);
      if (s.equivalente) nom.append(" ", nodo("i", "", "equivalente"));
      el.append(cod, nom, nodo("span", `estado ${s.hay ? "hay" : "pedido"}`, s.hay ? `Hay ${s.existencia}` : "Pedido"),
                nodo("span", "opcion-precio", s.precio_txt));
      el.addEventListener("mousedown", (e) => { e.preventDefault(); elegir(i); });
      caja.append(el);
    });
    caja.append(nodo("div", "sugerencias-nota", lista.length
      ? "↑ ↓ y Enter para elegir · Esc cierra · Enter sin elegir busca lo tecleado"
      : "Sin sugerencias. Enter busca de todos modos (también por equivalencia)."));
    caja.hidden = false;
    c.setAttribute("aria-expanded", "true");
    marcar(-1);
  };
  const elegir = (i) => {
    const s = items[i];
    if (!s) return;
    const m = c.value.match(CANTIDAD);
    if (m) cant.value = m[1];
    c.value = s.codigo;
    clave.value = s.clave;
    precio.value = s.precio;
    precio.dataset.auto = s.precio; // si no lo cambian, se cobra el de la lista
    cerrar();
    cant.focus();
    cant.select();
  };
  const buscar = async () => {
    const q = c.value.replace(CANTIDAD, "").trim();
    if (q.replace(/[^a-z0-9]/gi, "").length < 2) { cerrar(); return; }
    pedido?.abort();
    pedido = new AbortController();
    try {
      const r = await fetch(`${f.dataset.sugerir}?q=${encodeURIComponent(q)}`,
                            { signal: pedido.signal, headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const d = await r.json();
      if (document.activeElement === c && c.value.replace(CANTIDAD, "").trim() === q) pintar(q, d.sugerencias);
    } catch { /* se canceló o no hubo red: el Enter sigue funcionando sin sugerencias */ }
  };
  const soltarPrecioAuto = () => {
    if (precio.dataset.auto !== undefined && precio.value === precio.dataset.auto) precio.value = "";
    delete precio.dataset.auto;
  };

  c.addEventListener("input", () => {
    clave.value = "";
    soltarPrecioAuto();
    clearTimeout(espera);
    espera = setTimeout(buscar, 140);
  });
  c.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { cerrar(); return; }
    if (caja.hidden || !items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); marcar((activo + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); marcar(activo <= 0 ? items.length - 1 : activo - 1); }
    else if (e.key === "Enter" && activo >= 0) { e.preventDefault(); elegir(activo); }
  });
  c.addEventListener("blur", () => setTimeout(cerrar, 150));
  f.addEventListener("submit", () => {
    clearTimeout(espera);
    pedido?.abort();
    if (precio.dataset.auto !== undefined && precio.value === precio.dataset.auto) precio.value = "";
  });
})();
