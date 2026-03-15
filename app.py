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
# CONFIGURACIÓN DE LA PÁGINA (UI)
# ==========================================
st.set_page_config(page_title="BioSTEAM & Gemini 2.0 Pro", layout="wide")

st.title("🔬 Simulador Interactivo con IA Avanzada")
st.markdown("""
Esta aplicación utiliza **BioSTEAM** para el cálculo de procesos y **Gemini 2.0 Pro** para la interpretación técnica.
""")

# Configuración de la API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.error("🔑 Falta GEMINI_API_KEY en Secrets.")

# ==========================================
# FUNCIÓN NÚCLEO DE SIMULACIÓN
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_v220):
    # Limpiar flowsheet para evitar duplicados
    bst.main_flowsheet.clear()
    
    # 1. Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # 2. Corrientes dinámicas
    mosto = bst.Stream("1_MOSTO", Water=flow_water, Ethanol=flow_ethanol, units="kg/hr", T=25+273.15)
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15)

    # 3. Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("3_Mosto_Pre", "Drenaje"))
    W210.outs[0].T = 85+273.15
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=temp_v220+273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, P=101325)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor", "Vinazas"), P=101325, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # 4. Simulación
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    return sys_bio

# ==========================================
# INTERFAZ LATERAL
# ==========================================
with st.sidebar:
    st.header("⚙️ Ajustes de Simulación")
    f_agua = st.slider("Flujo Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Flujo Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temperatura W220 (°C)", 85, 110, 92)

# ==========================================
# PROCESAMIENTO Y RESULTADOS
# ==========================================
try:
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    
    # Métricas de cabecera
    prod = sistema.flowsheet.stream.Producto_Final
    col1, col2, col3 = st.columns(3)
    col1.metric("Pureza Etanol", f"{(prod.imass['Ethanol']/prod.F_mass):.1%}")
    col2.metric("Producción", f"{prod.F_mass:.2f} kg/h")
    col3.metric("Energía P200", f"{sistema.flowsheet.unit.P200.power_utility.rate:.2f} kW")

    tab1, tab2, tab3 = st.tabs(["📊 Datos de Balance", "📐 PFD Visual", "🤖 Tutor Gemini 2.0 Pro"])

    with tab1:
        # Generar DataFrame de corrientes
        datos_m = [{"ID": s.ID, "T(C)": s.T-273.15, "Flujo(kg/h)": s.F_mass} for s in sistema.streams if s.F_mass > 0]
        df_materia = pd.DataFrame(datos_m)
        st.dataframe(df_materia)

    with tab2:
        # Diagrama de flujo
        st.graphviz_chart(sistema.diagram('dot'))

    with tab3:
        st.subheader("Análisis con Gemini 2.0 Pro")
        if st.button("Pedir análisis a la IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Gemini 2.0 está pensando..."):
                    # LLAMADA AL MODELO 2.0 PRO
                    model = genai.GenerativeModel('gemini-2.0-pro-exp-02-05') 
                    
                    prompt = f"""
                    Analiza como ingeniero senior:
                    Resultados de simulación BioSTEAM:
                    {df_materia.to_string()}
                    
                    ¿Es óptima esta separación de etanol? Sugiere mejoras en la temperatura de calentamiento.
                    """
                    response = model.generate_content(prompt)
                    st.success("Análisis completado:")
                    st.write(response.text)
            else:
                st.error("Por favor, añade tu clave de API en Secrets.")

except Exception as e:
    st.error(f"Hubo un error: {e}")
