import streamlit as st
import sys
import json

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

if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================
# INTERFAZ (SIDEBAR) - LÍMITES AMPLIADOS
# ==========================================
with st.sidebar:
    st.header("🌡️ Parámetros de Proceso")
    t_feed = st.slider("Temp. Alimentación Mosto (°C)", 15, 60, 25)
    t_w220 = st.slider("Temp. Salida Intercambiador W220 (°C)", 80, 120, 98)
    p_v100 = st.slider("Presión Separador V100 (Pa)", 10000, 200000, 101325)
    
    st.header("💰 Costos de Mercado (Límites Pro)")
    p_luz = st.slider("Precio Luz (USD/kWh)", 0.01, 0.50, 0.12)
    p_vapor = st.slider("Precio Vapor (USD/kg)", 0.00, 0.20, 0.03)
    p_agua = st.slider("Precio Agua (USD/m3)", 0.00, 0.20, 0.02)
    p_mosto = st.slider("Precio Mosto (USD/kg)", 0.01, 2.00, 0.35)
    p_etanol = st.slider("Precio Etanol (USD/kg)", 0.50, 15.00, 5.00)

# ==========================================
# FUNCIÓN DE SIMULACIÓN Y ECONOMÍA (CORREGIDA)
# ==========================================
def run_full_simulation(params, prices):
    bst.main_flowsheet.clear()
    
    # Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # Precios de Servicios Globales
    bst.settings.electricity_price = prices['luz']
    
    # Corrientes
    mosto = bst.Stream("MOOSTO", 
                       Water=900, Ethanol=100, units="kg/hr", 
                       T=params['t_feed'] + 273.15,
                       phase='l', 
                       price=prices['mosto'])
    
    # Corriente auxiliar para definir las propiedades térmicas del retorno inicial
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15, phase='l')
    
    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    # Se añade rigurosidad termodinámica a las corrientes de fase del intercambiador de proceso
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                        outs=("Mosto_Pre", "Drenaje"),
                        phase0='l', phase1='l')
    
    W210.T = 85 + 273.15 
    
    # W220 - Calentador por utilidad activa (Sin el argumento 'phase')
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=params['t_w220'] + 273.15)
    
    # V100 - Válvula de estrangulamiento isentálpica
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=params['p_v100'])
    
    # V1 - Separador Flash con balances químicos acoplados (Q=0 adiabático)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=params['p_v100'], Q=0)
    
    # W310 - Condensador total utilizando especificación de fracción de vapor V=0 (fuerza fase líquida)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15, V=0)
    
    # P200 - Bomba de descarga de los fondos (vinazas) hacia el acople cruzado
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)
    
    # Simulación convergente por secuencia de cálculo explícita
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    # --- CÁLCULOS ECONÓMICOS ---
    prod = W310.outs[0]
    etanol_puro_hr = prod.imass['Ethanol']
    
    # Estimación de costos operativos (OPEX) vinculada a los sliders
    costo_utilidades = (abs(W220.Q)/2200 * prices['vapor']) + (abs(W310.Q)/40 * prices['agua'])
    costo_electricidad = (P100.power_utility.rate + P200.power_utility.rate) * prices['luz']
    costo_materia_prima = mosto.F_mass * prices['mosto']
    
    total_opex_hr = costo_utilidades + costo_electricidad + costo_materia_prima
    ingresos_hr = etanol_puro_hr * prices['etanol']
    
    # Indicadores Financieros (Anuales)
    horas_año = 8000
    inversion_inicial = 200000 
    utilidad_anual = (ingresos_hr - total_opex_hr) * horas_año
    
    roi = (utilidad_anual / inversion_inicial) * 100
    payback = inversion_inicial / utilidad_anual if utilidad_anual > 0 else 999
    npv = -inversion_inicial + (utilidad_anual / 0.12)
    
    return sys_bio, {
        "ROI": roi, "Payback": payback, "NPV": npv, 
        "CostoProd": total_opex_hr / etanol_puro_hr if etanol_puro_hr > 0 else 0,
        "VentaSug": (total_opex_hr / etanol_puro_hr) * 1.30 if etanol_puro_hr > 0 else 0
    }

# ==========================================
# EXTRACCIÓN DE DATOS PARA PFD DINÁMICO
# ==========================================
def obtener_datos_unidades(sistema):
    data_map = {}
    for unit in sistema.units:
        corriente_salida = unit.outs[0] if unit.outs else None
        data_map[unit.ID] = {
            "T": f"{corriente_salida.T - 273.15:.1f} °C" if corriente_salida else "N/A",
            "P": f"{corriente_salida.P / 101325:.2f} atm" if corriente_salida else "N/A",
            "Flow": f"{corriente_salida.F_mass:.1f} kg/h" if corriente_salida else "N/A",
            "Duty": f"{abs(unit.Q)/1000:.1f} kW" if hasattr(unit, 'Q') else "N/A"
        }
    return json.dumps(data_map)

# ==========================================
# LÓGICA DE RESULTADOS Y RENDERIZADO
# ==========================================
try:
    params = {'t_feed': t_feed, 't_w220': t_w220, 'p_v100': p_v100}
    prices = {'luz': p_luz, 'vapor': p_vapor, 'agua': p_agua, 'mosto': p_mosto, 'etanol': p_etanol}

    sistema, econ = run_full_simulation(params, prices)
    prod = next((s for s in sistema.streams if s.ID == "Producto_Final"), None)

    # RECUADROS DE PRODUCTO FINAL
    st.subheader("🎯 Estado del Producto Final")
    c1, c2, c3, c4 = st.columns(4)
    if prod:
        c1.metric("Presión", f"{prod.P/101325:.2f} atm")
        c2.metric("Temperatura", f"{prod.T-273.15:.1f} °C")
        c3.metric("Etanol Puro", f"{prod.imass['Ethanol']:.2f} kg/h")
        pureza = (prod.imass['Ethanol']/prod.F_mass) if prod.F_mass > 0 else 0
        c4.metric("Pureza Etanol", f"{pureza:.1%}")

    # RECUADROS ECONÓMICOS
    st.subheader("💸 Evaluación Económica")
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Costo Real", f"${econ['CostoProd']:.2f}/kg")
    e2.metric("Venta Sugerida (30% mrg)", f"${econ['VentaSug']:.2f}/kg")
    e3.metric("NPV (VAN)", f"${econ['NPV']:,.0f}")
    
    pb_display = f"{econ['Payback']:.1f} años" if econ['Payback'] < 50 else "No Rentable"
    e4.metric("Payback", pb_display)
    e5.metric("ROI Anual", f"{econ['ROI']:.1f}%")

    # TABS
    tab_m, tab_pfd, tab_interactivo, tab_tutor = st.tabs([
        "📊 Balances", "📐 Diagrama BioSTEAM", "🎯 PFD Interactivo Real", "🤖 Tutor IA Interactivo"
    ])

    with tab_m:
        st.subheader("Tabla de Balances de Materia y Energía")
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
            source = dot.source if hasattr(dot, 'source') else str(dot)
            st.graphviz_chart(source)
        except:
            st.info("Diagrama BioSTEAM base generado.")

    with tab_interactivo:
        st.subheader("Visualización sobre Plano SVG de Planta")
        st.info("Pasa el mouse sobre las unidades (Bomba P100, Intercambiador W210, Separador V1, etc.) para ver datos en vivo.")
        
        json_data_sim = obtener_datos_unidades(sistema)
        
        html_pfd_interactivo = f"""
        <div id="contenedor-pfd" style="position: relative; display: inline-block; background: #ffffff; padding: 15px; border-radius: 8px; width: 100%; overflow: auto;">
            
            <div id="pfd-tooltip" style="position: absolute; display: none; background: rgba(20, 26, 36, 0.96); color: #ffffff; padding: 12px; border-radius: 6px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 13px; z-index: 9999; pointer-events: none; border: 1px solid #4FA8FF; box-shadow: 0px 4px 20px rgba(0,0,0,0.4); min-width: 190px;">
            </div>

            <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 2161 1626.32" width="100%" height="auto">
                <style>
                    .equipo-nodo {{
                        cursor: pointer;
                        fill: rgba(79, 168, 255, 0);
                        stroke: transparent;
                        transition: all 0.2s ease-in-out;
                    }}
                    .equipo-nodo:hover {{
                        fill: rgba(79, 168, 255, 0.25);
                        stroke: #007BFF;
                        stroke-width: 3px;
                    }}
                </style>
                
                <g transform="translate(160.5 61)">
                    <path d="M115.2 91.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76L165.4 88.1a.78.78 0 0 0-.82-.28l-.5.13" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M103 41.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M125.4 49.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7M900 780c0 22.08-17.92 40-40 40s-40-17.92-40-40 17.92-40 40-40 40 17.92 40 780z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M822.56 765.28h60.56l-36.16 15.12 36.16 15.2-60.56-.88" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M1080 860v120c0 11.05 17.9 20 40 20s40-8.95 40-20V860c0-11.05-17.9-20-40-20s-40 8.95-40 20zM1515.16 1130c0 23.66-17.64 42.84-39.24 42.84-21.72 0-39.36-19.18-39.36-42.84 0-23.66 17.64-42.84 39.36-42.84 21.6 0 39.24 19.18 39.24 42.84z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M1531.72 1069.1L1420 1200" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M1540 1060l-5.28 19.32-12.12-12.32z" stroke="#000" stroke-width="2"/>
                    <path d="M1436.56 1132.24h8.28l12.36-22.54 29.04 45.08 20.64-22.54h8.28" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M-159.5 60h84.6M-159.5 60h-.5" stroke="#3a414a" fill="none"/>
                    <path d="M-60.14 60l-14.26 4.64v-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M-.98 60h85.55" stroke="#3a414a" fill="none"/>
                    <path d="M-.97 60.48h-.55l.04-.48-.04-.48h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M99.33 60l-14.27 4.64v-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1675.2 1351.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76l-13.08-16.65a.78.78 0 0 0-.82-.28l-.5.13" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M1663 1301.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M1685.4 1309.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M-139.5 765.28h5.02m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.03m5.03 0h10.04m5.02 0h10.03m5.02 0H-14m5 0H1.06m5.02 0H16.1m5.03 0h10.04m5.02 0h10.03m5.02 0H61.3m5 0h10.05m5.02 0H91.4m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.03m5.03 0H242m5 0h10.05m5.02 0h10.04m5.03 0h10.04m5.02 0h10.03m5.02 0h10.04m5 0h10.05m5.02 0h10.04m5.03 0h10.04m5.02 0h10.02m5.03 0h10.03m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0H498m5.02 0h10.04m5.02 0h10.03m5.03 0h10.04m5.02 0h10.03m5.02 0h10.04m5 0h10.05m5.02 0h10.04m5.03 0h10.04m5.02 0h10.03m5.02 0h10.04m5 0h10.05m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0H754m5.02 0h10.04m5.02 0h10.04m5.02 0h10.04m5.02 0h5.02M-139.5 765.28h-.5" stroke="#3a414a" fill="none"/>
                    <path d="M823.98 765.28l-14.26 4.63v-9.25z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M825.1 794.72h-5.02m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H774.9m-5.02 0h-10.04m-5 0h-10.06m-5 0h-10.05m-5.02 0h-10.03m-5.02 0H699.6m-5 0h-10.05m-5.02 0H669.5m-5.03 0h-10.04m-5.02 0h-10.03m-5.02 0H624.3m-5 0h-10.04m-5.02 0H594.2m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H518.9m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.03m-5.03 0H443.6m-5 0h-10.05m-5.02 0H413.5m-5.03 0h-10.04m-5.02 0h-10.03m-5.02 0H368.3m-5 0h-10.05m-5.02 0H338.2m-5.03 0h-10.04m-5.02 0h-10.03m-5 0H293m-5.02 0h-10.04m-5.02 0H262.9m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H187.6m-5.02 0h-10.04m-5.02 0H157.5m-5.03 0h-10.04m-5.02 0h-10.03m-5.02 0H112.3m-5 0H97.24m-5.02 0H82.2m-5.03 0H67.13m-5.02 0H52.08m-5.02 0H37m-5 0H21.94m-5.02 0H6.9m-5.03 0H-8.15m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H-68.4m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-5.02M825.1 794.72h3.5" stroke="#3a414a" fill="none"/>
                    <path d="M-138.38 794.72l14.26-4.63v9.25z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M-34.24 34.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#a" transform="matrix(1,0,0,1,-55,35) translate(20.48568576388889 33.24652777777778)"/>
                    <path d="M345.76 244.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#b" transform="matrix(1,0,0,1,325,245) translate(18.228741319444445 33.24652777777778)"/>
                    <path d="M179.05 65H344a6 6 0 0 1 6 6v154.1" stroke="#3a414a" fill="none"/>
                    <path d="M179.06 65.47h-.6l.06-.3.08-.64h.46z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M350 239.86l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M350 299.02V534a6 6 0 0 0 6 6h126.62" stroke="#3a414a" fill="none"/>
                    <path d="M350 298.52l.48-.04v.55h-.96v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M497.38 540l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M506 500a6 6 0 0 0-6 6v68a6 6 0 0 0 6 6h188a6 6 0 0 0 6-6v-68a6 6 0 0 0-6-6zM520 500v80m160-80v80m-160-60h160m-160 20h160m-160 20h160M855.76 514.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#c" transform="matrix(1,0,0,1,835,515) translate(18.14193576388889 33.24652777777778)"/>
                    <path d="M701.5 540h113.6" stroke="#3a414a" fill="none"/>
                    <path d="M701.5 540.48h-.5v-.96h.5z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M829.86 540l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M860 569.02v153.6" stroke="#3a414a" fill="none"/>
                    <path d="M860 568.52l.48-.04v.55h-.96v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M860 737.38l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M595.76 894.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#d" transform="matrix(1,0,0,1,575,895) translate(17.827265625000003 33.24652777777778)"/>
                    <path d="M600 581.5v293.6" stroke="#3a414a" fill="none"/>
                    <path d="M600.48 581.5h-.96v-.5h.96z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M600 889.86l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M600 949.02V1554a6 6 0 0 0 6 6h1377.62" stroke="#3a414a" fill="none"/>
                    <path d="M1998.38 1554l-14.26 4.63v-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1005.76 894.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#e" transform="matrix(1,0,0,1,985,895) translate(18.114809027777778 33.24652777777778)"/>
                    <path d="M1039.02 920h23.6" stroke="#3a414a" fill="none"/>
                    <path d="M1039.03 920.48h-.55l.04-.48-.04-.48h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1077.38 920l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M855.76 894.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#f" transform="matrix(1,0,0,1,835,895) translate(18.25044270833333 33.24652777777778)"/>
                    <path d="M920 906a6 6 0 0 1 6-6h28a6 6 0 0 1 6 6v28a6 6 0 0 1-6 6h-28a6 6 0 0 1-6-6z" fill="none"/>
                    <path d="M924 910v20l15-10zm15 10h2zm2 0l15-10v20zM940 920v-12m-8 0c0-2.2 3.58-4 8-4s8 1.8 8 4z" stroke="#000" fill="#fff"/>
                    <path d="M957 920h8.1M957 920h-3.5" stroke="#3a414a" fill="none"/>
                    <path d="M979.86 920l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M889.02 920h18.1" stroke="#3a414a" fill="none"/>
                    <path d="M889.03 920.48h-.55l.04-.48-.04-.48h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M921.88 920l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M860 821.5v53.6" stroke="#3a414a" fill="none"/>
                    <path d="M860 821l.48-.02v.53h-.96v-.53z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M860 889.86l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1472.12 784.24a6 6 0 0 1 8.5 0l21.5 21.52a6 6 0 0 1 0 8.48l-21.5 21.52a6 6 0 0 1-8.5 0l-21.5-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#g" transform="matrix(1,0,0,1,1451.365516096675,785) translate(18.717022569444445 33.24652777777778)"/>
                    <path d="M1120 838.5V816a6 6 0 0 1 6-6h305.46" stroke="#3a414a" fill="none"/>
                    <path d="M1120.47 839.02l-.48-.02-.47.03v-.54h.94z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1446.23 810l-14.27 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1476.37 839.02v230.78" stroke="#3a414a" fill="none"/>
                    <path d="M1476.37 838.52l.47-.04v.55h-.95v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1476.37 1084.57l-4.64-14.27h9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1115.76 1288.36a6 6 0 0 1 8.48 0l21.52 21.5a6 6 0 0 1 0 8.5l-21.52 21.5a6 6 0 0 1-8.48 0l-21.52-21.5a6 6 0 0 1 0-8.5z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#h" transform="matrix(1,0,0,1,1095,1289.1141187915582) translate(18.131085069444445 33.24652777777778)"/>
                    <path d="M1120 1001.5v267.7" stroke="#3a414a" fill="none"/>
                    <path d="M1120.47 1001.5h-.94v-.52l.48.02.47-.03z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1120 1283.98l-4.63-14.27h9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1149.02 1314.1h496.2" stroke="#3a414a" fill="none"/>
                    <path d="M1149.03 1314.6h-.55l.04-.5-.04-.46h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1660 1314.1l-14.27 4.65v-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1475.76 1348.36a6 6 0 0 1 8.48 0l21.52 21.5a6 6 0 0 1 0 8.5l-21.52 21.5a6 6 0 0 1-8.48 0l-21.52-21.5a6 6 0 0 1 0-8.5z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#i" transform="matrix(1,0,0,1,1455,1349.1141187915582) translate(18.114809027777778 33.24652777777778)"/>
                    <path d="M1480 1174.05v155.16" stroke="#3a414a" fill="none"/>
                    <path d="M1480.47 1174.07h-.94v-.46l.94-.1z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1480 1343.98l-4.63-14.27h9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1480 1403.13V1474a6 6 0 0 0 6 6h497.62" stroke="#3a414a" fill="none"/>
                    <path d="M1480 1402.63l.47-.04v.55h-.94v-.56z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1998.38 1480l-14.26 4.63v-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1805.76 764.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#j" transform="matrix(1,0,0,1,1785,765) translate(13.47613715277778 33.24652777777778)"/>
                    <path d="M1761.45 1297.5H1804a6 6 0 0 0 6-6V834.9" stroke="#3a414a" fill="none"/>
                    <path d="M1761.46 1297.96h-.5v-.95h.5z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1810 820.14l4.63 14.26h-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1810 760.98V465a6 6 0 0 0-6-6H606a6 6 0 0 0-6 6v17.62" stroke="#3a414a" fill="none"/>
                    <path d="M1810.47 761.52l-.47-.04-.47.04v-.55h.94z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M600 497.38l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M170-40c0-11.05-8.95-20-20-20s-20 8.95-20 20 8.95 20 20 20 20-8.95 20-20z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#k" transform="matrix(1,0,0,1,134,-56) translate(5.963107638888889 24.08376736111111)"/>
                    <path d="M150-18.5v1.6m0 2.1v3.17m0 2.12v3.16m0 2.12v3.17m0 2.1v1.6" stroke="#3a414a" fill="none"/>
                    <path d="M150.47-18.5h-.94V-19l.5.02.44-.05z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M150 17.4l-4.63-14.25h9.26z" stroke="#3a414a" fill="#3a414a"/>
                    <path d="M1730 1220c0-11.05-8.95-20-20-20s-20 8.95-20 20 8.95 20 20 20 20-8.95 20-20z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <use xlink:href="#k" transform="matrix(1,0,0,1,1694,1204) translate(5.963107638888889 24.08376736111111)"/>
                    <path d="M1710 1241.5v1.6m0 2.1v3.17m0 2.12v3.16m0 2.12v3.17m0 2.1v1.6" stroke="#3a414a" fill="none"/>
                    <path d="M1710.47 1241.5h-.94v-.52l.52.02.42-.05z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/>
                    <path d="M1710 1277.4l-4.63-14.25h9.27z" stroke="#3a414a" fill="#3a414a"/>
                    
                    <defs>
                        <path d="M653-1490V0H466v-1314h-10L96-1047v-204l324-239h233" id="l"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#l" id="a"/>
                        <path d="M154 0v-137l495-537c165-179 249-281 249-418 0-156-121-253-280-253-170 0-278 110-278 278H158c0-264 200-443 465-443 266 0 455 183 455 416 0 161-73 288-336 568L416-179v12h687V0H154" id="m"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#m" id="b"/>
                        <path d="M635 20c-292 0-500-160-510-396h192c11 142 145 229 315 229 187 0 323-105 323-260 0-161-125-274-346-274H488v-165h121c174 0 294-100 294-254 0-148-104-245-266-245-152 0-291 85-297 230H157c8-234 222-395 484-395 278 0 448 188 448 400 0 168-95 291-247 336v12c190 31 301 169 301 357 0 244-216 425-508 425" id="n"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#n" id="c"/>
                        <path d="M120-303v-155l652-1032h231v1020h202v167h-202V0H821v-303H120zm702-167v-782h-12L323-482v12h499" id="o"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#o" id="d"/>
                        <path d="M646 20c-249 0-524-159-524-708 0-524 209-822 547-822 250 0 431 161 467 395H950c-33-129-126-227-281-227-229 0-367 208-367 566h12c80-124 212-198 367-198 255 0 467 205 467 493 0 278-198 501-502 501zm0-167c179 0 318-148 318-334 0-182-133-328-313-328-181 0-322 156-322 330 0 176 134 332 317 332" id="p"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#p" id="e"/>
                        <path d="M626 20c-262 0-458-168-468-396h184c12 133 134 229 284 229 180 0 311-137 311-326 0-192-136-335-323-335-92 0-196 33-255 78l-178-22 88-738h784v167H429l-51 435h8c61-51 160-87 263-87 273 0 474 211 474 499 0 286-210 496-497 496" id="q"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#q" id="f"/>
                        <path d="M200 0l662-1311v-12H98v-167h963v177L400 0H200" id="r"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#r" id="g"/>
                        <path d="M633 20c-303 0-511-173-511-416 0-188 124-348 291-378v-8c-145-37-237-174-237-332 0-227 192-396 457-396 261 0 456 169 456 396 0 158-94 295-235 332v8c162 30 291 190 291 378 0 243-212 416-512 416zm0-165c197 0 322-103 322-261 0-165-138-283-322-283-188 0-324 118-324 283 0 158 123 261 324 261zm0-703c157 0 272-101 272-252 0-149-110-246-272-246-165 0-273 97-273 246 0 151 112 252 273 252" id="s"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#s" id="h"/>
                        <path d="M603 20c-253 0-434-161-469-399h188c31 132 124 231 281 231 227 0 367-207 367-568h-12c-80 122-211 199-367 199-257 0-469-206-469-493 0-278 200-505 506-500 245 4 520 158 520 706 0 527-208 824-545 824zm16-703c181 0 324-157 324-332 0-171-135-328-318-328-180 0-317 148-317 332 0 183 131 328 311 328" id="t"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#t" id="i"/>
                        <path d="M646 20c-332 0-524-278-524-764 0-483 194-766 524-766s524 283 524 766c0 485-191 764-524 764zm0-166c218 0 341-220 341-598 0-380-123-601-341-601s-341 222-341 601c0 378 123 598 341 598" id="u"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#l" id="j"/>
                        <path d="M180 0v-1490h270l367 940c28 72 75 218 110 339 35-117 81-264 110-339l362-940h271V0h-187c2-448-5-837 7-1287-157 497-311 829-483 1287H842C666-458 514-784 354-1284c12 438 5 843 7 1284H180" id="v"/>
                        <use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#v" id="k"/>
                    </defs>

                    <rect id="P100" class="equipo-nodo" x="90" y="30" width="100" height="90" onmouseover="mostrarTooltip(evt, 'P100')" onmouseout="ocultarTooltip()"/>
                    
                    <rect id="W210" class="equipo-nodo" x="500" y="490" width="200" height="100" onmouseover="mostrarTooltip(evt, 'W210')" onmouseout="ocultarTooltip()"/>
                    
                    <circle id="W220" class="equipo-nodo" cx="860" cy="780" r="45" onmouseover="mostrarTooltip(evt, 'W220')" onmouseout="ocultarTooltip()"/>
                    
                    <rect id="V100" class="equipo-nodo" x="915" y="900" width="50" height="50" onmouseover="mostrarTooltip(evt, 'V100')" onmouseout="ocultarTooltip()"/>
                    
                    <rect id="V1" class="equipo-nodo" x="1070" y="830" width="100" height="180" onmouseover="mostrarTooltip(evt, 'V1')" onmouseout="ocultarTooltip()"/>
                    
                    <rect id="W310" class="equipo-nodo" x="1410" y="1080" width="130" height="100" onmouseover="mostrarTooltip(evt, 'W310')" onmouseout="ocultarTooltip()"/>
                    
                    <rect id="P200" class="equipo-nodo" x="1650" y="1290" width="110" height="90" onmouseover="mostrarTooltip(evt, 'P200')" onmouseout="ocultarTooltip()"/>
                </g>
            </svg>
            
        </div>

        <script>
            const datosSimulacion = {json_data_sim};
            const tooltipElement = document.getElementById('pfd-tooltip');

            function mostrarTooltip(evt, idEquipo) {{
                const datos = datosSimulacion[idEquipo];
                if (!datos) return;

                tooltipElement.style.display = 'block';
                tooltipElement.innerHTML = `
                    <div style="border-bottom: 1px solid #4FA8FF; padding-bottom: 4px; margin-bottom: 6px; font-weight: bold; color: #4FA8FF;">
                        ⚙️ Equipo: ${{idEquipo}}
                    </div>
                    <table style="width: 100%; font-size: 12px; border-collapse: collapse;">
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Temperatura:</td><td style="text-align: right; font-weight: 500;">${{datos.T}}</td></tr>
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Presión:</td><td style="text-align: right; font-weight: 500;">${{datos.P}}</td></tr>
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Flujo Másico:</td><td style="text-align: right; font-weight: 500;">${{datos.Flow}}</td></tr>
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Carga Térmica (Q):</td><td style="text-align: right; font-weight: 500;">${{datos.Duty}}</td></tr>
                    </table>
                `;
                
                const rectContenedor = document.getElementById('contenedor-pfd').getBoundingClientRect();
                tooltipElement.style.left = (evt.clientX - rectContenedor.left + 20) + 'px';
                tooltipElement.style.top = (evt.clientY - rectContenedor.top + 20) + 'px';
            }}

            function ocultarTooltip() {{
                tooltipElement.style.display = 'none';
            }}
        </script>
        """
        st.components.v1.html(html_pfd_interactivo, height=750, scrolling=True)

    with tab_tutor:
        st.write("Conversa con el tutor sobre el proceso, costos o indicadores.")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input("Ej: ¿Cómo optimizo la pureza de etanol en el Flash?"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                try:
                    model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    target_model = next((m for m in model_list if "flash" in m), model_list[0])
                    model = genai.GenerativeModel(target_model)
                    
                    context = f"""
                    Eres un ingeniero experto en plantas de proceso y simulación en BioSTEAM. Contexto técnico actual:
                    - ROI: {econ['ROI']:.2f}%, NPV: {econ['NPV']:.2f} USD
                    - Costo producción calculado: {econ['CostoProd']:.2f} USD/kg. Precio Venta Etanol: {p_etanol} USD/kg.
                    - Parámetros operativos actuales: Temperatura de alimento Mosto {t_feed}°C, Salida de calentador W220 {t_w220}°C, Presión del separador Flash {p_v100} Pa.
                    Responde de forma concisa, profesional e ingenieril.
                    """
                    response = model.generate_content(f"{context}\nUsuario pregunta: {prompt}")
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Error IA: {e}")

except Exception as e:
    st.error(f"Error en Simulación: {e}")
