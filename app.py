"""Monitor SEIN - cuadro de control con datos del IEOD del COES.

Abrir con:  streamlit run app.py      (o doble clic en Iniciar.bat)
"""

import io
import threading
import time
from datetime import timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from monitor import config, consultas as q, publicar
from monitor.actualizar import EnCurso, actualizar, hoy_peru

st.set_page_config(page_title="Monitor SEIN", page_icon="⚡", layout="wide")

# ----------------------------------------------------------------------------- estilo
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRIS_REF = "#8a8985"
TECNOLOGIA_COLOR = {
    "Hidro regulación": "#1c5cab", "Hidro pasada": "#6da7ec",
    "Gas natural Camisea": "#eb6834", "Gas natural Norte (Malacas/Aguaytía)": "#f2a57f",
    "Solar": "#eda100", "Eólica": "#1baf7a", "Biomasa (bagazo/biogás)": "#008300",
    "Diésel y residual": "#e34948", "Carbón": "#52514e",
    "Otros térmicos (nafta/flexigas/refinería)": "#6250d6", "Otros": "#b4b2aa",
}
ORDEN_TEC = list(TECNOLOGIA_COLOR)
AREA_COLOR = {"Norte": "#2a78d6", "Centro": "#eb6834", "Sur": "#1baf7a"}

st.markdown("""
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
  [data-testid="stMetricValue"] {font-size: 1.55rem; font-variant-numeric: tabular-nums;}
  [data-testid="stMetricLabel"] p {font-size: .82rem; color: #52514e;}
  .nota {color:#6b6a66; font-size:.82rem; margin-top:-.4rem;}
  .chip {display:inline-block; padding:1px 8px; border-radius:10px; font-size:.75rem;
         background:#fdf1d8; color:#7a5200; border:1px solid #f3d79a;}
</style>""", unsafe_allow_html=True)


MESES_CORTOS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Set", "Oct", "Nov", "Dic"]
FORMATOS_FECHA = [  # ejes de fecha en formato peruano
    dict(dtickrange=[None, 86400000], value="%H:%M<br>%d/%m"),
    dict(dtickrange=[86400000, 2592000000], value="%d/%m<br>%Y"),
    dict(dtickrange=[2592000000, None], value="%m/%Y"),
]


def mes_txt(m):
    """'2025-01' -> 'Ene-25'."""
    a, mm = str(m).split("-")[:2]
    return f"{MESES_CORTOS[int(mm) - 1]}-{a[2:]}"


def estilo(fig, alto=380, unidad=None, leyenda=True, meses=False):
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=36, b=10), template="plotly_white",
        font=dict(family="Inter, Segoe UI, sans-serif", size=12, color="#2b2a28"),
        hovermode="x unified", showlegend=leyenda, title=dict(font=dict(size=14), y=0.98),
        legend=dict(orientation="h", yanchor="top", y=-0.12 if meses else -0.25, x=0, title=None,
                    font=dict(size=11)),
    )
    fig.update_xaxes(showgrid=False, linecolor="#d6d5d0")
    if meses:  # orden cronologico aunque una serie no tenga todos los meses
        vistos = {x for tr in fig.data for x in (tr.x if tr.x is not None else [])}
        orden = sorted(vistos, key=lambda m: (int(m[-2:]), MESES_CORTOS.index(m[:3])))
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=orden)
    else:
        fig.update_xaxes(tickformatstops=FORMATOS_FECHA)
    fig.update_yaxes(gridcolor="#ecebe8", zeroline=False, title=unidad)
    return fig


# ----------------------------------------------------------------------------- datos (cache)
def _version_db():
    return config.RUTA_DB.stat().st_mtime if config.RUTA_DB.exists() else 0


@st.cache_data(show_spinner=False)
def cargar(nombre, version, *args):
    return getattr(q, nombre)(*args)


def datos(nombre, *args):
    return cargar(nombre, _version_db(), *args)


def a_excel(hojas: dict):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre[:31], index=False)
    return buf.getvalue()


cfg = config.cargar()
BARRAS = config.barras_monitoreadas()          # visible -> COES
VISIBLE = {v: k for k, v in BARRAS.items()}    # COES -> visible
REF = cfg["barra_referencia"]
COMPARTIR = cfg.get("compartir", {})


# ----------------------------------------------------------------------------- arranque (una vez por servidor)
@st.cache_resource(show_spinner="Preparando la base…")
def preparar_base():
    """Si existe la copia publicada (Publicar.bat) y la base local falta o esta atrasada, la carga."""
    try:
        return publicar.sembrar()
    except Exception as e:  # una copia dañada no debe impedir abrir el tablero
        return f"No se pudo cargar la copia publicada: {e}"


@st.cache_resource
def iniciar_auto_actualizacion(minutos):
    """Hilo que actualiza la base cada N minutos mientras el servidor del tablero este encendido."""
    if not minutos:
        return None

    def ciclo():
        time.sleep(20)
        while True:
            try:
                actualizar(progreso=lambda f, t: None)
            except EnCurso:
                pass
            except Exception:  # sin internet o COES caido: se intenta en el siguiente ciclo
                pass
            time.sleep(minutos * 60)

    hilo = threading.Thread(target=ciclo, name="auto-actualizacion", daemon=True)
    hilo.start()
    return hilo


def _clave_configurada():
    try:
        clave = st.secrets.get("clave_actualizar", "")
    except Exception:  # sin archivo de secrets
        clave = ""
    return str(clave or COMPARTIR.get("clave_actualizar", "") or "")


def puede_actualizar():
    """En tu propia PC siempre; desde otra PC o la nube solo con la clave."""
    if st.context.ip_address is None:  # localhost
        return True
    if st.session_state.get("admin_ok"):
        return True
    clave = _clave_configurada()
    if not clave:
        return False
    with st.expander("🔒 Administrador"):
        escrita = st.text_input("Clave para actualizar", type="password")
        if escrita and escrita == clave:
            st.session_state["admin_ok"] = True
            st.rerun()
        elif escrita:
            st.caption("Clave incorrecta.")
    return False


aviso_semilla = preparar_base()
iniciar_auto_actualizacion(int(COMPARTIR.get("auto_actualizar_minutos", 0) or 0))

# ----------------------------------------------------------------------------- barra lateral
with st.sidebar:
    st.markdown("## ⚡ Monitor SEIN")
    st.caption("Datos públicos del COES · IEOD y tiempo real")

    if puede_actualizar():
        if st.button("🔄 Actualizar datos", type="primary", width="stretch"):
            barra = st.progress(0.0, text="Conectando con el COES…")
            try:
                resumen = actualizar(progreso=lambda f, t: barra.progress(min(f, 1.0), text=t))
                st.cache_data.clear()
                st.success(resumen)
            except EnCurso:
                barra.empty()
                st.info("Ya hay una actualización en curso (automática o de otro usuario). "
                        "Recarga la página en unos minutos.")
            except Exception as e:
                st.error(f"No se pudo actualizar: {e}")
    if (config.DIR_DATA / "actualizando.lock").exists():
        st.caption("⏳ Actualizando datos en segundo plano…")
    elif COMPARTIR.get("auto_actualizar_minutos"):
        ua = q.ultima_actualizacion()
        st.caption(f"Se actualiza sola cada {COMPARTIR['auto_actualizar_minutos']} min"
                   + (f" · última: {ua['momento'][:16]}" if ua else ""))

    primero, ultimo = datos("rango")
    if not q.hay_datos():
        st.info("La base está vacía. La primera carga desde "
                f"{cfg['fecha_inicio']:%d/%m/%Y} tarda entre 30 y 60 min: corre **Actualizar.bat** "
                "o pulsa **Actualizar datos**. Si la actualización automática está activa, ya empezó: "
                "recarga la página más tarde.")
        st.stop()

    st.caption(f"Datos del {primero:%d/%m/%Y} al {ultimo:%d/%m/%Y}")
    st.divider()

    atajos = {"Últimas 48 h": 1, "Últimos 7 días": 6, "Últimos 30 días": 29, "Mes actual": None,
              "Desde inicio": "todo", "Personalizado": "pers"}
    eleccion = st.selectbox("Periodo para gráficas de 30 min", list(atajos), index=1)
    if atajos[eleccion] == "pers":
        r = st.date_input("Rango", (max(primero, ultimo - timedelta(days=6)), ultimo),
                          min_value=primero, max_value=ultimo, format="DD/MM/YYYY")
        desde, hasta = (r if isinstance(r, tuple) and len(r) == 2 else (r[0], r[0]))
    elif atajos[eleccion] == "todo":
        desde, hasta = primero, ultimo
    elif atajos[eleccion] is None:
        desde, hasta = ultimo.replace(day=1), ultimo
    else:
        desde, hasta = max(primero, ultimo - timedelta(days=atajos[eleccion])), ultimo

    disponibles = q.barras_disponibles()
    nombres_coes = list(BARRAS.values()) + [b for b in disponibles if b not in BARRAS.values()]
    elegidas = st.multiselect("Barras", [b for b in nombres_coes if b in disponibles or b in BARRAS.values()],
                              default=[b for b in BARRAS.values()],
                              format_func=lambda b: VISIBLE.get(b, b.title()))
    ver_ref = st.checkbox(f"Mostrar referencia {REF.title()}", value=True)
    st.divider()
    moneda = st.radio("Moneda", ["S/ por MWh", "US$ por MWh"], horizontal=True)
    tc = 1.0
    if moneda.startswith("US$"):
        tc = st.number_input("Tipo de cambio S/ por US$ (ingrésalo tú)", min_value=1.0, max_value=10.0,
                             value=3.75, step=0.01, format="%.3f")
    unidad = "S/./MWh" if tc == 1.0 else "US$/MWh"

if not elegidas:
    st.warning("Elige al menos una barra.")
    st.stop()

colores = {b: SERIES[i % len(SERIES)] for i, b in enumerate(nombres_coes)}
colores[REF] = GRIS_REF
lista_barras = elegidas + ([REF] if ver_ref and REF not in elegidas else [])
etq = lambda b: VISIBLE.get(b, b.title())  # noqa: E731

# ----------------------------------------------------------------------------- cabecera
c1, c2 = st.columns([3, 1])
c1.markdown("### Cuadro de control del SEIN")
c1.markdown(f"<div class='nota'>Periodo: {desde:%d/%m/%Y} – {hasta:%d/%m/%Y} · "
            f"CMg en {unidad} · marcas al fin de cada intervalo de 30 min</div>", unsafe_allow_html=True)
c2.markdown(f"<div style='text-align:right;color:#6b6a66;font-size:.8rem'>Hoy {hoy_peru():%d/%m/%Y}<br>"
            f"Último dato: {ultimo:%d/%m/%Y}</div>", unsafe_allow_html=True)

tab_res, tab_cmg, tab_dem, tab_gen, tab_dat = st.tabs(
    ["📊 Resumen", "💲 Costos marginales", "📈 Demanda", "🏭 Parque generador", "🗂️ Datos y estado"])

cm = datos("cmg", tuple(lista_barras), desde, hasta)
if not cm.empty:
    for c in ("total", "energia", "congestion"):
        cm[c] = cm[c] / tc
dm = datos("demanda", desde, hasta)
gen = datos("generacion", desde, hasta)

# ============================================================================= RESUMEN
with tab_res:
    ult_cm = datos("cmg", tuple(lista_barras), ultimo - timedelta(days=1), ultimo)
    if ult_cm.empty:
        st.info("Sin costos marginales recientes.")
    else:
        ult_cm["total"] = ult_cm["total"] / tc
        ultima_marca = ult_cm["ts"].max()
        prelim = (ult_cm.loc[ult_cm["ts"] == ultima_marca, "fuente"] == "TR").any()
        st.markdown(f"**Último costo marginal: {ultima_marca:%d/%m/%Y %H:%M}** "
                    + ("<span class='chip'>preliminar · tiempo real</span>" if prelim else ""),
                    unsafe_allow_html=True)
        dia_ult = (ultima_marca - pd.Timedelta(minutes=1)).normalize()
        cols = st.columns(len(elegidas))
        for col, b in zip(cols, elegidas):
            serie = ult_cm[ult_cm["barra"] == b].set_index("ts")["total"]
            if serie.empty:
                col.metric(etq(b), "s/d")
                continue
            hoy_s = serie[(serie.index - pd.Timedelta(minutes=1)).normalize() == dia_ult]
            previo = serie[serie.index <= ultima_marca - pd.Timedelta(hours=24)]
            delta = (serie.iloc[-1] - previo.iloc[-1]) if not previo.empty else None
            col.metric(etq(b), f"{serie.iloc[-1]:,.1f}",
                       None if delta is None else f"{delta:+,.1f} vs 24 h antes", delta_color="inverse",
                       help=f"Promedio del día: {hoy_s.mean():,.1f} {unidad}")

    st.write("")
    d2 = datos("demanda", ultimo - timedelta(days=1), ultimo)
    g_dia = datos("generacion", ultimo, ultimo)
    k = st.columns(5)
    if not d2.empty:
        fila_max = d2.loc[d2["sein"].idxmax()]
        k[0].metric("Demanda actual SEIN", f"{d2['sein'].dropna().iloc[-1]:,.0f} MW",
                    help=f"{d2['ts'].max():%d/%m %H:%M}")
        k[1].metric("Máxima 48 h", f"{fila_max['sein']:,.0f} MW", help=f"{fila_max['ts']:%d/%m %H:%M}")
    if not g_dia.empty:
        tot = g_dia["mw"].sum()
        hidro = g_dia[g_dia["tecnologia"].str.startswith("Hidro")]["mw"].sum()
        rer = g_dia[g_dia["tecnologia"].isin(cfg["rer_no_convencional"])]["mw"].sum()
        gas = g_dia[g_dia["tecnologia"].str.startswith("Gas")]["mw"].sum()
        k[2].metric(f"Energía generada {ultimo:%d/%m}", f"{tot * 0.5 / 1000:,.1f} GWh")
        k[3].metric("Hidro / Gas", f"{hidro / tot:.0%} / {gas / tot:.0%}")
        k[4].metric("RER no convencional", f"{rer / tot:.1%}", help="Solar + eólica + biomasa")
    else:
        k[2].metric("Generación", "IEOD pendiente")

    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        for b in lista_barras:
            s = ult_cm[ult_cm["barra"] == b] if not ult_cm.empty else pd.DataFrame()
            if s.empty:
                continue
            fig.add_scatter(x=s["ts"], y=s["total"], name=etq(b), mode="lines",
                            line=dict(color=colores[b], width=2, dash="dot" if b == REF else "solid"))
        st.plotly_chart(estilo(fig, 340, unidad).update_layout(title="Costo marginal · últimas 48 h"),
                        width="stretch")
    with c2:
        if not d2.empty:
            fig = go.Figure()
            fig.add_scatter(x=d2["ts"], y=d2["sein"], name="Ejecutada", line=dict(color=SERIES[0], width=2))
            if d2["programada"].notna().any():
                fig.add_scatter(x=d2["ts"], y=d2["programada"], name="Programada (diaria)",
                                line=dict(color=GRIS_REF, width=1.5, dash="dot"))
            st.plotly_chart(estilo(fig, 340, "MW").update_layout(title="Demanda SEIN · últimas 48 h"),
                            width="stretch")
    st.caption("Valores marcados *preliminar* vienen del costo marginal y la demanda en tiempo real del COES; "
               "se reemplazan solos por los del IEOD (post operación) cuando el COES lo publica (1 a 2 días).")

# ============================================================================= COSTOS MARGINALES
with tab_cmg:
    if cm.empty:
        st.info("Sin datos de costo marginal en el periodo.")
    else:
        componente = st.radio("Componente", ["Total", "Energía", "Congestión"], horizontal=True)
        col = {"Total": "total", "Energía": "energia", "Congestión": "congestion"}[componente]

        fig = go.Figure()
        for b in lista_barras:
            s = cm[cm["barra"] == b]
            fig.add_scatter(x=s["ts"], y=s[col], name=etq(b), mode="lines",
                            line=dict(color=colores[b], width=1.8, dash="dot" if b == REF else "solid"),
                            hovertemplate="%{y:,.1f}")
        tr = cm[cm["fuente"] == "TR"]
        if not tr.empty:
            fig.add_vrect(x0=tr["ts"].min() - pd.Timedelta(minutes=30), x1=tr["ts"].max(),
                          fillcolor="#fdf1d8", opacity=0.5, line_width=0,
                          annotation_text="preliminar (tiempo real)", annotation_position="top left",
                          annotation_font_size=10)
        st.plotly_chart(estilo(fig, 430, unidad).update_layout(title=f"CMg {componente.lower()} cada 30 min"),
                        width="stretch")

        # tabla resumen del periodo
        punta = cm["ts"].dt.strftime("%H:%M").between(cfg["punta_desde"], cfg["punta_hasta"])
        res = cm.assign(punta=punta).groupby("barra").agg(
            Promedio=("total", "mean"), Máximo=("total", "max"), Mínimo=("total", "min"),
            Desv_est=("total", "std"), Congestión=("congestion", "mean"))
        res["Punta"] = cm[punta].groupby("barra")["total"].mean()
        res["Fuera de punta"] = cm[~punta].groupby("barra")["total"].mean()
        if REF in cm["barra"].unique():
            ref_s = cm[cm["barra"] == REF].set_index("ts")["total"]
            res[f"Δ vs {REF.title()}"] = [
                (cm[cm["barra"] == b].set_index("ts")["total"] - ref_s).mean() for b in res.index]
        res.index = [etq(b) for b in res.index]
        res = res.rename(columns={"Desv_est": "Desv. est."}).reindex(
            [etq(b) for b in lista_barras if etq(b) in res.index])
        st.markdown(f"**Resumen del periodo ({unidad})** · punta {cfg['punta_desde']}–{cfg['punta_hasta']}")
        st.dataframe(res.style.format("{:,.1f}"), width="stretch")

        c1, c2 = st.columns(2)
        with c1:
            perfil = cm.assign(hora=cm["ts"].dt.strftime("%H:%M").replace("00:00", "24:00")) \
                       .groupby(["hora", "barra"], as_index=False)["total"].mean().sort_values("hora")
            fig = go.Figure()
            for b in lista_barras:
                s = perfil[perfil["barra"] == b]
                fig.add_scatter(x=s["hora"], y=s["total"], name=etq(b), mode="lines",
                                line=dict(color=colores[b], width=2, dash="dot" if b == REF else "solid"))
            st.plotly_chart(estilo(fig, 360, unidad).update_layout(title="Perfil horario promedio del periodo"),
                            width="stretch")
        with c2:
            fig = go.Figure()
            for b in lista_barras:
                s = cm[cm["barra"] == b]
                fig.add_box(y=s["total"], name=etq(b), marker_color=colores[b], boxmean=True, line_width=1.2)
            st.plotly_chart(estilo(fig, 360, unidad, leyenda=False).update_layout(
                title="Distribución en el periodo", hovermode="closest"), width="stretch")

        # mapa de calor
        bh = st.selectbox("Mapa de calor día × hora", elegidas, format_func=etq)
        s = cm[cm["barra"] == bh].copy()
        s["dia"] = (s["ts"] - pd.Timedelta(minutes=1)).dt.date
        s["hora"] = s["ts"].dt.strftime("%H:%M").replace("00:00", "24:00")
        piv = s.pivot_table(index="dia", columns="hora", values="total").sort_index(ascending=False)
        fig = px.imshow(piv, aspect="auto", color_continuous_scale=["#eef5fd", "#86b6ef", "#2a78d6", "#104281"],
                        labels=dict(color=unidad, x="Hora", y="Día"))
        st.plotly_chart(estilo(fig, max(260, 18 * len(piv) + 120), leyenda=False).update_layout(
            title=f"{etq(bh)} · CMg por media hora", hovermode="closest"), width="stretch")

    st.markdown("#### Histórico desde " + f"{cfg['fecha_inicio']:%B %Y}".capitalize())
    diario = datos("cmg_diario", tuple(lista_barras))
    mensual = datos("cmg_mensual", tuple(lista_barras))
    if not mensual.empty:
        mensual["mes"] = mensual["mes"].map(mes_txt)
    if not diario.empty:
        for c in ("prom", "maximo", "minimo"):
            diario[c] = diario[c] / tc
        fig = go.Figure()
        for b in lista_barras:
            s = diario[diario["barra"] == b]
            fig.add_scatter(x=s["dia"], y=s["prom"], name=etq(b), mode="lines",
                            line=dict(color=colores[b], width=1.5, dash="dot" if b == REF else "solid"))
        st.plotly_chart(estilo(fig, 380, unidad).update_layout(title="CMg promedio diario"),
                        width="stretch")
    if not mensual.empty:
        for c in ("prom", "punta", "fuera_punta", "maximo", "minimo"):
            mensual[c] = mensual[c] / tc
        bloque = st.radio("Promedio mensual", ["Todo el día", "Punta", "Fuera de punta"], horizontal=True)
        cc = {"Todo el día": "prom", "Punta": "punta", "Fuera de punta": "fuera_punta"}[bloque]
        fig = go.Figure()
        for b in lista_barras:
            s = mensual[mensual["barra"] == b]
            fig.add_bar(x=s["mes"], y=s[cc], name=etq(b), marker_color=colores[b], hovertemplate="%{y:,.1f}")
        fig.update_layout(bargap=0.25, bargroupgap=0.08)
        st.plotly_chart(estilo(fig, 400, unidad, meses=True).update_layout(title=f"CMg promedio mensual · {bloque.lower()}"),
                        width="stretch")
        tabla = mensual.pivot_table(index="mes", columns="barra", values=cc, sort=False).rename(columns=etq)
        st.dataframe(tabla.style.format("{:,.1f}"), width="stretch")

# ============================================================================= DEMANDA
with tab_dem:
    if dm.empty:
        st.info("Sin datos de demanda en el periodo.")
    else:
        fila = dm.loc[dm["sein"].idxmax()]
        k = st.columns(4)
        k[0].metric("Máxima del periodo", f"{fila['sein']:,.0f} MW", help=f"{fila['ts']:%d/%m/%Y %H:%M}")
        k[1].metric("Mínima del periodo", f"{dm['sein'].min():,.0f} MW")
        k[2].metric("Promedio", f"{dm['sein'].mean():,.0f} MW")
        k[3].metric("Energía", f"{dm['sein'].sum() * 0.5 / 1000:,.1f} GWh")

        fig = go.Figure()
        fig.add_scatter(x=dm["ts"], y=dm["sein"], name="SEIN ejecutada", line=dict(color=SERIES[0], width=2))
        if dm["programada"].notna().any():
            fig.add_scatter(x=dm["ts"], y=dm["programada"], name="Programada (tiempo real)",
                            line=dict(color=GRIS_REF, width=1.5, dash="dot"))
        st.plotly_chart(estilo(fig, 380, "MW").update_layout(title="Demanda del SEIN cada 30 min"),
                        width="stretch")

        if dm["norte"].notna().any():
            fig = go.Figure()
            for area in ("Sur", "Centro", "Norte"):
                fig.add_scatter(x=dm["ts"], y=dm[area.lower()], name=area, stackgroup="a", mode="lines",
                                line=dict(width=0.5, color=AREA_COLOR[area]), fillcolor=AREA_COLOR[area])
            st.plotly_chart(estilo(fig, 360, "MW").update_layout(title="Demanda por área operativa (IEOD)"),
                            width="stretch")

    st.markdown("#### Histórico mensual")
    dmm = datos("demanda_mensual")
    if not dmm.empty:
        dmm["mes"] = dmm["mes"].map(mes_txt)
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure()
            fig.add_bar(x=dmm["mes"], y=dmm["max_mw"], name="Máxima", marker_color=SERIES[0])
            fig.add_scatter(x=dmm["mes"], y=dmm["prom_mw"], name="Promedio", mode="lines+markers",
                            line=dict(color=SERIES[1], width=2), marker=dict(size=7))
            st.plotly_chart(estilo(fig, 380, "MW", meses=True).update_layout(title="Demanda máxima y media mensual"),
                            width="stretch")
        with c2:
            fig = go.Figure()
            for area in ("Norte", "Centro", "Sur"):
                fig.add_bar(x=dmm["mes"], y=dmm[f"e_{area.lower()}"], name=area, marker_color=AREA_COLOR[area])
            fig.update_layout(barmode="stack", bargap=0.25)
            st.plotly_chart(estilo(fig, 380, "GWh", meses=True).update_layout(title="Energía mensual por área"),
                            width="stretch")
        dd = datos("demanda_diaria")
        fig = go.Figure()
        fig.add_scatter(x=dd["dia"], y=dd["max_mw"], name="Máxima diaria", line=dict(color=SERIES[0], width=1.5))
        st.plotly_chart(estilo(fig, 320, "MW", leyenda=False).update_layout(title="Máxima demanda diaria"),
                        width="stretch")
        t = dmm[["mes", "max_mw", "min_mw", "prom_mw", "energia_gwh", "max_norte", "max_centro", "max_sur", "n"]]
        t.columns = ["Mes", "Máx MW", "Mín MW", "Prom MW", "Energía GWh", "Máx Norte", "Máx Centro", "Máx Sur",
                     "Intervalos"]
        st.dataframe(t.style.format({c: "{:,.0f}" for c in t.columns[1:]} | {"Energía GWh": "{:,.1f}"}),
                     width="stretch", hide_index=True)

# ============================================================================= GENERACION
with tab_gen:
    gm = datos("generacion_mensual")
    if not gm.empty:
        gm["mes"] = gm["mes"].map(mes_txt)
    if gm.empty:
        st.info("Sin datos de generación (hoja TIPO_RECURSO del Anexo A).")
    else:
        orden = [t for t in ORDEN_TEC if t in gm["tecnologia"].unique()]
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure()
            for t in orden:
                s = gm[gm["tecnologia"] == t]
                fig.add_bar(x=s["mes"], y=s["gwh"], name=t, marker_color=TECNOLOGIA_COLOR[t],
                            hovertemplate="%{y:,.0f} GWh")
            fig.update_layout(barmode="stack", bargap=0.2)
            st.plotly_chart(estilo(fig, 520, "GWh", meses=True).update_layout(title="Producción mensual por tecnología"),
                            width="stretch")
        with c2:
            pct = gm.assign(pct=gm["gwh"] / gm.groupby("mes")["gwh"].transform("sum") * 100)
            fig = go.Figure()
            for t in orden:
                s = pct[pct["tecnologia"] == t]
                fig.add_bar(x=s["mes"], y=s["pct"], name=t, marker_color=TECNOLOGIA_COLOR[t],
                            hovertemplate="%{y:.1f} %")
            fig.update_layout(barmode="stack", bargap=0.2)
            st.plotly_chart(estilo(fig, 520, "%", meses=True).update_layout(title="Participación mensual (%)"),
                            width="stretch")

        st.markdown("**Producción mensual (GWh)**")
        tabla = gm.pivot_table(index="mes", columns="tecnologia", values="gwh", aggfunc="sum", sort=False)[orden]
        tabla["Total"] = tabla.sum(axis=1)
        dias_mes = gm.groupby("mes")["dias"].max()
        tabla["Días con dato"] = dias_mes
        st.dataframe(tabla.style.format({c: ("{:.0f}" if c == "Días con dato" else "{:,.1f}") for c in tabla.columns}),
                     width="stretch")

        c1, c2 = st.columns(2)
        with c1:
            pm = datos("generacion_pmax_mensual")
            pm["mes"] = pm["mes"].map(mes_txt)
            fig = go.Figure()
            for t in orden:
                s = pm[pm["tecnologia"] == t]
                fig.add_scatter(x=s["mes"], y=s["mw"], name=t, mode="lines+markers",
                                line=dict(color=TECNOLOGIA_COLOR[t], width=2), marker=dict(size=6))
            st.plotly_chart(estilo(fig, 480, "MW", meses=True).update_layout(
                title="Potencia máxima despachada por tecnología (MW)"), width="stretch")
        with c2:
            um = datos("unidades_mensual")
            if not um.empty:
                um["mes"] = um["mes"].map(mes_txt)
                fig = go.Figure()
                tipos = {"HIDROELÉCTRICA": "#1c5cab", "TERMOELÉCTRICA": "#eb6834", "SOLAR": "#eda100",
                         "EÓLICA": "#1baf7a"}
                for i, tp in enumerate(um["tipo"].unique()):
                    s = um[um["tipo"] == tp]
                    fig.add_bar(x=s["mes"], y=s["unidades"], name=tp.title(),
                                marker_color=tipos.get(tp, SERIES[(i + 4) % 8]))
                fig.update_layout(barmode="stack", bargap=0.2)
                st.plotly_chart(estilo(fig, 480, "unidades", meses=True).update_layout(
                    title="Unidades que despacharon en el mes (DESPACHO_EJECUTADO)"), width="stretch")
        st.caption("Potencia máxima = mayor potencia simultánea registrada en el mes. El IEOD no publica la potencia "
                   "efectiva instalada; para ese dato usa el reporte de potencia efectiva del COES.")

    if not gen.empty:
        st.markdown(f"#### Despacho por tecnología · {desde:%d/%m} – {hasta:%d/%m/%Y}")
        orden = [t for t in ORDEN_TEC if t in gen["tecnologia"].unique()]
        fig = go.Figure()
        for t in orden:
            s = gen[gen["tecnologia"] == t]
            fig.add_scatter(x=s["ts"], y=s["mw"], name=t, stackgroup="g", mode="lines",
                            line=dict(width=0.4, color=TECNOLOGIA_COLOR[t]), fillcolor=TECNOLOGIA_COLOR[t])
        if not dm.empty:
            fig.add_scatter(x=dm["ts"], y=dm["sein"], name="Demanda SEIN", mode="lines",
                            line=dict(color="#0b0b0b", width=1.4, dash="dot"))
        st.plotly_chart(estilo(fig, 540, "MW").update_layout(title="Generación cada 30 min por tecnología"),
                        width="stretch")
        rk = datos("ranking_unidades", desde, hasta)
        if not rk.empty:
            with st.expander("Ranking de unidades por energía en el periodo"):
                rk.columns = ["Unidad", "Empresa", "Tipo", "Área", "GWh", "Pmáx MW"]
                st.dataframe(rk.style.format({"GWh": "{:,.2f}", "Pmáx MW": "{:,.1f}"}),
                             width="stretch", hide_index=True)

# ============================================================================= DATOS
with tab_dat:
    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown("**Cobertura del IEOD por mes**")
        cob = datos("cobertura")
        st.dataframe(cob.rename(columns={"mes": "Mes", "dias": "Días", "dias_cmg": "Con CMg",
                                         "dias_anexo": "Con Anexo A"}), width="stretch", hide_index=True)
    with c2:
        st.markdown("**Días con faltantes (se reintentan en cada actualización)**")
        st.dataframe(datos("dias_con_aviso"), width="stretch", hide_index=True)
        with st.expander("Bitácora de actualizaciones"):
            st.dataframe(datos("bitacora"), width="stretch", hide_index=True)

    st.markdown("**Exportar el periodo seleccionado**")
    cm_ancho = cm.pivot_table(index="ts", columns="barra", values="total").rename(columns=etq).reset_index() \
        if not cm.empty else pd.DataFrame()
    gen_ancho = gen.pivot_table(index="ts", columns="tecnologia", values="mw").reset_index() \
        if not gen.empty else pd.DataFrame()
    st.download_button(
        "⬇️ Descargar Excel (CMg, demanda y generación cada 30 min)",
        a_excel({"CMg_30min": cm_ancho, "Demanda_30min": dm, "Generacion_30min": gen_ancho,
                 "Generacion_mensual": datos("generacion_mensual"), "Demanda_mensual": datos("demanda_mensual")}),
        file_name=f"monitor_sein_{desde:%Y%m%d}_{hasta:%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("Fuente: COES-SINAC, Informe de Evaluación de la Operación Diaria (IEOD) — CMgCP y Anexo A — "
               "y portal de tiempo real. Datos públicos; los del IEOD pueden revisarse al cierre de mes.")
