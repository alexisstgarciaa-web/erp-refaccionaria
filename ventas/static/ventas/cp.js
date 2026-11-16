// Dirección por código postal: al teclear los 5 números se llenan municipio y estado, y la colonia se
// elige de una lista con las de ese CP (con «Otra» por si la del cliente no viene en el catálogo).
// Sirve en cualquier formulario con un input[data-cp] (la URL de consulta, con 00000 en lugar del CP)
// y campos colonia, municipio y estado: el alta rápida del ticket y el cliente en la Administración.
(() => {
  const OTRA = "__otra__";
  const cache = new Map();
  const sinAcentos = (t) => t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  const campo = (form, nombre) => form && form.querySelector(`[name="${nombre}"]`);

  const aviso = (cp) => {
    let a = cp.parentNode.querySelector(".cp-aviso");
    if (!a) {
      a = document.createElement("span");
      a.className = "cp-aviso";
      a.setAttribute("aria-live", "polite");
      if (cp.closest(".form-row")) a.style.marginLeft = "10px"; // en la Administración va a un lado del campo
      cp.insertAdjacentElement("afterend", a);
    }
    return a;
  };

  const lista = (col) => { // el <select> que acompaña al campo de colonia
    let sel = col.parentNode.querySelector("select.colonias");
    if (!sel) {
      sel = document.createElement("select");
      sel.className = "colonias";
      sel.hidden = true;
      sel.setAttribute("aria-label", "Colonia");
      col.insertAdjacentElement("afterend", sel);
      sel.addEventListener("change", () => {
        const otra = sel.value === OTRA;
        col.hidden = !otra;
        col.value = otra ? "" : sel.value;
        if (otra) col.focus();
      });
    }
    return sel;
  };

  const opcion = (valor, texto) => {
    const o = document.createElement("option");
    o.value = valor;
    o.textContent = texto;
    return o;
  };

  const pintar = (cp, datos, conservar) => {
    const form = cp.form, col = campo(form, "colonia");
    const mun = campo(form, "municipio"), edo = campo(form, "estado");
    const a = aviso(cp);
    if (!col) return;
    const sel = lista(col);
    if (!datos.encontrado) {
      sel.hidden = true;
      col.hidden = false;
      a.className = "cp-aviso cp-no";
      a.textContent = "No encontré ese código postal: escribe colonia, municipio y estado.";
      return;
    }
    a.className = "cp-aviso";
    a.textContent = `${datos.municipio}, ${datos.estado}`;
    if (mun) mun.value = datos.municipio;
    if (edo) edo.value = datos.estado;
    const actual = conservar ? col.value.trim() : "";
    const igual = datos.colonias.find((c) => sinAcentos(c) === sinAcentos(actual));
    const n = datos.colonias.length;
    sel.replaceChildren(
      ...(n > 1 ? [opcion("", `Elige una (${n})`)] : []),
      ...datos.colonias.map((c) => opcion(c, c)),
      opcion(OTRA, "Otra (escribirla)"),
    );
    if (igual) sel.value = igual;
    else if (actual) sel.value = OTRA;
    else sel.value = n === 1 ? datos.colonias[0] : "";
    col.value = sel.value === OTRA ? actual : sel.value;
    sel.hidden = false;
    col.hidden = sel.value !== OTRA;
  };

  const consultar = async (cp, conservar) => {
    const numero = cp.value.replace(/\D/g, "");
    if (numero.length !== 5) return;
    cp.dataset.cpVisto = numero;
    try {
      if (!cache.has(numero)) {
        const r = await fetch(cp.dataset.cp.replace("00000", numero), { headers: { Accept: "application/json" } });
        if (!r.ok) return;
        cache.set(numero, await r.json());
      }
      if (cp.value.replace(/\D/g, "") === numero) pintar(cp, cache.get(numero), conservar);
    } catch (e) { /* sin red: se escribe a mano */ }
  };

  document.addEventListener("input", (e) => {
    const cp = e.target;
    if (!cp.matches || !cp.matches("input[data-cp]")) return;
    const numero = cp.value.replace(/\D/g, "");
    if (numero.length === 5 && numero !== cp.dataset.cpVisto) consultar(cp, false);
  });
  // al abrir un cliente que ya tiene CP (o el formulario que regresó con un error), su colonia queda elegida
  const inicio = () => document.querySelectorAll("input[data-cp]").forEach((cp) => consultar(cp, true));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", inicio);
  else inicio();
})();
