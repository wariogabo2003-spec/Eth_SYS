import streamlit as st
import sys

# 1. SOLUCIÓN AL ERROR DE ALTAIR (v4 a v5)
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
st.set_page_config(page_title="BioSTEAM & Gemini 2.0", layout="wide")

st.title("🔬 Simulador de Procesos: Bioetanol")
st.markdown("Plataforma de simulación termodinámica con análisis por IA.")

# Configuración Segura de API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ Configura GEMINI_API_KEY en Secrets.")

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
    st.header("⚙️ Variables de Control")
    f_agua = st.slider("Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temp. Calentamiento (°C)", 80, 115, 95)

# ==========================================
# RESULTADOS
# ==========================================
try:
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    
    # Buscar producto final
    corrientes = sistema.streams
    prod = next((s for s in corrientes if s.ID == "Producto_Final"), None)
    
    if prod:
        c1, c2, c3 = st.columns(3)
        pureza = (prod.imass['Ethanol'] / prod.F_mass) if prod.F_mass > 0 else 0
        c1.metric("Pureza Etanol", f"{pureza:.1%}")
        c2.metric("Producción Total", f"{prod.F_mass:.2f} kg/h")
        c3.metric("Etanol Recuperado", f"{prod.imass['Ethanol']:.2f} kg/h")

    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 Diagrama PFD", "🤖 Tutor IA"])

    with tab1:
        datos_m = []
        for s in corrientes:
            if s.F_mass > 0.01:
                datos_m.append({
                    "Corriente": s.ID,
                    "T (°C)": round(s.T - 273.15, 2),
                    "Flujo (kg/h)": round(s.F_mass, 2),
                    "Etanol (kg/h)": round(s.imass['Ethanol'], 2)
                })
        df_materia = pd.DataFrame(datos_m)
        st.table(df_materia)

    with tab2:
        st.subheader("Diagrama del Proceso")
        # 2. SOLUCIÓN AL ERROR NONETYPE: Usamos el método .diagram() con retorno DOT de string
        try:
            # Generamos el código fuente de Graphviz
            pfd_source = sistema.diagram(kind='surface') 
            # BioSTEAM genera un objeto Digraph de Graphviz, lo pasamos a DOT string
            st.graphviz_chart(pfd_source.source)
        except Exception as diag_err:
            st.warning(f"Error visual: {diag_err}. Esto ocurre por dependencias de Graphviz en el servidor.")

    with tab3:
        st.subheader("Análisis con Gemini 2.0")
        if st.button("Generar Reporte IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Consultando a Gemini 2.0..."):
                    # 3. SOLUCIÓN AL ERROR 404: Usamos el ID de modelo más estable para v2
                    model = genai.GenerativeModel('gemini-2.0-flash') 
                    
                    prompt = f"""
                    Analiza como ingeniero químico los resultados:
                    {df_materia.to_string()}
                    
                    ¿Qué está pasando en el Flash V1 con una temperatura de {t_v220}°C? 
                    ¿Es una separación eficiente? Responde de forma concisa.
                    """
                    response = model.generate_content(prompt)
                    st.info(response.text)
            else:
                st.error("Configura la API Key en los Secrets.")

except Exception as e:
    st.error(f"Error detectado: {e}")
