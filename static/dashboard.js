/* Dashboard: pide JSON a /api/* y dibuja. Sin build step, sin framework.
 *
 * Convenciones de la guía de dataviz que sí importan y por qué:
 *  - Los colores salen de las variables CSS, así el modo oscuro es un juego de
 *    pasos elegido para su fondo, no una inversión automática.
 *  - Marcas finas: barras <=24px, líneas de 2px, punta redondeada de 4px,
 *    hueco de 2px del color de la superficie entre segmentos apilados.
 *  - Rejilla y ejes discretos, sólidos, de una tinta por encima del fondo.
 *  - El texto nunca lleva el color de la serie: la identidad la da la marca
 *    de color que va al lado.
 *  - Cada gráfico tiene su tabla alterna: es el alivio obligatorio para los
 *    tonos que en claro quedan por debajo de 3:1 contra la superficie.
 */

const Q = window.GOB_QUERY || "";
const MONEDA = document.documentElement.dataset.moneda || "USD";

const css = (nombre) => getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();

const T = {
  get superficie() { return css("--superficie"); },
  get tinta() { return css("--tinta"); },
  get tinta2() { return css("--tinta-2"); },
  get apagada() { return css("--tinta-apagada"); },
  get rejilla() { return css("--rejilla"); },
  get eje() { return css("--eje"); },
};

const SERIES = () => [css("--serie-1"), css("--serie-2"), css("--serie-3"), css("--serie-4"), css("--serie-5")];
const NIVELES = () => [css("--nivel-1"), css("--nivel-2"), css("--nivel-3"), css("--nivel-4"), css("--nivel-5")];

const nf = new Intl.NumberFormat("es-CL");
const num = (n) => (n === null || n === undefined ? "—" : nf.format(Math.round(n)));
const pct = (n) => (n === null || n === undefined ? "—" : `${Math.round(n)}%`);

async function traer(ruta, extra = "") {
  const sep = Q && extra ? "&" : "";
  const qs = [Q, extra].filter(Boolean).join(sep);
  const r = await fetch(`${ruta}${qs ? "?" + qs : ""}`);
  if (!r.ok) throw new Error(`${ruta} devolvió ${r.status}`);
  return r.json();
}

const $ = (sel) => document.querySelector(sel);

function tabla(destino, columnas, filas) {
  if (!filas.length) {
    destino.innerHTML = '<p class="vacio">Sin datos para estos filtros.</p>';
    return;
  }
  const th = columnas.map((c) => `<th class="${c.num ? "num" : ""}">${c.titulo}</th>`).join("");
  const tr = filas
    .map((f) => "<tr>" + columnas.map((c) => `<td class="${c.num ? "num" : ""}">${c.valor(f)}</td>`).join("") + "</tr>")
    .join("");
  destino.innerHTML = `<div class="desplazable"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
}

function leyenda(destino, items) {
  destino.innerHTML = items
    .map(
      (i) =>
        `<span class="leyenda__item"><span class="leyenda__marca" style="background:${i.color}"></span>${i.texto}</span>`
    )
    .join("");
}

/* Opciones comunes: rejilla fina y sólida, ejes discretos, tooltip siempre.
 *
 * `plugins` se saca de `extra` antes de mezclar: si se dejara dentro, el spread
 * final lo reemplazaría entero y los gráficos que personalizan el tooltip
 * perderían `legend: {display:false}` — Chart.js pintaría su propia leyenda
 * encima de la nuestra. */
function base({ plugins: pluginsExtra, ...extra } = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 220 },
    interaction: { mode: "nearest", intersect: false },
    ...extra,
    plugins: {
      legend: { display: false },          // las leyendas van en HTML, con tokens de texto
      ...pluginsExtra,
      tooltip: {
        backgroundColor: T.superficie,
        titleColor: T.tinta,
        bodyColor: T.tinta2,
        borderColor: T.eje,
        borderWidth: 1,
        padding: 10,
        cornerRadius: 6,
        displayColors: true,
        boxWidth: 10,
        boxHeight: 10,
        boxPadding: 4,
        ...pluginsExtra?.tooltip,
      },
    },
  };
}

const ejeValor = (titulo) => ({
  grid: { color: T.rejilla, drawTicks: false, lineWidth: 1 },
  border: { display: false },
  ticks: { color: T.apagada, font: { size: 11 }, callback: (v) => nf.format(v) },
  title: titulo ? { display: true, text: titulo, color: T.tinta2, font: { size: 11 } } : undefined,
});

const ejeCategoria = () => ({
  grid: { display: false },
  border: { color: T.eje },
  ticks: { color: T.tinta2, font: { size: 11 }, autoSkip: false },
});

/* ------------------------------------------------------------------ KPIs */

async function pintarKpis() {
  const d = await traer("/api/resumen");
  const filtrado = d.ofertas !== d.total_sin_filtrar;
  const tarjetas = [
    { valor: num(d.ofertas), etiqueta: "ofertas", nota: filtrado ? `de ${num(d.total_sin_filtrar)} sin filtrar` : "todas las activas" },
    { valor: num(d.empresas), etiqueta: "empresas" },
    { valor: num(d.tags), etiqueta: "tecnologías distintas" },
    { valor: pct(d.ofertas ? (100 * d.remotas) / d.ofertas : 0), etiqueta: "remotas", nota: `${num(d.remotas)} ofertas` },
    {
      valor: d.mediana_salario ? num(d.mediana_salario) : "—",
      etiqueta: `mediana ${d.moneda}/mes`,
      nota: `solo ${d.pct_con_salario}% publica sueldo (${num(d.con_salario)})`,
    },
  ];
  $("#kpis").innerHTML = tarjetas
    .map(
      (t) => `<div class="kpi">
        <div class="kpi__valor">${t.valor}</div>
        <div class="kpi__etiqueta">${t.etiqueta}</div>
        ${t.nota ? `<div class="kpi__nota">${t.nota}</div>` : ""}
      </div>`
    )
    .join("");
  return d;
}

/* ------------------------------------------------------------- top tags */

async function pintarTop() {
  const datos = await traer("/api/top-tags", "limit=25");
  const [oficial, texto] = SERIES();

  leyenda($("#leyenda-top"), [
    { color: oficial, texto: "Tag oficial de la oferta" },
    { color: texto, texto: "Deducido de la descripción" },
  ]);

  new Chart($("#grafico-top"), {
    type: "bar",
    data: {
      labels: datos.map((d) => d.slug),
      datasets: [
        {
          label: "Tag oficial",
          data: datos.map((d) => d.api),
          backgroundColor: oficial,
          borderColor: T.superficie,
          borderWidth: { right: 2 },       // el hueco de 2px que separa los segmentos
          borderSkipped: false,
          borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 4, bottomRight: 4 },
          maxBarThickness: 18,
        },
        {
          label: "Deducido del texto",
          data: datos.map((d) => d.texto),
          backgroundColor: texto,
          borderColor: T.superficie,
          borderWidth: { right: 2 },
          borderSkipped: false,
          borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 4, bottomRight: 4 },
          maxBarThickness: 18,
        },
      ],
    },
    options: base({
      indexAxis: "y",
      scales: { x: { stacked: true, ...ejeValor("ofertas") }, y: { stacked: true, ...ejeCategoria() } },
      plugins: {
        tooltip: {
          callbacks: {
            afterTitle: (ctx) => {
              const d = datos[ctx[0].dataIndex];
              return `${num(d.ofertas)} ofertas en total` + (d.solo_texto ? " · ningún tag oficial" : "");
            },
          },
        },
      },
    }),
  });

  tabla($("#tabla-top"), [
    { titulo: "Tecnología", valor: (d) => d.slug + (d.solo_texto ? ' <span class="marca-texto">solo texto</span>' : "") },
    { titulo: "Ofertas", num: true, valor: (d) => num(d.ofertas) },
    { titulo: "Tag oficial", num: true, valor: (d) => num(d.api) },
    { titulo: "Del texto", num: true, valor: (d) => num(d.texto) },
  ], datos);

  return datos;
}

/* ---------------------------------------------------------- por seniority */

async function pintarSeniority() {
  const d = await traer("/api/tags-seniority", "limit=12");
  const paleta = NIVELES();
  const colorDe = (clave, i) => (clave === "sin_dato" ? css("--nivel-sin-dato") : paleta[i] || paleta[paleta.length - 1]);

  leyenda(
    $("#leyenda-seniority"),
    d.seniorities.map((s, i) => ({ color: colorDe(s.clave, i), texto: s.nombre }))
  );

  new Chart($("#grafico-seniority"), {
    type: "bar",
    data: {
      labels: d.filas.map((f) => f.slug),
      datasets: d.seniorities.map((s, i) => ({
        label: s.nombre,
        data: d.filas.map((f) => f.conteos[i]),
        backgroundColor: colorDe(s.clave, i),
        borderColor: T.superficie,
        borderWidth: { top: 2 },
        borderSkipped: false,
        borderRadius: { topLeft: 4, topRight: 4 },
        maxBarThickness: 24,
      })),
    },
    options: base({
      scales: { x: { stacked: true, ...ejeCategoria() }, y: { stacked: true, ...ejeValor("ofertas") } },
    }),
  });

  tabla(
    $("#tabla-seniority"),
    [
      { titulo: "Tecnología", valor: (f) => f.slug },
      ...d.seniorities.map((s, i) => ({ titulo: s.nombre, num: true, valor: (f) => num(f.conteos[i]) })),
      { titulo: "Total", num: true, valor: (f) => num(f.conteos.reduce((a, b) => a + b, 0)) },
    ],
    d.filas
  );
}

/* ---------------------------------------------------------------- tendencia */

let graficoTendencia = null;
let tagsTendencia = [];

/* Etiqueta directa al final de cada línea: es lo que permite leer las series
   sin depender solo del color (3 de los 5 tonos quedan bajo 3:1 en modo claro).
   Las etiquetas se separan si dos líneas acaban a la misma altura — con valores
   pegados (git 248, aws 236, python 235) se solaparían y no se leería ninguna. */
const ALTO_ETIQUETA = 14;

const etiquetasFinales = {
  id: "etiquetasFinales",
  afterDatasetsDraw(chart) {
    const { ctx, chartArea } = chart;
    const puntos = chart.data.datasets
      .map((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        const ultimo = meta.data[meta.data.length - 1];
        return ultimo ? { texto: ds.label, x: ultimo.x, y: ultimo.y } : null;
      })
      .filter(Boolean)
      .sort((a, b) => a.y - b.y);

    // Empuja hacia abajo lo justo para que ninguna pise a la anterior.
    let minimo = chartArea.top;
    for (const p of puntos) {
      p.yEtiqueta = Math.max(p.y, minimo);
      minimo = p.yEtiqueta + ALTO_ETIQUETA;
    }
    // Si se salieron por abajo, se recolocan hacia arriba desde el borde.
    const exceso = minimo - ALTO_ETIQUETA - chartArea.bottom;
    if (exceso > 0) puntos.forEach((p) => (p.yEtiqueta -= exceso));

    ctx.save();
    ctx.font = '600 11px system-ui, -apple-system, "Segoe UI", sans-serif';
    ctx.textBaseline = "middle";
    for (const p of puntos) {
      // Guía cuando la etiqueta se ha tenido que separar de su punto.
      if (Math.abs(p.yEtiqueta - p.y) > 1) {
        ctx.strokeStyle = T.rejilla;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(p.x + 3, p.y);
        ctx.lineTo(p.x + 9, p.yEtiqueta);
        ctx.stroke();
      }
      ctx.fillStyle = T.tinta2;              // texto en tinta, nunca en color de serie
      ctx.fillText(p.texto, p.x + 11, p.yEtiqueta);
    }
    ctx.restore();
  },
};

async function pintarTendencia() {
  const d = await traer("/api/tendencia", tagsTendencia.length ? `tags=${tagsTendencia.join(",")}` : "");
  const paleta = SERIES();

  const aviso = $("#aviso-tendencia");
  aviso.innerHTML = d.historico_insuficiente
    ? `<div class="aviso aviso--serio"><strong>Histórico insuficiente.</strong>
         Solo hay ${d.n_snapshots} snapshot. La API únicamente devuelve ofertas activas, así que
         la serie empieza a existir desde la primera ingesta y crece hacia adelante:
         corre <code>python -m gob.ingest --full</code> otro día para tener con qué comparar.</div>`
    : "";

  leyenda(
    $("#leyenda-tendencia"),
    d.series.map((s, i) => ({ color: paleta[i % paleta.length], texto: s.slug }))
  );

  const etiquetas = (d.series[0]?.puntos || []).map((p) => p.run_at.slice(0, 10));
  if (graficoTendencia) graficoTendencia.destroy();
  graficoTendencia = new Chart($("#grafico-tendencia"), {
    type: "line",
    data: {
      labels: etiquetas,
      datasets: d.series.map((s, i) => ({
        label: s.slug,
        data: s.puntos.map((p) => p.ofertas),
        borderColor: paleta[i % paleta.length],
        backgroundColor: paleta[i % paleta.length],
        borderWidth: 2,
        tension: 0.15,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointBorderColor: T.superficie,     // anillo de 2px del color de la superficie
        pointBorderWidth: 2,
        pointHitRadius: 14,
      })),
    },
    options: base({
      layout: { padding: { right: 96 } },   // sitio para las etiquetas finales
      interaction: { mode: "index", intersect: false },
      scales: { x: ejeCategoria(), y: { beginAtZero: true, ...ejeValor("ofertas") } },
    }),
    plugins: [etiquetasFinales],
  });

  tabla(
    $("#tabla-tendencia"),
    [
      { titulo: "Tecnología", valor: (s) => s.slug },
      ...etiquetas.map((e, i) => ({ titulo: e, num: true, valor: (s) => num(s.puntos[i]?.ofertas) })),
      { titulo: "Variación", num: true, valor: (s) => (s.variacion === null ? "—" : (s.variacion > 0 ? "+" : "") + s.variacion) },
    ],
    d.series
  );
}

function montarSelectorTendencia(top) {
  const paleta = SERIES();
  const contenedor = $("#selector-tendencia");
  const candidatos = top.slice(0, 14).map((t) => t.slug);
  tagsTendencia = candidatos.slice(0, 4);

  const pintar = () => {
    contenedor.innerHTML = candidatos
      .map((slug) => {
        const activo = tagsTendencia.includes(slug);
        const color = activo ? paleta[tagsTendencia.indexOf(slug) % paleta.length] : "transparent";
        return `<button type="button" class="pastilla" aria-pressed="${activo}" data-tag="${slug}">
          <span class="pastilla__marca" style="background:${color};${activo ? "" : "border:1px solid var(--eje)"}"></span>${slug}
        </button>`;
      })
      .join("");
  };

  contenedor.addEventListener("click", (ev) => {
    const boton = ev.target.closest("[data-tag]");
    if (!boton) return;
    const slug = boton.dataset.tag;
    if (tagsTendencia.includes(slug)) {
      tagsTendencia = tagsTendencia.filter((t) => t !== slug);
    } else if (tagsTendencia.length < 5) {
      // Tope de 5: por encima, los colores dejan de distinguirse y una línea más
      // no aporta nada que no diga mejor la tabla.
      tagsTendencia.push(slug);
    } else {
      return;
    }
    pintar();
    pintarTendencia();
  });

  pintar();
}

/* ------------------------------------------------------------ co-ocurrencia */

let graficoCoocurrencia = null;

async function pintarCoocurrencia(tag) {
  const d = await traer("/api/co-ocurrencia", `tag=${encodeURIComponent(tag)}&limit=12`);
  const color = SERIES()[0];

  if (graficoCoocurrencia) graficoCoocurrencia.destroy();
  graficoCoocurrencia = new Chart($("#grafico-coocurrencia"), {
    type: "bar",
    data: {
      labels: d.items.map((c) => c.slug),
      datasets: [{
        label: `Ofertas junto a ${tag}`,
        data: d.items.map((c) => c.juntas),
        backgroundColor: color,
        borderRadius: { topRight: 4, bottomRight: 4 },
        maxBarThickness: 16,
      }],
    },
    options: base({
      indexAxis: "y",
      scales: { x: ejeValor(`ofertas junto a ${tag}`), y: ejeCategoria() },
      plugins: {
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const c = d.items[ctx.dataIndex];
              return [`${pct(c.pct)} de las ofertas de ${tag}`, `lift ${c.lift.toFixed(2)}`];
            },
          },
        },
      },
    }),
  });

  tabla($("#tabla-coocurrencia"), [
    { titulo: "Tecnología", valor: (c) => c.slug },
    { titulo: "Juntas", num: true, valor: (c) => num(c.juntas) },
    { titulo: `% de ${tag}`, num: true, valor: (c) => pct(c.pct) },
    { titulo: "Lift", num: true, valor: (c) => c.lift.toFixed(2) },
  ], d.items);
}

function montarSelectorCoocurrencia(top) {
  const sel = $("#selector-coocurrencia");
  sel.innerHTML = top.slice(0, 30).map((t) => `<option value="${t.slug}">${t.slug}</option>`).join("");
  sel.addEventListener("change", () => pintarCoocurrencia(sel.value));
  if (top.length) pintarCoocurrencia(top[0].slug);
}

/* ------------------------------------------------------------------ sueldos */

async function pintarSalarios() {
  const d = await traer("/api/salarios", "limit=15");
  const color = SERIES()[0];

  $("#nota-salarios").innerHTML = `Mediana del punto medio del rango, en <strong>${d.moneda}/mes</strong>
    (supuesto: la API no expone moneda). Solo se listan las tecnologías con al menos
    ${d.min_muestra} ofertas que publiquen sueldo — el <em>n</em> va siempre a la vista.
    Mediana del conjunto: <strong>${num(d.global.mediana)}</strong> sobre ${num(d.global.n_con_salario)}
    de ${num(d.global.n_total)} ofertas (${d.global.cobertura}%).`;

  new Chart($("#grafico-salarios"), {
    type: "bar",
    data: {
      labels: d.items.map((s) => s.slug),
      datasets: [{
        label: `Mediana ${d.moneda}/mes`,
        data: d.items.map((s) => s.mediana),
        backgroundColor: color,
        borderRadius: { topRight: 4, bottomRight: 4 },
        maxBarThickness: 16,
      }],
    },
    options: base({
      indexAxis: "y",
      scales: { x: ejeValor(`mediana ${d.moneda}/mes`), y: ejeCategoria() },
      plugins: {
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const s = d.items[ctx.dataIndex];
              return [
                `p25 ${num(s.p25)} · p75 ${num(s.p75)}`,
                `${s.n_con_salario} de ${s.n_total} ofertas publican sueldo (${s.cobertura}%)`,
              ];
            },
          },
        },
      },
    }),
  });

  tabla($("#tabla-salarios"), [
    { titulo: "Tecnología", valor: (s) => s.slug },
    { titulo: "Mediana", num: true, valor: (s) => num(s.mediana) },
    { titulo: "p25", num: true, valor: (s) => num(s.p25) },
    { titulo: "p75", num: true, valor: (s) => num(s.p75) },
    { titulo: "Con sueldo", num: true, valor: (s) => `${s.n_con_salario}/${s.n_total}` },
  ], d.items);
}

/* ------------------------------------------------------------------ brechas */

async function pintarBrechas() {
  const d = await traer("/api/brechas", "limit=20");
  const panel = $("#panel-brechas");

  if (d.stack_vacio) {
    panel.innerHTML = `<p class="vacio">Rellena <code>data/mi_stack.yml</code> con lo que ya manejas
      y recarga: sin eso, todo el mercado sale como brecha.</p>`;
    return;
  }

  const maximo = Math.max(...d.items.map((b) => b.ofertas), 1);
  const encabezado = `<p class="sub" style="margin-bottom:12px">
      Tu stack cubre de media el <strong>${d.cobertura_media}%</strong> de los tags de cada oferta,
      sobre ${num(d.total_ofertas)} con los filtros actuales.
      Ninguna oferta se cubre al 100%: la lista de tags es una carta a los Reyes, no un requisito.
    </p>`;

  const cuerpo = d.items
    .map(
      (b) => `<tr>
        <td>${b.slug}${b.solo_texto ? ' <span class="marca-texto">solo texto</span>' : ""}</td>
        <td class="num">${num(b.ofertas)}</td>
        <td style="width:34%"><span class="barra-mini" style="width:${(100 * b.ofertas) / maximo}%"></span></td>
        <td class="num">${pct(b.afinidad)}</td>
      </tr>`
    )
    .join("");

  const cola = d.sin_demanda.length
    ? `<p class="sub" style="margin-top:12px">
         <strong>En tu stack pero sin demanda aquí (${d.sin_demanda.length}):</strong>
         ${d.sin_demanda.join(", ")}.
         O el mercado no lo pide, o el nombre no casa con ningún tag — en ese segundo caso
         se arregla con un alias en <code>data/tag_aliases.yml</code>.
       </p>`
    : "";

  panel.innerHTML = `${encabezado}
    <div class="desplazable"><table>
      <thead><tr>
        <th>Tecnología</th><th class="num">Ofertas</th><th></th><th class="num">Afinidad</th>
      </tr></thead>
      <tbody>${cuerpo}</tbody>
    </table></div>${cola}`;
}

/* ----------------------------------------------------------------- empresas */

async function pintarEmpresas() {
  const d = await traer("/api/empresas", "limit=15");
  tabla($("#tabla-empresas"), [
    { titulo: "Empresa", valor: (e) => e.name },
    { titulo: "Ofertas", num: true, valor: (e) => num(e.ofertas) },
    { titulo: "Con sueldo", num: true, valor: (e) => num(e.con_salario) },
    { titulo: "Stack dominante", valor: (e) => `<span class="tags-oferta">${e.stack.map((s) => s.slug).join(", ")}</span>` },
  ], d);
}

/* ------------------------------------------------------------------ ofertas */

let offsetOfertas = 0;
const PASO_OFERTAS = 50;
let filasOfertas = [];

const COLUMNAS_OFERTAS = [
  { titulo: "Oferta", valor: (j) => `<a href="${j.url}" target="_blank" rel="noopener">${j.title}</a>` },
  { titulo: "Empresa", valor: (j) => j.empresa || "—" },
  { titulo: "Seniority", valor: (j) => j.seniority || "—" },
  { titulo: "Modalidad", valor: (j) => j.remote_modality || "—" },
  { titulo: "Dónde", valor: (j) => (j.paises.join(", ") || "—") + (j.ciudades.length ? ` · ${j.ciudades.join(", ")}` : "") },
  {
    titulo: `Sueldo ${MONEDA}`,
    num: true,
    valor: (j) => (j.min_salary || j.max_salary ? `${num(j.min_salary)}–${num(j.max_salary)}` : "—"),
  },
  { titulo: "Publicada", valor: (j) => (j.published_at ? new Date(j.published_at * 1000).toISOString().slice(0, 10) : "—") },
  { titulo: "Tecnologías", valor: (j) => `<span class="tags-oferta">${j.tags.slice(0, 8).join(", ")}</span>` },
];

async function pintarOfertas(acumular = false) {
  const d = await traer("/api/jobs", `limit=${PASO_OFERTAS}&offset=${offsetOfertas}`);
  filasOfertas = acumular ? filasOfertas.concat(d.items) : d.items;
  tabla($("#tabla-ofertas"), COLUMNAS_OFERTAS, filasOfertas);
  $("#nota-ofertas").textContent = `${num(filasOfertas.length)} de ${num(d.total)} con los filtros actuales, de la más reciente a la más antigua.`;
  const boton = $("#mas-ofertas");
  boton.disabled = filasOfertas.length >= d.total;
  boton.textContent = boton.disabled ? "No hay más" : `Ver ${Math.min(PASO_OFERTAS, d.total - filasOfertas.length)} más`;
}

/* ------------------------------------------------------------------- tema */

function montarTema() {
  const boton = $("#conmutador-tema");
  const oscuroAhora = () =>
    document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;

  const etiquetar = () => {
    const oscuro = oscuroAhora();
    boton.textContent = oscuro ? "Tema claro" : "Tema oscuro";
    boton.setAttribute("aria-pressed", String(oscuro));
  };

  boton.addEventListener("click", () => {
    localStorage.setItem("gob-tema", oscuroAhora() ? "claro" : "oscuro");
    // Chart.js congela los colores al crear el gráfico: recargar es más honesto
    // (y más corto) que reconstruir los siete a mano.
    location.reload();
  });

  etiquetar();
}

/* --------------------------------------------------------------- arranque */

async function iniciar() {
  montarTema();
  const resumen = await pintarKpis();
  if (!resumen.ofertas) {
    document.querySelectorAll(".lienzo").forEach((c) => {
      c.innerHTML = '<p class="vacio">Sin ofertas para estos filtros.</p>';
    });
    return;
  }

  const top = await pintarTop();
  montarSelectorTendencia(top);
  montarSelectorCoocurrencia(top);

  await Promise.all([
    pintarSeniority(),
    pintarTendencia(),
    pintarSalarios(),
    pintarBrechas(),
    pintarEmpresas(),
    pintarOfertas(),
  ]);

  $("#mas-ofertas").addEventListener("click", () => {
    offsetOfertas += PASO_OFERTAS;
    pintarOfertas(true);
  });
}

iniciar().catch((err) => {
  console.error(err);
  document.querySelector("main").insertAdjacentHTML(
    "afterbegin",
    `<div class="aviso aviso--serio"><strong>Error cargando los datos.</strong> ${err.message}</div>`
  );
});
