// NIP: los formularios con data-nip piden el NIP en un cuadro antes de mandarse.
// data-nip="vendedor" (quien lo hace) o "admin" (lo autoriza un administrador); data-nip-para dice qué se
// autoriza; data-nip-cuando="campo" lo pide solo si ese campo trae un número mayor que cero. Un botón con
// data-sin-nip manda el formulario sin pedirlo. data-nip-admin-si="campo:VALOR" pide el de administrador
// cuando ese campo trae ese valor (p. ej. crédito a quien debe algo vencido), con data-nip-admin-para de motivo.
// Se carga después de mostrador.js: si otra validación ya detuvo el envío (p. ej. «Paga con» no alcanza),
// no se pide el NIP. El servidor siempre vuelve a revisarlo.
(() => {
  const dlg = document.getElementById("nip-dialogo");
  if (!dlg || typeof dlg.showModal !== "function") return;
  const campo = dlg.querySelector("#nip-campo"), titulo = dlg.querySelector("#nip-titulo"),
        para = dlg.querySelector("#nip-para"), ayuda = dlg.querySelector("#nip-ayuda");
  let pendiente = null;

  const pideAdmin = (f) => {
    if (f.dataset.nip === "admin") return true;
    const [nombre, valor] = (f.dataset.nipAdminSi || "").split(":");
    if (!nombre) return false;
    const marcado = f.querySelector(`[name="${CSS.escape(nombre)}"]:checked`) || f.elements[nombre];
    return !!marcado && marcado.value === valor;
  };
  const oculto = (f) => {
    let i = f.querySelector('input[type=hidden][name="nip"]');
    if (!i) { i = document.createElement("input"); i.type = "hidden"; i.name = "nip"; f.append(i); }
    return i;
  };

  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (!(f instanceof HTMLFormElement) || !f.dataset.nip || e.defaultPrevented) return;
    if (e.submitter && e.submitter.hasAttribute("data-sin-nip")) return; // p. ej. «Guardar borrador»
    const cuando = f.dataset.nipCuando && f.elements[f.dataset.nipCuando];
    if (cuando && !(parseFloat(String(cuando.value).replace(/[$,\s]/g, "")) > 0)) return;
    if (f.dataset.nipListo === "1") { delete f.dataset.nipListo; return; } // ya lo trae: que siga
    e.preventDefault();
    e.stopImmediatePropagation();
    if (dlg.open) return;
    pendiente = { f, boton: e.submitter };
    const admin = pideAdmin(f);
    titulo.textContent = admin ? "NIP de administrador" : "Tu NIP";
    para.textContent = [f.dataset.nipPara, admin && f.dataset.nip !== "admin" ? f.dataset.nipAdminPara : ""].filter(Boolean).join(" · ");
    ayuda.textContent = "4 a 6 números · Enter para seguir · Esc para regresar";
    ayuda.classList.remove("error");
    campo.value = "";
    dlg.showModal();
    campo.focus();
  });

  dlg.querySelector("#nip-regresar").addEventListener("click", () => dlg.close());
  dlg.addEventListener("submit", (e) => {
    e.preventDefault();
    const nip = campo.value.trim();
    if (!/^\d{4,6}$/.test(nip)) {
      ayuda.textContent = "El NIP lleva de 4 a 6 números.";
      ayuda.classList.add("error");
      campo.select();
      return;
    }
    const p = pendiente;
    dlg.close();
    if (!p) return;
    oculto(p.f).value = nip;
    p.f.dataset.nipListo = "1";
    const boton = p.boton && p.boton.form === p.f ? p.boton : undefined;
    p.f.requestSubmit ? p.f.requestSubmit(boton) : p.f.submit();
  });

  dlg.addEventListener("close", () => {
    campo.value = "";
    const p = pendiente;
    pendiente = null;
    // si regresó sin NIP, el cursor vuelve a donde estaba
    if (p && p.f.dataset.nipListo !== "1") (p.boton || p.f.querySelector("button, input:not([type=hidden])"))?.focus();
  });
  campo.addEventListener("input", () => { campo.value = campo.value.replace(/\D/g, "").slice(0, 6); });
})();
