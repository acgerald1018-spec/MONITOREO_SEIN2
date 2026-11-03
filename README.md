# Monitor SEIN

Cuadro de control en Python (Streamlit) que descarga el **IEOD del COES** y arma:

- **Costos marginales cada 30 min** en Chavarría 220, Arequipa 220 (Socabaya), Piura Oeste 220,
  Zorritos 220, Ica 220 y Tacna 220 (Los Héroes), con Santa Rosa 220 como referencia.
- **Demanda cada 30 min** del SEIN y por área (Norte, Centro, Sur).
- **Histórico desde enero 2025** de producción por tecnología por mes, participación,
  potencia máxima despachada y unidades que despacharon.
- Botón **🔄 Actualizar datos**: baja solo los días que faltan.

## Instalación (Windows, una sola vez)

1. Instala Python 3.11 o más nuevo desde python.org (marca *Add python.exe to PATH*).
2. Descomprime la carpeta `monitor_sein` donde quieras (por ejemplo `PROYECTOS_G\monitor_sein`).
3. Doble clic en **`Iniciar.bat`**. La primera vez crea el entorno e instala librerías;
   luego abre el tablero en el navegador (`http://localhost:8501`).

## Primera carga del histórico

Desde enero 2025 son ~670 días; cada día son 2 archivos del COES (~6.5 MB). Tarda
aproximadamente **30 a 60 minutos** la primera vez. Recomendado: doble clic en
**`Actualizar.bat`** y dejarlo corriendo. Se puede cortar y retomar: lo ya bajado no se repite.

Después, el botón del tablero tarda segundos (solo baja lo nuevo y vuelve a revisar los
últimos 3 días, que el COES puede corregir).

Opcional: **`Programar_actualizacion.bat`** crea una tarea de Windows que actualiza cada
30 minutos aunque el tablero esté cerrado.

## Compartir el tablero

**Misma red (oficina o VPN):** doble clic en `Compartir_en_red.bat`. La ventana muestra el link
`http://<tu IP>:8501` para tus compañeros. Tu PC debe estar encendida con esa ventana abierta.

**Por internet (Streamlit Community Cloud, gratis):**

1. Corre `Actualizar.bat` hasta tener el histórico y luego `Publicar.bat`. Esto crea la carpeta
   `publicado` (~4 MB), una copia de la base para que la nube arranque sin bajar todo de nuevo.
2. Sube la carpeta `monitor_sein` a un repositorio **privado** de GitHub (con GitHub Desktop:
   *File → Add local repository → Publish repository*, con *Keep this code private* marcado).
3. En share.streamlit.io: *Create app → Deploy a public app from GitHub*, elige el repositorio,
   rama `main`, archivo `app.py`. En *Advanced settings* elige Python 3.12 y en *Secrets* escribe
   `clave_actualizar = "tu-clave"`.
4. Con el botón *Share* de la app invita por correo a quien quieras que la vea.

En la nube la app se actualiza sola cada 30 min mientras está despierta (se duerme tras 12 h sin
visitas; al entrar alguien se reactiva). Corre `Publicar.bat` cada cierto tiempo (por ejemplo,
cada semana) para que, si se reinicia, arranque desde una copia reciente.

Quién ve el botón **Actualizar datos**: tú, siempre, en tu PC; los demás, solo si escriben
`clave_actualizar` en la sección *🔒 Administrador*. Sin clave, los visitantes solo miran.

## Qué viene de dónde

| Dato | Fuente COES | Archivo / hoja | Retraso |
|---|---|---|---|
| CMg total, energía y congestión | IEOD (post operación) | `CMgAAAAMMDD.zip` → `CMgCPDDMM.xlsx`, hojas Cmg_Barra/Ener/Cong | 1–2 días |
| Demanda SEIN y por áreas | IEOD | `AnexoA_DDMM.xlsx`, hoja DEMANDA_AREAS | 1–2 días |
| Generación por tipo de recurso | IEOD | Anexo A, hoja TIPO_RECURSO | 1–2 días |
| Despacho por unidad | IEOD | Anexo A, hoja DESPACHO_EJECUTADO | 1–2 días |
| CMg y demanda de hoy/ayer | Portal en tiempo real | `costosmarginales/lista`, `portalinformacion/Demanda` | ~30 min |

Lo de tiempo real se marca como **preliminar** (franja amarilla en las gráficas) y se
reemplaza solo cuando el COES publica el IEOD de ese día.

Convenciones: la hora es el **fin** del intervalo de 30 min (00:30 = 00:00–00:30); el
último intervalo del día (24:00) se guarda como 00:00 del día siguiente pero cuenta para
el día anterior. CMg en S/./MWh como lo publica el COES; el tablero puede mostrarlo en
US$/MWh con el tipo de cambio que tú ingreses.

## Configuración (`config.yaml`)

- `barras`: nombre visible → nombre exacto en el IEOD. En el IEOD **no existe "AREQUIPA 220"
  ni "TACNA 220"**: se usan SOCABAYA 220 y LOS HEROES 220. Cámbialo si prefieres otra barra
  (por ejemplo SAN JOSE 220 para Arequipa).
- `guardar_barras: "220"` guarda todas las barras de 220 kV, así puedes agregar otras al
  tablero sin volver a descargar.
- `tecnologias`: cómo se agrupan las columnas de TIPO_RECURSO.
- `punta_desde` / `punta_hasta`: bloque de punta para los promedios.
- `red.proxy`: si la red de la empresa pide proxy, ponlo aquí.

## Si algo falla

- **Error de certificado SSL** en la red de la empresa: deja `certificados_sistema: true`
  (usa los certificados de Windows). Si persiste, pide a TI el proxy y ponlo en `red.proxy`.
- **"IEOD aún no publicado"**: normal para ayer/hoy; se reintenta en la siguiente actualización.
- La pestaña **Datos y estado** muestra la cobertura por mes, los días con faltantes y la bitácora.
- Base de datos: `data/sein.sqlite`. Borrarla obliga a descargar todo de nuevo.

## Estructura

```
app.py                  tablero Streamlit
monitor/publicar.py     copia liviana para la nube (Publicar.bat)
actualizar.py           actualización por consola / tarea programada
config.yaml             barras, tecnologías y red
monitor/cliente.py      HTTP al COES (pausas, reintentos, proxy, certificados)
monitor/ieod.py         ubica y descarga los archivos diarios del IEOD
monitor/parsers.py      lectura de los Excel (por contenido, no por fila fija)
monitor/tiempo_real.py  CMg y demanda preliminares
monitor/db.py           base SQLite
monitor/consultas.py    consultas del tablero
```

Datos: COES-SINAC, información pública. Los valores del IEOD pueden revisarse al cierre de mes
(costos marginales revisados).
