"""
Genera una vista previa HTML del feed RSS de salida, con estética de lector
de feeds, pensada para documentación (README).

Uso:
    python scripts/generar_preview_feed.py
    python scripts/generar_preview_feed.py output/licitaciones_defensa.xml docs/preview.html
"""
from __future__ import annotations

import html
import re
import sys
from datetime import datetime

import feedparser

DEFAULT_ENTRADA = "output/licitaciones_defensa.xml"
DEFAULT_SALIDA = "docs/preview.html"

# Colores por fuente para la etiqueta del origen de cada item.
COLORES_FUENTE = {
    "PLACSP": ("#1d6f42", "#e6f4ec"),
    "TED": ("#1d4ed8", "#e8effd"),
}

PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{titulo}</title>
<style>
  :root {{
    --fondo: #f1f5f9;
    --tarjeta: #ffffff;
    --texto: #0f172a;
    --secundario: #64748b;
    --acento: #1d4ed8;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 28px;
    background: var(--fondo);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--texto);
  }}
  .cabecera {{
    background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
    color: #fff; border-radius: 14px; padding: 22px 26px;
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; margin-bottom: 20px;
  }}
  .cabecera h1 {{ margin: 0; font-size: 20px; font-weight: 700; }}
  .cabecera .meta {{ margin-top: 4px; font-size: 13px; opacity: .8; }}
  .insignia-feed {{
    background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.3);
    border-radius: 999px; padding: 6px 12px; font-size: 12px; white-space: nowrap;
  }}
  .item {{
    background: var(--tarjeta); border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 16px 20px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(15,23,42,.05);
  }}
  .item .cabecera-item {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
  .fuente {{
    font-size: 11px; font-weight: 700; letter-spacing: .04em;
    padding: 3px 9px; border-radius: 999px; white-space: nowrap;
  }}
  .titulo-item {{ margin: 0; font-size: 15px; line-height: 1.45; font-weight: 600; }}
  .titulo-item a {{ color: var(--texto); text-decoration: none; }}
  .titulo-item a:hover {{ color: var(--acento); text-decoration: underline; }}
  .descripcion {{ margin: 8px 0 0; font-size: 13px; line-height: 1.5; color: #334155; }}
  .descripcion .etiquetas {{ color: var(--acento); font-weight: 600; }}
  .enlace {{
    display: inline-block; margin-top: 10px; font-size: 12.5px;
    color: var(--acento); text-decoration: none; font-weight: 600;
  }}
  .enlace:hover {{ text-decoration: underline; }}
  .pie {{ text-align: center; font-size: 12px; color: var(--secundario); margin-top: 18px; }}
</style>
</head>
<body>
  <div class="cabecera">
    <div>
      <h1>{titulo}</h1>
      <div class="meta">Feed RSS · {fecha} · {num_items} licitaciones relevantes</div>
    </div>
    <div class="insignia-feed">RSS 2.0 · idioma: es</div>
  </div>
  {items}
  <div class="pie">Generado con el agregador del proyecto · fuente: {ruta}</div>
</body>
</html>
"""


def _estilo_item(item: dict) -> str:
    # En el RSS la fuente va como prefijo del título ("[TED] ..."): se extrae
    # de ahí y se quita del título mostrado, ya que la insignia la representa.
    titulo_original = item.get("title", "(sin título)")
    fuente = "?"
    titulo = titulo_original
    if titulo_original.startswith("[") and "]" in titulo_original:
        fuente, _, titulo = titulo_original.partition("]")
        fuente = fuente.lstrip("[")
        titulo = titulo.lstrip()
    fondo, texto = COLORES_FUENTE.get(fuente, ("#475569", "#eef2f7"))

    descripcion = item.get("description", "") or ""
    # feedparser puede normalizar <br/> a <br />: se aceptan ambas formas.
    descripcion = re.sub(r"&lt;br\s*/?&gt;", "<br>", html.escape(descripcion))
    if descripcion.startswith("Capacidades detectadas:"):
        primera, _, resto = descripcion.partition("<br>")
        etiquetas = primera.replace("Capacidades detectadas:", "").strip()
        descripcion_html = (
            f'<span class="etiquetas">Capacidades detectadas:</span> {etiquetas}'
            + (f"<br>{resto}" if resto else "")
        )
    else:
        descripcion_html = descripcion

    enlace = item.get("link", "")
    enlace_html = (
        f'<a class="enlace" href="{html.escape(enlace)}">Ver en la fuente →</a>'
        if enlace
        else ""
    )

    titulo = html.escape(titulo)
    return f"""
  <div class="item">
    <div class="cabecera-item">
      <span class="fuente" style="background:{texto};color:{fondo}">{html.escape(fuente)}</span>
      <h2 class="titulo-item">{titulo}</h2>
    </div>
    <p class="descripcion">{descripcion_html}</p>
    {enlace_html}
  </div>"""


def main() -> None:
    entrada = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENTRADA
    salida = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SALIDA

    feed = feedparser.parse(entrada)
    items_html = "\n".join(_estilo_item(e) for e in feed.entries)

    titulo = (feed.feed.get("title") or "Licitaciones de Defensa").replace(
        " - Radar PYME", ""
    )
    if feed.feed.get("updated_parsed"):
        fecha = datetime(*feed.feed.updated_parsed[:6]).strftime("%d/%m/%Y %H:%M")
    else:
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

    pagina = PLANTILLA.format(
        titulo=html.escape(titulo),
        fecha=fecha,
        num_items=len(feed.entries),
        items=items_html,
        ruta=html.escape(entrada),
    )

    with open(salida, "w", encoding="utf-8") as f:
        f.write(pagina)
    print(f"Vista previa generada: {salida} ({len(feed.entries)} items)")


if __name__ == "__main__":
    main()
