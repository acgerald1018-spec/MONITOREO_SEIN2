"""Consultas a la base para el tablero. Devuelven DataFrames listos para graficar."""

import sqlite3
from datetime import date, timedelta

import pandas as pd

from monitor import config
from monitor.parsers import clave

# dia operativo de una marca de fin de intervalo (00:00 pertenece al dia anterior)
DIA = "date(ts, '-1 minute')"
MES = "strftime('%Y-%m', ts, '-1 minute')"


def _con():
    return sqlite3.connect(f"file:{config.RUTA_DB}?mode=ro", uri=True)


def _q(sql, params=()):
    if not config.RUTA_DB.exists():
        return pd.DataFrame()
    with _con() as con:
        return pd.read_sql_query(sql, con, params=params)


def _lim(desde, hasta):
    """Limites de ts para un rango de dias operativos [desde, hasta]."""
    return f"{desde:%Y-%m-%d} 00:30", f"{hasta + timedelta(days=1):%Y-%m-%d} 00:00"


def hay_datos():
    if not config.RUTA_DB.exists():
        return False
    df = _q("select count(*) n from cmg")
    return not df.empty and df["n"].iloc[0] > 0


def rango():
    """(primer dia, ultimo dia) con datos de CMg o demanda."""
    df = _q(f"select min({DIA}) a, max({DIA}) b from (select ts from cmg union all select ts from demanda)")
    if df.empty or df["a"].iloc[0] is None:
        return None, None
    return date.fromisoformat(df["a"].iloc[0]), date.fromisoformat(df["b"].iloc[0])


def barras_disponibles():
    """Barras con historico (al menos la mitad de los datos de la barra mas completa).

    En la nube, la copia publicada trae solo las barras del config; las demas de 220 kV solo
    tendrian los ultimos dias y no se ofrecen.
    """
    if not hay_datos():
        return []
    df = _q("select barra, count(*) n from cmg group by barra")
    return sorted(df.loc[df["n"] >= 0.5 * df["n"].max(), "barra"])


def ultima_actualizacion():
    df = _q("select momento, mensaje from bitacora where mensaje like 'Fin:%' order by id desc limit 1")
    return None if df.empty else df.iloc[0].to_dict()


# ---------------------------------------------------------------- costo marginal

def cmg(barras, desde, hasta):
    a, b = _lim(desde, hasta)
    marcas = ",".join("?" * len(barras))
    df = _q(f"select ts, barra, total, energia, congestion, fuente from cmg "
            f"where barra in ({marcas}) and ts between ? and ? order by ts", (*barras, a, b))
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
    return df


def cmg_diario(barras):
    marcas = ",".join("?" * len(barras))
    df = _q(f"select {DIA} dia, barra, avg(total) prom, max(total) maximo, min(total) minimo, "
            f"max(fuente = 'TR') preliminar from cmg where barra in ({marcas}) group by dia, barra order by dia",
            tuple(barras))
    if not df.empty:
        df["dia"] = pd.to_datetime(df["dia"])
    return df


def cmg_mensual(barras):
    cfg = config.cargar()
    p1, p2 = cfg["punta_desde"], cfg["punta_hasta"]
    marcas = ",".join("?" * len(barras))
    hora = "strftime('%H:%M', ts)"
    df = _q(f"""select {MES} mes, barra, avg(total) prom,
                avg(case when {hora} between ? and ? then total end) punta,
                avg(case when {hora} not between ? and ? then total end) fuera_punta,
                max(total) maximo, min(total) minimo, count(*) n
                from cmg where barra in ({marcas}) group by mes, barra order by mes""",
            (p1, p2, p1, p2, *barras))
    return df


# ---------------------------------------------------------------- demanda

def demanda(desde, hasta):
    a, b = _lim(desde, hasta)
    df = _q("select * from demanda where ts between ? and ? order by ts", (a, b))
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
    return df


def demanda_mensual():
    df = _q(f"""select {MES} mes, max(sein) max_mw, min(sein) min_mw, avg(sein) prom_mw,
                sum(sein) * 0.5 / 1000 energia_gwh, max(norte) max_norte, max(centro) max_centro,
                max(sur) max_sur, sum(norte) * 0.5 / 1000 e_norte, sum(centro) * 0.5 / 1000 e_centro,
                sum(sur) * 0.5 / 1000 e_sur, count(*) n
                from demanda group by mes order by mes""")
    return df


def demanda_diaria():
    df = _q(f"select {DIA} dia, max(sein) max_mw, sum(sein) * 0.5 / 1000 energia_gwh, "
            f"max(fuente = 'TR') preliminar from demanda group by dia order by dia")
    if not df.empty:
        df["dia"] = pd.to_datetime(df["dia"])
    return df


# ---------------------------------------------------------------- generacion

def _tecnologia(recurso):
    return config.cargar()["tecnologias"].get(clave(recurso), "Otros")


def generacion(desde, hasta):
    a, b = _lim(desde, hasta)
    df = _q("select ts, recurso, mw from generacion where ts between ? and ?", (a, b))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    df["tecnologia"] = df["recurso"].map(_tecnologia)
    return df.groupby(["ts", "tecnologia"], as_index=False)["mw"].sum()


def generacion_mensual():
    df = _q(f"select {MES} mes, recurso, sum(mw) * 0.5 / 1000 gwh, count(distinct {DIA}) dias "
            f"from generacion group by mes, recurso")
    if df.empty:
        return df
    df["tecnologia"] = df["recurso"].map(_tecnologia)
    return df.groupby(["mes", "tecnologia"], as_index=False).agg(gwh=("gwh", "sum"), dias=("dias", "max"))


def generacion_pmax_mensual():
    """Maxima potencia simultanea despachada por tecnologia en el mes (MW)."""
    df = _q("select ts, recurso, mw from generacion")
    if df.empty:
        return df
    df["tecnologia"] = df["recurso"].map(_tecnologia)
    df["ts"] = pd.to_datetime(df["ts"])
    df["mes"] = (df["ts"] - pd.Timedelta(minutes=1)).dt.strftime("%Y-%m")
    por_ts = df.groupby(["mes", "ts", "tecnologia"], as_index=False)["mw"].sum()
    return por_ts.groupby(["mes", "tecnologia"], as_index=False)["mw"].max()


def unidades_mensual():
    """Unidades que generaron en el mes por tipo de central (hoja DESPACHO_EJECUTADO)."""
    df = _q("""select substr(fecha, 1, 7) mes, tipo, count(distinct case when energia_mwh > 0 then codigo end) unidades,
               count(distinct case when energia_mwh > 0 then unidad end) nombres,
               sum(energia_mwh) / 1000 gwh, max(pmax_mw) pmax_unidad
               from unidades group by mes, tipo order by mes""")
    return df


def ranking_unidades(desde, hasta, n=25):
    df = _q("""select unidad, empresa, tipo, area, sum(energia_mwh) / 1000 gwh, max(pmax_mw) pmax_mw
               from unidades where fecha between ? and ? group by codigo order by gwh desc limit ?""",
            (desde.isoformat(), hasta.isoformat(), n))
    return df


# ---------------------------------------------------------------- estado

def cobertura():
    df = _q("""select substr(fecha, 1, 7) mes, count(*) dias, sum(cmg) dias_cmg, sum(anexo) dias_anexo
               from dias group by mes order by mes""")
    return df


def dias_con_aviso(n=50):
    return _q("select fecha, cmg, anexo, detalle, actualizado from dias where cmg = 0 or anexo = 0 "
              "order by fecha desc limit ?", (n,))


def bitacora(n=200):
    return _q("select momento, nivel, mensaje from bitacora order by id desc limit ?", (n,))
