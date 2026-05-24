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
st.set_page_config(page_title="BioSTEAM Industrial Pro", layout="wide", page_icon="🏭")
st.title("🏭 Planta de Bioetanol: Simulación e Indicadores Económicos")

# Verificación e inicio de la API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("🔑 Configura GEMINI_API_KEY en st.secrets para activar el Tutor IA.")

if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================
# INTERFAZ (SIDEBAR) - PARÁMETROS
# ==========================================
with st.sidebar:
    st.header("Terminal de Control")
    st.subheader("🌡️ Parámetros de Proceso")
    t_feed = st.slider("Temp. Alimentación Mosto (°C)", 15, 60, 25)
    t_w220 = st.slider("Temp. Salida Intercambiador W220 (°C)", 80, 120, 98)
    p_v100 = st.slider("Presión Separador V100 (Pa)", 10000, 200000, 101325)
    
    st.subheader("💰 Costos de Mercado")
    p_luz = st.slider("Precio Luz (USD/kWh)", 0.01, 0.50, 0.12)
    p_vapor = st.slider("Precio Vapor (USD/kg)", 0.00, 0.20, 0.03)
    p_agua = st.slider("Precio Agua (USD/m3)", 0.00, 0.20, 0.02)
    p_mosto = st.slider("Precio Mosto (USD/kg)", 0.01, 2.00, 0.35)
    p_etanol = st.slider("Precio Etanol (USD/kg)", 0.50, 15.00, 5.00)

# ==========================================
# FUNCIÓN DE SIMULACIÓN Y ECONOMÍA
# ==========================================
def run_full_simulation(params, prices):
    # Resetear el flowsheet para evitar colisión de nombres en re-ejecuciones
    bst.main_flowsheet.clear()
    
    # Configuración Termodinámica básica (Agua-Etanol)
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # Precios de Servicios Globales en BioSTEAM
    bst.settings.electricity_price = prices['luz']
    
    # Definición de Corrientes de Entrada
    mosto = bst.Stream("MOSTO", 
                       Water=900, Ethanol=100, units="kg/hr", 
                       T=params['t_feed'] + 273.15,
                       price=prices['mosto'])
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, T=95+273.15)
    
    # --- Topología de Equipos ---
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    # W210 - Intercambiador de proceso cruzado
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                        outs=("Mosto_Pre", "Drenaje"),
                        phase0='l', phase1='l')
    W210.outs[0].T = 85+273.15
    
    # W220 - Calentador por servicio auxiliar
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=params['t_w220'] + 273.15)
    
    # V100 y V1 - Sistema de expansión instantánea (Flash)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=params['p_v100'])
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_V1", "Liquido_V1"), P=params['p_v100'], Q=0)
    
    # W310 - Condensador de producto de cabeza
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    # P200 - Bomba de recirculación del fondo del Flash
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)
    
    # Generar y simular sistema secuencial
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    # --- CÁLCULOS ECONÓMICOS ---
    prod = W310.outs[0]
    etanol_puro_hr = prod.imass['Ethanol']
    
    # OPEX adaptado matemáticamente a los sliders
    costo_utilidades = (abs(W220.Q)/2200 * prices['vapor']) + (abs(W310.Q)/40 * prices['agua'])
    costo_electricidad = (P100.power_utility.rate + P200.power_utility.rate) * prices['luz']
    costo_materia_prima = mosto.F_mass * prices['mosto']
    
    total_opex_hr = costo_utilidades + costo_electricidad + costo_materia_prima
    ingresos_hr = etanol_puro_hr * prices['etanol']
    
    # Indicadores Macroeconómicos de la Planta
    horas_ano = 8000
    inversion_inicial = 200000 
    utilidad_anual = (ingresos_hr - total_opex_hr) * horas_ano
    
    roi = (utilidad_anual / inversion_inicial) * 100
    payback = inversion_inicial / utilidad_anual if utilidad_anual > 0 else 999
    npv = -inversion_inicial + (utilidad_anual / 0.12) # Calculado a una tasa de descuento del 12%
    
    return sys_bio, {
        "ROI": roi, "Payback": payback, "NPV": npv, 
        "CostoProd": total_opex_hr / etanol_puro_hr if etanol_puro_hr > 0 else 0,
        "VentaSug": (total_opex_hr / etanol_puro_hr) * 1.30 if etanol_puro_hr > 0 else 0
    }

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
# LÓGICA DE RENDERIZADO PRINCIPAL
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
        c1.metric("Presión de Salida", f"{prod.P/101325:.2f} atm")
        c2.metric("Temperatura de Salida", f"{prod.T-273.15:.1f} °C")
        c3.metric("Rendimiento Etanol", f"{prod.imass['Ethanol']:.2f} kg/h")
        pureza = (prod.imass['Ethanol']/prod.F_mass) if prod.F_mass > 0 else 0
        c4.metric("Pureza Obtenida", f"{pureza:.1%}")

    # RECUADROS ECONÓMICOS
    st.subheader("💸 Evaluación Económica")
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Costo Real Fabricación", f"${econ['CostoProd']:.2f}/kg")
    e2.metric("Venta Mínima Sugerida", f"${econ['VentaSug']:.2f}/kg")
    e3.metric("NPV / VAN (12%)", f"${econ['NPV']:,.2f}")
    
    pb_display = f"{econ['Payback']:.1f} años" if econ['Payback'] < 50 else "No Rentable"
    e4.metric("Periodo de Payback", pb_display)
    e5.metric("ROI Anualizado", f"{econ['ROI']:.1f}%")

    # SISTEMA DE PANTALLAS (TABS)
    tab_m, tab_pfd, tab_interactivo, tab_tutor = st.tabs([
        "📊 Balances de Materia", "📐 Diagrama de Bloques", "🎯 PFD Dinámico SVG", "🤖 Tutor IA Avanzado"
    ])

    with tab_m:
        st.subheader("Reporte de Corrientes")
        df_data = []
        for s in sistema.streams:
            if s.F_mass > 0.01:
                df_data.append({
                    "Corriente ID": s.ID,
                    "Temperatura [°C]": round(s.T - 273.15, 2),
                    "Presión [Pa]": round(s.P, 1),
                    "Flujo Total [kg/h]": round(s.F_mass, 2),
                    "Flujo Etanol [kg/h]": round(s.imass['Ethanol'], 2),
                    "Entalpía [kJ/h]": round(s.H, 1)
                })
        st.dataframe(pd.DataFrame(df_data), use_container_width=True)

    with tab_pfd:
        try:
            dot = sistema.diagram(kind='surface', display=False)
            source = dot.source if hasattr(dot, 'source') else str(dot)
            st.graphviz_chart(source)
        except Exception:
            st.info("Gráfico base generado por motor nativo de BioSTEAM.")

    with tab_interactivo:
        st.subheader("Mapeo Operativo en Tiempo Real")
        st.caption("Pasa el cursor sobre los nodos activos del diagrama para extraer los perfiles termodinámicos.")
        
        json_data_sim = obtener_datos_unidades(sistema)
        
        # Inyección segura del string HTML/SVG que construiste
        html_pfd_interactivo = f"""
        <div id="contenedor-pfd" style="position: relative; display: inline-block; background: #ffffff; padding: 15px; border-radius: 8px; width: 100%; overflow: auto;">
            <div id="pfd-tooltip" style="position: absolute; display: none; background: rgba(20, 26, 36, 0.96); color: #ffffff; padding: 12px; border-radius: 6px; font-family: sans-serif; font-size: 13px; z-index: 9999; pointer-events: none; border: 1px solid #4FA8FF; box-shadow: 0px 4px 20px rgba(0,0,0,0.4); min-width: 190px;"></div>
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2161 1626.32" width="100%" height="auto">
                <style>
                    .equipo-nodo {{ cursor: pointer; fill: rgba(79, 168, 255, 0); stroke: transparent; transition: all 0.2s ease; }}
                    .equipo-nodo:hover {{ fill: rgba(79, 168, 255, 0.2); stroke: #007BFF; stroke-width: 4px; }}
                </style>
                <g transform="translate(160.5 61)">
                    <path d="M115.2 91.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76L165.4 88.1a.78.78 0 0 0-.82-.28l-.5.13" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M103 41.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M125.4 49.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7M900 780c0 22.08-17.92 40-40 40s-40-17.92-40-40 17.92-40 40-40 40 17.92 40 40z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M822.56 765.28h60.56l-36.16 15.12 36.16 15.2-60.56-.88" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M1080 860v120c0 11.05 17.9 20 40 20s40-8.95 40-20V860c0-11.05-17.9-20-40-20s-40 8.95-40 20zM1515.16 1130c0 23.66-17.64 42.84-39.24 42.84-21.72 0-39.36-19.18-39.36-42.84 0-23.66 17.64-42.84 39.36-42.84 21.6 0 39.24 19.18 39.24 42.84z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M1531.72 1069.1L1420 1200" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M1540 1060l-5.28 19.32-12.12-12.32z" stroke="#000" stroke-width="2"/>
                    <path d="M1436.56 1132.24h8.28l12.36-22.54 29.04 45.08 20.64-22.54h8.28" stroke="#000" stroke-width="2" fill="none"/>
                    <path d="M506 500a6 6 0 0 0-6 6v68a6 6 0 0 0 6 6h188a6 6 0 0 0 6-6v-68a6 6 0 0 0-6-6zM520 500v80m160-80v80m-160-60h160m-160 20h160m-160 20h160M855.76 514.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#000" stroke-width="2" fill="#fff"/>
                    <path d="M701.5 540h113.6M860 569.02v153.6M600 581.5v293.6M920 906a6 6 0 0 1 6-6h28a6 6 0 0 1 6 6v28a6 6 0 0 1-6 6h-28a6 6 0 0 1-6-6z" fill="none"/>
                    <path d="M924 910v20l15-10zm15 10h2zm2 0l15-10v20zM940 920v-12m-8 0c0-2.2 3.58-4 8-4s8 1.8 8 4z" stroke="#000" fill="#fff"/>

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
                        ⚙️ Unidad: ${{idEquipo}}
                    </div>
                    <table style="width: 100%; font-size: 12px; border-collapse: collapse;">
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Temperatura:</td><td style="text-align: right; font-weight: 600;">${{datos.T}}</td></tr>
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Presión:</td><td style="text-align: right; font-weight: 600;">${{datos.P}}</td></tr>
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Flujo Másico:</td><td style="text-align: right; font-weight: 600;">${{datos.Flow}}</td></tr>
                        <tr><td style="color: #A0AABF; padding: 2px 0;">Carga Térmica:</td><td style="text-align: right; font-weight: 600;">${{datos.Duty}}</td></tr>
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
        st.components.v1.html(html_pfd_interactivo, height=650, scrolling=True)

    with tab_tutor:
        st.subheader("🤖 Consultoría de Procesos Automatizada")
        st.write("Interactúa con el modelo para evaluar el rendimiento físico y comercial del diseño.")
        
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input("Ej: ¿Cómo afecta la reducción de presión en V100 al ROI de la planta?"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                if "GEMINI_API_KEY" in st.secrets:
                    try:
                        model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                        target_model = next((m for m in model_list if "flash" in m), model_list[0])
                        model = genai.GenerativeModel(target_model)
                        
                        context = f"""
                        Eres un ingeniero consultor experto en plantas de proceso químico y optimización en BioSTEAM.
                        Métricas operativas y de mercado actuales:
                        - ROI actual: {econ['ROI']:.2f}%
                        - NPV (VAN): {econ['NPV']:.2f} USD
                        - Costo Unitario de Producción: {econ['CostoProd']:.2f} USD/kg
                        - Precio de Venta establecido: {p_etanol} USD/kg
                        - Temperatura de Alimentación: {t_feed} °C
                        - Temperatura en Calentador W220: {t_w220} °C
                        - Presión de Operación en el Separador Flash V1: {p_v100} Pa
                        
                        Responde con un enfoque técnico robusto, conciso, de manera profesional y directa.
                        """
                        response = model.generate_content(f"{context}\nPregunta del operador técnico: {prompt}")
                        st.markdown(response.text)
                        st.session_state.messages.append({"role": "assistant", "content": response.text})
                    except Exception as e:
                        st.error(f"Error de comunicación con Gemini: {e}")
                else:
                    st.error("La clave de API no está configurada. Agrégala en `.streamlit/secrets.toml`.")

except Exception as e:
    st.error(f"Fallo crítico en la simulación iterativa de BioSTEAM: {e}")
