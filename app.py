import streamlit as st
import sys

# SOLUCIÓN AL ERROR DE ALTAIR (Compatibilidad v4/v5)
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

st.title("🔬 Simulador Interactivo de Procesos Químicos")
st.markdown("""
Esta plataforma integra **BioSTEAM** para simulación de procesos y **Gemini 2.0 Pro** para análisis técnico avanzado.
""")

# Configuración de la API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ Falta GEMINI_API_KEY en los Secrets de Streamlit.")

# ==========================================
# FUNCIÓN NÚCLEO DE SIMULACIÓN
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_v220):
    # Limpieza total para evitar duplicados en el registro de Streamlit
    bst.main_flowsheet.clear()
    
    # 1. Definición de Compuestos
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # 2. Corrientes de entrada
    mosto = bst.Stream("MOSTO", Water=flow_water, Ethanol=flow_ethanol, units="kg/hr", T=25+273.15)
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15)

    # 3. Equipos y Conexiones
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    W210 = bst.HXprocess("W210", 
                        ins=(P100-0, vinazas_retorno), 
                        outs=("Mosto_Pre", "Drenaje"),
                        phase0="l", phase1="l")
    W210.outs[0].T = 85+273.15

    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=temp_v220+273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=101325)
    
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=101325, Q=0)
    
    # Esta es la corriente que causaba el error de registro, ahora la manejamos localmente
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # 4. Creación del Sistema
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    return sys_bio

# ==========================================
# BARRA LATERAL (CONTROLES)
# ==========================================
with st.sidebar:
    st.header("⚙️ Variables de Control")
    f_agua = st.slider("Agua en Alimentación (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Etanol en Alimentación (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temperatura de Calentamiento (°C)", 85, 1
