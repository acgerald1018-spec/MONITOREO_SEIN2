"""Ubica y descarga los archivos diarios del IEOD en el visor del COES.

Ruta tipica: Post Operación/Reportes/IEOD/2026/10_Octubre/01/
    AnexoA_0110.xlsx      -> demanda por areas, generacion por recurso, despacho por unidad
    CMg20261001.zip       -> CMgCP0110.xlsx con el costo marginal de todas las barras

Los nombres de carpetas cambian a veces ('03_MARZO', 'Dia 01'), por eso primero se
lista la carpeta y se busca por patron; si el listado falla se prueba la ruta tipica.
"""

import re
import threading

from monitor import cliente, config, parsers
from monitor.cliente import NoPublicado


def _nombre(ruta):
    return ruta.rstrip("/").rsplit("/", 1)[-1]


class Navegador:
    """Lista carpetas del IEOD guardando en memoria las de año y mes (con candado entre hilos)."""

    def __init__(self):
        self._cache = {}
        self._candado = threading.Lock()

    def _listado(self, ruta, usar_cache=True):
        with self._candado:
            if usar_cache and ruta in self._cache:
                return self._cache[ruta]
        items = cliente.listar(ruta)
        with self._candado:
            self._cache[ruta] = items
        return items

    def carpeta_dia(self, dia, mes_reciente):
        anio = self._listado(f"{config.BASE_IEOD}{dia.year}/", usar_cache=not mes_reciente)
        mes = next((r for t, r in anio if t == "D" and re.match(rf"^{dia.month:02d}\D", _nombre(r))), None)
        if mes is None:
            raise NoPublicado(f"carpeta del mes {dia:%m/%Y}")
        dias = self._listado(mes, usar_cache=not mes_reciente)
        for t, r in dias:
            m = re.search(r"(\d{1,2})$", _nombre(r))
            if t == "D" and m and int(m.group(1)) == dia.day:
                return r
        raise NoPublicado(f"carpeta del dia {dia:%d/%m/%Y}")


def _ruta_tipica(dia):
    return f"{config.BASE_IEOD}{dia.year}/{dia.month:02d}_{config.MESES[dia.month - 1]}/{dia.day:02d}/"


def ubicar(dia, navegador, mes_reciente=False):
    """(ruta_anexo, ruta_zip_cmg) del dia; cualquiera puede ser None si no esta publicado."""
    try:
        carpeta = navegador.carpeta_dia(dia, mes_reciente)
        archivos = [r for t, r in cliente.listar(carpeta) if t == "F"]
    except NoPublicado:
        raise
    except Exception:
        # sin listado: se prueba la ruta tipica
        base = _ruta_tipica(dia)
        return f"{base}AnexoA_{dia:%d%m}.xlsx", f"{base}CMg{dia:%Y%m%d}.zip"
    nombres = {r: _nombre(r) for r in archivos}
    anexos = [r for r, n in nombres.items() if re.match(r"(?i)^anexo.*\.xlsx$", n)]
    anexo = next((r for r in anexos if re.match(r"(?i)^anexoa", nombres[r])), anexos[0] if anexos else None)
    cmg = next((r for r, n in nombres.items() if re.match(r"(?i)^cmg.*\.zip$", n)), None)
    return anexo, cmg


def procesar_dia(dia, navegador, mes_reciente=False, guardar_crudos=False):
    """Descarga y lee un dia. Devuelve dict con DataFrames, banderas y mensajes (no toca la base)."""
    res = {"dia": dia, "cmg": None, "anexo": None, "ok_cmg": False, "ok_anexo": False, "avisos": []}
    try:
        r_anexo, r_cmg = ubicar(dia, navegador, mes_reciente)
    except NoPublicado:
        res["avisos"].append("IEOD aún no publicado")
        return res

    if r_cmg:
        try:
            z = cliente.descargar(r_cmg)
            xlsx = parsers.excel_cmg_del_zip(z, dia)
            if guardar_crudos:
                _guardar(dia, f"CMgCP_{dia:%Y%m%d}.xlsx", xlsx)
            res["cmg"] = parsers.leer_cmg(xlsx, dia)
            res["ok_cmg"] = True
        except NoPublicado:
            res["avisos"].append("CMg aún no publicado")
        except Exception as e:
            res["avisos"].append(f"CMg: {e}")
    else:
        res["avisos"].append("sin zip de CMg en la carpeta")

    if r_anexo:
        try:
            x = cliente.descargar(r_anexo)
            if guardar_crudos:
                _guardar(dia, f"AnexoA_{dia:%Y%m%d}.xlsx", x)
            res["anexo"] = parsers.leer_anexo(x, dia)
            res["ok_anexo"] = res["anexo"]["demanda"] is not None and res["anexo"]["generacion"] is not None
            if not res["ok_anexo"]:
                res["avisos"].append("Anexo sin DEMANDA_AREAS o TIPO_RECURSO")
        except NoPublicado:
            res["avisos"].append("Anexo A aún no publicado")
        except Exception as e:
            res["avisos"].append(f"Anexo A: {e}")
    else:
        res["avisos"].append("sin Anexo A en la carpeta")
    return res


def _guardar(dia, nombre, contenido):
    carpeta = config.DIR_CRUDOS / f"{dia:%Y}" / f"{dia:%m}"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre).write_bytes(contenido)
