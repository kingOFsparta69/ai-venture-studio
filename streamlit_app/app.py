# streamlit_app/app.py
import os, json, re, time, io
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import google.generativeai as genai
from jinja2 import Template

# --- Lizenz-Import (mit Fallback) -------------------------------------------------
try:
    from utils.licensing import verify_license, plan_limits
except Exception:
    # Fallback: einfache Lizenzprüfung (HMAC-signiert)
    import base64, hmac, hashlib

    def _b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    def _unb64url(s: str) -> bytes:
        pad = "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s + pad)

    def verify_license(license_token: str, secret: str):
        try:
            body_b64, sig_b64 = license_token.split(".", 1)
            body = _unb64url(body_b64)
            expected = hmac.new(secret.encode(), body, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64url(sig_b64)):
                return False, None, "invalid-signature"
            payload = json.loads(body.decode())
            if payload.get("exp") and time.time() > int(payload["exp"]):
                return False, payload, "expired"
            return True, payload, "ok"
        except Exception:
            return False, None, "malformed"

    def plan_limits(plan: str) -> dict:
        if plan == "agency":
            return {"max_runs_per_day": 200, "max_ideas": 15, "allow_export": True}
        if plan == "pro":
            return {"max_runs_per_day": 20, "max_ideas": 12, "allow_export": True}
        return {"max_runs_per_day": 2, "max_ideas": 5, "allow_export": False}

# --- Grund-Setup ------------------------------------------------------------------
st.set_page_config(page_title="AI Venture Studio", page_icon="🚀", layout="wide")

st.sidebar.title("⚙️ Setup")
gemini_key = st.sidebar.text_input("Gemini API Key", type="password")
license_token = st.sidebar.text_input("Lizenzschlüssel (optional)", type="password").strip()
signing_secret = st.secrets.get("LICENSE_SIGNING_SECRET", os.getenv("LICENSE_SIGNING_SECRET", "dev-secret"))

valid_license, license_payload, lic_status = (
    verify_license(license_token, signing_secret) if license_token else (False, None, "missing")
)
active_plan = (license_payload or {}).get("plan", "free") if valid_license else "free"
limits = plan_limits(active_plan)

st.sidebar.markdown(f"**Plan:** `{active_plan}`")
st.sidebar.caption(f"🔐 License status: {lic_status}")
st.sidebar.caption(f"Runs/Tag: {limits['max_runs_per_day']} • max Ideen/Run: {limits['max_ideas']} • Export: {'ja' if limits['allow_export'] else 'nein'}")

# Gemini Key prüfen
if not gemini_key:
    st.info("Bitte links deinen **Gemini API Key** eintragen. Key holen: https://aistudio.google.com/app/apikey")
    st.stop()

# Gemini konfigurieren
genai.configure(api_key=gemini_key)
MODEL_ID = st.secrets.get("MODEL_ID", os.getenv("MODEL_ID", "models/gemini-2.5-flash"))

# Tagesnutzung zählen (einfacher In-Memory-Counter)
if "usage" not in st.session_state:
    st.session_state.usage = {"count_today": 0, "date": datetime.utcnow().date().isoformat()}
today = datetime.utcnow().date().isoformat()
if st.session_state.usage["date"] != today:
    st.session_state.usage = {"count_today": 0, "date": today}
if st.session_state.usage["count_today"] >= limits["max_runs_per_day"]:
    st.error("Tageslimit erreicht. Upgrade auf Pro/Agency für mehr Runs.")
    st.stop()

# --- Hilfsfunktionen --------------------------------------------------------------
@st.cache_data(show_spinner=False)
def render_lp(idea: dict) -> str:
    tpl = Template("""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ name }} - {{ one_liner }}</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:0 16px}
.hero{padding:36px 0;text-align:center}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin:16px 0}
.card{padding:14px;border:1px solid #eee;border-radius:12px}
footer{text-align:center;padding:16px;color:#666}
input,button{padding:10px 12px;border-radius:10px;border:1px solid #ddd}
</style></head>
<body>
<section class="hero"><h1>{{ name }}</h1><p>{{ one_liner }}</p>
<form><input type="email" placeholder="E-Mail für Early Access" required /><button>Warteliste</button></form></section>
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

def gemini_json(prompt: str, temperature: float = 0.55):
    model = genai.GenerativeModel(model_name=MODEL_ID)
    res = model.generate_content(prompt, generation_config={"temperature": temperature})
    txt = res.text or ""
    # robustes JSON-Parsing
    try:
        return json.loads(txt)
    except Exception:
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

Gib ausschließlich **gültiges JSON** im Format:
{{"ideas":[{{"name":"","one_liner":"","description":"","unique_angle":"",
"target_user":"","jobs_to_be_done":["",""]}}]}}
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
    total = (
        _i(s.get("market_potential", 0))
        + _i(s.get("differentiation_moat", 0))
        + (10 - eff) + (10 - risk)
        + _i(s.get("time_to_value", 0))
    )
    return {**idea, "score_details": s, "total_score": int(total)}

# --- Session-Container ------------------------------------------------------------
if "results" not in st.session_state:
    st.session_state.results = {"ideas": None, "scored": None, "df": None, "params": None}

# --- UI ---------------------------------------------------------------------------
st.title("🚀 AI Venture Studio")

with st.form("controls"):
    col1, col2, col3 = st.columns(3)
    with col1:
        domain = st.text_input("Branche / Markt", "Pflegebranche", key="inp_domain")
    with col2:
        audience = st.text_input("Zielgruppe", "Stationsleitungen", key="inp_audience")
    with col3:
        problem = st.text_input("Kernproblem", "Dienstplan-Chaos & Personalmangel", key="inp_problem")

    n_ideas = st.slider("Anzahl Ideen", 3, limits["max_ideas"], min(10, limits["max_ideas"]), key="inp_nideas")
    submitted = st.form_submit_button("Ideen generieren & bewerten")

# Reset-Button
if st.sidebar.button("🔄 Ergebnisse zurücksetzen"):
    st.session_state.results = {"ideas": None, "scored": None, "df": None, "params": None}
    st.success("Ergebnisse zurückgesetzt.")

# --- Ausführung nur bei Submit ----------------------------------------------------
if submitted:
    params = (domain, audience, problem, n_ideas)
    with st.spinner("Generiere Ideen ..."):
        ideas = gen_ideas(domain, audience, problem, n_ideas)
    with st.spinner("Bewerte Ideen ..."):
        scored = [score_one(x) for x in ideas]

    df = pd.DataFrame([{
        "name": x["name"],
        "one_liner": x["one_liner"],
        "market_potential": x["score_details"].get("market_potential", 0),
        "differentiation_moat": x["score_details"].get("differentiation_moat", 0),
        "build_effort": x["score_details"].get("build_effort", 0),
        "regulatory_risk": x["score_details"].get("regulatory_risk", 0),
        "time_to_value": x["score_details"].get("time_to_value", 0),
        "total_score": x["total_score"],
    } for x in scored]).sort_values("total_score", ascending=False)

    st.session_state.usage["count_today"] += 1
    st.session_state.results = {"ideas": ideas, "scored": scored, "df": df, "params": params}

# --- Anzeige (stabil bei Reruns) -------------------------------------------------
res = st.session_state.results
if res["df"] is None:
    st.info("So startest du: 1) API-Key links eintragen 2) Formular ausfüllen 3) Button klicken.")
else:
    df = res["df"]

    st.subheader("🏁 Ranking")
    df_view = df.rename(columns={
        "name": "Idee",
        "one_liner": "Kurzbeschreibung",
        "total_score": "Gesamtscore",
    })[["Idee", "Kurzbeschreibung", "Gesamtscore"]]
    st.dataframe(df_view, use_container_width=True)

    st.subheader("Top-Ideen")
    scored = res["scored"]
    top = scored[:3] if len(scored) >= 3 else scored
    for idea in top:
        with st.expander(f"{idea['name']} — Score {idea['total_score']}"):
            st.write(idea["one_liner"])
            st.write("**Unique angle:** ", idea["unique_angle"])
            st.write("**Beschreibung:** ", idea["description"])
            st.write("**JTBD:** ", ", ".join(idea.get("jobs_to_be_done", [])))
            st.markdown("---")
            st.markdown("**Landing-Preview**")
            st.components.v1.html(render_lp(idea), height=520, scrolling=True)

    # -------------------- Export (Pro/Agency) --------------------
    if limits["allow_export"]:
        st.success("Export freigeschaltet (Pro/Agency)")

        df_out = df.rename(columns={
            "name": "Idee",
            "one_liner": "Kurzbeschreibung",
            "market_potential": "Marktpotenzial (0–10)",
            "differentiation_moat": "Differenzierung/Moat (0–10)",
            "build_effort": "Aufwand (0–10)",
            "regulatory_risk": "Regulatorik-Risiko (0–10)",
            "time_to_value": "Time-to-Value (0–10)",
            "total_score": "Gesamtscore",
        })[
            ["Idee", "Kurzbeschreibung", "Gesamtscore",
             "Marktpotenzial (0–10)", "Differenzierung/Moat (0–10)",
             "Aufwand (0–10)", "Regulatorik-Risiko (0–10)", "Time-to-Value (0–10)"]
        ]

        # CSV: Semikolon + UTF-8-BOM (Excel-DE freundlich)
        csv_bytes = df_out.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("CSV herunterladen (DE, Excel-freundlich)", csv_bytes,
                           file_name="ideen_ranking.csv", mime="text/csv")

        # Excel: nur wenn XlsxWriter vorhanden
        try:
            import XlsxWriter  # noqa: F401  (nur Verfügbarkeit prüfen)
            xbuf = io.BytesIO()
            with pd.ExcelWriter(xbuf, engine="xlsxwriter") as writer:
                df_out.to_excel(writer, index=False, sheet_name="Ranking")
                wb = writer.book
                ws = writer.sheets["Ranking"]
                header_fmt = wb.add_format({"bold": True, "text_wrap": True, "valign": "top"})
                for col_idx, col in enumerate(df_out.columns):
                    max_len = max([len(str(col))] + [len(str(v)) for v in df_out[col].astype(str).values])
                    ws.set_column(col_idx, col_idx, min(max_len + 2, 60))
                ws.set_row(0, 24, header_fmt)
                ws.autofilter(0, 0, len(df_out), len(df_out.columns) - 1)
            st.download_button("Excel herunterladen (formatiert)",
                               xbuf.getvalue(),
                               file_name="ideen_ranking.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception:
            st.info("Hinweis: Excel-Export benötigt XlsxWriter. Wird aktiv, sobald das Deployment mit aktualisiertem requirements.txt durch ist.")
    else:
        st.warning("Export ist in der Free-Tier deaktiviert. Upgrade auf Pro/Agency, um CSV/ZIP zu exportieren.")

