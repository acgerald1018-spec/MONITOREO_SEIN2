"""Copia liviana de la base para publicar en GitHub / Streamlit Community Cloud.

exportar():  data/sein.sqlite  ->  publicado/*.parquet  (+ manifiesto.json)
sembrar():   publicado/*.parquet -> data/sein.sqlite, si la base no existe o esta mas atrasada.

En la nube los archivos locales pueden borrarse al reiniciar la app: con la semilla arranca al
instante y la actualizacion automatica solo baja los dias posteriores a la copia.
"""

import json
import sqlite3
from datetime import datetime

import pandas as pd

from monitor import config, db

DIR_PUB = config.RAIZ / "publicado"
# Si la copia se subio sin su carpeta (archivos sueltos en la raiz del repositorio), tambien sirve
if not (DIR_PUB / "manifiesto.json").exists() and (config.RAIZ / "manifiesto.json").exists():
    DIR_PUB = config.RAIZ
MANIFIESTO = DIR_PUB / "manifiesto.json"
TABLAS = ["demanda", "generacion", "unidades", "dias"]


def _barras_a_publicar(con):
    cfg = config.cargar()
    modo = str(cfg.get("compartir", {}).get("publicar_barras", "config")).lower()
    todas = [b for (b,) in con.execute("select distinct barra from cmg")]
    if modo == "config":
        fijas = set(config.barras_monitoreadas().values()) | {cfg["barra_referencia"]}
        return [b for b in todas if b in fijas]
    if modo == "220":
        return config.barras_a_guardar(todas)
    return todas


def exportar(progreso=print):
    """Escribe la copia en publicado/. Devuelve el manifiesto."""
    if not config.RUTA_DB.exists():
        raise FileNotFoundError("No hay base local: primero corre Actualizar.bat")
    DIR_PUB.mkdir(exist_ok=True)
    with sqlite3.connect(config.RUTA_DB) as con:
        barras = _barras_a_publicar(con)
        marcas = ",".join("?" * len(barras))
        cm = pd.read_sql_query(f"select * from cmg where barra in ({marcas}) and fuente = 'IEOD'", con,
                               params=barras)
        cm["anio"] = cm["ts"].str[:4]
        for anio, parte in cm.groupby("anio"):  # un archivo por año: los años cerrados no cambian
            _guardar(parte.drop(columns="anio"), f"cmg_{anio}.parquet")
        for t in TABLAS:
            df = pd.read_sql_query(f"select * from {t}" + (" where fuente = 'IEOD'" if t == "demanda" else ""), con)
            _guardar(df, f"{t}.parquet")
        hasta = con.execute("select max(fecha) from dias where cmg = 1 and anexo = 1").fetchone()[0]
    manifiesto = {"exportado": datetime.now().strftime("%Y-%m-%d %H:%M"), "hasta": hasta,
                  "barras": barras, "filas_cmg": len(cm)}
    MANIFIESTO.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")
    tam = sum(p.stat().st_size for p in DIR_PUB.glob("*.parquet")) / 1e6
    progreso(f"Copia lista en 'publicado' ({tam:.1f} MB), datos del IEOD hasta {hasta}.")
    return manifiesto


def _guardar(df, nombre):
    for col in ("barra", "recurso", "fuente", "unidad", "empresa", "tipo", "area", "codigo", "detalle"):
        if col in df:
            df[col] = df[col].astype("category")
    df.to_parquet(DIR_PUB / nombre, compression="zstd", index=False)


def sembrar():
    """Carga la copia publicada si la base local no existe o esta mas atrasada. Devuelve texto o None."""
    if not MANIFIESTO.exists():
        return None
    man = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    with db.conectar() as con:
        ultimo = con.execute("select max(fecha) from dias where cmg = 1 and anexo = 1").fetchone()[0]
        if ultimo and man.get("hasta") and ultimo >= man["hasta"]:
            return None
        n = 0
        for archivo in sorted(DIR_PUB.glob("cmg_*.parquet")):
            df = pd.read_parquet(archivo)
            con.executemany("insert or ignore into cmg values (?,?,?,?,?,?)",
                            df[["ts", "barra", "total", "energia", "congestion", "fuente"]]
                            .astype(object).itertuples(index=False))
            n += len(df)
        for t in TABLAS:
            archivo = DIR_PUB / f"{t}.parquet"
            if archivo.exists():
                df = pd.read_parquet(archivo).astype(object)
                df = df.where(df.notna(), None)
                cols = ",".join(df.columns)
                con.executemany(f"insert or ignore into {t} ({cols}) values ({','.join('?' * len(df.columns))})",
                                df.itertuples(index=False))
        db.anotar(con, "INFO", f"Base cargada desde la copia publicada ({man.get('exportado')}, hasta {man.get('hasta')})")
    return f"Copia publicada cargada: {n:,} valores de CMg hasta {man.get('hasta')}"


if __name__ == "__main__":
    exportar()
