import streamlit as st
import sys
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# Parche Altair
try:
    import altair.vegalite.v5 as lv5
    sys.modules['altair.vegalite.v4'] = lv5
except ImportError: pass

# ==========================================
# CONFIGURACIÓN Y ESTADO (Punto 16)
# ==========================================
st.set_page_config(page_title="BioSTEAM Industrial Pro", layout="wide")

# Inicializar estados para que la IA y los Sliders estén sincronizados
if "t_feed" not in st.session_state: st.session_state.t_feed = 25.0
if "t_w220" not in st.session_state: st.session_state.t_w220 = 98.0
if "p_v100" not in st.session_state: st.session_state.p_v100 = 101325.0
if "messages" not in st.session_state: st.session_state.messages = []

st.title("🏭 Planta de Bioetanol Inteligente")

# ==========================================
# SIDEBAR - CONTROL DINÁMICO
# ==========================================
with st.sidebar:
    st.header("🌡️ Parámetros de Proceso")
    # Vinculamos los sliders al session_state para permitir cambios externos de la IA
    t_feed = st.slider("Temp. Alimento (°C)", 10.0, 70.0, st.session_state.t_feed, key="slider_t_feed")
    t_w220 = st.slider("Temp. Salida W220 (°C)", 70.0, 130.0, st.session_state.t_w220, key="slider_t_w220")
    p_v100 = st.slider("Presión V100 (Pa)", 10000.0, 200000.0, st.session_state.p_v100, key="slider_p_v100")
    
    # Actualizar session_state con lo que el usuario mueva manualmente
    st.session_state.t_feed = t_feed
    st.session_state.t_w220 = t_w220
    st.session_state.p_v100 = p_v100

    st.header("💰 Precios de Mercado")
    p_luz = st.slider("Luz (USD/kWh)", 0.01, 0.50, 0.15)
    p_vapor = st.slider("Vapor (USD/kg)", 0.01, 0.20, 0.05)
    p_agua = st.slider("Agua (USD/m3)", 0.001, 0.10, 0.02)
    p_mosto = st.slider("Costo Mosto (USD/kg)", 0.05, 1.50, 0.30)
    p_etanol = st.slider("Venta Etanol (USD/kg)", 0.50, 10.0, 4.0)

# ==========================================
# SIMULACIÓN Y ECONOMÍA
# ==========================================
def run_simulation():
    bst.main_flowsheet.clear()
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # Corrientes
    mosto = bst.Stream("MOSTO", Water=900, Ethanol=100, units="kg/hr", 
                       T=st.session_state.t_feed + 273.15)
    
    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    # Vinaza ficticia para el intercambio de calor
    vin = bst.Stream("vin", Water=1000, T=360) 
    W210 = bst.HXprocess("W210", ins=(P100-0, vin), outs=("Mosto_Pre", "d"), phase0='l', phase1='l')
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_H", T=st.session_state.t_w220 + 273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, P=st.session_state.p_v100)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liq_V1"), P=st.session_state.p_v100, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    sys = bst.System("sys", path=(P100, W210, W220, V100, V1, W310))
    sys.simulate()
    
    # Economía
    prod = W310.outs[0]
    flow_et = prod.imass['Ethanol']
    costo_op = (mosto.F_mass * p_mosto) + (abs(W220.Q)/2200 * p_vapor) + (P100.power_utility.rate * p_luz)
    utilidad = (flow_et * p_etanol) - costo_op
    inv = 250000
    
    return sys, {
        "ROI": (utilidad * 8000 / inv) * 100,
        "NPV": -inv + (utilidad * 8000 / 0.12),
        "Payback": inv / (utilidad * 8000) if utilidad > 0 else 0,
        "CostoP": costo_op / flow_et if flow_et > 0 else 0,
        "Prod": prod
    }

sistema, econ = run_simulation()

# ==========================================
# DASHBOARD PRINCIPAL
# ==========================================
st.subheader("🎯 Resultados de Operación y Finanzas")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Etanol Recuperado", f"{econ['Prod'].imass['Ethanol']:.2f} kg/h")
c2.metric("Pureza Etanol", f"{(econ['Prod'].imass['Ethanol']/econ['Prod'].F_mass):.1%}")
c3.metric("Costo Producción", f"${econ['CostoP']:.2f}/kg")
c4.metric("ROI Anual", f"{econ['ROI']:.1f}%")

tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 PFD (Diagrama)", "🤖 Tutor IA"])

with tab1:
    st.table(pd.DataFrame([{"Corriente": s.ID, "T(C)": s.T-273.15, "Flujo(kg/h)": s.F_mass} for s in sistema.streams if s.F_mass > 0]))

with tab2:
    st.subheader("Diagrama de Flujo")
    try:
        # Lógica de extracción blindada para evitar el AttributeError
        dot_data = sistema.diagram('surface', display=False)
        if hasattr(dot_data, 'source'):
            st.graphviz_chart(dot_data.source)
        else:
            st.graphviz_chart(str(dot_data))
    except Exception as e:
        st.error(f"Error visual: {e}")

with tab3:
    st.write("Consulta al tutor o pide cambios (ej: 'Sube la temperatura de W220 a 105')")
    for m in st.session_state.messages:
        with st.chat_message(m["role"]): st.markdown(m["content"])

    if prompt := st.chat_input("¿Cómo puedo mejorar el NPV?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): st.markdown(prompt)

        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # Contexto para que la IA actúe sobre los parámetros
            sys_info = f"""
            Eres un ingeniero de control. Parámetros actuales: Temp_Feed={st.session_state.t_feed}, Temp_W220={st.session_state.t_w220}, Presion_V100={st.session_state.p_v100}.
            Si el usuario pide cambiar valores, usa exactamente el formato: [SET_VAL: t_w220=X], [SET_VAL: t_feed=X], [SET_VAL: p_v100=X].
            """
            
            res = model.generate_content(sys_info + "\n" + prompt)
            
            # Procesar posible cambio de parámetros (Punto 16)
            if "[SET_VAL:" in res.text:
                try:
                    parts = res.text.split("[SET_VAL:")[1].split("]")[0].split("=")
                    var_name = parts[0].strip()
                    var_val = float(parts[1].strip())
                    st.session_state[var_name] = var_val
                    st.rerun() # Reiniciar para aplicar el cambio en sliders y simulación
                except: pass

            with st.chat_message("assistant"): st.markdown(res.text)
            st.session_state.messages.append({"role": "assistant", "content": res.text})
