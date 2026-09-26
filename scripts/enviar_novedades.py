"""
Envía por email las NOVEDADES del feed RSS generado (output/licitaciones_defensa.xml),
sin repetir los items que ya se enviaron en ejecuciones anteriores.

Cómo funciona la deduplicación:
- El estado de lo ya enviado se guarda en output/vistos.json (versionado en
  el repo si usas GitHub Actions, o en el propio VPS si usas cron).
- Cada ejecución: lee el feed, calcula los items cuyo id no está en vistos.json,
  envía el email con solo esos, y añade sus ids al estado.

Configuración (SMTP estándar, gratuito, SIN API de terceros):
- Prioridad 1: bloque `email.smtp` de config.yaml.
- Prioridad 2: variables de entorno SMTP_HOST, SMTP_PORT, SMTP_USER,
  SMTP_PASS, MAIL_FROM, MAIL_TO.
Ejemplo con Gmail (requiere contraseña de aplicación):
  SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=tu@gmail.com \
  SMTP_PASS=xxxxxxxx MAIL_TO=tu@gmail.com python scripts/enviar_novedades.py

Uso:
    python scripts/enviar_novedades.py            # envía y actualiza vistos.json
    python scripts/enviar_novedades.py --dry-run  # simula: no envía, no guarda estado
    python scripts/enviar_novedades.py --marcar-sin-enviar  # primera vez: marca los
                                                  # items actuales como vistos sin
                                                  # enviar email (evita el histórico)
    python scripts/enviar_novedades.py --todos    # ignora vistos.json, envía TODO el feed
    # Nota: si se pasan --dry-run y --marcar-sin-enviar juntos, --dry-run gana
    # (no se marca nada, solo se simula).
"""
from __future__ import annotations
import argparse
import html as html_lib
import json
import logging
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import feedparser
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("novedades")

RUTA_FEED = "output/licitaciones_defensa.xml"
RUTA_ESTADO = "output/vistos.json"
RUTA_CONFIG = "config.yaml"

# ── Colores por fuente y por capacidad ──────────────────────────────────────
COLOR_FUENTE = {
    "TED":    {"bg": "#dbeafe", "fg": "#1e40af", "accent": "#3b82f6", "label": "TED · UE"},
    "PLACSP": {"bg": "#fef3c7", "fg": "#92400e", "accent": "#f59e0b", "label": "PLACSP · España"},
}
COLOR_FUENTE_DEFAULT = {"bg": "#e2e8f0", "fg": "#334155", "accent": "#64748b", "label": "?"}

# ── Helpers config / vistos ─────────────────────────────────────────────────

def cargar_config() -> dict:
    try:
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def configuracion_smtp() -> dict:
    """Junta config.yaml + variables de entorno (las env ganan)."""
    cfg = (cargar_config().get("email") or {}).get("smtp") or {}
    try:
        puerto = int(os.environ.get("SMTP_PORT") or cfg.get("puerto") or 587)
    except (TypeError, ValueError):
        logger.warning("SMTP_PORT no es un número; se usa 587.")
        puerto = 587
    cfg_smtp = {
        "host": os.environ.get("SMTP_HOST") or cfg.get("host"),
        "puerto": puerto,
        "usuario": os.environ.get("SMTP_USER") or cfg.get("usuario"),
        "password": os.environ.get("SMTP_PASS") or cfg.get("password"),
        "desde": os.environ.get("MAIL_FROM") or cfg.get("desde"),
        "para": os.environ.get("MAIL_TO") or cfg.get("para"),
    }
    return cfg_smtp


def _extraer_capacidades_y_resumen(raw_desc: str) -> tuple[list[str], str]:
    """Separa 'Capacidades detectadas: X, Y<br/>resto' en (capacidades, resto)."""
    if not raw_desc:
        return [], ""
    # feedparser decodifica entidades pero deja <br/> como texto; normalizamos
    # Posibles variantes: <br/>, <br />, <br>, &lt;br/&gt;
    partes = re.split(r"(?:<br\s*/?>|&lt;br\s*/?&gt;)", raw_desc, maxsplit=1, flags=re.IGNORECASE)
    primera = html_lib.unescape(partes[0]).strip() if partes else ""
    resto = html_lib.unescape(partes[1]).strip() if len(partes) > 1 else ""
    capacidades: list[str] = []
    if primera.startswith("Capacidades detectadas:"):
        caps_raw = primera[len("Capacidades detectadas:"):].strip()
        capacidades = [c.strip() for c in caps_raw.split(",") if c.strip()]
        return capacidades, resto
    # sin prefijo → todo es resumen
    return [], html_lib.unescape(raw_desc).strip()


def leer_ids_feed(ruta: str) -> list[dict]:
    """Devuelve [{id, titulo, enlace, fuente, capacidades, resumen, fecha}] desde el RSS."""
    feed = feedparser.parse(ruta)
    items = []
    for entrada in feed.entries:
        titulo_raw = entrada.get("title", "(sin título)")
        fuente = "?"
        titulo = titulo_raw
        if titulo_raw.startswith("[") and "] " in titulo_raw:
            fuente, _, titulo = titulo_raw.partition("] ")
            fuente = fuente.lstrip("[")

        raw_desc = entrada.get("description") or entrada.get("summary") or ""
        capacidades, resumen = _extraer_capacidades_y_resumen(raw_desc)

        # Fecha legible
        fecha_raw = entrada.get("published") or entrada.get("updated") or ""
        fecha_corta = ""
        if fecha_raw:
            try:
                # feedparser puede dar published_parsed
                pp = entrada.get("published_parsed") or entrada.get("updated_parsed")
                if pp:
                    dt = datetime(*pp[:6], tzinfo=timezone.utc)
                    fecha_corta = dt.strftime("%d %b %Y")
                else:
                    fecha_corta = fecha_raw[:16]
            except Exception:
                fecha_corta = fecha_raw[:16]

        items.append({
            "id": entrada.get("id") or entrada.get("link") or entrada.get("title"),
            "titulo": titulo.strip(),
            "enlace": entrada.get("link", ""),
            "fuente": fuente,
            "capacidades": capacidades,
            "resumen": resumen,
            "fecha": fecha_corta,
        })
    return items


def cargar_vistos(ruta: str) -> set:
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def guardar_vistos(ruta: str, vistos: set) -> None:
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(sorted(vistos), f, ensure_ascii=False, indent=2)


# ── Construcción del email (HTML + texto plano) ─────────────────────────────

def _asunto(items_nuevos: list[dict]) -> str:
    n = len(items_nuevos)
    hoy = datetime.now().strftime("%d/%m/%Y")
    if n == 1:
        return f"Radar Defensa · 1 nueva licitación · {hoy}"
    return f"Radar Defensa · {n} nuevas licitaciones · {hoy}"


def _html_cap_badges(capacidades: list[str]) -> str:
    if not capacidades:
        return ""
    badges = []
    for cap in capacidades:
        badges.append(
            f'<span style="display:inline-block;background:#e0e7ff;color:#3730a3;'
            f'font-size:11px;font-weight:600;letter-spacing:0.3px;'
            f'padding:3px 8px;border-radius:999px;margin:2px 4px 2px 0;'
            f'border:1px solid #c7d2fe;">{html_lib.escape(cap)}</span>'
        )
    return "".join(badges)


def _html_card(item: dict, idx: int) -> str:
    fuente_cfg = COLOR_FUENTE.get(item.get("fuente", ""), COLOR_FUENTE_DEFAULT)
    capacidades_html = _html_cap_badges(item.get("capacidades", []))
    titulo = html_lib.escape(item.get("titulo", "(sin título)"))
    resumen = html_lib.escape(item.get("resumen", ""))
    enlace = html_lib.escape(item.get("enlace", ""))
    fecha = html_lib.escape(item.get("fecha", ""))
    fuente_label = html_lib.escape(fuente_cfg["label"])
    accent = fuente_cfg["accent"]
    bg = fuente_cfg["bg"]
    fg = fuente_cfg["fg"]

    # Resumen truncado para email
    if len(resumen) > 220:
        resumen = resumen[:217] + "…"

    capacidades_block = ""
    if capacidades_html:
        capacidades_block = (
            f'<tr><td style="padding:8px 20px 0 20px;">'
            f'<div style="line-height:1.6;">{capacidades_html}</div></td></tr>'
        )

    enlace_btn = ""
    if enlace:
        enlace_btn = (
            f'<a href="{enlace}" '
            f'style="display:inline-block;background:{accent};color:#ffffff;'
            f'text-decoration:none;font-size:13px;font-weight:600;'
            f'padding:8px 16px;border-radius:6px;margin-top:4px;">'
            f'Ver licitaci\u00f3n&nbsp;&rarr;</a>'
        )
        # link fallback para clientes que bloquean botones
        enlace_fallback = (
            f'<div style="margin-top:8px;word-break:break-all;">'
            f'<a href="{enlace}" style="color:{accent};font-size:11px;text-decoration:underline;">{enlace}</a></div>'
        )
    else:
        enlace_btn = ""
        enlace_fallback = ""

    fecha_html = f'<span style="color:#94a3b8;font-size:11px;">{fecha}</span>' if fecha else ""

    return f"""
    <!-- Card {idx} -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px 0;">
      <tr>
        <td style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid {accent};border-radius:8px;overflow:hidden;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:14px 20px 6px 20px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td>
                      <span style="display:inline-block;background:{bg};color:{fg};font-size:10px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;padding:3px 8px;border-radius:4px;">{fuente_label}</span>
                      <span style="display:inline-block;width:8px;"></span>
                      {fecha_html}
                    </td>
                    <td align="right" style="color:#94a3b8;font-size:11px;font-weight:600;">#{idx}</td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 20px 0 20px;">
                <div style="font-size:15px;font-weight:700;line-height:1.35;color:#0f172a;">{titulo}</div>
              </td>
            </tr>
            {f'<tr><td style="padding:6px 20px 0 20px;"><div style="font-size:13px;line-height:1.5;color:#475569;">{resumen}</div></td></tr>' if resumen else ''}
            {capacidades_block}
            <tr>
              <td style="padding:14px 20px 16px 20px;">
                {enlace_btn}
                {enlace_fallback}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """


def _resumen_por_capacidad(items: list[dict]) -> str:
    """Barra de resumen agrupada por capacidad para la cabecera del email."""
    from collections import Counter
    counter: Counter = Counter()
    for it in items:
        for c in it.get("capacidades", []):
            counter[c] += 1
    if not counter:
        return ""
    # Orden por frecuencia descendente
    chips = []
    for cap, n in counter.most_common():
        chips.append(
            f'<span style="display:inline-block;background:rgba(255,255,255,0.15);'
            f'color:#ffffff;font-size:11px;font-weight:600;'
            f'padding:4px 10px;border-radius:999px;margin:3px;'
            f'border:1px solid rgba(255,255,255,0.25);">{html_lib.escape(cap)} · {n}</span>'
        )
    return "".join(chips)


def construir_email(items_nuevos: list[dict]) -> EmailMessage:
    n = len(items_nuevos)
    asunto = _asunto(items_nuevos)
    hoy_largo = datetime.now().strftime("%A %d de %B de %Y").capitalize()
    # Intento de traducir días/meses si locale no es es
    try:
        import locale
        locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
        hoy_largo = datetime.now().strftime("%A %d de %B de %Y").capitalize()
    except Exception:
        pass

    resumen_chips = _resumen_por_capacidad(items_nuevos)
    cards_html = "\n".join(_html_card(it, i) for i, it in enumerate(items_nuevos, 1))

    # ── Texto plano (fallback) ──────────────────────────────────────────────
    lineas_txt = [f"Radar de Licitaciones de Defensa — {hoy_largo}", f"{n} novedad(es) detectadas", ""]
    for i, it in enumerate(items_nuevos, 1):
        lineas_txt.append(f"{i}. [{it.get('fuente','?')}] {it['titulo']}")
        if it.get("capacidades"):
            lineas_txt.append(f"   Capacidades: {', '.join(it['capacidades'])}")
        if it.get("resumen"):
            lineas_txt.append(f"   {it['resumen'][:180]}")
        if it.get("fecha"):
            lineas_txt.append(f"   Fecha: {it['fecha']}")
        if it.get("enlace"):
            lineas_txt.append(f"   {it['enlace']}")
        lineas_txt.append("")
    lineas_txt.append("—")
    lineas_txt.append("Radar de licitaciones de defensa (auto-generado). Feed: output/licitaciones_defensa.xml")
    texto_plano = "\n".join(lineas_txt)

    # ── HTML ─────────────────────────────────────────────────────────────────
    html = f"""\
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_lib.escape(asunto)}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    {n} nueva(s) licitaci\u00f3n(es) detectadas para tu PYME — {hoy_largo}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:0;margin:0;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">

          <!-- Header -->
          <tr>
            <td style="background:#0f172a;border-radius:12px 12px 0 0;padding:28px 24px 22px 24px;border-bottom:4px solid #3b82f6;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <div style="font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#38bdf8;margin:0 0 8px 0;">Radar de Licitaciones</div>
                    <div style="font-size:22px;font-weight:800;line-height:1.2;color:#ffffff;margin:0;">Defensa &amp; Seguridad</div>
                    <div style="font-size:13px;color:#94a3b8;margin-top:6px;">Selecci\u00f3n diaria personalizada para tu PYME</div>
                  </td>
                  <td align="right" valign="top" style="padding-left:12px;">
                    <div style="display:inline-block;background:#3b82f6;color:#ffffff;font-size:13px;font-weight:800;padding:10px 14px;border-radius:999px;text-align:center;min-width:56px;">
                      <div style="font-size:22px;line-height:1;">{n}</div>
                      <div style="font-size:9px;letter-spacing:0.6px;text-transform:uppercase;opacity:0.9;">nueva(s)</div>
                    </div>
                  </td>
                </tr>
                <tr>
                  <td colspan="2" style="padding-top:14px;">
                    <div style="font-size:12px;color:#64748b;">
                      <span style="color:#cbd5e1;">&#9673; {html_lib.escape(hoy_largo)}</span>
                      <span style="margin:0 8px;color:#334155;">|</span>
                      <span style="color:#94a3b8;">{n} licitaci\u00f3n(es) relevante(s) seg\u00fan tus capacidades</span>
                    </div>
                    {f'<div style="margin-top:12px;">{resumen_chips}</div>' if resumen_chips else ''}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Intro -->
          <tr>
            <td style="background:#ffffff;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;padding:18px 20px;">
              <div style="font-size:13px;line-height:1.6;color:#334155;">
                Hola — hemos rastreado <strong style="color:#0f172a;">PLACSP</strong> y <strong style="color:#0f172a;">TED (UE)</strong> y estas son las licitaciones que encajan con tus capacidades configuradas en <code style="background:#f1f5f9;padding:1px 6px;border-radius:4px;font-size:12px;">config.yaml</code>.
                Cada tarjeta indica la fuente, las capacidades detectadas y el enlace oficial.
              </div>
            </td>
          </tr>

          <!-- Cards -->
          <tr>
            <td style="background:#f8fafc;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;padding:16px 16px 8px 16px;">
              {cards_html}
            </td>
          </tr>

          <!-- CTA secundario -->
          <tr>
            <td style="background:#ffffff;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;padding:16px 20px;text-align:center;">
              <div style="font-size:12px;color:#64748b;line-height:1.6;">
                Feed RSS completo (para Feedly / Inoreader):<br>
                <code style="background:#f1f5f9;color:#334155;padding:4px 8px;border-radius:6px;font-size:11px;word-break:break-all;">output/licitaciones_defensa.xml</code>
              </div>
              <div style="margin-top:12px;">
                <a href="https://github.com/danizd/radar-licitaciones-defensa" style="display:inline-block;color:#3b82f6;font-size:12px;font-weight:600;text-decoration:none;border:1px solid #dbeafe;background:#eff6ff;padding:6px 12px;border-radius:999px;">Ver repositorio en GitHub &rarr;</a>
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:18px 8px 0 8px;text-align:center;">
              <div style="font-size:11px;line-height:1.6;color:#94a3b8;">
                Radar de licitaciones de defensa · Generado autom\u00e1ticamente cada d\u00eda a las 07:00 UTC.<br>
                Si no quieres recibir m\u00e1s avisos, desactiva el workflow en GitHub Actions o quita tu email de <code style="background:#e2e8f0;padding:1px 4px;border-radius:3px;">MAIL_TO</code>.
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg.set_content(texto_plano)
    msg.add_alternative(html, subtype="html")
    return msg


def enviar_email(smtp: dict, msg: EmailMessage, num_novedades: int) -> None:
    if not smtp.get("host") or not smtp.get("usuario") or not smtp.get("password"):
        raise RuntimeError(
            "Falta configuración SMTP (host/usuario/password). "
            "Configúralo en config.yaml (email.smtp) o con SMTP_HOST/SMTP_USER/SMTP_PASS."
        )
    para = smtp["para"]
    if isinstance(para, str):
        para = [para]
    if not para:
        raise RuntimeError("Falta destinatario (MAIL_TO o email.smtp.para).")

    msg["From"] = smtp.get("desde") or smtp["usuario"]
    msg["To"] = ", ".join(para)

    with smtplib.SMTP(smtp["host"], smtp["puerto"], timeout=30) as servidor:
        servidor.starttls()
        servidor.login(smtp["usuario"], smtp["password"])
        servidor.send_message(msg)
    logger.info("Email enviado a %s (%d novedades)", msg["To"], num_novedades)


def main() -> None:
    parser = argparse.ArgumentParser(description="Envía por email las novedades del feed RSS")
    parser.add_argument("--feed", default=RUTA_FEED, help="Ruta al RSS generado")
    parser.add_argument("--estado", default=RUTA_ESTADO, help="Fichero de estado (ids ya enviados)")
    parser.add_argument("--dry-run", action="store_true", help="Simula: no envía ni guarda estado")
    parser.add_argument(
        "--marcar-sin-enviar",
        action="store_true",
        help=(
            "Solo marca los items actuales como ya vistos (puebla vistos.json) "
            "sin enviar ningún email. Útil en la primera ejecución para no "
            "recibir el histórico completo. Si se usa junto a --dry-run, "
            "--dry-run tiene prioridad."
        ),
    )
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Ignora vistos.json y envía TODO el feed (útil para reenvíos o búsquedas históricas).",
    )
    parser.add_argument(
        "--ignorar-vistos",
        action="store_true",
        help="Alias de --todos.",
    )
    parser.add_argument("--preview-html", default=None, help="Si se indica, guarda el HTML del email en ese fichero (útil para previsualizar sin enviar).")
    args = parser.parse_args()

    forzar_todos = args.todos or args.ignorar_vistos

    items = leer_ids_feed(args.feed)
    vistos = set() if forzar_todos else cargar_vistos(args.estado)
    nuevos = items if forzar_todos else [it for it in items if it["id"] not in vistos]

    if not nuevos:
        logger.info("Sin novedades: no se envía email.")
        return

    if forzar_todos:
        logger.info("Modo --todos: se enviarán %d items (ignorando vistos.json con %d ids).", len(nuevos), len(cargar_vistos(args.estado)))
    else:
        logger.info("%d novedad(es) sin enviar de %d items totales.", len(nuevos), len(items))

    if args.dry_run:
        print(f"[dry-run] Se enviaría un email con {len(nuevos)} novedades:")
        for it in nuevos:
            print(f"  - [{it['fuente']}] {it['titulo']}")
            if it["enlace"]:
                print(f"    {it['enlace']}")
        # preview html opcional
        if args.preview_html:
            _guardar_preview(items_nuevos=nuevos, ruta=args.preview_html)
            print(f"[dry-run] Preview HTML guardado en {args.preview_html}")
        return

    if args.marcar_sin_enviar and not forzar_todos:
        nuevos_ids = {it["id"] for it in nuevos}
        vistos |= nuevos_ids
        guardar_vistos(args.estado, vistos)
        logger.info(
            "--marcar-sin-enviar: %d items marcados como vistos en %s (sin enviar email).",
            len(nuevos_ids),
            args.estado,
        )
        return

    smtp = configuracion_smtp()
    msg = construir_email(nuevos)

    if args.preview_html:
        _guardar_preview_html_msg(msg, args.preview_html)
        logger.info("Preview HTML guardado en %s", args.preview_html)

    enviar_email(smtp, msg, len(nuevos))

    # Si era modo --todos, actualizar vistos con todo lo enviado igualmente
    nuevos_ids = {it["id"] for it in nuevos}
    vistos = cargar_vistos(args.estado) | nuevos_ids
    guardar_vistos(args.estado, vistos)
    logger.info("Estado actualizado: %d ids registrados en %s", len(vistos), args.estado)


def _guardar_preview(items_nuevos: list[dict], ruta: str) -> None:
    msg = construir_email(items_nuevos)
    _guardar_preview_html_msg(msg, ruta)


def _guardar_preview_html_msg(msg: EmailMessage, ruta: str) -> None:
    html_part = msg.get_body(preferencelist=("html",))
    if html_part:
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        Path(ruta).write_text(html_part.get_content(), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - salida limpia en cron; traceback via logger
        logger.exception("Error al enviar novedades: %s", exc)
        sys.exit(1)
