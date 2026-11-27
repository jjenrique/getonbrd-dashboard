/* Versión estática: contesta los fetch() a /api/* desde las respuestas que
 * scripts/exportar_estatico.py dejó congeladas en window.GOB_ESTATICO.
 *
 * Así dashboard.js no sabe que no hay servidor: pide lo mismo de siempre y
 * recibe exactamente lo que habría devuelto la app.
 *
 * Única ruta que se arma aquí: /api/tendencia con varias tecnologías elegidas
 * a mano. Cada serie se calcula sola en el servidor, así que el exportador
 * guarda una por tecnología y aquí se juntan en el orden pedido.
 */
(function () {
  const D = window.GOB_ESTATICO || { respuestas: {} };
  const R = D.respuestas;
  const fetchReal = window.fetch ? window.fetch.bind(window) : null;

  const responder = (cuerpo, status) =>
    Promise.resolve(
      new Response(JSON.stringify(cuerpo), { status: status || 200, headers: { "Content-Type": "application/json" } })
    );

  function tendencia(qs) {
    const tags = (new URLSearchParams(qs).get("tags") || "").split(",").filter(Boolean);
    const series = [];
    let base = null;
    for (const tag of tags) {
      const una = R[`/api/tendencia?tags=${encodeURIComponent(tag)}`];
      if (!una) return null;
      base = base || una;
      series.push(...una.series);
    }
    return base ? { ...base, series } : null;
  }

  window.fetch = function (recurso, opciones) {
    const url = typeof recurso === "string" ? recurso : recurso.url;
    if (!url.startsWith("/api/")) {
      return fetchReal ? fetchReal(recurso, opciones) : Promise.reject(new Error("sin red"));
    }
    if (Object.prototype.hasOwnProperty.call(R, url)) return responder(R[url]);

    const [ruta, qs] = url.split("?");
    if (ruta === "/api/tendencia") {
      const armada = tendencia(qs || "");
      if (armada) return responder(armada);
    }
    return responder({ error: `${url} no está en la versión estática` }, 404);
  };
})();
