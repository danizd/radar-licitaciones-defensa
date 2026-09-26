"""
Test de humo (sin red real): valida que el pipeline completo funciona
usando fixtures locales para PLACSP y TED.

Ejecutar con:
    python -m tests.test_smoke   (desde la raíz del proyecto)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matcher import construir_indice_palabras, capacidades_coincidentes
from sources import placsp, ted
import aggregator
import scripts.enviar_novedades as novedades

RUTA_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def test_matcher_basico():
    capacidades = {
        "uas_uav": {"palabras_clave": ["dron", "UAS", "aeronave no tripulada"]},
        "vehiculos": {"palabras_clave": ["vetrónica"]},
    }
    indice = construir_indice_palabras(capacidades)
    assert capacidades_coincidentes("Adquisición de un DRON de vigilancia", indice) == ["uas_uav"]
    assert capacidades_coincidentes("Suministro de sillas de oficina", indice) == []
    # la palabra clave lleva tilde ("vetrónica") pero el texto de entrada no
    # ("vetronica"): debe seguir coincidiendo gracias a la normalización.
    assert capacidades_coincidentes("Mantenimiento de vetronica para blindados", indice) == ["vehiculos"]
    print("OK: matcher básico")


def test_acronimos_solo_palabra_completa():
    """
    Los acrónimos cortos (UAS, SOC, UAV...) solo deben coincidir como
    palabra completa (límites de palabra), no como subcadena dentro de
    otras palabras: "UAS" no debe colar por "aguas" ni "SOC" por
    "sociedad"/"asociación".
    """
    capacidades = {
        "uas_uav": {"palabras_clave": ["UAS", "UAV", "aeronave no tripulada"]},
        "ciberseguridad": {"palabras_clave": ["SOC"]},
    }
    indice = construir_indice_palabras(capacidades)

    # Falsos positivos previos: acrónimo como subcadena de otra palabra
    assert capacidades_coincidentes("Depuradora de aguas residuales", indice) == []
    assert capacidades_coincidentes("Sociedad cooperativa veladas", indice) == []
    assert capacidades_coincidentes("Asociación de municipios", indice) == []

    # Positivos reales: acrónimo como palabra independiente
    assert capacidades_coincidentes("Sistema UAS de vigilancia", indice) == ["uas_uav"]
    assert capacidades_coincidentes("Servicio SOC 24x7", indice) == ["ciberseguridad"]
    # El plural inglés típico de acrónimos ("UAVs") sigue coincidiendo
    assert capacidades_coincidentes("Adquisición de UAVs tácticos", indice) == ["uas_uav"]
    # Las palabras clave largas siguen coincidiendo como subcadena
    assert capacidades_coincidentes("contrato de aeronave no tripulada ligera", indice) == ["uas_uav"]
    print("OK: acrónimos solo como palabra completa")


def test_umbral_acronimo_configurable():
    """
    `longitud_max_acronimo` es configurable: con umbral 0 todas las
    palabras clave coinciden por subcadena (comportamiento antiguo),
    y con un umbral alto más palabras cortas exigen coincidencia
    exacta de palabra.
    """
    capacidades = {
        "uas_uav": {"etiqueta": "UAS / UAV / Drones", "palabras_clave": ["UAS", "dron"]},
    }
    indice = construir_indice_palabras(capacidades)

    # Umbral 0: subcadena pura -> "UAS" vuelve a colar por "aguas"
    assert capacidades_coincidentes(
        "Depuradora de aguas residuales", indice, longitud_max_acronimo=0
    ) == ["uas_uav"]
    assert capacidades_coincidentes(
        "dron de vigilancia", indice, longitud_max_acronimo=0
    ) == ["uas_uav"]

    # Umbral por defecto (4): "UAS" exige palabra completa, "dron" (4
    # letras) también
    assert capacidades_coincidentes("Depuradora de aguas residuales", indice) == []
    assert capacidades_coincidentes("dron de vigilancia", indice) == ["uas_uav"]

    # Umbral 6: "dron" (4 letras) exige palabra completa -> ya no casa
    # con "drones" por subcadena
    assert capacidades_coincidentes(
        "suministro de drones", indice, longitud_max_acronimo=6
    ) == []

    # El agregador lee el umbral de config.yaml (bloque filtrado)
    config_filtrado = {
        "capacidades": capacidades,
        "filtrado": {"longitud_max_acronimo": 0},
    }
    items = [{"titulo": "Depuradora de aguas residuales", "resumen": ""}]
    filtrados = aggregator.filtrar_por_capacidades(items, config_filtrado)
    assert len(filtrados) == 1, "con umbral 0 el item debe pasar por subcadena"
    print("OK: umbral de acrónimo configurable (config.yaml -> filtrado)")


def test_placsp_parsing():
    ruta = os.path.join(RUTA_FIXTURES, "placsp_ejemplo.atom")
    with open(ruta, "rb") as f:
        contenido = f.read()
    items = placsp.parsear_atom(contenido)
    assert len(items) == 3
    assert items[0]["fuente"] == "PLACSP"
    assert "cloud" in items[0]["titulo"].lower() or "borde" in items[0]["titulo"].lower()
    # El summary trae "Importe: 850000.00 EUR" -> campo importe numérico
    assert items[0]["importe"] == 850000.0
    assert items[1]["importe"] == 15000.0
    # Sin importe en el summary -> None
    assert placsp._parsear_importe("Id licitación: X; Estado: PUB") is None
    print(f"OK: PLACSP parseó {len(items)} entradas (con importe)")


def test_filtro_por_importe_maximo():
    """
    `filtrado.importe_maximo` descarta las licitaciones por encima del
    umbral (oportunidades tier-2 accesibles para PYME). Los items sin
    importe (p. ej. TED) se conservan siempre.
    """
    items = [
        {"titulo": "Pequeña", "importe": 40000.0},
        {"titulo": "En el límite", "importe": 100000.0},
        {"titulo": "Grande", "importe": 1200000.0},
        {"titulo": "Sin importe (TED)", "importe": None},
    ]

    # Sin umbral configurado: no filtra nada
    assert len(aggregator.filtrar_por_importe(items, {})) == 4

    config = {"filtrado": {"importe_maximo": 100000}}
    conservados = aggregator.filtrar_por_importe(items, config)
    assert [i["titulo"] for i in conservados] == ["Pequeña", "En el límite", "Sin importe (TED)"]
    print("OK: filtro por importe máximo (tier-2)")


def test_novedades_email():
    """El script de email solo envía lo que no está en el estado vistos.json."""
    ruta_feed = os.path.join("output", "test_smoke.xml")
    items = novedades.leer_ids_feed(ruta_feed)
    assert len(items) > 0
    assert all(it["fuente"] in ("PLACSP", "TED") for it in items)

    # Primera ejecución: todo es novedad
    nuevos = [it for it in items if it["id"] not in set()]
    assert len(nuevos) == len(items)

    # Simula un vistos.json ya lleno con el primer id: ese ya no es novedad
    vistos = {items[0]["id"]}
    nuevos = [it for it in items if it["id"] not in vistos]
    assert len(nuevos) == len(items) - 1
    assert items[0]["id"] not in {n["id"] for n in nuevos}

    # El email construido contiene los títulos de las novedades
    msg = novedades.construir_email(items[:1])
    assert items[0]["titulo"] in msg.get_content()
    print("OK: novedades por email solo con items no enviados")


def test_novedades_sin_config_smtp_error():
    """Sin SMTP configurado, el envío debe fallar con un error claro (no crashear)."""
    try:
        novedades.enviar_email({}, novedades.construir_email([{"titulo": "x"}]), 1)
    except RuntimeError as exc:
        assert "SMTP" in str(exc)
    else:
        raise AssertionError("Se esperaba RuntimeError por falta de SMTP")
    print("OK: falta de SMTP da error claro")


def test_ted_normalizacion():
    ruta = os.path.join(RUTA_FIXTURES, "ted_ejemplo.json")
    with open(ruta, "r", encoding="utf-8") as f:
        datos = json.load(f)
    items = ted._normalizar_respuesta(datos)
    assert len(items) == 2
    assert items[0]["fuente"] == "TED"
    assert "ciberseguridad" in items[0]["titulo"].lower()
    assert items[0]["enlace"].startswith("https://ted.europa.eu/en/notice/-/detail/")
    print(f"OK: TED normalizó {len(items)} anuncios")


def test_ted_prefiere_espanol():
    """
    La API de TED devuelve los títulos en varios idiomas con claves ISO
    639-2 de 3 letras ({"hun": ..., "spa": ..., "eng": ...}); se debe
    elegir el español aunque no sea el primer idioma del dict, y el
    buyer-name (lista) debe quedar como texto plano.
    """
    anuncio = {
        "publication-number": "999999-2026",
        "notice-title": {
            "hun": "Spanyolország – X",
            "eng": "Spain – X",
            "spa": "España – Servicios de ciberseguridad",
        },
        "buyer-name": {"spa": ["Ministerio de Defensa"]},
    }
    normalizado = ted._normalizar_respuesta({"notices": [anuncio]})[0]
    assert normalizado["titulo"] == "España – Servicios de ciberseguridad"
    assert "Spanyolország" not in normalizado["titulo"]
    assert normalizado["resumen"] == "Ministerio de Defensa"

    # Sin español: cae al inglés; sin inglés: al primer idioma disponible
    assert ted._valor_multilingue({"hun": "X", "eng": "EN title"}) == "EN title"
    assert ted._valor_multilingue({"hun": "HUN title"}) == "HUN title"
    print("OK: TED elige título en español (y fallback a inglés/cualquiera)")


def test_pipeline_completo_con_config_real():
    """
    Reutiliza config.yaml real, pero sustituye las funciones de red por
    las fixtures locales (monkeypatch), para probar filtrado + generación
    de RSS de punta a punta sin tocar internet.
    """
    config = aggregator.cargar_config(
        os.path.join(os.path.dirname(RUTA_FIXTURES), "..", "config.yaml")
    )

    with open(os.path.join(RUTA_FIXTURES, "placsp_ejemplo.atom"), "rb") as f:
        items_placsp = placsp.parsear_atom(f.read())

    with open(os.path.join(RUTA_FIXTURES, "ted_ejemplo.json"), "r", encoding="utf-8") as f:
        items_ted = ted._normalizar_respuesta(json.load(f))

    items = items_placsp + items_ted
    items = aggregator.deduplicar(items)
    items_filtrados = aggregator.filtrar_por_capacidades(items, config)

    # de los 5 items de ejemplo (3 PLACSP + 2 TED), deben colar 3:
    # cloud/borde, UAS+blindado, y ciberseguridad. La papelería y el
    # mobiliario deben quedar fuera.
    assert len(items_filtrados) == 3, f"Se esperaban 3 items relevantes, salieron {len(items_filtrados)}"

    config_test = dict(config)
    config_test["salida"] = {
        "titulo_feed": "Test",
        "fichero": os.path.join("output", "test_smoke.xml"),
        "max_items": 60,
    }
    ruta_generada = aggregator.generar_rss(items_filtrados, config_test)
    assert os.path.exists(ruta_generada)
    with open(ruta_generada, "r", encoding="utf-8") as f:
        contenido_rss = f.read()
    assert "<rss" in contenido_rss
    assert "Capacidades detectadas" in contenido_rss
    print(f"OK: pipeline completo -> {len(items_filtrados)} items relevantes, RSS válido en {ruta_generada}")


if __name__ == "__main__":
    test_matcher_basico()
    test_acronimos_solo_palabra_completa()
    test_umbral_acronimo_configurable()
    test_placsp_parsing()
    test_ted_normalizacion()
    test_ted_prefiere_espanol()
    test_pipeline_completo_con_config_real()
    test_filtro_por_importe_maximo()
    test_novedades_email()
    test_novedades_sin_config_smtp_error()
    print("\nTodos los tests de humo han pasado correctamente.")
