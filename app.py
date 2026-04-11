import streamlit as st
import sys

# 1. PARCHE DE COMPATIBILIDAD PARA ALTAIR
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
    
    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 Diagrama PFD", "🤖 Tutor IA"])

    with tab1:
        datos_m = [{"Corriente": s.ID, "T (°C)": round(s.T-273.15, 2), "Flujo (kg/h)": round(s.F_mass, 2)} for s in corrientes if s.F_mass > 0.01]
        df_materia = pd.DataFrame(datos_m)
        st.table(df_materia)

    with tab2:
        st.subheader("Diagrama de Flujo del Proceso")
        try:
            # MÉTODO ROBUSTO: Generar el objeto Digraph y extraer su código fuente DOT
            # kind='surface' suele ser el más estable para visualización web
            dot_obj = sistema.diagram(kind='surface')
            
            # Si dot_obj es una cadena, la imprimimos; si es un objeto, extraemos .source
            dot_code = dot_obj.source if hasattr(dot_obj, 'source') else str(dot_obj)
            
            # Renderizar usando el motor de Streamlit
            st.graphviz_chart(dot_code)
            
        except Exception as e:
            st.warning(f"No se pudo generar el diagrama dinámico. Error: {e}")
            st.info("Representación lógica: P100 (Bomba) -> W210 (Intercambiador) -> W220 (Calentador) -> V1 (Flash) -> W310 (Condensador)")

    with tab3:
        st.subheader("Análisis Inteligente")
        if st.button("Generar Reporte IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Analizando con Gemini..."):
                    try:
                        # Usamos la lógica de auto-detección que ya te funcionó
                        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                        target_model = next((m for m in ["models/gemini-1.5-flash", "models/gemini-1.5-pro"] if m in available_models), None)
                        
                        if not target_model and available_models:
                            target_model = available_models[0]

                        if target_model:
                            model = genai.GenerativeModel(target_model)
                            prompt = f"Como experto en ingeniería, analiza estos datos de BioSTEAM: {df_materia.to_string()}. Evalúa la separación a {t_v220}°C."
                            response = model.generate_content(prompt)
                            st.info(f"### Informe (Modelo: {target_model})")
                            st.markdown(response.text)
                        else:
                            st.error("No hay modelos disponibles.")
                    except Exception as e:
                        st.error(f"Error en IA: {e}")
            else:
                st.error("Falta API Key.")

except Exception as e:
    st.error(f"Error: {e}")
