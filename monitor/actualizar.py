"""Actualizacion incremental: baja solo los dias que faltan del IEOD y completa con tiempo real.

Uso desde consola:  python -m monitor.actualizar            (incremental)
                    python -m monitor.actualizar --desde 2025-01-01 --forzar
"""

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from monitor import config, db, ieod, tiempo_real

log = logging.getLogger("monitor")


def hoy_peru():
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def dias_pendientes(con, desde, hasta, forzar=False):
    todos = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    if forzar:
        return todos
    completos = db.dias_completos(con)
    provisionales = config.cargar()["descarga"]["dias_provisionales"]
    ultimo = db.ultimo_dia_ieod(con)
    limite = (date.fromisoformat(ultimo) - timedelta(days=provisionales - 1)) if ultimo else desde
    return [d for d in todos if d.isoformat() not in completos or d >= limite]


class EnCurso(Exception):
    """Ya hay otra actualizacion corriendo (boton de la app o tarea programada)."""


def actualizar(progreso=None, desde=None, hasta=None, forzar=False, con_tiempo_real=None):
    """Corre la actualizacion. progreso(fraccion 0-1, texto). Devuelve resumen en texto."""
    candado = config.DIR_DATA / "actualizando.lock"
    config.DIR_DATA.mkdir(parents=True, exist_ok=True)
    # el candado se renueva en cada paso; si nadie lo toca en 15 min, el proceso que lo tenia murio
    if candado.exists() and time.time() - candado.stat().st_mtime < 15 * 60:
        raise EnCurso("Ya hay una actualización en curso; espera a que termine.")
    candado.write_text(datetime.now().isoformat())
    original = progreso or (lambda f, t: print(f"[{f:5.0%}] {t}"))

    def progreso_con_latido(f, t):
        candado.touch()
        original(f, t)

    try:
        return _actualizar(progreso_con_latido, desde, hasta, forzar, con_tiempo_real)
    finally:
        candado.unlink(missing_ok=True)


def _actualizar(progreso, desde, hasta, forzar, con_tiempo_real):
    cfg = config.cargar()
    progreso = progreso or (lambda f, t: print(f"[{f:5.0%}] {t}"))
    desde = desde or cfg["fecha_inicio"]
    hoy = hoy_peru()
    hasta = hasta or (hoy - timedelta(days=1))  # el IEOD de hoy no existe aun
    con_tiempo_real = cfg["descarga"].get("tiempo_real", True) if con_tiempo_real is None else con_tiempo_real
    nav = ieod.Navegador()
    resumen = {"ok": 0, "parcial": 0, "pendiente": 0}

    with db.conectar() as con:
        pendientes = dias_pendientes(con, desde, hasta, forzar)
        db.anotar(con, "INFO", f"Inicio: {len(pendientes)} días por revisar ({desde} a {hasta})")
        con.commit()
        total = max(len(pendientes), 1)
        progreso(0.0, f"{len(pendientes)} días del IEOD por revisar")
        mes_actual = (hoy.year, hoy.month)
        mes_anterior = ((hoy.replace(day=1) - timedelta(days=1)).year, (hoy.replace(day=1) - timedelta(days=1)).month)

        with ThreadPoolExecutor(max_workers=cfg["descarga"]["hilos"]) as pool:
            futuros = {
                pool.submit(ieod.procesar_dia, d, nav, (d.year, d.month) in (mes_actual, mes_anterior),
                            cfg["descarga"]["guardar_crudos"]): d
                for d in pendientes
            }
            for i, fut in enumerate(as_completed(futuros), 1):
                d = futuros[fut]
                try:
                    r = fut.result()
                except Exception as e:  # error de red u otro: se reintenta en la proxima corrida
                    db.anotar(con, "ERROR", f"{d}: {e}")
                    resumen["pendiente"] += 1
                    progreso(i / total * (0.9 if con_tiempo_real else 1), f"{d:%d/%m/%Y}: error ({e})")
                    continue
                if r["ok_cmg"]:
                    db.guardar_cmg(con, _filtrar_barras(r["cmg"]), "IEOD")
                if r["anexo"]:
                    db.guardar_demanda(con, r["anexo"]["demanda"], "IEOD")
                    db.guardar_generacion(con, r["anexo"]["generacion"])
                    db.guardar_unidades(con, r["anexo"]["unidades"])
                db.marcar_dia(con, d, r["ok_cmg"], r["ok_anexo"], "; ".join(r["avisos"]))
                if r["avisos"] and not (r["ok_cmg"] and r["ok_anexo"]):
                    db.anotar(con, "AVISO", f"{d}: {'; '.join(r['avisos'])}")
                con.commit()
                clave = "ok" if r["ok_cmg"] and r["ok_anexo"] else ("parcial" if r["ok_cmg"] or r["ok_anexo"] else "pendiente")
                resumen[clave] += 1
                estado = "OK" if clave == "ok" else "; ".join(r["avisos"])
                progreso(i / total * (0.9 if con_tiempo_real else 1), f"IEOD {d:%d/%m/%Y}: {estado}")

        texto_tr = ""
        if con_tiempo_real:
            ultimo = db.ultimo_dia_ieod(con)
            inicio_tr = (date.fromisoformat(ultimo) + timedelta(days=1)) if ultimo else hoy - timedelta(days=1)
            inicio_tr = max(inicio_tr, hoy - timedelta(days=6))
            dias_tr = [inicio_tr + timedelta(days=i) for i in range((hoy - inicio_tr).days + 1)]
            texto_tr = tiempo_real.actualizar(con, dias_tr, lambda t: progreso(0.95, t))

        texto = (f"IEOD: {resumen['ok']} días completos, {resumen['parcial']} parciales, "
                 f"{resumen['pendiente']} aún no publicados o con error. {texto_tr}").strip()
        db.anotar(con, "INFO", "Fin: " + texto)
    progreso(1.0, texto)
    return texto


def _filtrar_barras(df):
    return df[df["barra"].isin(config.barras_a_guardar(df["barra"].unique()))]


def main(argv=None):
    p = argparse.ArgumentParser(description="Actualiza la base del Monitor SEIN con el IEOD del COES")
    p.add_argument("--desde", type=date.fromisoformat, help="AAAA-MM-DD (por defecto fecha_inicio del config)")
    p.add_argument("--hasta", type=date.fromisoformat, help="AAAA-MM-DD (por defecto ayer)")
    p.add_argument("--forzar", action="store_true", help="vuelve a bajar aunque ya este en la base")
    p.add_argument("--sin-tiempo-real", action="store_true")
    p.add_argument("--silencioso", action="store_true", help="sin consola: escribe en data/actualizacion.log")
    a = p.parse_args(argv)
    if a.silencioso:
        config.DIR_DATA.mkdir(parents=True, exist_ok=True)
        log_f = open(config.RUTA_LOG, "a", encoding="utf-8")
        progreso = lambda f, t: log_f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{f:4.0%}] {t}\n")  # noqa: E731
    else:
        progreso = None
    try:
        texto = actualizar(progreso=progreso, desde=a.desde, hasta=a.hasta, forzar=a.forzar,
                           con_tiempo_real=False if a.sin_tiempo_real else None)
        if not a.silencioso:
            print("\n" + texto)
    except EnCurso as e:
        print(e)


if __name__ == "__main__":
    main()
