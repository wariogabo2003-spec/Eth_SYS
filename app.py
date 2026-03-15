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

st.title("🔬 Simulador Interactivo de Procesos: Bioetanol")
st.markdown("""
Esta plataforma integra **BioSTEAM** para el diseño de procesos químicos y **Gemini 2.0 Pro** para el análisis técnico de resultados. 
Ajusta las variables en el panel izquierdo para observar el comportamiento del sistema en tiempo real.
""")

# Configuración de la API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ GEMINI_API_KEY no configurada en Secrets.")

# ==========================================
# FUNCIÓN NÚCLEO DE SIMULACIÓN
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_v220):
    # Limpieza total del flowsheet para evitar colisión de IDs
    bst.main_flowsheet.clear()
    
    # 1. Definición de Componentes
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # 2. Corrientes de entrada
    mosto = bst.Stream("MOSTO", 
                       Water=flow_water, 
                       Ethanol=flow_ethanol, 
                       units="kg/hr", 
                       T=25+273.15)
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", 
                                 Water=200, 
                                 T=95+273.15)

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
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # 4. Creación del Sistema y Ejecución
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    return sys_bio

# ==========================================
# BARRA LATERAL (CONTROLES)
# ==========================================
with st.sidebar:
    st.header("⚙️ Variables de Proceso")
    f_agua = st.slider("Flujo Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Flujo Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temperatura Calentamiento (°C)", 80, 115, 95)
    st.divider()
    st.caption("BioSTEAM + Gemini 2.0 Pro")

# ==========================================
# LÓGICA DE VISUALIZACIÓN
# ==========================================
try:
    # Ejecutar la simulación
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    
    # Acceso dinámico a corrientes para evitar errores de registro
    corrientes = sistema.streams
    prod = next((s for s in corrientes if s.ID == "Producto_Final"), None)
    
    # Métricas principales
    if prod:
        c1, c2, c3 = st.columns(3)
        with c1:
            pureza = (prod.imass['Ethanol'] / prod.F_mass) if prod.F_mass > 0 else 0
            st.metric("Pureza Etanol", f"{pureza:.1%}")
        with c2:
            st.metric("Producción Total", f"{prod.F_mass:.2f} kg/h")
        with c3:
            st.metric("Etanol Recuperado", f"{prod.imass['Ethanol']:.2f} kg/h")

    # Organización de la Información
    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 PFD", "🤖 Tutor IA"])

    with tab1:
        # Tabla de Balances de Materia
        datos_m = []
        for s in corrientes:
            if s.F_mass > 0.01:
                datos_m.append({
                    "ID Corriente": s.ID,
                    "Temp (°C)": round(s.T - 273.15, 2),
                    "Presión (bar)": round(s.P / 1e5, 2),
                    "Flujo Total (kg/h)": round(s.F_mass, 2),
                    "Etanol (kg/h)": round(s.imass['Ethanol'], 2)
                })
        df_materia = pd.DataFrame(datos_m)
        st.subheader("Tabla de Resultados por Corriente")
        st.dataframe(df_materia, use_container_width=True)

    with tab2:
        st.subheader("Diagrama de Flujo del Proceso (PFD)")
        st.graphviz_chart(sistema.diagram('dot'))

    with tab3:
        st.subheader("Informe Técnico por Gemini 2.0 Pro")
        if st.button("Generar Análisis"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Analizando simulación..."):
                    model = genai.GenerativeModel('gemini-2.0-pro-exp-02-05')
                    
                    prompt = f"""
                    Actúa como un ingeniero químico experto. Analiza estos resultados de BioSTEAM:
                    {df_materia.to_string()}
                    
                    Variables de entrada:
                    - Flujo alimento: {f_agua + f_etanol} kg/h
                    - Temp. Calentamiento (W220): {t_v220}°C
                    
                    Preguntas:
                    1. ¿Es razonable la pureza obtenida en el Producto_Final?
                    2. ¿Qué impacto tiene la temperatura de {t_v220}°C en la separación flash?
                    3. Sugiere un ajuste técnico para maximizar la recuperación de etanol.
                    """
                    
                    response = model.generate_content(prompt)
                    st.info("### Análisis del Experto")
                    st.markdown(response.text)
            else:
                st.error("Por favor, verifica la GEMINI_API_KEY en los Secrets del dashboard.")

except Exception as e:
    st.error(f"Error detectado: {e}")
