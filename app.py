import streamlit as st
import sys
import json
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# Parche Altair
try:
    import altair.vegalite.v5 as lv5
    sys.modules['altair.vegalite.v4'] = lv5
except ImportError:
    pass

st.set_page_config(page_title="BioSTEAM Industrial Pro", layout="wide")
st.title("🏭 Planta de Bioetanol: Simulación e Indicadores Económicos")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
def run_full_simulation(params):
    bst.main_flowsheet.clear()
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)
    
    # Definición de corrientes con IDs mapeables al SVG (R1, R10, etc)
    mosto = bst.Stream("R1", Water=900, Ethanol=100, units="kg/hr", T=params['t_feed'] + 273.15)
    vinazas_retorno = bst.Stream("R10", Water=200, T=95+273.15)
    
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), outs=("R2", "R4"))
    W220 = bst.HXutility("W220", ins=W210-0, outs="R3", T=params['t_w220'] + 273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="R5", P=params['p_v100'])
    V1 = bst.Flash("V1", ins=V100-0, outs=("R7", "R8"), P=params['p_v100'], Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="R9", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)
    
    sys_bio = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys_bio.simulate()
    return sys_bio

def obtener_datos_completos(sistema):
    data_map = {}
    for unit in sistema.units:
        power = unit.power_utility.rate if hasattr(unit, 'power_utility') else 0
        data_map[unit.ID] = {"Tipo": "Equipo", "Nombre": unit.ID, "Duty": f"{abs(unit.Q)/1000:.1f} kW", "Potencia": f"{power:.2f} kW"}
    for s in sistema.streams:
        data_map[s.ID] = {"Tipo": "Corriente", "Nombre": s.ID, "T": f"{s.T - 273.15:.1f} °C", "P": f"{s.P/101325:.2f} atm", 
                          "Flow": f"{s.F_mass:.1f} kg/h", "Comp": f"Et: {s.imass['Ethanol']:.1f} kg/h | W: {s.imass['Water']:.1f} kg/h"}
    return json.dumps(data_map)
t_feed = st.sidebar.slider("Temp. Mosto (°C)", 15, 60, 25)
t_w220 = st.sidebar.slider("Temp. W220 (°C)", 80, 120, 98)
p_v100 = st.sidebar.slider("Presión V100 (Pa)", 10000, 200000, 101325)

sistema = run_full_simulation({'t_feed': t_feed, 't_w220': t_w220, 'p_v100': p_v100})
json_data = obtener_datos_completos(sistema)

st.components.v1.html(f"""
    <div id="pfd-tooltip" style="position:absolute; background:#0f172a; color:#fff; padding:10px; border-radius:5px; font-size:12px; display:none; border:1px solid #38bdf8; z-index:1000; pointer-events:none;"></div>
    <script>
        const simData = {json_data};
        document.querySelectorAll('.interactivo-nodo').forEach(el => {{
            el.onmousemove = (e) => {{
                const d = simData[el.id];
                if(!d) return;
                const tt = document.getElementById('pfd-tooltip');
                tt.style.display = 'block'; tt.style.left = (e.pageX+15)+'px'; tt.style.top = (e.pageY+15)+'px';
                tt.innerHTML = `<strong>${{d.Nombre}}</strong><br>${{d.T ? 'T: '+d.T+'<br>':''}}${{d.Flow ? 'Flujo: '+d.Flow+'<br>':''}}<small style="color:#94a3b8">${{d.Comp || ''}}</small><br><span style="color:#fbbf24">Potencia: ${{d.Potencia || 'N/A'}}</span>`;
            }};
            el.onmouseleave = () => document.getElementById('pfd-tooltip').style.display = 'none';
        }});
    </script>
""", height=600)
