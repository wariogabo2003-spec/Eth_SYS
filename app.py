import streamlit as st
import sys

# SOLUCIÓN AL ERROR DE ALTAIR (v4 a v5)
try:
    import altair.vegalite.v5 as lv5
    sys.modules['altair.vegalite.v4'] = lv5
except ImportError:
    pass

import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# ==========================================
# CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="BioSTEAM & Gemini 2.0 Pro", layout="wide")

st.title("🔬 Simulador de Procesos Bioquímicos")
st.markdown("Optimización de separación de etanol mediante BioSTEAM e Inteligencia Artificial.")

# API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ Falta GEMINI_API_KEY.")

# ==========================================
# SIMULACIÓN NÚCLEO
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_v220):
    bst.main_flowsheet.clear()
    
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    mosto = bst.Stream("MOSTO", Water=flow_water, Ethanol=flow_ethanol, units="kg/hr", T=25+273.15)
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15)

    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("Mosto_Pre", "Drenaje"))
    W210.outs[0].T = 85+273.15
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=temp_v220+273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=101325)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=101325, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    return sys_bio

# ==========================================
# INTERFAZ (SLIDERS)
# ==========================================
with st.sidebar:
    st.header("⚙️ Variables")
    f_agua = st.slider("Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temp. Calentamiento (°C)", 80, 115, 95)

# ==========================================
# RESULTADOS
# ==========================================
try:
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    
    # Buscar producto final
    prod = next((s for s in sistema.streams if s.ID == "Producto_Final"), None)
    
    if prod:
        c1, c2, c3 = st.columns(3)
        pureza = (prod.imass['Ethanol'] / prod.F_mass) if prod.F_mass > 0 else 0
        c1.metric("Pureza Etanol", f"{pureza:.1%}")
        c2.metric("Producción Total", f"{prod.F_mass:.2f} kg/h")
        c3.metric("Etanol Recuperado", f"{prod.imass['Ethanol']:.2f} kg/h")

    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 PFD", "🤖 Tutor IA"])

    with tab1:
        datos_m = []
        for s in sistema.streams:
            if s.F_mass > 0.01:
                datos_m.append({
                    "Corriente": s.ID,
                    "T (°C)": round(s.T - 273.15, 2),
                    "Flujo (kg/h)": round(s.F_mass, 2),
                    "Etanol (kg/h)": round(s.imass['Ethanol'], 2)
                })
        df_materia = pd.DataFrame(datos_m)
        st.dataframe(df_materia, use_container_width=True)

    with tab2:
        st.subheader("Diagrama del Proceso")
        # SOLUCIÓN AL ERROR 'kind': Forzamos el tipo 'surface'
        try:
            dot_graph = sistema.diagram(kind='surface', format='dot')
            st.graphviz_chart(dot_graph)
        except Exception as diag_err:
            st.warning(f"No se pudo renderizar el diagrama: {diag_err}")

    with tab3:
        st.subheader("Análisis Gemini 2.0 Pro")
        if st.button("Analizar con IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Generando reporte..."):
                    model = genai.GenerativeModel('gemini-2.0-pro-exp-02-05')
                    prompt = f"Analiza estos resultados de BioSTEAM: {df_materia.to_string()}. Explica el rendimiento del Flash V1."
                    response = model.generate_content(prompt)
                    st.info(response.text)
            else:
                st.error("Configura la API Key.")

except Exception as e:
    st.error(f"Error detectado: {e}")
