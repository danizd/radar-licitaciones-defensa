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
    # Nota: si se pasan --dry-run y --marcar-sin-enviar juntos, --dry-run gana
    # (no se marca nada, solo se simula).
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import feedparser
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("novedades")

RUTA_FEED = "output/licitaciones_defensa.xml"
RUTA_ESTADO = "output/vistos.json"
RUTA_CONFIG = "config.yaml"

ASUNTO = "Novedades de licitaciones de defensa"


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


def leer_ids_feed(ruta: str) -> list[dict]:
    """Devuelve [{id, titulo, enlace, fuente}] desde el RSS generado."""
    feed = feedparser.parse(ruta)
    items = []
    for entrada in feed.entries:
        titulo = entrada.get("title", "(sin título)")
        fuente = "?"
        if titulo.startswith("[") and "] " in titulo:
            fuente, _, titulo = titulo.partition("] ")
            fuente = fuente.lstrip("[")
        items.append({
            "id": entrada.get("id") or entrada.get("link") or entrada.get("title"),
            "titulo": titulo,
            "enlace": entrada.get("link", ""),
            "fuente": fuente,
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


def construir_email(items_nuevos: list[dict]) -> EmailMessage:
    cuerpo = [f"Hola, hay {len(items_nuevos)} novedad(es) en el radar de licitaciones:\n"]
    for it in items_nuevos:
        cuerpo.append(f"[{it.get('fuente', '?')}] {it['titulo']}")
        if it.get("enlace"):
            cuerpo.append(f"    {it['enlace']}")
        cuerpo.append("")
    cuerpo.append("---")
    cuerpo.append("Radar de licitaciones de defensa (auto-generado).")

    msg = EmailMessage()
    msg["Subject"] = ASUNTO
    msg.set_content("\n".join(cuerpo))
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
    args = parser.parse_args()

    items = leer_ids_feed(args.feed)
    vistos = cargar_vistos(args.estado)
    nuevos = [it for it in items if it["id"] not in vistos]

    if not nuevos:
        logger.info("Sin novedades: no se envía email.")
        return

    logger.info("%d novedad(es) sin enviar de %d items totales.", len(nuevos), len(items))

    if args.dry_run:
        print(f"[dry-run] Se enviaría un email con {len(nuevos)} novedades:")
        for it in nuevos:
            print(f"  - [{it['fuente']}] {it['titulo']}")
            if it["enlace"]:
                print(f"    {it['enlace']}")
        return

    if args.marcar_sin_enviar:
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
    enviar_email(smtp, msg, len(nuevos))

    nuevos_ids = {it["id"] for it in nuevos}
    vistos |= nuevos_ids
    guardar_vistos(args.estado, vistos)
    logger.info("Estado actualizado: %d ids registrados en %s", len(vistos), args.estado)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - salida limpia en cron; traceback via logger
        logger.exception("Error al enviar novedades: %s", exc)
        sys.exit(1)
