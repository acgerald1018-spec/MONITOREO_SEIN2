"""Lectura de los Excel del IEOD. Todo se busca por contenido (no por fila fija),
porque el COES mueve filas y columnas entre años.

Convencion de tiempo: la marca es el FIN del intervalo de 30 min, hora Peru.
El ultimo intervalo del dia (que el COES escribe 24:00, 00:00 o 23:59) pasa a las
00:00 del dia siguiente. El dia operativo de una marca es (marca - 1 minuto).
"""

import io
import re
import unicodedata
import zipfile
from datetime import date, datetime, time, timedelta

import openpyxl
import pandas as pd


def clave(texto):
    """'BIOGÁS', '\\nH. PASADA\\n' -> 'BIOGAS', 'HPASADA'."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", t)


def limpio(texto):
    return re.sub(r"\s+", " ", str(texto).replace("�", "")).strip()


def _hm(valor):
    """(hora, minuto) de una celda de hora, o None."""
    if isinstance(valor, datetime):
        return valor.hour, valor.minute
    if isinstance(valor, time):
        return valor.hour, valor.minute
    if isinstance(valor, str):
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", valor)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def marca(dia: date, hm):
    """Fin de intervalo como Timestamp; 00:00 / 23:59 / 24:00 -> 00:00 del dia siguiente."""
    h, m = hm
    minutos = h * 60 + m
    if minutos == 0 or (h == 23 and m == 59) or minutos >= 24 * 60:
        minutos = 24 * 60
    return pd.Timestamp(dia) + pd.Timedelta(minutes=minutos)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _libro(contenido):
    fuente = io.BytesIO(contenido) if isinstance(contenido, (bytes, bytearray)) else contenido
    return openpyxl.load_workbook(fuente, read_only=True, data_only=True)


def _hoja(libro, nombre):
    """Hoja por nombre, sin importar mayusculas, tildes ni espacios."""
    buscado = clave(nombre)
    for n in libro.sheetnames:
        if clave(n) == buscado:
            return libro[n]
    return None


# ---------------------------------------------------------------------------
# Costo marginal (zip CMgAAAAMMDD.zip -> CMgCPDDMM.xlsx)
# ---------------------------------------------------------------------------

def excel_cmg_del_zip(contenido_zip, dia: date):
    """Bytes del CMgCP del dia dentro del zip del IEOD."""
    with zipfile.ZipFile(io.BytesIO(contenido_zip)) as z:
        nombres = [n for n in z.namelist() if n.lower().endswith((".xlsx", ".xlsm"))]
        preferido = f"cmgcp{dia:%d%m}.xlsx"
        for n in nombres:
            if n.replace("\\", "/").split("/")[-1].lower() == preferido:
                return z.read(n)
        for n in nombres:
            if n.replace("\\", "/").split("/")[-1].lower().startswith("cmgcp"):
                return z.read(n)
    raise ValueError("El zip del IEOD no trae el Excel CMgCP")


def _leer_hoja_cmg(ws, dia):
    filas = list(ws.iter_rows(values_only=True))
    i_cab = None
    for i, f in enumerate(filas[:15]):
        textos = [c for c in f if isinstance(c, str) and c.strip()]
        if len(textos) > 10:
            i_cab = i
            break
    if i_cab is None:
        raise ValueError(f"Hoja {ws.title}: no encontre la fila de barras")
    cab = filas[i_cab]
    # columna de la hora: la primera que tiene horas debajo de la cabecera
    col_hora = None
    for j in range(min(4, len(cab))):
        if any(_hm(f[j]) for f in filas[i_cab + 1:i_cab + 5] if j < len(f)):
            col_hora = j
            break
    if col_hora is None:
        raise ValueError(f"Hoja {ws.title}: no encontre la columna de horas")
    barras = {j: limpio(c) for j, c in enumerate(cab) if j > col_hora and isinstance(c, str) and c.strip()}
    registros = []
    for f in filas[i_cab + 1:]:
        hm = _hm(f[col_hora]) if col_hora < len(f) else None
        if hm is None:
            continue
        ts = marca(dia, hm)
        for j, b in barras.items():
            v = _num(f[j]) if j < len(f) else None
            if v is not None:
                registros.append((ts, b, v))
    return pd.DataFrame(registros, columns=["ts", "barra", "valor"])


def leer_cmg(contenido_xlsx, dia: date):
    """DataFrame ts, barra, total, energia, congestion (S/./MWh)."""
    libro = _libro(contenido_xlsx)
    try:
        partes = {}
        for nombre, col in (("Cmg_Barra", "total"), ("Cmg_Ener", "energia"), ("Cmg_Cong", "congestion")):
            ws = _hoja(libro, nombre)
            if ws is None:
                if col == "total":
                    raise ValueError("El Excel de CMg no tiene la hoja Cmg_Barra")
                continue
            partes[col] = _leer_hoja_cmg(ws, dia).rename(columns={"valor": col}).set_index(["ts", "barra"])
    finally:
        libro.close()
    df = pd.concat(partes.values(), axis=1).reset_index()
    for c in ("total", "energia", "congestion"):
        if c not in df:
            df[c] = None
    return _validar_dia(df, dia, "CMg")[["ts", "barra", "total", "energia", "congestion"]]


# ---------------------------------------------------------------------------
# Anexo A del IEOD
# ---------------------------------------------------------------------------

def leer_demanda_areas(libro, dia):
    """DataFrame ts, sein, norte, centro, sur, intercambio (MW). None si no esta la hoja."""
    ws = _hoja(libro, "DEMANDA_AREAS")
    if ws is None:
        return None
    filas = list(ws.iter_rows(values_only=True))
    normal = [[clave(c) if isinstance(c, str) else None for c in f] for f in filas]
    i_cab = next((i for i, f in enumerate(normal) if "SEIN" in f), None)
    if i_cab is None:
        raise ValueError("DEMANDA_AREAS sin cabecera SEIN")
    cols = {}
    for nombre in ("SEIN", "NORTE", "CENTRO", "SUR"):
        if nombre in normal[i_cab]:
            cols[nombre.lower()] = normal[i_cab].index(nombre)
    inter = next((j for j, c in enumerate(normal[i_cab]) if c and c.startswith("INTERCAMBIO")), None)
    if inter is not None:
        cols["intercambio"] = inter
    registros = []
    for f in filas[i_cab + 1:]:
        hm = next((_hm(c) for c in f[:cols["sein"]] if _hm(c)), None)
        if hm is None:
            continue
        registros.append({"ts": marca(dia, hm), **{k: _num(f[j]) for k, j in cols.items()}})
    df = pd.DataFrame(registros)
    return _validar_dia(df, dia, "DEMANDA_AREAS")


def leer_tipo_recurso(libro, dia):
    """DataFrame ts, recurso, mw (generacion por tipo de recurso). None si no esta la hoja."""
    ws = _hoja(libro, "TIPO_RECURSO")
    if ws is None:
        return None
    filas = list(ws.iter_rows(values_only=True))
    i_cab = j_hora = None
    for i, f in enumerate(filas[:20]):
        for j, c in enumerate(f):
            if isinstance(c, str) and clave(c) == "HORA":
                i_cab, j_hora = i, j
                break
        if i_cab is not None:
            break
    if i_cab is None:
        raise ValueError("TIPO_RECURSO sin columna HORA")
    recursos = {}
    for j in range(j_hora + 1, len(filas[i_cab])):
        c = filas[i_cab][j]
        if not isinstance(c, str) or not c.strip():
            break  # despues del bloque viene otra tabla (centrales hidro)
        if clave(c) == "TOTAL":
            break
        recursos[j] = limpio(c)
    registros = []
    for f in filas[i_cab + 1:]:
        hm = _hm(f[j_hora]) if j_hora < len(f) else None
        if hm is None:
            continue
        ts = marca(dia, hm)
        for j, nombre in recursos.items():
            v = _num(f[j]) if j < len(f) else None
            registros.append((ts, nombre, v or 0.0))
    df = pd.DataFrame(registros, columns=["ts", "recurso", "mw"])
    return _validar_dia(df, dia, "TIPO_RECURSO")


def leer_despacho_unidades(libro, dia):
    """Energia (MWh) y potencia maxima (MW) del dia por unidad de generacion.

    DataFrame fecha, codigo, unidad, empresa, tipo, area, energia_mwh, pmax_mw. None si no esta la hoja.
    """
    ws = _hoja(libro, "DESPACHO_EJECUTADO")
    if ws is None:
        return None
    filas = list(ws.iter_rows(values_only=True))
    i_cab = next((i for i, f in enumerate(filas[:20])
                  if len(f) > 1 and isinstance(f[1], str) and clave(f[1]) == "HORA"), None)
    if i_cab is None:
        raise ValueError("DESPACHO_EJECUTADO sin cabecera HORA")
    cab = filas[i_cab:i_cab + 5]  # codigo, area, tipo, empresa, unidad
    unidades = {}
    for j in range(2, len(cab[0])):
        tipo = cab[2][j] if j < len(cab[2]) else None
        if not isinstance(tipo, str) or not tipo.strip():
            continue  # columnas de totales
        unidades[j] = {
            "codigo": str(cab[0][j]), "area": limpio(cab[1][j] or ""), "tipo": limpio(tipo),
            "empresa": limpio(cab[3][j] or ""), "unidad": limpio(cab[4][j] or ""),
        }
    suma = {j: 0.0 for j in unidades}
    pmax = {j: 0.0 for j in unidades}
    n = 0
    for f in filas[i_cab + 5:]:
        if _hm(f[1]) is None:
            continue
        n += 1
        for j in unidades:
            v = _num(f[j]) if j < len(f) else None
            if v:
                suma[j] += v
                pmax[j] = max(pmax[j], v)
    if n == 0:
        return None
    registros = [{"fecha": dia.isoformat(), **u, "energia_mwh": suma[j] * 0.5, "pmax_mw": pmax[j]}
                 for j, u in unidades.items()]
    return pd.DataFrame(registros)


def leer_anexo(contenido_xlsx, dia: date):
    """{'demanda': df, 'generacion': df, 'unidades': df} (cada uno puede ser None)."""
    libro = _libro(contenido_xlsx)
    try:
        return {
            "demanda": leer_demanda_areas(libro, dia),
            "generacion": leer_tipo_recurso(libro, dia),
            "unidades": leer_despacho_unidades(libro, dia),
        }
    finally:
        libro.close()


def _validar_dia(df, dia, que):
    """Descarta marcas fuera del dia operativo y avisa si faltan intervalos."""
    if df.empty:
        raise ValueError(f"{que}: hoja sin datos")
    inicio = pd.Timestamp(dia) + pd.Timedelta(minutes=30)
    fin = pd.Timestamp(dia) + pd.Timedelta(days=1)
    df = df[(df["ts"] >= inicio) & (df["ts"] <= fin)]
    if df["ts"].nunique() < 40:
        raise ValueError(f"{que}: solo {df['ts'].nunique()} intervalos de 48")
    return df.reset_index(drop=True)


def dia_operativo(ts):
    """Dia al que pertenece una marca de fin de intervalo."""
    return (pd.Timestamp(ts) - timedelta(minutes=1)).date()
