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

# Configuración de API Key con manejo de errores
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.error("🔑 Falta la GEMINI_API_KEY en Secrets.")

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
# INTERFAZ (SLIDERS)
# ==========================================
with st.sidebar:
    st.header("⚙️ Variables")
    f_agua = st.slider("Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temp. Calentamiento (°C)", 80, 115, 95)

# ==========================================
# EJECUCIÓN Y TABS
# ==========================================
try:
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    corrientes = sistema.streams
    
    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 Diagrama PFD", "🤖 Tutor IA"])

    with tab1:
        datos_m = [{"Corriente": s.ID, "T (°C)": round(s.T-273.15, 2), "Flujo (kg/h)": round(s.F_mass, 2)} for s in corrientes if s.F_mass > 0.01]
        df_materia = pd.DataFrame(datos_m)
        st.table(df_materia)

    with tab2:
        st.subheader("Diagrama de Flujo")
        try:
            dot_obj = sistema.diagram(kind='surface', display=False)
            source_code = getattr(dot_obj, 'source', str(dot_obj))
            st.graphviz_chart(source_code)
        except Exception as e:
            st.warning("Diagrama no disponible.")

    with tab3:
        st.subheader("Análisis con Gemini")
        # El botón debe estar fuera de cualquier otro widget condicional complejo
        if st.button("🚀 Generar Informe Técnico"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("La IA está analizando los datos..."):
                    try:
                        # Buscamos modelos disponibles
                        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                        # Prioridad Flash
                        target = next((m for m in models if "1.5-flash" in m), models[0] if models else None)
                        
                        if target:
                            model = genai.GenerativeModel(target)
                            prompt = f"""
                            Actúa como ingeniero químico. Datos: {df_materia.to_string()}
                            Explica brevemente la eficiencia de separación del alcohol a {t_v220}°C. 
                            Responde en español y de forma directa.
                            """
                            response = model.generate_content(prompt)
                            
                            st.success(f"Informe generado con: {target}")
                            st.markdown("---")
                            st.markdown(response.text)
                        else:
                            st.error("No se encontraron modelos de IA activos en tu cuenta.")
                    except Exception as e:
                        st.error(f"Error al conectar con la IA: {e}")
            else:
                st.error("Por favor, agrega la API Key en los Secrets.")

except Exception as e:
    st.error(f"Error en la simulación: {e}")
