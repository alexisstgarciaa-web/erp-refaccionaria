// Cobrar en caja una remisión pendiente: «Paga con» solo en efectivo, con el cambio o lo que falta.
(() => {
  const num = (t) => parseFloat(String(t).replace(/[$,\s]/g, ""));
  const pesos = (n) => "$" + n.toLocaleString("es-MX", { minimumFractionDigits: Number.isInteger(n) ? 0 : 2, maximumFractionDigits: 2 });
  const efectivo = (f) => !!f.querySelector("input[name=forma_pago][value=EFECTIVO]:checked");

  const revisar = (f) => { // false si lo que paga no alcanza
    const paga = f.querySelector("input[name=paga_con]"), letrero = f.querySelector(".cobrar-rem-cambio");
    if (!paga || !letrero) return true;
    const total = num(f.dataset.total), recibe = num(paga.value);
    letrero.classList.remove("faltan");
    if (!efectivo(f) || !paga.value.trim() || Number.isNaN(recibe)) { letrero.textContent = ""; return true; }
    if (recibe < total) { letrero.textContent = `Faltan ${pesos(total - recibe)}`; letrero.classList.add("faltan"); return false; }
    letrero.textContent = `Cambio ${pesos(Math.round((recibe - total) * 100) / 100)}`;
    return true;
  };
  const mostrar = (f) => {
    const caja = f.querySelector(".cobrar-rem-paga");
    if (caja) caja.hidden = !efectivo(f);
    revisar(f);
  };

  const todas = () => document.querySelectorAll("form.cobrar-rem").forEach(mostrar);
  todas();
  document.addEventListener("vivo:cambio", todas); // la lista de Caja se actualiza sola: las nuevas también
  document.addEventListener("change", (e) => {
    const f = e.target.closest && e.target.closest("form.cobrar-rem");
    if (f && e.target.name === "forma_pago") mostrar(f);
  });
  document.addEventListener("input", (e) => {
    const f = e.target.closest && e.target.closest("form.cobrar-rem");
    if (f && e.target.name === "paga_con") revisar(f);
  });
  // antes que el NIP: si no alcanza, no se pide
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (!f.matches || !f.matches("form.cobrar-rem") || revisar(f)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    f.querySelector("input[name=paga_con]").focus();
  }, true);
})();
