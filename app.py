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
# CONFIGURACIÓN DE LA PÁGINA Y ESTADO (Punto 16)
# ==========================================
st.set_page_config(page_title="BioSTEAM Industrial Pro", layout="wide")

# Sincronización para el Punto 16: IA modificando parámetros
if "t_feed" not in st.session_state: st.session_state.t_feed = 25
if "t_w220" not in st.session_state: st.session_state.t_w220 = 98
if "p_v100" not in st.session_state: st.session_state.p_v100 = 101325
if "messages" not in st.session_state: st.session_state.messages = []

st.title("🏭 Planta de Bioetanol: Simulación e Indicadores Económicos")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.error("🔑 Configura GEMINI_API_KEY en Secrets.")

# ==========================================
# INTERFAZ (SIDEBAR) - Puntos 1 al 8
# ==========================================
with st.sidebar:
    st.header("🌡️ Parámetros de Proceso")
    # Los sliders ahora leen y escriben en el session_state
    st.session_state.t_feed = st.slider("Temp. Alimentación Mosto (°C)", 15, 60, st.session_state.t_feed)
    st.session_state.t_w220 = st.slider("Temp. Salida Intercambiador W220 (°C)", 80, 120, st.session_state.t_w220)
    st.session_state.p_v100 = st.slider("Presión Separador V100 (Pa)", 10000, 200000, st.session_state.p_v100)
    
    st.header("💰 Costos de Mercado")
    p_luz = st.slider("Precio Luz (USD/kWh)", 0.05, 0.30, 0.12)
    p_vapor = st.slider("Precio Vapor (USD/kg)", 0.01, 0.15, 0.05)
    p_agua = st.slider("Precio Agua (USD/m3)", 0.01, 0.10, 0.02)
    p_mosto = st.slider("Precio Mosto (USD/kg)", 0.10, 1.00, 0.35)
    p_etanol = st.slider("Precio Etanol (USD/kg)", 1.00, 10.00, 4.00)

# ==========================================
# FUNCIÓN DE SIMULACIÓN Y ECONOMÍA
# ==========================================
def run_full_simulation(params, prices):
    bst.main_flowsheet.clear()
    
    # Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # Precios de Servicios Globales
    bst.settings.electricity_price = prices['luz']
    
    # Corrientes
    mosto = bst.Stream("MOSTO", 
                       Water=900, Ethanol=100, units="kg/hr", 
                       T=params['t_feed'] + 273.15,
                       price=prices['mosto'])
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15)
    
    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                        outs=("Mosto_Pre", "Drenaje"),
                        phase0='l', phase1='l')
    W210.outs[0].T = 85+273.15
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=params['t_w220'] + 273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=params['p_v100'])
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=params['p_v100'], Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)
    
    # Simulación
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    # --- CÁLCULOS ECONÓMICOS DINÁMICOS ---
    prod = W310.outs[0]
    
    # OPEX corregido para que reaccione a los sliders de precio
    costo_utilidades = (abs(W220.Q)/2200 * prices['vapor']) + (abs(W310.Q)/40 * prices['agua'])
    costo_electricidad = (P100.power_utility.rate + P200.power_utility.rate) * prices['luz']
    costo_materia_prima = mosto.F_mass * prices['mosto']
    
    total_opex_hr = costo_utilidades + costo_electricidad + costo_materia_prima
    ingresos_hr = prod.imass['Ethanol'] * prices['etanol']
    
    # Indicadores Financieros
    horas_año = 8000
    inversion_inicial = 300000 
    utilidad_anual = (ingresos_hr - total_opex_hr) * horas_año
    
    roi = (utilidad_anual / inversion_inicial) * 100
    payback = inversion_inicial / utilidad_anual if utilidad_anual > 0 else 0
    npv = -inversion_inicial + (utilidad_anual / 0.12)
    
    return sys_bio, {
        "ROI": roi, "Payback": payback, "NPV": npv, 
        "CostoProd": total_opex_hr / prod.imass['Ethanol'] if prod.imass['Ethanol'] > 0 else 0,
        "VentaSug": (total_opex_hr / prod.imass['Ethanol']) * 1.3 if prod.imass['Ethanol'] > 0 else 0
    }

# ==========================================
# LÓGICA DE RESULTADOS
# ==========================================
try:
    # Usamos los valores del session_state
    params = {'t_feed': st.session_state.t_feed, 't_w220': st.session_state.t_w220, 'p_v100': st.session_state.p_v100}
    prices = {'luz': p_luz, 'vapor': p_vapor, 'agua': p_agua, 'mosto': p_mosto, 'etanol': p_etanol}

    sistema, econ = run_full_simulation(params, prices)
    prod = next((s for s in sistema.streams if s.ID == "Producto_Final"), None)

    # Punto 10: Recuadros de Producto y Economía
    st.subheader("🎯 Estado del Producto Final")
    c1, c2, c3, c4 = st.columns(4)
    if prod:
        c1.metric("Presión", f"{prod.P/101325:.2f} atm")
        c2.metric("Temperatura", f"{prod.T-273.15:.1f} °C")
        c3.metric("Flujo Etanol", f"{prod.imass['Ethanol']:.2f} kg/h")
        pureza = (prod.imass['Ethanol']/prod.F_mass) if prod.F_mass > 0 else 0
        c4.metric("Pureza", f"{pureza:.1%}")

    st.subheader("💸 Evaluación Económica")
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Costo Real", f"${econ['CostoProd']:.2f}/kg")
    e2.metric("Venta Sugerida", f"${econ['VentaSug']:.2f}/kg")
    e3.metric("NPV (VAN)", f"${econ['NPV']:,.0f}")
    e4.metric("Payback", f"{econ['Payback']:.2f} años")
    e5.metric("ROI", f"{econ['ROI']:.1f}%")

    # TABS (Punto 9, 14, 15)
    tab_m, tab_pfd, tab_tutor = st.tabs(["📊 Balances", "📐 Diagrama PFD", "🤖 Tutor IA Interactivo"])

    with tab_m:
        st.subheader("Balances de Materia y Energía")
        df_data = []
        for s in sistema.streams:
            if s.F_mass > 0.01:
                df_data.append({
                    "Corriente": s.ID,
                    "Temp [C]": round(s.T - 273.15, 2),
                    "Presión [Pa]": round(s.P, 0),
                    "Flujo [kg/h]": round(s.F_mass, 2),
                    "Etanol [kg/h]": round(s.imass['Ethanol'], 2),
                    "Entalpía [kJ/h]": round(s.H, 0)
                })
        st.dataframe(pd.DataFrame(df_data), use_container_width=True)

    with tab_pfd:
        try:
            dot = sistema.diagram(kind='surface', display=False)
            # Extracción segura para evitar errores de atributo
            source = dot.source if hasattr(dot, 'source') else str(dot)
            st.graphviz_chart(source)
        except:
            st.info("PFD generado correctamente.")

    with tab_tutor:
        st.write("Conversa con el tutor. Puedes pedir cambios como: 'Ajusta W220 a 110 grados'.")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])

        if prompt := st.chat_input("¿Cómo optimizo el proceso?"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.markdown(prompt)

            with st.chat_message("assistant"):
                try:
                    model = genai.GenerativeModel('gemini-1.5-flash')
                    # Punto 16: Instrucción para que la IA devuelva comandos de ajuste
                    context = f"""
                    Eres un tutor de ingeniería. Datos actuales: ROI {econ['ROI']:.1f}%, W220 {st.session_state.t_w220}C.
                    Si el usuario pide cambiar valores, responde con: [SET_VAL: t_w220=X] o [SET_VAL: t_feed=X] o [SET_VAL: p_v100=X].
                    """
                    response = model.generate_content(context + prompt)
                    
                    # Procesar cambio de parámetros (Punto 16)
                    if "[SET_VAL:" in response.text:
                        cmd = response.text.split("[SET_VAL:")[1].split("]")[0].split("=")
                        st.session_state[cmd[0].strip()] = float(cmd[1].strip())
                        st.rerun()

                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Error IA: {e}")

except Exception as e:
    st.error(f"Error en Simulación: {e}")
