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
# CONFIGURACIÓN Y ESTADO
# ==========================================
st.set_page_config(page_title="BioSTEAM Smart Plant", layout="wide")

# Inicializar estados para el punto 16
if "t_feed" not in st.session_state: st.session_state.t_feed = 25.0
if "t_w220" not in st.session_state: st.session_state.t_w220 = 98.0
if "p_v100" not in st.session_state: st.session_state.p_v100 = 101325.0
if "messages" not in st.session_state: st.session_state.messages = []

st.title("🏭 Planta Industrial BioSTEAM + IA")

# ==========================================
# SIDEBAR - CONTROL DE PARÁMETROS
# ==========================================
with st.sidebar:
    st.header("🌡️ Parámetros de Proceso")
    # Los sliders ahora usan el session_state para que la IA pueda moverlos
    st.session_state.t_feed = st.slider("Temp. Alimento (°C)", 10.0, 70.0, st.session_state.t_feed)
    st.session_state.t_w220 = st.slider("Temp. Salida W220 (°C)", 70.0, 130.0, st.session_state.t_w220)
    st.session_state.p_v100 = st.slider("Presión V100 (Pa)", 10000.0, 200000.0, st.session_state.p_v100)
    
    st.header("💰 Precios de Mercado")
    p_luz = st.slider("Luz (USD/kWh)", 0.01, 0.50, 0.15)
    p_vapor = st.slider("Vapor (USD/kg)", 0.01, 0.20, 0.05)
    p_agua = st.slider("Agua (USD/m3)", 0.001, 0.10, 0.02)
    p_mosto = st.slider("Costo Mosto (USD/kg)", 0.05, 1.50, 0.30)
    p_etanol = st.slider("Venta Etanol (USD/kg)", 0.50, 10.0, 4.0)

# ==========================================
# LÓGICA DE SIMULACIÓN MEJORADA
# ==========================================
def run_simulation():
    bst.main_flowsheet.clear()
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # 1. Corrientes
    mosto = bst.Stream("MOSTO", Water=900, Ethanol=100, units="kg/hr", 
                       T=st.session_state.t_feed + 273.15)
    
    # 2. Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, bst.Stream("vin", Water=1, T=350)), outs=("Mosto_Pre", "d"), phase0='l', phase1='l')
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_H", T=st.session_state.t_w220 + 273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, P=st.session_state.p_v100)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liq_V1"), P=st.session_state.p_v100, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    sys = bst.System("sys", path=(P100, W210, W220, V100, V1, W310))
    sys.simulate()
    
    # --- ECONOMÍA DINÁMICA ---
    prod = W310.outs[0]
    flow_etanol = prod.imass['Ethanol']
    
    # Cálculo manual de OPEX para que responda a los sliders de precio
    costo_mosto = mosto.F_mass * p_mosto
    # Estimamos energía basada en el calor intercambiado (Q en kJ/hr)
    costo_vapor = abs(W220.Q) / 2200 * p_vapor # 2200 kJ/kg entalpía vapor aprox.
    costo_agua = abs(W310.Q) / 40 * p_agua     # enfriamiento aprox.
    costo_luz = P100.power_utility.rate * p_luz
    
    opex_total = costo_mosto + costo_vapor + costo_agua + costo_luz
    ingresos = flow_etanol * p_etanol
    
    # Indicadores
    inv = 200000 # Inversión reducida para ver cambios más sensibles
    utilidad_anual = (ingresos - opex_total) * 8000
    roi = (utilidad_anual / inv) * 100
    payback = inv / utilidad_anual if utilidad_anual > 0 else 0
    npv = -inv + (utilidad_anual / 0.12) # Tasa 12%
    
    return sys, {"ROI": roi, "Payback": payback, "NPV": npv, "CostoP": opex_total/flow_etanol if flow_etanol > 0 else 0, "Prod": prod}

# Ejecutar
sistema, econ = run_simulation()
prod = econ['Prod']

# ==========================================
# VISUALIZACIÓN
# ==========================================
st.subheader("🎯 Variables de Salida y Economía")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Flujo Etanol", f"{prod.imass['Ethanol']:.2f} kg/h")
c2.metric("Pureza", f"{(prod.imass['Ethanol']/prod.F_mass):.1%}")
c3.metric("Costo Real", f"${econ['CostoP']:.2f}/kg")
c4.metric("ROI", f"{econ['ROI']:.1f}%")

e1, e2, e3 = st.columns(3)
e1.metric("NPV (VAN)", f"${econ['NPV']:,.0f}")
e2.metric("Payback", f"{econ['Payback']:.2f} años")
e3.metric("Venta Sugerida", f"${econ['CostoP']*1.3:.2f}/kg")

t1, t2, t3 = st.tabs(["📊 Balances", "📐 PFD", "🤖 IA Tutor & Chat"])

with t1:
    st.table(pd.DataFrame([{"ID": s.ID, "T(C)": s.T-273.15, "F(kg/h)": s.F_mass} for s in sistema.streams if s.F_mass > 0]))

with t2:
    st.graphviz_chart(sistema.diagram('surface').source)

with t3:
    # CHAT CON CAPACIDAD DE MODIFICAR PARÁMETROS (Punto 16)
    st.write("Escribe: 'Ajusta la temperatura W220 a 110 grados' o haz preguntas técnicas.")
    
    for m in st.session_state.messages:
        with st.chat_message(m["role"]): st.markdown(m["content"])

    if prompt := st.chat_input("¿Cómo optimizo el ROI?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): st.markdown(prompt)

        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # Instrucción de sistema para el Punto 16
            sys_instr = f"""
            Eres un ingeniero de planta. Datos: W220={st.session_state.t_w220}C, Alimento={st.session_state.t_feed}C, Presion={st.session_state.p_v100}Pa.
            Si el usuario te pide cambiar un valor de estos 3, responde primero con el cambio en este formato: 
            [SET_VAL: t_w220=valor] o [SET_VAL: t_feed=valor] o [SET_VAL: p_v100=valor].
            Luego explica el porqué.
            """
            
            response = model.generate_content(sys_instr + prompt)
            text = response.text
            
            # Lógica para aplicar cambios del Punto 16
            if "[SET_VAL:" in text:
                try:
                    cmd = text.split("[SET_VAL:")[1].split("]")[0].strip()
                    var, val = cmd.split("=")
                    st.session_state[var.strip()] = float(val)
                    st.rerun() # Reinicia para aplicar el cambio en los sliders
                except: pass
                
            with st.chat_message("assistant"):
                st.markdown(text)
            st.session_state.messages.append({"role": "assistant", "content": text})
