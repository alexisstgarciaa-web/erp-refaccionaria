// Mandar la cotización al cliente: arma el enlace de WhatsApp (wa.me) y el de correo (mailto) con el número o el
// correo que se teclee, y copia el enlace o el mensaje. Las mismas reglas del número que ventas/compartir.py.
(() => {
  const datos = JSON.parse(document.getElementById("enviar-datos").textContent);
  const $ = (id) => document.getElementById(id);
  const tel = $("enviar-tel"), wa = $("enviar-wa"), nota = $("enviar-tel-nota");
  const correo = $("enviar-correo"), mail = $("enviar-mail");

  const numero = (texto) => {
    const t = texto.trim(), d = t.replace(/\D/g, "");
    if (t.startsWith("+") && d.length >= 8 && d.length <= 15) return d.length === 13 && d.startsWith("521") ? "52" + d.slice(3) : d;
    if (d.length === 10) return "52" + d;
    if (d.length === 12 && d.startsWith("52")) return d;
    if (d.length === 13 && ["521", "044", "045"].includes(d.slice(0, 3))) return "52" + d.slice(3);
    return "";
  };
  const pintarWa = () => {
    const n = numero(tel.value), escrito = /\d/.test(tel.value), sirve = Boolean(n) || !escrito;
    wa.href = "https://wa.me/" + n + "?text=" + encodeURIComponent(datos.texto_wa);
    wa.setAttribute("aria-disabled", String(!sirve));
    nota.textContent = !escrito ? "Sin número, WhatsApp te deja elegir el contacto."
      : sirve ? "" : "El celular lleva 10 dígitos (si es extranjero, con + y la lada del país).";
  };
  const pintarMail = () => {
    mail.href = "mailto:" + encodeURIComponent(correo.value.trim()).replace(/%40/g, "@")
      + "?subject=" + encodeURIComponent(datos.asunto) + "&body=" + encodeURIComponent(datos.texto_correo);
  };
  tel.addEventListener("input", pintarWa);
  correo.addEventListener("input", pintarMail);
  wa.addEventListener("click", (e) => { if (wa.getAttribute("aria-disabled") === "true") { e.preventDefault(); tel.focus(); } });
  tel.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); wa.click(); } });
  correo.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); mail.click(); } });

  const avisoCopia = document.querySelector(".doc-enviar-copiado");
  const copiar = async (texto, que) => {
    try { await navigator.clipboard.writeText(texto); }
    catch (e) { // sin permiso del portapapeles: el método de antes
      const area = Object.assign(document.createElement("textarea"), { value: texto });
      document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
    }
    avisoCopia.textContent = que + " copiado. Pégalo en el chat o en el correo.";
    clearTimeout(copiar.t); copiar.t = setTimeout(() => { avisoCopia.textContent = ""; }, 4000);
  };
  document.querySelectorAll("[data-copiar]").forEach((b) => b.addEventListener("click", () =>
    copiar(datos[b.dataset.copiar], b.dataset.copiar === "enlace" ? "Enlace" : "Mensaje")));

  // En teléfono o tableta: el menú de compartir del aparato (WhatsApp Business, Telegram, SMS…)
  const compartir = $("enviar-compartir");
  if (navigator.share) {
    compartir.hidden = false;
    compartir.addEventListener("click", () => navigator.share({ title: datos.asunto, text: datos.texto_correo }).catch(() => {}));
  }
  pintarWa();
  if (location.hash === "#enviar") document.getElementById("enviar").scrollIntoView({ block: "start" });
  // con #enviar en la dirección el navegador se salta el autofocus (y quita el foco al ir al ancla): se pone después
  const enfocar = () => setTimeout(() => document.querySelector("#enviar [autofocus]")?.focus({ preventScroll: true }), 0);
  if (document.readyState === "complete") enfocar(); else window.addEventListener("load", enfocar);
})();
