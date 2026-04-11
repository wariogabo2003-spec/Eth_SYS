import streamlit as st
import sys

# 1. PARCHE DE COMPATIBILIDAD ALTAIR
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
st.set_page_config(page_title="BioSTEAM Industrial Pro", layout="wide")
st.title("🏭 Planta de Bioetanol: Simulación e Indicadores Económicos")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.error("🔑 Configura GEMINI_API_KEY en Secrets.")

# Inicializar historial de chat si no existe
if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================
# FUNCIÓN DE SIMULACIÓN Y ECONOMÍA
# ==========================================
def run_full_simulation(params, prices):
    bst.main_flowsheet.clear()
    
    # Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # Precios de Servicios y Materias Primas
    bst.settings.electricity_price = prices['luz']
    
    # Corrientes
    mosto = bst.Stream("MOSTO", 
                       Water=900, Ethanol=100, units="kg/hr", 
                       T=params['t_feed'] + 273.15,
                       price=prices['mosto'])
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15)
    
    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("Mosto_Pre", "Drenaje"))
    W210.outs[0].T = 85+273.15
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=params['t_w220'] + 273.15)
    W220.heat_utilities[0].agent.price = prices['vapor']
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=params['p_v100'])
    
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=params['p_v100'], Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    W310.heat_utilities[0].agent.price = prices['agua'] # Costo enfriamiento
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)
    
    # Simulación Técnica
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    # --- CÁLCULOS ECONÓMICOS MANUALES (Basados en flujos) ---
    prod = sys_bio.flowsheet.stream.Producto_Final
    prod.price = prices['etanol']
    
    total_opex = (mosto.F_mass * mosto.price) + (W220.heat_utilities[0].cost) + (W310.heat_utilities[0].cost) + (P100.power_utility.cost + P200.power_utility.cost)
    ingresos = prod.F_mass * prod.price
    capital_investment = 500000  # Inversión estimada en USD
    utilidad_anual = (ingresos - total_opex) * 8000 # 8000 horas/año
    
    # Indicadores
    roi = (utilidad_anual / capital_investment) * 100
    payback = capital_investment / utilidad_anual if utilidad_anual > 0 else 0
    npv = -capital_investment + (utilidad_anual / 0.1) # Simplificado al 10% tasa
    
    return sys_bio, {
        "ROI": roi, "Payback": payback, "NPV": npv, 
        "CostoProd": total_opex / prod.F_mass if prod.F_mass > 0 else 0,
        "VentaSug": (total_opex / prod.F_mass) * 1.3 if prod.F_mass > 0 else 0
    }

# ==========================================
# INTERFAZ (SIDEBAR - SLIDERS)
# ==========================================
with st.sidebar:
    st.header("🌡️ Parámetros de Proceso")
    t_feed = st.slider("Temp. Alimentación (°C)", 15, 50, 25)
    t_w220 = st.slider("Temp. Salida W220 (°C)", 80, 120, 95)
    p_v100 = st.slider("Presión V100 (Pa)", 50000, 200000, 101325)
    
    st.header("💰 Costos de Mercado")
    p_luz = st.slider("Precio Electricidad (USD/kWh)", 0.05, 0.30, 0.12)
    p_vapor = st.slider("Precio Vapor (USD/kg)", 0.01, 0.10, 0.03)
    p_agua = st.slider("Precio Agua Enfriamiento (USD/m3)", 0.01, 0.05, 0.02)
    p_mosto = st.slider("Precio Mosto (USD/kg)", 0.10, 1.00, 0.40)
    p_etanol = st.slider("Precio Etanol Venta (USD/kg)", 1.00, 5.00, 2.50)

# ==========================================
# EJECUCIÓN Y RESULTADOS
# ==========================================
params = {'t_feed': t_feed, 't_w220': t_w220, 'p_v100': p_v100}
prices = {'luz': p_luz, 'vapor': p_vapor, 'agua': p_agua, 'mosto': p_mosto, 'etanol': p_etanol}

try:
    sistema, econ = run_full_simulation(params, prices)
    prod = sistema.flowsheet.stream.Producto_Final
    
    # 10. RECUADROS DE PRODUCTO FINAL
    st.subheader("📦 Indicadores de Producto Final")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Presión", f"{prod.P/101325:.2f} atm")
    m2.metric("Temperatura", f"{prod.T-273.15:.1f} °C")
    m3.metric("Flujo Másico", f"{prod.F_mass:.2f} kg/h")
    m4.metric("Comp. Etanol", f"{(prod.imass['Ethanol']/prod.F_mass):.1%}" if prod.F_mass > 0 else "0%")

    st.subheader("📈 Análisis Económico")
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Costo Real Prod.", f"${econ['CostoProd']:.2f}/kg")
    e2.metric("Venta Sugerida", f"${econ['VentaSug']:.2f}/kg")
    e3.metric("NPV (VAN)", f"${econ['NPV']:,.0f}")
    e4.metric("Payback", f"{econ['Payback']:.2f} años")
    e5.metric("ROI", f"{econ['ROI']:.1f}%")

    tab_m, tab_pfd, tab_tutor = st.tabs(["📊 Balances Materia/Energía", "📐 Diagrama PFD", "🤖 Tutor Interactivo"])

    with tab_m:
        # 9. TABLA DE BALANCES
        st.subheader("Balances Detallados")
        data = []
        for s in sistema.streams:
            if s.F_mass > 0.01:
                data.append({"ID": s.ID, "T [C]": s.T-273.15, "P [atm]": s.P/101325, "Flujo [kg/h]": s.F_mass, "Entalpía [kJ/h]": s.H})
        st.dataframe(pd.DataFrame(data), use_container_width=True)

    with tab_pfd:
        dot_obj = sistema.diagram(kind='surface', display=False)
        st.graphviz_chart(getattr(dot_obj, 'source', str(dot_obj)))

    with tab_tutor:
        # 14 y 15. VENTANA DE CONTEXTO Y MODO TUTOR
        st.subheader("Chat con Tutor IA de Ingeniería")
        
        # Mostrar mensajes previos
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("Pregunta al tutor sobre los resultados..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                try:
                    # Detectar modelos disponibles
                    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    target = next((m for m in models if "1.5-flash" in m), models[0])
                    model = genai.GenerativeModel(target)
                    
                    contexto = f"""
                    Eres un tutor experto en BioSTEAM. Datos actuales:
                    - ROI: {econ['ROI']}% , NPV: {econ['NPV']}
                    - Costo producción: {econ['CostoProd']} USD/kg
                    - Parámetros: Alimentación {t_feed}C, W220 {t_w220}C, Flash {p_v100}Pa.
                    Responde de forma técnica y breve.
                    """
                    
                    full_prompt = f"{contexto}\nUsuario: {prompt}"
                    response = model.generate_content(full_prompt)
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Error IA: {e}")

except Exception as e:
    st.error(f"Error en Simulación: {e}")
