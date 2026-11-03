"""Lectura de config.yaml y rutas del proyecto."""

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATA = RAIZ / "data"
DIR_CRUDOS = DIR_DATA / "crudos"
RUTA_DB = DIR_DATA / "sein.sqlite"
RUTA_LOG = DIR_DATA / "actualizacion.log"

PORTAL = "https://www.coes.org.pe/Portal"
BASE_IEOD = "Post Operación/Reportes/IEOD/"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Setiembre", "Octubre", "Noviembre", "Diciembre"]


@lru_cache(maxsize=1)
def cargar():
    """Diccionario con la configuracion (se lee una vez por proceso)."""
    with open(RAIZ / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["fecha_inicio"] = date.fromisoformat(str(cfg["fecha_inicio"]))
    return cfg


def barras_monitoreadas():
    """{nombre visible: nombre COES} de las barras del config."""
    return dict(cargar()["barras"])


def barras_a_guardar(nombres_en_archivo):
    """Filtra los nombres de barra del IEOD segun guardar_barras."""
    cfg = cargar()
    modo = str(cfg.get("guardar_barras", "220")).lower()
    fijas = set(barras_monitoreadas().values()) | {cfg["barra_referencia"]}
    if modo == "todas":
        return list(nombres_en_archivo)
    if modo == "config":
        return [n for n in nombres_en_archivo if n in fijas]
    return [n for n in nombres_en_archivo if n in fijas or str(n).strip().endswith(" 220")]
