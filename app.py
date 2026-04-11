import streamlit as st
import sys

# 1. PARCHE DE COMPATIBILIDAD PARA ALTAIR (Evita errores de versión v4/v5)
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
st.set_page_config(page_title="BioSTEAM & Gemini Pro", layout="wide")

st.title("🔬 Simulador de Procesos: Bioetanol")
st.markdown("""
Esta plataforma integra **BioSTEAM** para el diseño de procesos químicos y **Gemini 1.5 Flash** para el análisis técnico. 
Ajusta las variables y observa los cambios en los balances y el diagrama.
""")

# Configuración Segura de API de Gemini
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.sidebar.warning("⚠️ Configura GEMINI_API_KEY en los Secrets de Streamlit.")

# ==========================================
# FUNCIÓN NÚCLEO DE SIMULACIÓN (BioSTEAM)
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_v220):
    # Limpieza del flowsheet para evitar errores de ID duplicado al mover sliders
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
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25+273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # 4. Creación del Sistema y Simulación
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    
    return sys_bio

# ==========================================
# INTERFAZ DE USUARIO (BARRA LATERAL)
# ==========================================
with st.sidebar:
    st.header("⚙️ Variables de Proceso")
    f_agua = st.slider("Agua (kg/h)", 500, 1500, 900)
    f_etanol = st.slider("Etanol (kg/h)", 50, 300, 100)
    t_v220 = st.slider("Temp. Calentamiento (°C)", 80, 115, 95)
    st.divider()
    st.caption("BioSTEAM v1.0 | Gemini 1.5 Flash")

# ==========================================
# LÓGICA PRINCIPAL Y RENDERIZADO
# ==========================================
try:
    # Ejecutar simulación
    sistema = run_simulation(f_agua, f_etanol, t_v220)
    
    # Recuperar datos de producto
    corrientes = sistema.streams
    prod = next((s for s in corrientes if s.ID == "Producto_Final"), None)
    
    # Métricas clave en la parte superior
    if prod:
        c1, c2, c3 = st.columns(3)
        pureza = (prod.imass['Ethanol'] / prod.F_mass) if prod.F_mass > 0 else 0
        c1.metric("Pureza Etanol", f"{pureza:.1%}")
        c2.metric("Producción Destilado", f"{prod.F_mass:.2f} kg/h")
        c3.metric("Etanol Recuperado", f"{prod.imass['Ethanol']:.2f} kg/h")

    # Organización en Tabs
    tab1, tab2, tab3 = st.tabs(["📊 Balances", "📐 Diagrama PFD", "🤖 Tutor IA"])

    with tab1:
        st.subheader("Balance de Materia")
        datos_m = []
        for s in corrientes:
            if s.F_mass > 0.01:
                datos_m.append({
                    "ID Corriente": s.ID,
                    "Temp (°C)": round(s.T - 273.15, 2),
                    "Flujo Total (kg/h)": round(s.F_mass, 2),
                    "Etanol (kg/h)": round(s.imass['Ethanol'], 2)
                })
        df_materia = pd.DataFrame(datos_m)
        st.table(df_materia)

    with tab2:
        st.subheader("Diagrama del Proceso (PFD)")
        try:
            # Extracción segura del código DOT para Graphviz
            pfd_source = sistema.diagram(kind='surface')
            if pfd_source and hasattr(pfd_source, 'source'):
                st.graphviz_chart(pfd_source.source)
            else:
                st.graphviz_chart(str(pfd_source))
        except Exception as diag_err:
            st.warning("No se pudo renderizar el gráfico dinámico.")
            st.info("Orden de equipos: P100 → W210 → W220 → V100 → V1 → W310")

    with tab3:
        st.subheader("Análisis con IA (Gemini 1.5 Flash)")
        if st.button("Generar Reporte IA"):
            if "GEMINI_API_KEY" in st.secrets:
                with st.spinner("Consultando al experto..."):
                    try:
                        # Usar el alias -latest para máxima compatibilidad
                        model = genai.GenerativeModel('gemini-1.5-flash-latest')
                        
                        prompt = f"""
                        Actúa como un ingeniero químico experto. Analiza estos resultados de BioSTEAM:
                        {df_materia.to_string()}
                        
                        Explica brevemente si la separación en el Flash V1 es adecuada a {t_v220}°C 
                        y qué componente se está recuperando mayoritariamente en el vapor.
                        """
                        response = model.generate_content(prompt)
                        
                        if response.text:
                            st.info("### Informe del Tutor")
                            st.markdown(response.text)
                        else:
                            st.warning("La IA no devolvió resultados. Revisa tu cuota.")
                    except Exception as ai_err:
                        st.error(f"Error de comunicación con Gemini: {ai_err}")
            else:
                st.error("Configura la GEMINI_API_KEY en los Secrets de Streamlit.")

except Exception as e:
    st.error(f"Error general en la aplicación: {e}")
