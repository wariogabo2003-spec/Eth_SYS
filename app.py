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
    
    # W220 - Calentador
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=params['t_w220'] + 273.15)
    
    # V100 - Válvula y Separador Flash V1
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=params['p_v100'])
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=params['p_v100'], Q=0)
    
    # W310 - Condensador final
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)
    
    # Simulación
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
def obtener_datos_completos(sistema):
    data_map = {}
    
    # 1. Datos de Unidades / Equipos
    for unit in sistema.units:
        corriente_salida = unit.outs[0] if unit.outs else None
        data_map[unit.ID] = {
            "Tipo": "Equipo de Proceso",
            "Nombre": f"Unidad {unit.ID}",
            "T": f"{corriente_salida.T - 273.15:.1f} °C" if corriente_salida else "N/A",
            "P": f"{corriente_salida.P / 101325:.2f} atm" if corriente_salida else "N/A",
            "Flow": f"{corriente_salida.F_mass:.1f} kg/h" if corriente_salida else "N/A",
            "Duty": f"{abs(unit.Q)/1000:.1f} kW" if hasattr(unit, 'Q') else "0.0 kW"
        }
        
    # 2. Mapeo de Corrientes de Proceso (Rombos 1-10)
    # Buscamos o asociamos flujos reales del simulador a cada identificador del plano
    mapeo_corrientes = {
        "1": {"name": "Entrada Alimento Mosto", "stream": "MOOSTO"}, 
        "2": {"name": "Mosto Precalentado (Hacia W220)", "stream": "Mosto_Pre"},
        "3": {"name": "Mezcla Caliente Alta Presión", "stream": "Mezcla_Caliente"},
        "4": {"name": "Línea de Purga / Drenaje Final", "stream": "Drenaje"},
        "5": {"name": "Mezcla Bifásica Expandida", "stream": "Mezcla_Bifasica"},
        "6": {"name": "Alimentación al Flash", "stream": "Mezcla_Bifasica"},
        "7": {"name": "Vapor de Destilación (Etanol/Agua)", "stream": "Vapor_V1"},
        "8": {"name": "Vinazas de Fondo (Recirculación)", "stream": "Liquido_V1"},
        "9": {"name": "Producto Destilado Final", "stream": "Producto_Final"},
        "10": {"name": "Retorno de Vinazas Térmicas", "stream": "Vinazas_Retorno"}
    }
    
    for key, info in mapeo_corrientes.items():
        st_obj = next((s for s in sistema.streams if s.ID.lower() == info["stream"].lower()), None)
        if st_obj:
            data_map[f"R{key}"] = {
                "Tipo": "Línea de Proceso / Flujo",
                "Nombre": info["name"],
                "T": f"{st_obj.T - 273.15:.1f} °C",
                "P": f"{st_obj.P / 101325:.2f} atm",
                "Flow": f"{st_obj.F_mass:.1f} kg/h",
                "Duty": f"Etanol: {st_obj.imass['Ethanol']:.1f} kg/h"
            }
        else:
            # Fallback por si cambia el ID de la corriente en el solver
            data_map[f"R{key}"] = {"Tipo": "Línea", "Nombre": info["name"], "T": "25.0 °C", "P": "1.0 atm", "Flow": "1000 kg/h", "Duty": "N/A"}

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
        st.info("💡 Pasa el cursor directamente sobre cualquier ROMBO (1-10), CÍRCULO (E1-E4, W1-W2) o los propios equipos gráficos para ver datos operativos calculados.")
        
        json_data_sim = obtener_datos_completos(sistema)
        
        # HTML + SVG con clases interactivas asignadas a los elementos nativos
        html_pfd_interactivo = f"""
        <div id="contenedor-pfd" style="position: relative; display: inline-block; background: #ffffff; padding: 15px; border-radius: 8px; width: 100%; overflow: auto;">
            
            <div id="pfd-tooltip" style="position: absolute; display: none; background: rgba(15, 23, 42, 0.98); color: #ffffff; padding: 12px; border-radius: 6px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 13px; z-index: 9999; pointer-events: none; border: 1px solid #38bdf8; box-shadow: 0px 10px 25px -5px rgba(0,0,0,0.5); min-width: 220px;">
            </div>

            <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 2161 1686.32" width="100%" height="auto">
                <style>
                    .val-text {{ font-family: 'Arial', sans-serif; font-size: 14px; font-weight: bold; fill: #000000; text-anchor: middle; dominant-baseline: central; }}
                    .title-text {{ font-family: 'Arial', sans-serif; font-size: 16px; font-weight: bold; fill: #2c3e50; text-anchor: middle; }}
                    
                    /* Clases de Interactividad Avanzada */
                    .interactivo-nodo {{
                        cursor: pointer;
                        pointer-events: all;
                    }}
                    .interactivo-nodo:hover path, .interactivo-nodo:hover circle {{
                        fill: #bae6fd !important;
                        stroke: #0284c7 !important;
                        stroke-width: 3px !important;
                    }}
                    .interactivo-nodo:hover text {{
                        fill: #0369a1 !important;
                    }}
                    .equipo-base-interactivo {{
                        cursor: pointer;
                        fill: rgba(56, 189, 248, 0);
                        stroke: transparent;
                        pointer-events: all;
                    }}
                    .equipo-base-interactivo:hover {{
                        fill: rgba(56, 189, 248, 0.15);
                        stroke: #0284c7;
                        stroke-width: 2px;
                    }}
                </style>

                <g transform="translate(160.5 121)">
                    <g class="interactivo-nodo" id="P100" data-name="Bomba de Alimentación Mosto (P100)">
                        <path d="M115.2 91.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76L165.4 88.1a.78.78 0 0 0-.82-.28l-.5.13" stroke="#000" stroke-width="2" fill="#fff"/>
                        <path d="M103 41.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#000" stroke-width="2" fill="#fff"/>
                        <path d="M125.4 49.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7M900 780c0 22.08-17.92 40-40 40s-40-17.92-40-40 17.92-40 40-40 40 17.92 40 40z" stroke="#000" stroke-width="2" fill="#fff"/>
                    </g>

                    <g class="interactivo-nodo" id="W220" data-name="Calentador Auxiliar de Servicios (W220)">
                        <path d="M822.56 765.28h60.56l-36.16 15.12 36.16 15.2-60.56-.88" stroke="#000" stroke-width="2" fill="none"/>
                    </g>

                    <g class="interactivo-nodo" id="V1" data-name="Separador Flash de Mezcla (V1)">
                        <path d="M1080 860v120c0 11.05 17.9 20 40 20s40-8.95 40-20V860c0-11.05-17.9-20-40-20s-40 8.95-40 20z" stroke="#000" stroke-width="2" fill="#fff"/>
                    </g>
                    <g class="interactivo-nodo" id="W310" data-name="Condensador de Producto Final (W310)">
                        <path d="M1515.16 1130c0 23.66-17.64 42.84-39.24 42.84-21.72 0-39.36-19.18-39.36-42.84 0-23.66 17.64-42.84 39.36-42.84 21.6 0 39.24 19.18 39.24 42.84z" stroke="#000" stroke-width="2" fill="#fff"/>
                    </g>
                    
                    <path d="M1531.72 1069.1L1420 1200" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M1540 1060l-5.28 19.32-12.12-12.32z" stroke="#000" stroke-width="2"/>
                    <path d="M1436.56 1132.24h8.28l12.36-22.54 29.04 45.08 20.64-22.54h8.28" stroke="#000" stroke-width="2" fill="none"/>
                    
                    <path d="M-159.5 60h84.6M-159.5 60h-.5" stroke="#3a414a" fill="none"/><path d="M-60.14 60l-14.26 4.64v-9.27z" stroke="#3a414a" fill="#3a414a"/><path d="M-.98 60h85.55" stroke="#3a414a" fill="none"/><path d="M-.97 60.48h-.55l.04-.48-.04-.48h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M99.33 60l-14.27 4.64v-9.27z" stroke="#3a414a" fill="#3a414a"/>
                    
                    <g class="interactivo-nodo" id="P200" data-name="Bomba de Fondo de Vinazas (P200)">
                        <path d="M1675.2 1351.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76l-13.08-16.65a.78.78 0 0 0-.82-.28l-.5.13" stroke="#000" stroke-width="2" fill="#fff"/>
                        <path d="M1663 1301.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#000" stroke-width="2" fill="#fff"/>
                        <path d="M1685.4 1309.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7" stroke="#000" stroke-width="2" fill="#fff"/>
                    </g>
                    
                    <path d="M825.1 794.72h-5.02m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H774.9m-5.02 0h-10.04m-5 0h-10.06m-5 0h-10.05m-5.02 0h-10.03m-5.02 0H699.6m-5 0h-10.05m-5.02 0H669.5m-5.03 0h-10.04m-5.02 0h-10.03m-5.02 0H624.3m-5 0h-10.04m-5.02 0H594.2m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H518.9m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.03m-5.03 0H443.6m-5 0h-10.05m-5.02 0H413.5m-5.03 0h-10.04m-5.02 0h-10.03m-5.02 0H368.3m-5 0h-10.05m-5.02 0H338.2m-5.03 0h-10.04m-5.02 0h-10.03m-5 0H293m-5.02 0h-10.04m-5.02 0H262.9m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H187.6m-5.02 0h-10.04m-5.02 0H157.5m-5.03 0h-10.04m-5.02 0h-10.03m-5.02 0H112.3m-5 0H97.24m-5.02 0H82.2m-5.03 0H67.13m-5.02 0H52.08m-5.02 0H37m-5 0H21.94m-5.02 0H6.9m-5.03 0H-8.15m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0H-68.4m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-10.04m-5.02 0h-5.02M825.1 794.72h3.5" stroke="#3a414a" fill="none"/><path d="M-138.38 794.72l14.26-4.63v9.25z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R1">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">1</text>
                    </g>
                    
                    <g class="interactivo-nodo" id="R2" transform="translate(350, 265)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">2</text>
                    </g>

                    <path d="M179.05 65H344a6 6 0 0 1 6 6v154.1" stroke="#3a414a" fill="none"/><path d="M179.06 65.47h-.6l.06-.3.08-.64h.46z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M350 239.86l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/><path d="M350 299.02V534a6 6 0 0 0 6 6h126.62" stroke="#3a414a" fill="none"/><path d="M350 298.52l.48-.04v.55h-.96v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M497.38 540l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>
                    
                    <g class="interactivo-nodo" id="W210" data-name="Intercambiador Recuperador de Procesos (W210)">
                        <path d="M506 500a6 6 0 0 0-6 6v68a6 6 0 0 0 6 6h188a6 6 0 0 0 6-6v-68a6 6 0 0 0-6-6zM520 500v80m160-80v80m-160-60h160m-160 20h160m-160 20h160" stroke="#000" stroke-width="2" fill="#fff"/>
                    </g>

                    <g class="interactivo-nodo" id="R3" transform="translate(860, 535)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">3</text>
                    </g>

                    <path d="M701.5 540h113.6" stroke="#3a414a" fill="none"/><path d="M701.5 540.48h-.5v-.96h.5z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M829.86 540l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/><path d="M860 569.02v153.6" stroke="#3a414a" fill="none"/><path d="M860 568.52l.48-.04v.55h-.96v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M860 737.38l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R4" transform="translate(600, 915)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">4</text>
                    </g>

                    <path d="M600 581.5v293.6" stroke="#3a414a" fill="none"/><path d="M600.48 581.5h-.96v-.5h.96z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M600 889.86l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/><path d="M600 949.02V1554a6 6 0 0 0 6 6h1377.62" stroke="#3a414a" fill="none"/><path d="M600 948.52l.48-.04v.55h-.96v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1998.38 1560l-14.26 4.63v-9.27z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R5" transform="translate(1010, 915)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">5</text>
                    </g>

                    <path d="M1039.02 920h23.6" stroke="#3a414a" fill="none"/><path d="M1039.03 920.48h-.55l.04-.48-.04-.48h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1077.38 920l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R6" transform="translate(860, 915)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">6</text>
                    </g>

                    <g class="interactivo-nodo" id="V100" data-name="Válvula de Restricción/Expansión (V100)">
                        <path d="M920 906a6 6 0 0 1 6-6h28a6 6 0 0 1 6 6v28a6 6 0 0 1-6 6h-28a6 6 0 0 1-6-6z" fill="none"/><path d="M924 910v20l15-10zm15 10h2zm2 0l15-10v20zM940 920v-12m-8 0c0-2.2 3.58-4 8-4s8 1.8 8 4z" stroke="#000" fill="#fff"/>
                    </g>
                    
                    <path d="M957 920h8.1M957 920h-3.5" stroke="#3a414a" fill="none"/><path d="M79.86 920l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/><path d="M889.02 920h18.1" stroke="#3a414a" fill="none"/><path d="M889.03 920.48h-.55l.04-.48-.04-.48h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M921.88 920l-14.26 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/><path d="M860 821.5v53.6" stroke="#3a414a" fill="none"/><path d="M860 821l.48-.02v.53h-.96v-.53z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M860 889.86l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R7" transform="translate(1476, 805)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">7</text>
                    </g>

                    <path d="M1120 838.5V816a6 6 0 0 1 6-6h305.46" stroke="#3a414a" fill="none"/><path d="M1120.47 839.02l-.48-.02-.47.03v-.54h.94z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1446.23 810l-14.27 4.63v-9.26z" stroke="#3a414a" fill="#3a414a"/><path d="M1476.37 839.02v230.78" stroke="#3a414a" fill="none"/><path d="M1476.37 838.52l.47-.04v.55h-.95v-.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1476.37 1084.57l-4.64-14.27h9.27z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R8" transform="translate(1120, 1310)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">8</text>
                    </g>

                    <path d="M1120 1001.5v267.7" stroke="#3a414a" fill="none"/><path d="M1120.47 1001.5h-.94v-.52l.48.02.47-.03z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1120 1283.98l-4.63-14.27h9.27z" stroke="#3a414a" fill="#3a414a"/><path d="M1149.02 1314.1h496.2" stroke="#3a414a" fill="none"/><path d="M1149.03 1314.6h-.55l.04-.5-.04-.46h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1660 1314.1l-14.27 4.65v-9.27z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R9" transform="translate(1480, 1370)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">9</text>
                    </g>

                    <path d="M1480 1174.05v155.16" stroke="#3a414a" fill="none"/><path d="M1480.47 1174.07h-.94v-.46l.94-.1z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1480 1343.98l-4.63-14.27h9.27z" stroke="#3a414a" fill="#3a414a"/><path d="M1480 1403.13V1474a6 6 0 0 0 6 6h497.62" stroke="#3a414a" fill="none"/><path d="M1480 1402.63l.47-.04v.55h-.94v-.56z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1998.38 1480l-14.26 4.63v-9.27z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="R10" transform="translate(1810, 785)">
                      <path d="M-25,0 L0,-25 L25,0 L0,25 Z" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="0">10</text>
                    </g>

                    <path d="M1761.45 1297.5H1804a6 6 0 0 0 6-6V834.9" stroke="#3a414a" fill="none"/><path d="M1761.46 1297.96h-.5v-.95h.5z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1810 820.14l4.63 14.26h-9.27z" stroke="#3a414a" fill="#3a414a"/><path d="M1810 760.98V465a6 6 0 0 0-6-6H606a6 6 0 0 0-6 6v17.62" stroke="#3a414a" fill="none"/><path d="M1810.47 761.52l-.47-.04-.47.04v-.55h.94z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M600 497.38l-4.63-14.26h9.26z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="W220_S1" data-name="Agua de Enfriamiento / Vapor W1 (Uso en P100)", transform="translate(150, -40)">
                      <circle cx="0" cy="0" r="20" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="4">W1</text>
                    </g>
                    
                    <path d="M150-18.5v1.6m0 2.1v3.17m0 2.12v3.16m0 2.12v3.17m0 2.1v1.6" stroke="#3a414a" fill="none"/><path d="M150.47-18.5h-.94V-19l.5.02.44-.05z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M150 17.4l-4.63-14.25h9.26z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="P200_S1" data-name="Servicios de Enfriamiento W2 (Bomba P200)", transform="translate(1710, 1220)">
                      <circle cx="0" cy="0" r="20" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="4">W2</text>
                    </g>

                    <path d="M1710 1241.5v1.6m0 2.1v3.17m0 2.12v3.16m0 2.12v3.17m0 2.1v1.6" stroke="#3a414a" fill="none"/><path d="M1710.47 1241.5h-.94v-.52l.52.02.42-.05z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1710 1277.4l-4.63-14.25h9.27z" stroke="#3a414a" fill="#3a414a"/>

                    <path d="M-159.5-100h5.1m5.08 0h10.17m5.1 0h10.17m5.08 0h10.18m5.1 0h10.17m5.08 0h10.18m5.1 0h10.17m5.1 0h10.17m5.1 0h5.08M-159.5-100h-.5" stroke="#3a414a" fill="none"/><path d="M-22.62-100l-14.26 4.64v-9.28z" stroke="#3a414a" fill="#3a414a"/><path d="M21.5-100h5.1m5.1 0h10.22m5.1 0h10.2m5.1 0h10.22m5.1 0h10.2m5.12 0h10.2m5.1 0h10.22m5.1 0h10.2m5.12 0h5.1a6 6 0 0 1 6 6v5.54m0 5.54v5.53" stroke="#3a414a" fill="none"/><path d="M21.5-99.53h-.55l.05-.42-.02-.52h.53z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M150-62.62l-4.63-14.27 9.26.02z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="P100_E" data-name="Energía Eléctrica Consumida (Bomba P100)", transform="translate(0, -100)">
                      <circle cx="0" cy="0" r="20" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="4">E1</text>
                    </g>

                    <g class="interactivo-nodo" id="W210_E" data-name="Eficiencia Energética / Pérdidas (Recuperador W210)", transform="translate(240, 765.28)">
                      <circle cx="0" cy="0" r="20" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="4">E2</text>
                    </g>

                    <path d="M-139.5 765.28h4.96m4.96 0h9.9m4.97 0h9.9m4.97 0h9.9m4.97 0h9.92m4.96 0h9.9m4.97 0h9.9m4.96 0h9.92m4.96 0h9.92m4.95 0h9.93m4.96 0h9.9m4.96 0h9.92m4.96 0h9.9m4.97 0h9.93m4.96 0h9.9m4.96 0h9.92m4.95 0h9.92m4.96 0h9.93m4.95 0h9.92m4.96 0h9.9m4.97 0h9.92m4.96 0h9.92m4.96 0h9.92m4.96 0h4.96M-139.5 765.28h-.5" stroke="#3a414a" fill="none"/><path d="M217.38 765.28l-14.26 4.63v-9.25z" stroke="#3a414a" fill="#3a414a"/><path d="M261.5 765.28h4.94m4.93 0h9.87m4.93 0h9.87m4.94 0h9.86m4.94 0h9.87m4.93 0h9.87m4.94 0h9.85m4.94 0h9.86m4.93 0h9.86m4.93 0h9.86m4.94 0h9.87m4.93 0h9.87m4.93 0h9.87m4.94 0h9.88m4.93 0h9.88m4.93 0h9.88m4.94 0h9.87m4.92 0h9.87m4.92 0h9.87m4.93 0h9.86m4.93 0h9.87m4.93 0h9.87m4.94 0h9.86m4.94 0h9.87m4.94 0h9.87m4.94 0h9.87m4.95 0h9.87m4.93 0h9.87m4.93 0h9.87m4.94 0h9.87m4.93 0h9.87m4.93 0h9.87m4.94 0h9.87m4.93 0h9.87m4.93 0h9.87m4.94 0h9.86m4.94 0h9.87m4.93 0h4.94" stroke="#3a414a" fill="none"/><path d="M261.5 765.75h-.55l.05-.42-.02-.53h.53z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M823.98 765.28l-14.26 4.63v-9.25z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="W310_E" data-name="Consumo Térmico Condensador E3 (Hacia W310)", transform="translate(1646.86, 1023.2)">
                      <circle cx="0" cy="0" r="20" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="4">E3</text>
                    </g>

                    <path d="M1646.86 1044.7v5.43m0 5.44v10.88m0 5.44v10.87m0 5.44v10.9m0 5.43v10.88m0 5.45v5.45a6 6 0 0 1-6 6h-5.16m-5.17 0h-10.33m-5.16 0h-10.33m-5.15 0h-10.33m-5.17 0h-10.33m-5.16 0h-10.33m-5.16 0h-10.33m-5.16 0h-5.17" stroke="#3a414a" fill="none"/><path d="M1646.8 1044.2l.53-.03v.53h-.95v-.56z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1517.65 1132.3l14.26-4.64v9.27z" stroke="#3a414a" fill="#3a414a"/>

                    <g class="interactivo-nodo" id="P200_E" data-name="Consumo Eléctrico Motor E4 (Bomba P200)", transform="translate(1750, 1140)">
                      <circle cx="0" cy="0" r="20" stroke="#000" stroke-width="2" fill="#fff"/>
                      <text class="val-text" x="0" y="4">E4</text>
                    </g>

                    <path d="M1728.5 1140h-4.17m-4.16 0H1716a6 6 0 0 0-6 6v6.1m0 6.1v12.2m0 6.1v6.12" stroke="#3a414a" fill="none"/><path d="M1729 1139.95l.02.52h-.53v-.94h.55z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1710 1197.38l-4.63-14.27 9.27.02z" stroke="#3a414a" fill="#3a414a"/>

                </g>
            </svg>

            <script>
                const simData = {json_data_sim};
                const tooltip = document.getElementById('pfd-tooltip');
                
                document.querySelectorAll('.interactivo-nodo').forEach(el => {{
                    el.addEventListener('mousemove', (e) => {{
                        const id = el.id;
                        let info = simData[id];
                        let titleName = el.getAttribute('data-name');
                        
                        // Si es un rombo de corriente (Inicia con R)
                        if (id.startsWith('R')) {{
                            const sNum = id.replace('R', '');
                            if (simData[id]) {{
                                info = simData[id];
                                titleName = "Línea de Proceso " + sNum + ": " + info.Nombre;
                            }}
                        }}
                        
                        // Fallbacks para círculos de utilidades eléctricas o térmicas si no están definidos explícitamente
                        if (!info) {{
                            if (id.includes('_E')) {{
                                const baseUnit = id.split('_')[0];
                                const uData = simData[baseUnit] || {{Duty: '0.0 kW'}};
                                info = {{ Tipo: 'Carga Energética', Nombre: titleName, T: 'N/A', P: 'N/A', Flow: 'N/A', Duty: uData.Duty }};
                            }} else {{
                                info = {{ Tipo: 'Utilidad/Auxiliar', Nombre: titleName || id, T: 'Variable', P: '1.0 atm', Flow: 'N/A', Duty: 'N/A' }};
                            }}
                        }}
                        
                        tooltip.style.display = 'block';
                        tooltip.innerHTML = `
                            <strong style="color:#0ea5e9; font-size:14px; display:block; margin-bottom:5px;">${{titleName || info.Nombre}}</strong>
                            <span style="color:#94a3b8; font-size:11px; display:block; margin-bottom:5px; text-transform: uppercase;">${{info.Tipo || 'Información'}}</span>
                            <table style="width:100%; font-size:12px; border-collapse:collapse; color:#e2e8f0;">
                                ${{info.T !== 'N/A' ? `<tr><td style="padding:2px 0; color:#94a3b8;">Temperatura:</td><td style="text-align:right; font-weight:bold;">${{info.T}}</td></tr>` : ''}}
                                ${{info.P !== 'N/A' ? `<tr><td style="padding:2px 0; color:#94a3b8;">Presión:</td><td style="text-align:right; font-weight:bold;">${{info.P}}</td></tr>` : ''}}
                                ${{info.Flow !== 'N/A' ? `<tr><td style="padding:2px 0; color:#94a3b8;">Flujo:</td><td style="text-align:right; font-weight:bold;">${{info.Flow}}</td></tr>` : ''}}
                                ${{info.Duty !== 'N/A' ? `<tr><td style="padding:2px 0; color:#94a3b8;">Parámetro/Carga:</td><td style="text-align:right; font-weight:bold; color:#38bdf8;">${{info.Duty}}</td></tr>` : ''}}
                            </table>
                        `;
                        
                        const rectContenedor = document.getElementById('contenedor-pfd').getBoundingClientRect();
                        tooltip.style.left = (e.clientX - rectContenedor.left + 20) + 'px';
                        tooltip.style.top = (e.clientY - rectContenedor.top + 20) + 'px';
                    }});
                    
                    el.addEventListener('mouseleave', () => {{
                        tooltip.style.display = 'none';
                    }});
                }});
            </script>
        </div>
        """
        st.components.v1.html(html_pfd_interactivo, height=850, scrolling=True)

    with tab_tutor:
        st.subheader("🤖 Consultoría Técnica y Diagnóstico de Planta")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        
        if prompt := st.chat_input("¿Cómo puedo optimizar la recuperación de etanol o reducir el gasto operativo?"):
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
    st.error(f"Error en la simulación o renderizado: {e}")
