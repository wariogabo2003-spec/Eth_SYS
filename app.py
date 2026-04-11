import streamlit as st
import sys

# PARCHE DE ALTAIR
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
st.set_page_config(page_title="BioSTEAM & Gemini Smart", layout="wide")
st.title("🔬 Simulador de Procesos: Bioetanol")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ Configura GEMINI_API_KEY en Secrets.")

# ==========================================
# SIMULACIÓN (BioSTEAM)
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
# INTERFAZ
# ==========================================
with st.sidebar:
    st.header("⚙️ Variables")
    f_agua = st.slider("Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temp. Calentamiento (°C)", 80, 115, 95)

try:
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    corrientes = sistema.streams
    
    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 PFD", "🤖 Tutor IA"])

    with tab1:
        datos_m = [{"Corriente": s.ID, "T (°C)": round(s.T-273.15, 2), "Flujo (kg/h)": round(s.F_mass, 2)} for s in corrientes if s.F_mass > 0.01]
        df_materia = pd.DataFrame(datos_m)
        st.table(df_materia)

    with tab2:
        try:
            pfd = sistema.diagram(kind='surface')
            st.graphviz_chart(pfd.source if hasattr(pfd, 'source') else str(pfd))
        except:
            st.info("P100 -> W210 -> W220 -> V100 -> V1 -> W310")

    with tab3:
        st.subheader("Análisis Inteligente")
        if st.button("Generar Reporte IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Buscando modelo disponible y analizando..."):
                    try:
                        # AUTO-DETECCIÓN DE MODELOS DISPONIBLES
                        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                        # Prioridad: 1.5-flash, luego 1.5-pro, luego lo que sea que empiece con gemini
                        target_model = None
                        for m in ["models/gemini-1.5-flash", "models/gemini-1.5-pro"]:
                            if m in available_models:
                                target_model = m
                                break
                        
                        if not target_model and available_models:
                            target_model = available_models[0]

                        if target_model:
                            model = genai.GenerativeModel(target_model)
                            prompt = f"Como ingeniero químico, analiza: {df_materia.to_string()}. ¿Es eficiente la separación a {t_v220}°C?"
                            response = model.generate_content(prompt)
                            st.info(f"### Informe (Modelo: {target_model})")
                            st.markdown(response.text)
                        else:
                            st.error("No se encontraron modelos de generación de contenido en esta API Key.")
                    except Exception as e:
                        st.error(f"Error crítico de API: {e}")
            else:
                st.error("Configura la API Key.")

except Exception as e:
    st.error(f"Error: {e}")
