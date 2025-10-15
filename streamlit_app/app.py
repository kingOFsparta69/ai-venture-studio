import os, re, json, time
import pandas as pd
import streamlit as st
import google.generativeai as genai
from jinja2 import Template
from datetime import datetime
from pathlib import Path

from utils.licensing import verify_license, plan_limits

st.set_page_config(page_title="AI Venture Studio", page_icon="🚀", layout="wide")

st.sidebar.title("⚙️ Setup")
gemini_key = st.sidebar.text_input("Gemini API Key", type="password")
license_token = st.sidebar.text_input("Lizenzschlüssel (optional)", type="password")
signing_secret = st.secrets.get("LICENSE_SIGNING_SECRET", os.getenv("LICENSE_SIGNING_SECRET", "dev-secret"))

valid_license, license_payload, lic_status = verify_license(license_token, signing_secret) if license_token else (False, None, "missing")
active_plan = (license_payload or {}).get("plan", "free") if valid_license else "free"
limits = plan_limits(active_plan)

st.sidebar.markdown(f"**Plan:** `{active_plan}`")
st.sidebar.caption(f"Runs/Tag: {limits['max_runs_per_day']}, max Ideen/Run: {limits['max_ideas']}, Export: {'ja' if limits['allow_export'] else 'nein'}")

if not gemini_key:
    st.info("Bitte im Sidebar deinen **Gemini API Key** eintragen, um zu starten.")
    st.stop()

genai.configure(api_key=gemini_key)
MODEL_ID = st.secrets.get("MODEL_ID", os.getenv("MODEL_ID", "models/gemini-2.5-flash"))

if "usage" not in st.session_state:
    st.session_state.usage = {"count_today": 0, "date": datetime.utcnow().date().isoformat()}
today = datetime.utcnow().date().isoformat()
if st.session_state.usage["date"] != today:
    st.session_state.usage = {"count_today": 0, "date": today}

if st.session_state.usage["count_today"] >= limits["max_runs_per_day"]:
    st.error("Tageslimit erreicht. Upgrade auf Pro/Agency für mehr Runs.")
    st.stop()

st.title("🚀 AI Venture Studio")
col1, col2, col3 = st.columns(3)
with col1:
    domain = st.text_input("Branche / Markt", "Pflegebranche")
with col2:
    audience = st.text_input("Zielgruppe", "Stationsleitungen")
with col3:
    problem = st.text_input("Kernproblem", "Dienstplan-Chaos & Personalmangel")

n_ideas = st.slider("Anzahl Ideen", 3, limits["max_ideas"], min(10, limits["max_ideas"]))

run = st.button("Ideen generieren & bewerten")

@st.cache_data(show_spinner=False)
def render_lp(idea: dict) -> str:
    tpl = Template("""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ name }} - {{ one_liner }}</title>
<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:0 16px}.hero{padding:36px 0;text-align:center}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin:16px 0}.card{padding:14px;border:1px solid #eee;border-radius:12px}footer{text-align:center;padding:16px;color:#666}input,button{padding:10px 12px;border-radius:10px;border:1px solid #ddd}</style></head>
<body>
<section class="hero"><h1>{{ name }}</h1><p>{{ one_liner }}</p><form><input type="email" placeholder="E-Mail für Early Access" required /><button>Warteliste</button></form></section>
<section class="grid">
<div class="card"><b>Warum?</b><p>{{ description }}</p></div>
<div class="card"><b>Unique Angle</b><p>{{ unique_angle }}</p></div>
<div class="card"><b>Für wen?</b><p>{{ target_user }}</p></div>
<div class="card"><b>JTBD</b><ul>{% for j in jobs_to_be_done %}<li>{{ j }}</li>{% endfor %}</ul></div>
</section>
<footer>Demo Landing-Page</footer>
</body></html>
""")
    return tpl.render(**idea)

def gemini_json(prompt: str, temperature: float=0.5):
    model = genai.GenerativeModel(model_name=MODEL_ID)
    res = model.generate_content(prompt, generation_config={"temperature": temperature})
    txt = res.text or ""
    try:
        return json.loads(txt)
    except Exception:
        import re
        m = re.search(r"```json\s*([\s\S]*?)```", txt)
        if m:
            try: return json.loads(m.group(1))
            except: pass
        m = re.search(r"\{[\s\S]*\}$", txt.strip())
        if m:
            try: return json.loads(m.group(0))
            except: pass
        raise ValueError("Konnte JSON nicht parsen:\n" + txt)

def gen_ideas(domain, audience, problem, n):
    prompt = f"""
Du bist ein Innovations-Copilot. Erzeuge {n} neuartige Produktideen (SaaS, API, Tool, Service) für:
- Branche: "{domain}"
- Zielgruppe: "{audience}"
- Kernproblem: "{problem}"

Gib ausschließlich gültiges JSON im Format:
{{"ideas":[{{"name":"","one_liner":"","description":"","unique_angle":"","target_user":"","jobs_to_be_done":["",""]}}]}}
"""
    obj = gemini_json(prompt, temperature=0.6)
    return obj.get("ideas", [])

def score_one(idea: dict):
    prompt = f"""
Bewerte die Idee. Antworte nur JSON:
{{
  "market_potential": 0,
  "differentiation_moat": 0,
  "build_effort": 0,
  "regulatory_risk": 0,
  "time_to_value": 0,
  "rationale": ""
}}

Idee:
NAME: {idea.get('name','')}
ONE_LINER: {idea.get('one_liner','')}
BESCHREIBUNG: {idea.get('description','')}
ZIEL: {idea.get('target_user','')}
UNIQUE_ANGLE: {idea.get('unique_angle','')}
"""
    s = gemini_json(prompt, temperature=0.3)
    def _i(x): 
        try: return int(x)
        except: return 0
    eff  = _i(s.get("build_effort", 0))
    risk = _i(s.get("regulatory_risk", 0))
    total = _i(s.get("market_potential",0)) + _i(s.get("differentiation_moat",0)) + (10-eff) + (10-risk) + _i(s.get("time_to_value",0))
    return {**idea, "score_details": s, "total_score": int(total)}

if run:
    with st.spinner("Generiere Ideen ..."):
        ideas = gen_ideas(domain, audience, problem, n_ideas)
    with st.spinner("Bewerte Ideen ..."):
        scored = [score_one(x) for x in ideas]
    st.session_state.usage["count_today"] += 1

    df = pd.DataFrame([{
        "name": x["name"],
        "one_liner": x["one_liner"],
        "market_potential": x["score_details"].get("market_potential",0),
        "differentiation_moat": x["score_details"].get("differentiation_moat",0),
        "build_effort": x["score_details"].get("build_effort",0),
        "regulatory_risk": x["score_details"].get("regulatory_risk",0),
        "time_to_value": x["score_details"].get("time_to_value",0),
        "total_score": x["total_score"]
    } for x in scored]).sort_values("total_score", ascending=False)

    st.subheader("🏁 Ranking")
    st.dataframe(df, use_container_width=True)

    top = scored[:3] if len(scored) >= 3 else scored
    st.subheader("Top-Ideen")
    for idea in top:
        with st.expander(f"{idea['name']} — Score {idea['total_score']}"):
            st.write(idea["one_liner"])
            st.write("**Unique angle:** ", idea["unique_angle"])
            st.write("**Beschreibung:** ", idea["description"])
            st.write("**JTBD:** ", ", ".join(idea.get("jobs_to_be_done", [])))
            st.markdown("---")
            st.markdown("**Landing-Preview**")
            st.components.v1.html(render_lp(idea), height=500, scrolling=True)

   if limits["allow_export"]:
    st.success("Export freigeschaltet (Pro/Agency)")

    # --- hübschere Spaltennamen & Reihenfolge ---
    df_out = df.rename(columns={
        "name": "Idee",
        "one_liner": "Kurzbeschreibung",
        "market_potential": "Marktpotenzial (0–10)",
        "differentiation_moat": "Differenzierung/Moat (0–10)",
        "build_effort": "Aufwand (0–10)",
        "regulatory_risk": "Regulatorik-Risiko (0–10)",
        "time_to_value": "Time-to-Value (0–10)",
        "total_score": "Gesamtscore"
    })[
        ["Idee","Kurzbeschreibung","Gesamtscore",
         "Marktpotenzial (0–10)","Differenzierung/Moat (0–10)",
         "Aufwand (0–10)","Regulatorik-Risiko (0–10)","Time-to-Value (0–10)"]
    ]

    # --- CSV für DE/Excel: Semikolon + UTF-8-BOM ---
    csv_bytes = df_out.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("CSV herunterladen (DE, Excel-freundlich)", csv_bytes,
                       file_name="ideen_ranking.csv", mime="text/csv")

    # --- XLSX mit Auto-Width & Header-Format ---
    import io
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="xlsxwriter") as writer:
        df_out.to_excel(writer, index=False, sheet_name="Ranking")
        wb  = writer.book
        ws  = writer.sheets["Ranking"]
        header_fmt = wb.add_format({"bold": True, "text_wrap": True, "valign": "top", "border": 0})
        for col_idx, col in enumerate(df_out.columns):
            # Auto-Breite: max( header, Daten )
            max_len = max([len(str(col))] + [len(str(v)) for v in df_out[col].astype(str).values]) 
            ws.set_column(col_idx, col_idx, min(max_len + 2, 60))
        ws.set_row(0, 24, header_fmt)
        # Optionale Filterzeile
        ws.autofilter(0, 0, len(df_out), len(df_out.columns)-1)
    st.download_button("Excel herunterladen (formatiert)", xbuf.getvalue(),
                       file_name="ideen_ranking.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.warning("Export ist in der Free-Tier deaktiviert. Upgrade auf Pro/Agency, um CSV/ZIP zu exportieren.")

else:
    st.caption("Tipp: Stelle Branche/Zielgruppe/Problem ein und klicke auf **Ideen generieren & bewerten**.")

st.sidebar.markdown("---")
st.sidebar.subheader("💳 Upgrade")
st.sidebar.write("Hole dir **Pro** oder **Agency** für mehr Runs, mehr Ideen und Export.")
st.sidebar.write("Beispiel-Flow: Stripe Checkout → Webhook → Lizenz per E-Mail → hier eintragen.")
