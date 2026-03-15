import streamlit as st
import sys

# SOLUCIÓN AL ERROR DE ALTAIR: Redirección de v4 a v5
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
st.set_page_config(page_title="BioSTEAM Interactive Pro", layout="wide")

st.title("🔬 Simulador Interactivo de Bioetanol")
st.markdown("""
Esta aplicación simula un proceso de separación de etanol utilizando **BioSTEAM**. 
Ajusta los parámetros en la barra lateral y consulta al **Tutor IA** para analizar los resultados.
""")

# Configuración de la API de Gemini desde Secrets de Streamlit
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ Configura GEMINI_API_KEY en Secrets.")

# ==========================================
# FUNCIÓN NÚCLEO DE SIMULACIÓN
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_v220):
    # Limpiar el flowsheet para evitar errores de IDs duplicados
    bst.main_flowsheet.clear()
    
    # 1. Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # 2. Corrientes dinámicas
    mosto = bst.Stream("1_MOSTO", 
                       Water=flow_water, 
                       Ethanol=flow_ethanol, 
                       units="kg/hr", 
                       T=25+273.15)
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", 
                                 Water=200, 
                                 T=95+273.15)

    # 3. Definición de Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    W210 = bst.HXprocess("W210", 
                        ins=(P100-0, vinazas_retorno), 
                        outs=("3_Mosto_Pre", "Drenaje"),
                        phase0="l", phase1="l")
    W210.outs[0].T = 85+273.15

    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=temp_v220+273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=101325)
    
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_Caliente", "Vinazas"), P=101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # 4. Crear Sistema y Simular
    eth_sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    eth_sys.simulate()
    
    return eth_sys

# ==========================================
# BARRA LATERAL (INPUTS)
# ==========================================
with st.sidebar:
    st.header("⚙️ Parámetros de Proceso")
    f_agua = st.slider("Flujo Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Flujo Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temperatura Calentador (°C)", 85, 110, 92)
    st.divider()

# ==========================================
# EJECUCIÓN Y VISUALIZACIÓN
# ==========================================
try:
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    
    # Métricas clave
    prod_final = sistema.flowsheet.stream.Producto_Final
    c1, c2, c3 = st.columns(3)
    c1.metric("Pureza Etanol", f"{(prod_final.imass['Ethanol']/prod_final.F_mass):.1%}")
    c2.metric("Producción Total", f"{prod_final.F_mass:.2f} kg/h")
    c3.metric("Temp. Vapor Flash", f"{sistema.flowsheet.unit.V1.outs[0].T - 273.15:.1f} °C")

    # Tabs para organizar información
    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 Diagrama PFD", "🤖 Tutor IA"])

    with tab1:
        col_a, col_b = st.columns(2)
        
        # Tabla Materia
        datos_m = []
        for s in sistema.streams:
            if s.F_mass > 0.01:
                datos_m.append({
                    "ID": s.ID, 
                    "T (°C)": s.T-273.15, 
                    "Flujo (kg/h)": s.F_mass, 
                    "% Etanol": (s.imass['Ethanol']/s.F_mass) if s.F_mass > 0 else 0
                })
        df_materia = pd.DataFrame(datos_m)
        
        with col_a:
            st.subheader("Balance de Materia")
            st.dataframe(df_materia.style.format({"T (°C)": "{:.1f}", "Flujo (kg/h)": "{:.2f}", "% Etanol": "{:.1%}"}))

        # Tabla Energía
        datos_e = []
        for u in sistema.units:
            duty = 0
            if isinstance(u, bst.HXprocess):
                duty = (u.outs[0].H - u.ins[0].H) / 3600
                func = "Recup. Térmica"
            elif hasattr(u, 'duty') and u.duty is not None:
                duty = u.duty / 3600
                func = "Servicio Aux"
            elif isinstance(u, bst.Flash):
                duty = (u.outs[0].H + u.outs[1].H - u.ins[0].H) / 3600
                func = "Flash Adiabático"
            
            if abs(duty) > 0.01:
                datos_e.append({"Equipo": u.ID, "Función": func, "Calor (kW)": duty})
        
        df_energia = pd.DataFrame(datos_e)
        with col_b:
            st.subheader("Balance Energético")
            st.dataframe(df_energia.style.format({"Calor (kW)": "{:.2f}"}))

    with tab2:
        st.subheader("Diagrama de Flujo del Proceso")
        # Generamos el objeto Graphviz a través de BioSTEAM
        st.graphviz_chart(sistema.diagram('dot'))

    with tab3:
        st.subheader("Análisis del Tutor de Ingeniería (Gemini)")
        if st.button("Generar Reporte IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Analizando simulación..."):
                    model = genai.GenerativeModel('gemini-pro')
                    prompt = f"""
                    Contexto: Simulación de purificación de etanol en BioSTEAM.
                    Datos de corrientes: {df_materia.to_string()}
                    Datos térmicos: {df_energia.to_string()}
                    Tarea: Explica de forma técnica pero amigable qué está pasando en el Flash (V1) 
                    y si el sistema está recuperando energía eficientemente.
                    """
                    response = model.generate_content(prompt)
                    st.write(response.text)
            else:
                st.error("API Key no encontrada.")

except Exception as e:
    st.error(f"Error en la simulación: {e}")
