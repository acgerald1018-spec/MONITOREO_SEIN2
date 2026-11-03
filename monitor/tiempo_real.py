"""Costo marginal y demanda en TIEMPO REAL (preliminar) para los dias que el IEOD aun no cubre.

- CMg: POST /mercadomayorista/costosmarginales/listaproceso (procesos del dia) y
       POST /mercadomayorista/costosmarginales/lista (tabla de barras >= 138 kV por proceso)
- Demanda SEIN ejecutada y programada: POST /portalinformacion/Demanda

Estos valores se guardan con fuente 'TR' y se reemplazan solos cuando llega el IEOD.
"""

import html
import json
import re
from datetime import datetime, timedelta

import pandas as pd

from monitor import cliente, config, db

FILA = re.compile(r"<tr>\s*" + r"\s*".join([r"<td>(.*?)</td>"] * 5) + r"\s*</tr>", re.S)


def _fecha(d):
    return d.strftime("%d/%m/%Y")


def _fin_intervalo(dia, hhmm):
    h, m = (int(x) for x in hhmm.split(":")[:2])
    t = datetime(dia.year, dia.month, dia.day, h, m)
    return t + timedelta(minutes=1) if m == 59 else t


def procesos(dia):
    crudo = cliente.pedir("POST", "/mercadomayorista/costosmarginales/listaproceso", datos={"fecha": _fecha(dia)})
    lista = json.loads(crudo.decode("utf-8-sig"))
    salida = {}
    for p in lista:
        if p.get("FechaProceso"):
            salida[_fin_intervalo(dia, p["FechaProceso"])] = p["Cmgncorrelativo"]  # el ultimo recalculo gana
    return sorted(salida.items())


def cmg_proceso(correlativo, momento):
    crudo = cliente.pedir("POST", "/mercadomayorista/costosmarginales/lista",
                          datos={"correlativo": correlativo, "defecto": "S"})
    filas = []
    for _hora, barra, ene, cong, tot in FILA.findall(crudo.decode("utf-8", "replace")):
        try:
            filas.append({"ts": momento, "barra": re.sub(r"\s+", " ", html.unescape(barra)).strip(),
                          "energia": float(ene), "congestion": float(cong), "total": float(tot)})
        except ValueError:
            continue
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df[df["barra"].isin(config.barras_a_guardar(df["barra"].unique()))]
    return df


def demanda_dia(dia):
    crudo = cliente.pedir("POST", "/portalinformacion/Demanda",
                          datos={"fechaInicial": _fecha(dia), "fechaFinal": _fecha(dia)})
    datos = json.loads(crudo.decode("utf-8-sig"))
    valores = {}
    for serie in datos["Chart"]["Series"]:
        nombre = serie["Name"].lower()
        col = "sein" if nombre.startswith("ejecutad") else ("programada" if "diari" in nombre else None)
        if col is None:
            continue
        for p in serie["Data"]:
            if p.get("Valor") in (None, 0):
                continue
            ts = datetime.strptime(p["Nombre"], "%Y/%m/%d %H:%M:%S")
            valores.setdefault(ts, {})[col] = float(p["Valor"])
    df = pd.DataFrame([{"ts": t, **v} for t, v in sorted(valores.items())])
    return df


def actualizar(con, dias, progreso=lambda *a: None):
    """Completa en la base los intervalos que faltan. Devuelve un texto de resumen."""
    ref = config.cargar()["barra_referencia"]
    ya = {r[0] for r in con.execute(
        "select ts from cmg where barra = ? and ts >= ?", (ref, f"{dias[0]:%Y-%m-%d}"))}
    n_cmg = n_dem = 0
    for dia in dias:
        try:
            pendientes = [(m, c) for m, c in procesos(dia) if m.strftime(db.FMT) not in ya]
            # se repite el ultimo proceso por si el COES lo recalculo
            for i, (momento, corr) in enumerate(pendientes):
                progreso(f"Tiempo real {dia:%d/%m}: CMg {momento:%H:%M} ({i + 1}/{len(pendientes)})")
                n_cmg += db.guardar_cmg(con, cmg_proceso(corr, momento), "TR")
            con.commit()
        except Exception as e:
            db.anotar(con, "AVISO", f"CMg tiempo real {dia}: {e}")
        try:
            progreso(f"Tiempo real {dia:%d/%m}: demanda")
            n_dem += db.guardar_demanda(con, demanda_dia(dia), "TR")
            con.commit()
        except Exception as e:
            db.anotar(con, "AVISO", f"Demanda tiempo real {dia}: {e}")
    return f"tiempo real: {n_cmg} valores de CMg y {n_dem} de demanda"
