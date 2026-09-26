"""
Genera config_30d.yaml a partir de config.yaml para la búsqueda histórica.

Uso (en el workflow de GitHub Actions o localmente):
    python scripts/gen_config_30d.py [--dias 30] [--limite 800] [--salida output/licitaciones_defensa_30d.xml]
"""
from __future__ import annotations
import argparse
import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera config histórica a partir de config.yaml")
    parser.add_argument("--base", default="config.yaml", help="Config de partida")
    parser.add_argument("--dias", type=int, default=30, help="Días hacia atrás")
    parser.add_argument("--limite", type=int, default=800, help="Límite total de resultados TED")
    parser.add_argument("--salida-config", default="config_30d.yaml", help="Ruta de config generada")
    parser.add_argument("--salida-feed", default="output/licitaciones_defensa_30d.xml", help="Feed de salida")
    parser.add_argument("--max-items", type=int, default=200, help="Máximo de items en el RSS")
    args = parser.parse_args()

    with open(args.base, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cfg.setdefault("fuentes", {}).setdefault("ted", {})
    cfg["fuentes"]["ted"]["dias_hacia_atras"] = args.dias
    cfg["fuentes"]["ted"]["limite_resultados"] = args.limite
    cfg.setdefault("salida", {})
    cfg["salida"]["fichero"] = args.salida_feed
    cfg["salida"]["max_items"] = args.max_items

    with open(args.salida_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    print(
        f"Generada {args.salida_config}: dias={args.dias}, "
        f"limite={args.limite}, feed={args.salida_feed}"
    )


if __name__ == "__main__":
    main()
