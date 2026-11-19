// Comas de miles en los campos de cifras (data-cifra) al terminar de teclear: 1500 → 1,500.
// «pesos» deja dos decimales cuando hay centavos (1234.5 → 1,234.50); los demás respetan los
// decimales tecleados (costos a 4, cantidades a 3). El servidor quita las comas al leerlos.
(() => {
  const agrupar = (entero) => entero.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const formatear = (el) => {
    const limpio = el.value.replace(/[$,\s]/g, "");
    const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(limpio);
    if (!limpio || !m || (!m[2] && !m[3])) return;
    const entero = (m[2] || "0").replace(/^0+(?=\d)/, "");
    let fraccion = m[3] || "";
    if (el.dataset.cifra === "pesos") fraccion = /[1-9]/.test(fraccion) ? fraccion.padEnd(2, "0") : "";
    const texto = m[1] + agrupar(entero) + (fraccion ? "." + fraccion : "");
    if (texto !== el.value) el.value = texto;
  };
  // en captura, antes que los demás oyentes del cambio (p. ej. el renglón que se envía solo)
  document.addEventListener("change", (e) => {
    if (e.target.matches && e.target.matches("input[data-cifra]")) formatear(e.target);
  }, true);
})();
