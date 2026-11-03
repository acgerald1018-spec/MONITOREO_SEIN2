"""Base SQLite local (data/sein.sqlite). Solo escribe el hilo principal.

fuente = 'IEOD' (post operacion, evaluado por el COES) o 'TR' (tiempo real, preliminar).
Cuando llega el IEOD de un dia, reemplaza lo preliminar.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

import pandas as pd

from monitor import config

ESQUEMA = """
create table if not exists cmg (
    ts text not null, barra text not null,
    total real, energia real, congestion real, fuente text not null,
    primary key (barra, ts)) without rowid;

create table if not exists demanda (
    ts text primary key, sein real, norte real, centro real, sur real,
    intercambio real, programada real, fuente text not null) without rowid;

create table if not exists generacion (
    ts text not null, recurso text not null, mw real, fuente text not null,
    primary key (ts, recurso)) without rowid;

create table if not exists unidades (
    fecha text not null, codigo text not null, unidad text, empresa text, tipo text, area text,
    energia_mwh real, pmax_mw real,
    primary key (fecha, codigo)) without rowid;

create table if not exists dias (
    fecha text primary key, cmg integer default 0, anexo integer default 0,
    actualizado text, detalle text) without rowid;

create table if not exists bitacora (
    id integer primary key autoincrement, momento text, nivel text, mensaje text);
"""

FMT = "%Y-%m-%d %H:%M"


@contextmanager
def conectar(ruta=None):
    ruta = ruta or config.RUTA_DB
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=60)
    con.execute("pragma journal_mode=wal")
    try:
        con.executescript(ESQUEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _ts(serie):
    return pd.to_datetime(serie).dt.strftime(FMT)


def guardar_cmg(con, df, fuente):
    if df is None or df.empty:
        return 0
    filas = list(zip(_ts(df["ts"]), df["barra"], df["total"], df["energia"], df["congestion"], [fuente] * len(df)))
    if fuente == "TR":  # lo preliminar nunca pisa al IEOD
        sql = """insert into cmg values (?,?,?,?,?,?) on conflict(barra, ts) do update set
                 total=excluded.total, energia=excluded.energia, congestion=excluded.congestion
                 where cmg.fuente = 'TR'"""
    else:
        sql = "insert or replace into cmg values (?,?,?,?,?,?)"
    con.executemany(sql, filas)
    return len(filas)


def guardar_demanda(con, df, fuente):
    if df is None or df.empty:
        return 0
    d = df.copy()
    for c in ("sein", "norte", "centro", "sur", "intercambio", "programada"):
        if c not in d:
            d[c] = None
    d = d.astype(object).where(d.notna(), None)
    filas = list(zip(_ts(df["ts"]), d["sein"], d["norte"], d["centro"], d["sur"], d["intercambio"],
                     d["programada"], [fuente] * len(d)))
    if fuente == "TR":
        sql = """insert into demanda values (?,?,?,?,?,?,?,?) on conflict(ts) do update set
                 sein=excluded.sein, programada=excluded.programada where demanda.fuente = 'TR'"""
    else:  # el IEOD conserva el programado que vino del tiempo real
        sql = """insert into demanda values (?,?,?,?,?,?,?,?) on conflict(ts) do update set
                 sein=excluded.sein, norte=excluded.norte, centro=excluded.centro, sur=excluded.sur,
                 intercambio=excluded.intercambio, fuente=excluded.fuente"""
    con.executemany(sql, filas)
    return len(filas)


def guardar_generacion(con, df, fuente="IEOD"):
    if df is None or df.empty:
        return 0
    filas = list(zip(_ts(df["ts"]), df["recurso"], df["mw"], [fuente] * len(df)))
    con.executemany("insert or replace into generacion values (?,?,?,?)", filas)
    return len(filas)


def guardar_unidades(con, df):
    if df is None or df.empty:
        return 0
    cols = ["fecha", "codigo", "unidad", "empresa", "tipo", "area", "energia_mwh", "pmax_mw"]
    con.executemany("insert or replace into unidades values (?,?,?,?,?,?,?,?)", df[cols].itertuples(index=False))
    return len(df)


def marcar_dia(con, fecha, cmg=None, anexo=None, detalle=""):
    previo = con.execute("select cmg, anexo from dias where fecha = ?", (fecha.isoformat(),)).fetchone()
    cmg_ok = cmg if cmg is not None else (previo[0] if previo else 0)
    anexo_ok = anexo if anexo is not None else (previo[1] if previo else 0)
    con.execute("insert or replace into dias values (?,?,?,?,?)",
                (fecha.isoformat(), int(cmg_ok), int(anexo_ok), datetime.now().strftime("%Y-%m-%d %H:%M"), detalle))


def dias_completos(con):
    return {f for (f,) in con.execute("select fecha from dias where cmg = 1 and anexo = 1")}


def ultimo_dia_ieod(con):
    fila = con.execute("select max(fecha) from dias where cmg = 1").fetchone()
    return fila[0] if fila and fila[0] else None


def anotar(con, nivel, mensaje):
    con.execute("insert into bitacora (momento, nivel, mensaje) values (?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), nivel, mensaje))
