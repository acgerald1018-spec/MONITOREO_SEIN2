"""Cliente HTTP para el portal del COES: pausas, reintentos, proxy y certificados corporativos.

Cada hilo de descarga usa su propia sesion (requests.Session no es seguro entre hilos).
"""

import html
import re
import threading
import time

import requests

from monitor import config

_local = threading.local()
_truststore_listo = False


class NoPublicado(Exception):
    """El COES aun no publica el archivo o la carpeta pedida."""


def _activar_certificados_sistema():
    """En redes con inspeccion SSL (comun en empresas) usa los certificados de Windows."""
    global _truststore_listo
    if _truststore_listo:
        return
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass
    _truststore_listo = True


def _sesion():
    if getattr(_local, "sesion", None) is None:
        red = config.cargar()["red"]
        if red.get("certificados_sistema", True):
            _activar_certificados_sistema()
        s = requests.Session()
        s.headers["User-Agent"] = red.get("user_agent", "MonitorSEIN/1.0")
        if red.get("proxy"):
            s.proxies.update({"http": red["proxy"], "https": red["proxy"]})
        _local.sesion = s
        _local.ultimo = 0.0
    return _local.sesion


def pedir(metodo, ruta, datos=None, params=None):
    """Bytes de la respuesta. Lanza RuntimeError si todos los intentos fallan."""
    cfg = config.cargar()["descarga"]
    url = ruta if ruta.startswith("http") else config.PORTAL + ruta
    sesion = _sesion()
    error = None
    for intento in range(1, cfg["reintentos"] + 1):
        espera = cfg["pausa_segundos"] - (time.monotonic() - _local.ultimo)
        if espera > 0:
            time.sleep(espera)
        try:
            if metodo == "POST":
                r = sesion.post(url, data=datos, params=params, timeout=cfg["timeout_segundos"])
            else:
                r = sesion.get(url, params=params, timeout=cfg["timeout_segundos"])
            if r.status_code == 404:
                raise NoPublicado(url)
            r.raise_for_status()
            return r.content
        except NoPublicado:
            raise
        except requests.RequestException as e:
            error = e
            if intento < cfg["reintentos"]:
                time.sleep(3 * intento)
        finally:
            _local.ultimo = time.monotonic()
    raise RuntimeError(f"No se pudo conectar con el COES ({url}): {error}")


# ---------------------------------------------------------------------------
# Visor de archivos del COES (donde vive el IEOD)
# ---------------------------------------------------------------------------

def listar(ruta):
    """[(tipo, ruta)] de una carpeta del visor. tipo: 'D' carpeta, 'F' archivo."""
    crudo = pedir("POST", "/browser/vistadatos",
                  datos={"baseDirectory": config.BASE_IEOD, "url": ruta, "indicador": "S"})
    texto = crudo.decode("utf-8", "replace")
    vistos = {}
    for r, tipo in re.findall(r"openBlob\('([^']+)',\s*'(\w)'", texto):
        vistos.setdefault(html.unescape(r), tipo)
    return [(t, r) for r, t in vistos.items()]


def descargar(ruta):
    """Bytes de un archivo del visor. NoPublicado si llega vacio o una pagina HTML."""
    contenido = pedir("GET", "/browser/download", params={"url": ruta})
    if not contenido or not contenido.startswith(b"PK"):
        raise NoPublicado(ruta)
    return contenido
