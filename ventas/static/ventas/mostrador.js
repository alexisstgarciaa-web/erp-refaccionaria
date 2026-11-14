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

// Captura por código, toda con Enter: Código › Cantidad › Precio › agrega.
//  · En Código sugiere mientras se teclea; Enter elige (el código exacto, la única sugerencia o la marcada
//    con ↑ ↓) y pasa a Cantidad. Si hay varias y ninguna es exacta, las muestra para elegir.
//  · Enter en Cantidad pasa a Precio, que ya trae el de lista seleccionado: se teclea encima sin borrar.
//  · Enter en Precio agrega al ticket. Sin JavaScript, el formulario funciona como antes.
(() => {
  const f = document.querySelector("form.captura");
  const c = document.getElementById("codigo");
  if (!f || !c || !window.fetch) return;
  const caja = document.getElementById("sugerencias");
  const clave = document.getElementById("codigo-clave");
  const cant = document.getElementById("captura-cantidad");
  const precio = document.getElementById("captura-precio");
  const CANTIDAD = /^\s*(\d+(?:\.\d+)?)\s*\*\s*/; // «4*GP-37»
  const norm = (t) => t.replace(/[^a-z0-9]/gi, "").toUpperCase();
  const texto = () => c.value.replace(CANTIDAD, "").trim();
  let items = [], itemsDe = null, activo = -1, pedido = null, espera = null, elegido = "";

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
  const nodo = (tag, clase, txt) => {
    const el = document.createElement(tag);
    if (clase) el.className = clase;
    if (txt !== undefined) el.textContent = txt;
    return el;
  };
  const pintar = (q, lista) => {
    items = lista;
    itemsDe = q;
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
      ? "Enter elige la marcada · ↑ ↓ para cambiar · Esc cierra"
      : `Sin coincidencias para «${q}». Revisa el código o prueba con parte del nombre.`));
    caja.hidden = false;
    c.setAttribute("aria-expanded", "true");
    marcar(lista.length ? 0 : -1); // la primera ya va marcada: Enter la elige
  };
  const elegir = (i) => {
    const s = items[i];
    if (!s) return;
    const m = c.value.match(CANTIDAD);
    if (m) cant.value = m[1];
    c.value = elegido = s.codigo;
    clave.value = s.clave;
    precio.value = s.precio;
    precio.dataset.auto = s.precio; // si no lo cambian, se cobra el de la lista
    precio.placeholder = s.precio ? "De lista" : "Sin precio: tecléalo";
    cerrar();
    cant.focus();
  };
  const traer = async (q) => {
    pedido?.abort();
    pedido = new AbortController();
    const r = await fetch(`${f.dataset.sugerir}?q=${encodeURIComponent(q)}`,
                          { signal: pedido.signal, headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(r.status);
    return (await r.json()).sugerencias;
  };
  const sugerir = async () => {
    const q = texto();
    if (norm(q).length < 2) { cerrar(); return; }
    try {
      const lista = await traer(q);
      if (document.activeElement === c && texto() === q) pintar(q, lista);
    } catch { /* se canceló o no hubo red */ }
  };
  // Enter en Código: elige y pasa a Cantidad; nunca agrega directo
  const enterEnCodigo = async () => {
    const q = texto();
    if (!norm(q)) return;
    if (clave.value && c.value === elegido) { cant.focus(); return; }
    if (!caja.hidden && itemsDe === q && activo >= 0) { elegir(activo); return; }
    clearTimeout(espera);
    let lista;
    try {
      lista = await traer(q);
    } catch {
      f.submit(); // sin sugerencias (red): que lo resuelva el servidor
      return;
    }
    if (texto() !== q) return;
    items = lista;
    const exacto = lista.findIndex((s) => norm(s.codigo) === norm(q));
    if (exacto >= 0) elegir(exacto);
    else if (lista.length === 1) elegir(0);
    else pintar(q, lista);
  };
  const soltarPrecioAuto = () => {
    if (precio.dataset.auto !== undefined && precio.value === precio.dataset.auto) precio.value = "";
    delete precio.dataset.auto;
    precio.placeholder = "De lista";
  };
  const nadaElegido = () => !clave.value && !norm(texto());

  c.addEventListener("input", () => {
    clave.value = "";
    elegido = "";
    soltarPrecioAuto();
    clearTimeout(espera);
    espera = setTimeout(sugerir, 140);
  });
  c.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { cerrar(); return; }
    if (e.key === "Enter") { e.preventDefault(); enterEnCodigo(); return; }
    if (caja.hidden || !items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); marcar((activo + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); marcar(activo <= 0 ? items.length - 1 : activo - 1); }
  });
  c.addEventListener("blur", () => setTimeout(cerrar, 150));
  cant.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (nadaElegido()) { c.focus(); return; }
    precio.focus();
  });
  precio.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (nadaElegido()) { c.focus(); return; }
    f.requestSubmit ? f.requestSubmit() : f.submit();
  });
  // Cantidad y Precio se seleccionan completos al entrar: se teclea encima, sin borrar
  [cant, precio].forEach((el) => {
    let recien = false;
    el.addEventListener("focus", () => { el.select(); recien = true; });
    el.addEventListener("mouseup", (e) => { if (recien) { e.preventDefault(); recien = false; } });
    el.addEventListener("blur", () => { recien = false; });
  });
  f.addEventListener("submit", () => {
    clearTimeout(espera);
    pedido?.abort();
    if (precio.dataset.auto !== undefined && precio.value === precio.dataset.auto) precio.value = "";
  });
})();

// Lo recién agregado o cambiado, a la vista dentro del ticket
const verReciente = () => {
  const lineas = document.querySelector(".ticket-lineas");
  const r = lineas && lineas.querySelector(".renglon.reciente");
  if (r && lineas.scrollHeight > lineas.clientHeight) lineas.scrollTop = Math.max(0, r.offsetTop - lineas.offsetTop - 44); // deja ver el título de su sección
};
verReciente();

// Cambios en el ticket (cantidad, precio, quitar, cliente) y en la hoja de piezas (opciones, «Llevar
// completa») sin recargar la página: se manda el formulario por detrás y se cambian solo la mesa y la
// barra de cobro, así la pantalla se queda donde estaba. Sin JavaScript, el formulario recarga como antes.
(() => {
  if (!window.fetch || !window.DOMParser) return;
  const aplica = (f) => f instanceof HTMLFormElement && f.method.toLowerCase() === "post" &&
                        f.closest(".mesa") && !f.closest(".por-codigo");
  let cola = Promise.resolve(); // uno tras otro, en el orden en que se hicieron

  const enviar = async (f, datos) => {
    const mesa = document.querySelector(".mesa");
    if (!mesa) return;
    const abiertos = [...mesa.querySelectorAll("details")].map((d) => d.open);
    const lineas = mesa.querySelector(".ticket-lineas");
    const scroll = lineas ? lineas.scrollTop : 0;
    mesa.classList.add("guardando");
    let r, html;
    try {
      r = await fetch(f.action, { method: "POST", body: datos, credentials: "same-origin" });
      html = await r.text();
    } catch {
      f.submit();
      return;
    }
    const doc = new DOMParser().parseFromString(html, "text/html");
    const nueva = doc.querySelector(".mesa");
    if (!r.ok || !nueva) { // sesión vencida u otro error: que lo muestre la página completa
      window.location.assign(r.ok ? r.url : window.location.href);
      return;
    }
    // los avisos (p. ej. precio abajo del costo) van dentro del ticket, que es donde se está viendo
    const avisos = [...doc.querySelectorAll(".avisos .aviso")];
    const cabeza = nueva.querySelector(".ticket-cabeza");
    if (avisos.length && cabeza) {
      const caja = document.createElement("div");
      caja.className = "ticket-avisos";
      caja.setAttribute("role", "status");
      caja.append(...avisos);
      cabeza.after(caja);
    }
    document.querySelector(".avisos")?.remove(); // los de antes ya no aplican
    document.querySelector(".captura-hecho")?.remove();
    // dónde estaba el cursor justo ahora (y lo que se haya alcanzado a teclear mientras se guardaba)
    const a = document.activeElement;
    const foco = a && mesa.contains(a) && a.form && a.name
      ? { action: a.form.getAttribute("action"), name: a.name, valor: a.value, tecleado: a.value !== a.defaultValue } : null;
    mesa.replaceWith(nueva);
    const cobro = doc.querySelector(".barra-cobro");
    if (cobro) document.querySelector(".barra-cobro")?.replaceWith(cobro);
    nueva.querySelectorAll("details").forEach((d, i) => { if (abiertos[i]) d.open = true; });
    const nuevasLineas = nueva.querySelector(".ticket-lineas");
    if (nuevasLineas) nuevasLineas.scrollTop = scroll;
    verReciente();
    if (foco) {
      const el = nueva.querySelector(`form[action="${CSS.escape(foco.action)}"] [name="${CSS.escape(foco.name)}"]`);
      if (el) {
        if (foco.tecleado && "value" in el) el.value = foco.valor;
        el.focus({ preventScroll: true });
        if (!foco.tecleado && el.select) el.select();
      }
    }
  };

  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (!aplica(f)) return;
    e.preventDefault();
    const datos = new FormData(f);
    if (e.submitter && e.submitter.name) datos.append(e.submitter.name, e.submitter.value);
    cola = cola.then(() => enviar(f, datos)).catch(() => {});
  });
})();
