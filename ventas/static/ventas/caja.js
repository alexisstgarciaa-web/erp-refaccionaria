// Caja: el cliente llega con su turno. La cajera teclea el número (o parte del nombre) y Enter abre su cuenta con
// el cursor en «Paga con»; Enter otra vez cobra (pide el NIP). Esc regresa al buscador. La lista se actualiza sola
// (vivo.js) y aquí se vuelve a aplicar lo que se estaba buscando.
(() => {
  const campo = document.getElementById("caja-buscar"), zona = document.getElementById("caja-cuentas");
  if (!campo || !zona) return;
  const normal = (t) => t.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^0-9a-z&]+/g, " ").trim();
  const cuentas = () => [...zona.querySelectorAll(".cuenta")];
  const coincide = (li, q) => {
    if (!q) return true;
    if (/^\d+$/.test(q) && (li.dataset.turno || "").startsWith(q)) return true;
    const texto = " " + li.dataset.busca;
    return q.split(" ").every((p) => texto.includes(/^\d+$/.test(p) ? p : " " + p) || texto.includes(p));
  };
  const filtrar = () => {
    const q = normal(campo.value);
    let hay = 0;
    cuentas().forEach((li) => { li.hidden = !coincide(li, q); if (!li.hidden) hay += 1; });
    zona.classList.toggle("sin-coincidencias", Boolean(q) && !hay);
    return q;
  };
  const abrir = (li) => {
    const d = li.querySelector("details");
    cuentas().forEach((otra) => { if (otra !== li) otra.querySelector("details").open = false; });
    d.open = true;
    const paga = li.querySelector("input[name=paga_con]"), boton = li.querySelector("button[type=submit]");
    (paga && !paga.closest("[hidden]") ? paga : boton || d.querySelector("summary")).focus();
    li.scrollIntoView({ block: "nearest" });
  };

  campo.addEventListener("input", filtrar);
  campo.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const q = filtrar();
    const visibles = cuentas().filter((li) => !li.hidden);
    const elegida = visibles.find((li) => li.dataset.turno === q) || (visibles.length === 1 || q ? visibles[0] : null);
    if (elegida) abrir(elegida);
  });
  zona.addEventListener("vivo:cambio", filtrar);
  // una cuenta abierta a la vez; al abrirla con el ratón, el cursor va a «Paga con»
  zona.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!d.open || !d.closest(".cuenta")) return;
    cuentas().forEach((otra) => { const x = otra.querySelector("details"); if (x !== d) x.open = false; });
    const paga = d.querySelector("input[name=paga_con]");
    if (paga && !d.contains(document.activeElement)) paga.focus();
  }, true);
  zona.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || document.querySelector("dialog[open]")) return;
    const d = e.target.closest("details[open]");
    if (!d) return;
    d.open = false;
    campo.focus();
    campo.select();
  });
})();
