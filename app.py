import streamlit as st
import sys
import json
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# 1. PARCHE DE COMPATIBILIDAD
try:
    import altair.vegalite.v5 as lv5
    sys.modules['altair.vegalite.v4'] = lv5
except ImportError:
    pass

# ==========================================
# CONFIGURACIÓN
# ==========================================
st.set_page_config(page_title="BioSTEAM Industrial Pro", layout="wide")
st.title("🏭 Planta de Bioetanol: Simulación e Indicadores Económicos")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================
# EXTRACCIÓN DE DATOS PARA TOOLTIPS
# ==========================================
def obtener_datos_completos(sistema):
    data_map = {}
    # Datos de Equipos
    for unit in sistema.units:
        out = unit.outs[0] if unit.outs else None
        data_map[unit.ID] = {
            "Nombre": f"Unidad {unit.ID}",
            "T": f"{out.T - 273.15:.1f} °C" if out else "N/A",
            "P": f"{out.P / 101325:.2f} atm" if out else "N/A",
            "Flow": f"{out.F_mass:.1f} kg/h" if out else "N/A",
            "Duty": f"Q: {abs(unit.Q)/1000:.1f} kW" if hasattr(unit, 'Q') else "N/A"
        }
    # Datos de Corrientes
    for s in sistema.streams:
        data_map[s.ID] = {
            "Nombre": f"Corriente {s.ID}",
            "T": f"{s.T - 273.15:.1f} °C",
            "P": f"{s.P / 101325:.2f} atm",
            "Flow": f"{s.F_mass:.1f} kg/h",
            "Duty": f"Et: {s.imass['Ethanol']:.1f} kg/h | H2O: {s.imass['Water']:.1f} kg/h"
        }
    return json.dumps(data_map)

# ==========================================
# SIMULACIÓN
# ==========================================
with st.sidebar:
    st.header("🌡️ Parámetros")
    t_feed = st.slider("Temp. Alimentación (°C)", 15, 60, 25)
    t_w220 = st.slider("Temp. Salida W220 (°C)", 80, 120, 98)
    p_v100 = st.slider("Presión V100 (Pa)", 10000, 200000, 101325)

bst.main_flowsheet.clear()
chemicals = tmo.Chemicals(["Water", "Ethanol"])
bst.settings.set_thermo(chemicals)

# Corrientes y Equipos
mosto = bst.Stream("R1", Water=900, Ethanol=100, units="kg/hr", T=t_feed + 273.15)
vinazas = bst.Stream("R2", Water=200, T=95+273.15)
P100 = bst.Pump("P100", ins=mosto, P=4*101325)
W210 = bst.HXprocess("W210", ins=(P100-0, vinazas), outs=("R3", "R4"))
W220 = bst.HXutility("W220", ins=W210-0, outs="R5", T=t_w220 + 273.15)
V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="R6", P=p_v100)
V1 = bst.Flash("V1", ins=V100-0, outs=("R7", "R8"), P=p_v100, Q=0)
W310 = bst.HXutility("W310", ins=V1-0, outs="R9", T=25+273.15)
P200 = bst.Pump("P200", ins=V1-1, outs=vinazas, P=3*101325)

sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
sys_bio.simulate()

# ==========================================
# VISUALIZACIÓN INTERACTIVA
# ==========================================
st.subheader("🎯 PFD Interactivo Real")
json_data = obtener_datos_completos(sys_bio)

html_pfd = f"""
<div id="wrapper" style="width: 100%; position: relative; border: 1px solid #ccc; background: white;">
    <div id="tt" style="position: absolute; display: none; background: #000; color: #0f0; padding: 12px; font-family: monospace; font-size: 13px; z-index: 9999; pointer-events: none; border: 1px solid #0f0; border-radius: 4px;"></div>
    <svg viewBox="0 0 2161 1686" width="100%" preserveAspectRatio="xMidYMid meet">
        <rect id="P100" class="n" x="100" y="50" width="150" height="100" fill="transparent" stroke="red" stroke-width="3"/>
        <rect id="R1" class="n" x="50" y="50" width="40" height="40" fill="transparent" stroke="blue" stroke-width="3"/>
    </svg>
    <style>.n {{ cursor: crosshair; }} .n:hover {{ fill: rgba(0,0,0,0.1); }}</style>
    <script>
        const d = {json_data};
        const tt = document.getElementById('tt');
        document.querySelectorAll('.n').forEach(el => {{
            el.addEventListener('mousemove', (e) => {{
                const info = d[el.id];
                if(!info) return;
                tt.style.display = 'block';
                tt.innerHTML = `<strong>${{info.Nombre}}</strong><br>T: ${{info.T}}<br>P: ${{info.P}}<br>Flujo: ${{info.Flow}}<br>${{info.Duty}}`;
                tt.style.left = (e.pageX + 15) + 'px'; tt.style.top = (e.pageY + 15) + 'px';
            }});
            el.addEventListener('mouseleave', () => tt.style.display = 'none');
        }});
    </script>
</div>
"""
st.components.v1.html(html_pfd, height=600)

# ==========================================
# TUTOR Y FINAL
# ==========================================
if prompt := st.chat_input("Consulta técnica:"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("assistant"):
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(f"Contexto: Planta Bioetanol. Usuario: {prompt}")
            st.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})
        except Exception as e:
            st.error(f"Error IA: {e}")
