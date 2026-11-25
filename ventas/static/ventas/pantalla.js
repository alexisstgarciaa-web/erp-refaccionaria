// Pantalla de llamados (la tele de la sala). Cada 3 segundos pregunta a quién se está llamando; cuando hay un
// llamado nuevo lo enseña en grande, suena una campana y lo dice en voz alta. Los navegadores no dejan sonar
// nada hasta que alguien toca la página: por eso el botón «activar el sonido» al abrirla.
(() => {
  const url = document.body.dataset.datos;
  if (!url || !window.fetch) return;
  const $ = (id) => document.getElementById(id);
  const actual = $("p-actual"), numero = $("p-numero"), quien = $("p-quien"), con = $("p-con"), lista = $("p-lista"),
        espera = $("p-espera"), aviso = $("p-aviso"), boton = $("p-sonido"), hora = $("p-hora");
  const dichos = new Set(); // llamados que ya se anunciaron (id:hora); volver a llamar a alguien cambia la hora
  let primera = true, audio = null, sonido = false, fallas = 0;
  const cola = [];
  let hablando = false;

  const campana = () => {
    if (!audio) return;
    const ahora = audio.currentTime;
    [[880, 0], [1174.66, 0.28]].forEach(([frecuencia, cuando]) => {
      const o = audio.createOscillator(), g = audio.createGain();
      o.type = "sine";
      o.frequency.value = frecuencia;
      g.gain.setValueAtTime(0.0001, ahora + cuando);
      g.gain.exponentialRampToValueAtTime(0.5, ahora + cuando + 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, ahora + cuando + 0.9);
      o.connect(g).connect(audio.destination);
      o.start(ahora + cuando);
      o.stop(ahora + cuando + 1);
    });
  };
  const decir = (texto) => new Promise((listo) => {
    if (!sonido || !window.speechSynthesis) { listo(); return; }
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = "es-MX";
    u.rate = 0.88;
    const voz = speechSynthesis.getVoices().find((v) => /^es[-_]MX/i.test(v.lang)) ||
                speechSynthesis.getVoices().find((v) => /^es/i.test(v.lang));
    if (voz) u.voice = voz;
    u.onend = u.onerror = () => listo();
    setTimeout(listo, 12000); // por si el navegador no avisa que terminó
    speechSynthesis.speak(u);
  });
  const frase = (l) => `Turno ${l.numero}${l.nombre ? ", " + l.nombre : ""}. ${l.con ? "Pase con " + l.con : "Pase al mostrador"}.`;
  const anunciar = async () => {
    if (hablando) return;
    hablando = true;
    while (cola.length) {
      const l = cola.shift();
      actual.classList.remove("llamando");
      void actual.offsetWidth; // reinicia la animación
      actual.classList.add("llamando");
      if (sonido) {
        campana();
        await new Promise((r) => setTimeout(r, 1300));
        await decir(frase(l));
        await new Promise((r) => setTimeout(r, 700));
        await decir(frase(l));
      } else {
        await new Promise((r) => setTimeout(r, 4000));
      }
    }
    hablando = false;
  };

  const pintar = (datos) => {
    const llamados = datos.llamados || [];
    const primero = llamados[0];
    numero.textContent = primero ? primero.numero : "—";
    quien.textContent = primero ? primero.nombre : "Aún no se llama a nadie";
    con.textContent = primero ? (primero.con ? `Pase con ${primero.con}` : "Pase al mostrador") : "";
    lista.replaceChildren(...llamados.slice(1).map((l) => {
      const li = document.createElement("li"), b = document.createElement("b"), s = document.createElement("span");
      b.textContent = l.numero;
      s.textContent = [l.nombre, l.con && `con ${l.con}`].filter(Boolean).join(" · ");
      li.append(b, s);
      return li;
    }));
    espera.textContent = datos.espera || 0;
    const nuevos = llamados.filter((l) => !dichos.has(`${l.id}:${l.t}`));
    nuevos.forEach((l) => dichos.add(`${l.id}:${l.t}`));
    if (!primera && nuevos.length) { cola.push(...nuevos.reverse()); anunciar(); } // del más viejo al más nuevo
    primera = false;
  };
  const preguntar = async () => {
    try {
      const r = await fetch(url, { cache: "no-store", credentials: "omit" });
      if (!r.ok) throw new Error(r.status);
      pintar(await r.json());
      fallas = 0;
    } catch {
      fallas += 1;
    }
    aviso.hidden = fallas < 3;
  };
  preguntar();
  setInterval(preguntar, 3000);

  const reloj = () => { hora.textContent = new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" }); };
  reloj();
  setInterval(reloj, 15000);

  boton.addEventListener("click", async () => {
    try {
      const Contexto = window.AudioContext || window.webkitAudioContext;
      if (Contexto) { audio = audio || new Contexto(); await audio.resume(); }
    } catch { /* sin campana: queda la voz */ }
    sonido = true;
    boton.hidden = true;
    campana();
    decir("Sonido activado");
  });
  if (window.speechSynthesis) speechSynthesis.getVoices(); // algunos navegadores cargan las voces hasta que se piden
})();
