// Pantalla donde el cliente toma su turno. Pregunta si tiene número de cliente y enseña solo el campo que toca;
// al dar el turno, el número se queda a la vista unos segundos y la pantalla vuelve a empezar para el siguiente.
// ?papel=no en la dirección apaga el papelito en este aparato (y ?papel=si lo vuelve a prender).
(() => {
  try {
    const papel = new URLSearchParams(location.search).get("papel");
    if (papel === "no" || papel === "si") localStorage.setItem("imprimir-turno", papel);
  } catch { /* sin almacenamiento: imprime */ }

  const forma = document.querySelector(".kiosco-forma");
  if (forma) {
    forma.classList.add("con-js");
    const numero = forma.elements.numero_cliente, nombre = forma.elements.nombre;
    const elegir = (enfocar) => {
      const cual = (forma.querySelector("input[name=tiene]:checked") || {}).value || "";
      forma.dataset.tiene = cual;
      numero.required = cual === "si";
      if (cual === "no") numero.value = "";
      if (cual === "si") nombre.value = "";
      if (enfocar && cual) (cual === "si" ? numero : nombre).focus();
    };
    forma.addEventListener("change", (e) => { if (e.target.name === "tiene") elegir(true); });
    elegir(false);
    // un toque de más no saca dos turnos
    forma.addEventListener("submit", (e) => {
      if (!forma.dataset.tiene) { e.preventDefault(); return; }
      const boton = forma.querySelector("button[type=submit]");
      if (boton.disabled) { e.preventDefault(); return; }
      setTimeout(() => { boton.disabled = true; }, 0);
    });
  }

  const turno = document.querySelector(".kiosco-turno");
  if (turno) {
    const volver = () => location.replace(turno.dataset.volver);
    setTimeout(volver, (parseInt(turno.dataset.segundos, 10) || 12) * 1000);
  }
})();
