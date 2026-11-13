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
