import streamlit as st
import json
import requests
import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="UK Science Parks Intelligence",
    page_icon="🔬",
    layout="wide"
)

# ─── PASSWORD ─────────────────────────────────────────────────────────────────
PASSWORD = "sciparks2026"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔬 UK Science Parks Intelligence")
    st.subheader("Please enter the access password")
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if pw == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

# ─── LOAD DATA ────────────────────────────────────────────────────────────────
@st.cache_data
def load_parks():
    with open("uk_science_parks.json") as f:
        return json.load(f)

@st.cache_data
def load_ofcom():
    try:
        with open("area_data.json") as f:
            return json.load(f)
    except Exception:
        return {}

parks_data = load_parks()
ofcom_data = load_ofcom()

# ─── FLATTEN NESTED OFCOM STRUCTURE ──────────────────────────────────────────
def flatten_ofcom(raw):
    """
    area_data.json uses nested sub-objects: connectivity, mobile, energy.
    This converts them to the flat field names the rest of the app expects.
    Returns an empty dict (falsy) if the raw entry is empty/None.
    """
    if not raw:
        return {}

    conn  = raw.get("connectivity") or {}
    mob   = raw.get("mobile") or {}
    # energy not used in scoring yet but available if needed

    flat = {
        # Connectivity
        "full_fibre_pct":        conn.get("full_fibre_pct"),
        "gigabit_pct":           conn.get("gigabit_pct"),
        "superfast_pct":         conn.get("superfast_pct"),
        "no_decent_pct":         conn.get("no_decent_pct"),
        "full_fibre_takeup_pct": conn.get("ff_takeup_pct"),   # key name differs
        "avg_data_usage_gb":     conn.get("avg_data_usage_gb"),
        # Mobile
        "indoor_4g_pct":         mob.get("indoor_4g_all_operators_pct"),
        "outdoor_4g_pct":        mob.get("outdoor_4g_all_operators_pct"),
        "outdoor_5g_pct":        mob.get("outdoor_5g_all_operators_pct"),
        "indoor_voice_pct":      mob.get("indoor_voice_all_operators_pct"),
    }

    # Guard against legacy merged-council entries that have all zeros:
    # treat them as no-data so they don't score 20/100 misleadingly.
    conn_vals = [v for v in [flat["full_fibre_pct"], flat["gigabit_pct"],
                              flat["indoor_4g_pct"], flat["outdoor_5g_pct"]] if v is not None]
    if conn_vals and all(v == 0 for v in conn_vals):
        return {}   # signal "no usable data"

    return flat

# ─── HELPERS: DATA LOOKUPS ────────────────────────────────────────────────────
def get_ofcom(local_authority):
    if not ofcom_data:
        return {}
    la_lower = local_authority.lower().strip()
    for key, val in ofcom_data.items():
        if key.lower().strip() == la_lower:
            return flatten_ofcom(val)
    # fuzzy
    for key, val in ofcom_data.items():
        if la_lower in key.lower() or key.lower() in la_lower:
            return flatten_ofcom(val)
    return {}

def get_companies(postcode, api_key, max_results=20):
    if not api_key or not postcode:
        return []
    try:
        pc = postcode.replace(" ", "+")
        url = f"https://api.company-information.service.gov.uk/search/companies?q={pc}&items_per_page={max_results}"
        r = requests.get(url, auth=(api_key, ""), timeout=8)
        if r.status_code == 200:
            data = r.json()
            return data.get("items", [])
    except Exception:
        pass
    return []

def score_connectivity(ofcom):
    if not ofcom:
        return None, "No data"
    ff = ofcom.get("full_fibre_pct", 0) or 0
    gig = ofcom.get("gigabit_pct", 0) or 0
    sup = ofcom.get("superfast_pct", 0) or 0
    no_decent = ofcom.get("no_decent_pct", 0) or 0
    score = min(40, ff * 0.4) + min(20, gig * 0.3) + min(20, sup * 0.2) + max(0, 20 - no_decent * 2)
    score = round(score)
    if score >= 70:
        rag = "Green"
    elif score >= 40:
        rag = "Amber"
    else:
        rag = "Red"
    return score, rag

def score_mobile(ofcom):
    if not ofcom:
        return None
    g4 = ofcom.get("indoor_4g_pct", 0) or 0
    g5 = ofcom.get("outdoor_5g_pct", 0) or 0
    voice = ofcom.get("indoor_voice_pct", 0) or 0
    score = min(40, g4 * 0.4) + min(40, g5 * 0.4) + min(20, voice * 0.2)
    return round(score)

def classify_companies(companies):
    sector_map = {
        "Research & Development": [72],
        "Software & IT": [62, 63],
        "Pharma & Biotech": [21, 8630],
        "Medical Devices": [2660, 8600],
        "Engineering": [25, 28, 33],
        "Energy Tech": [35, 3511, 3512],
        "Telecoms": [61],
        "Manufacturing": [24, 26, 27, 29, 30],
    }
    counts = {k: 0 for k in sector_map}
    for co in companies:
        if co.get("company_status", "").lower() != "active":
            continue
        for sic in co.get("sic_codes", []) or []:
            try:
                code = int(str(sic)[:4])
                for sector, codes in sector_map.items():
                    if code in codes:
                        counts[sector] += 1
            except Exception:
                pass
    return {k: v for k, v in counts.items() if v > 0}

def generate_opportunities(park, ofcom, companies):
    ops = []
    ff = ofcom.get("full_fibre_pct", 0) or 0
    gig = ofcom.get("gigabit_pct", 0) or 0
    takeup = ofcom.get("full_fibre_takeup_pct", 0) or 0
    g4 = ofcom.get("indoor_4g_pct", 0) or 0
    g5 = ofcom.get("outdoor_5g_pct", 0) or 0
    sector = (park.get("sector") or "").lower()
    notes = (park.get("notes") or "").lower()
    tenants = park.get("tenants", "") or ""

    if ff < 60:
        ops.append("Campus-wide full fibre upgrade — current LA area significantly below research-grade threshold")
    if gig < 50:
        ops.append("Gigabit connectivity programme for high-bandwidth research data transmission and cloud uplinks")
    if ff > 60 and takeup < 30:
        ops.append("Managed connectivity migration — full fibre available but take-up low; active programme needed")
    if g4 < 80:
        ops.append("Indoor 4G/mobile enhancement — below threshold for campus working and IoT lab equipment")
    if g5 < 40:
        ops.append("5G readiness / private 5G network for smart campus applications and sensor networks")

    if any(x in sector or x in notes for x in ["genomic", "biomedical", "sequenc", "clinical"]):
        ops.append("Dedicated high-bandwidth research network tier for genomics/biomedical data transmission (100GB+ per sequencing run)")
    if any(x in sector or x in notes for x in ["space", "satellite", "itar", "defence"]):
        ops.append("Secure segregated network architecture for ITAR/export control compliance and sensitive research")
    if any(x in sector or x in notes for x in ["incubat", "early-stage", "spinout", "accelerat"]):
        ops.append("Flexible start-up connectivity packages with scalable bandwidth to match company growth stages")
    if any(x in sector or x in notes for x in ["nuclear", "fusion", "energy"]):
        ops.append("High-resilience network with dual-path routing for research process continuity")
    if any(x in sector or x in notes for x in ["ai", "gpu", "computing", "hpc", "deep tech"]):
        ops.append("10Gbps+ connectivity for GPU/HPC cluster operations and large model training data ingestion")
    if any(x in sector or x in notes for x in ["pharma", "clinical trial", "mhra"]):
        ops.append("Compliance-ready network with audit logging and access controls for MHRA/ICH regulatory environment")

    active_count = sum(1 for c in (companies or []) if c.get("company_status", "").lower() == "active")
    if active_count >= 20:
        ops.append(f"Campus-wide managed connectivity covering {active_count}+ active registered companies — economies of scale vs individual procurement")

    try:
        t_num = int("".join(filter(str.isdigit, str(tenants).split("+")[0].split(",")[0])))
        if t_num > 100:
            ops.append("Campus-scale network more cost-effective than per-tenant provision at this scale")
    except Exception:
        pass

    return ops[:8] if len(ops) > 8 else ops

def generate_flags(park, ofcom):
    flags = []
    ff = ofcom.get("full_fibre_pct", 0) or 0
    gig = ofcom.get("gigabit_pct", 0) or 0
    takeup = ofcom.get("full_fibre_takeup_pct", 0) or 0
    g4 = ofcom.get("indoor_4g_pct", 0) or 0
    g5 = ofcom.get("outdoor_5g_pct", 0) or 0
    sector = (park.get("sector") or "").lower()
    notes = (park.get("notes") or "").lower()

    if ff < 50:
        flags.append(("⚠ Full fibre below 50%", f"{ff:.1f}% availability — significantly below science campus threshold"))
    elif ff < 75:
        flags.append(("⚠ Full fibre availability gap", f"{ff:.1f}% — below 75% recommended for research operations"))
    if gig < 50:
        flags.append(("⚠ Gigabit coverage insufficient", f"{gig:.1f}% — inadequate for high-bandwidth research applications"))
    if ff > 50 and takeup < 25:
        flags.append(("⚠ Take-up gap identified", f"Full fibre available ({ff:.1f}%) but take-up only {takeup:.1f}% — migration programme needed"))
    if g4 < 75:
        flags.append(("⚠ Indoor 4G below threshold", f"{g4:.1f}% across all operators — affects campus mobility and IoT"))
    if g5 < 30:
        flags.append(("⚠ 5G readiness low", f"Outdoor 5G at {g5:.1f}% — limiting smart campus and future-proofing options"))
    if any(x in sector or x in notes for x in ["genomic", "biomedical", "sequenc"]):
        flags.append(("ℹ Life sciences data intensity", "Genomics/biomedical operations require dedicated high-bandwidth research links beyond standard connectivity"))
    if any(x in sector or x in notes for x in ["defence", "itar", "space", "nuclear", "secret"]):
        flags.append(("ℹ Sensitive sector — elevated security", "Potential ITAR/export control requirements; network architecture should include physical access security"))
    return flags

# ─── PDF GENERATION: SINGLE PARK ──────────────────────────────────────────────
NAVY   = colors.HexColor("#1F4E79")
TEAL   = colors.HexColor("#2E74B5")
LGREY  = colors.HexColor("#F2F4F7")
MGREY  = colors.HexColor("#D0D9E8")
AMBER  = colors.HexColor("#C55A00")
GREEN  = colors.HexColor("#375623")
RED_C  = colors.HexColor("#C00000")
WHITE  = colors.white

RAG_COLORS = {"Green": GREEN, "Amber": AMBER, "Red": RED_C}

def get_styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", fontSize=22, textColor=WHITE, fontName="Helvetica-Bold", spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", fontSize=11, textColor=colors.HexColor("#BDD7EE"), fontName="Helvetica"),
        "h2": ParagraphStyle("h2", fontSize=13, textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontSize=11, textColor=TEAL, fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", fontSize=9, textColor=colors.HexColor("#2C2C2C"), fontName="Helvetica", spaceAfter=4, leading=13),
        "small": ParagraphStyle("small", fontSize=7.5, textColor=colors.HexColor("#595959"), fontName="Helvetica-Oblique"),
        "caveat": ParagraphStyle("caveat", fontSize=8, textColor=colors.HexColor("#595959"), fontName="Helvetica-Oblique", spaceBefore=4),
        "flag": ParagraphStyle("flag", fontSize=9, textColor=AMBER, fontName="Helvetica-Bold"),
        "flagbody": ParagraphStyle("flagbody", fontSize=8.5, textColor=colors.HexColor("#2C2C2C"), fontName="Helvetica"),
        "opp": ParagraphStyle("opp", fontSize=9, textColor=NAVY, fontName="Helvetica"),
    }

def header_row(cells, widths, bg=None):
    row = [[Paragraph(str(c), ParagraphStyle("th", fontSize=8.5, textColor=WHITE, fontName="Helvetica-Bold"))] for c in cells]
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg or NAVY),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, MGREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t

def data_table(headers, rows, col_widths, zebra=True):
    h_style = ParagraphStyle("th2", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")
    b_style = ParagraphStyle("td", fontSize=8.5, textColor=colors.HexColor("#2C2C2C"), fontName="Helvetica")
    data = [[Paragraph(str(h), h_style) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c or ""), b_style) for c in r])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    ts = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, MGREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]
    if zebra:
        for i in range(1, len(data)):
            if i % 2 == 0:
                ts.append(("BACKGROUND", (0, i), (-1, i), LGREY))
    t.setStyle(TableStyle(ts))
    return t

def score_bar_table(label, score, rag, width=170*mm):
    bar_w = int((score or 0) / 100 * (width - 90*mm))
    bar_cell = Table([[""]], colWidths=[bar_w], rowHeights=[8*mm])
    bar_cell.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), RAG_COLORS.get(rag, MGREY))]))
    score_str = f"{score}/100  [{rag}]" if score is not None else "No data"
    row = [
        [Paragraph(label, ParagraphStyle("sl", fontSize=9, fontName="Helvetica-Bold", textColor=NAVY))],
        [bar_cell],
        [Paragraph(score_str, ParagraphStyle("sv", fontSize=9, fontName="Helvetica-Bold",
                                              textColor=RAG_COLORS.get(rag, MGREY)))],
    ]
    t = Table([row], colWidths=[50*mm, width - 90*mm, 40*mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0,0),(-1,-1), 0)]))
    return t

def build_park_header(story, park, styles):
    region = park.get("_region", "")
    cluster = park.get("_cluster", "")
    header_data = [[
        Paragraph(park["name"], styles["title"]),
        Paragraph(f"{park.get('location','')} · {park.get('postcode','')}", styles["subtitle"]),
        Paragraph(f"{region}  ›  {cluster}", styles["subtitle"]),
    ]]
    t = Table([header_data], colWidths=[180*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("PADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))

def build_park_profile_table(story, park, styles):
    story.append(Paragraph("Park Profile", styles["h2"]))
    fields = [
        ("Zone / Cluster", park.get("_cluster", "")),
        ("Location", park.get("location", "")),
        ("County", park.get("county", "")),
        ("Postcode", park.get("postcode", "")),
        ("Local Authority", park.get("local_authority", "")),
        ("Sector Focus", park.get("sector", "")),
        ("Tenants / Scale", park.get("tenants", "")),
        ("Operator", park.get("operator", "")),
        ("Status", park.get("status", "")),
        ("Website", park.get("website", "")),
    ]
    body_s = ParagraphStyle("td2", fontSize=8.5, fontName="Helvetica", textColor=colors.HexColor("#2C2C2C"))
    key_s = ParagraphStyle("tk", fontSize=8.5, fontName="Helvetica-Bold", textColor=NAVY)
    rows = []
    for i, (k, v) in enumerate(fields):
        bg = LGREY if i % 2 == 0 else WHITE
        rows.append(Table([[Paragraph(k, key_s), Paragraph(str(v), body_s)]],
                          colWidths=[50*mm, 120*mm]))
    t = Table([[r] for r in rows], colWidths=[170*mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), LGREY if i % 2 == 0 else WHITE) for i in range(len(rows))] +
                           [("GRID", (0, 0), (-1, -1), 0.4, MGREY), ("PADDING", (0, 0), (-1, -1), 0)]))
    story.append(t)
    if park.get("notes"):
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph(f"<b>Notes:</b> {park['notes']}", styles["body"]))
    story.append(Spacer(1, 5*mm))

def build_connectivity_section(story, ofcom, styles):
    story.append(Paragraph("Connectivity Profile", styles["h2"]))
    conn_score, conn_rag = score_connectivity(ofcom)
    mob_score = score_mobile(ofcom)

    if not ofcom:
        story.append(Paragraph("Ofcom data not available for this local authority.", styles["body"]))
        return

    story.append(score_bar_table("Broadband Connectivity Score", conn_score, conn_rag))
    story.append(Spacer(1, 3*mm))

    conn_rows = [
        ["Full Fibre availability", f"{ofcom.get('full_fibre_pct', 'N/A'):.1f}%" if ofcom.get('full_fibre_pct') is not None else 'N/A', "% of premises with full fibre available"],
        ["Gigabit-capable", f"{ofcom.get('gigabit_pct', 'N/A'):.1f}%" if ofcom.get('gigabit_pct') is not None else 'N/A', "% of premises with gigabit broadband"],
        ["Superfast (30Mbps+)", f"{ofcom.get('superfast_pct', 'N/A'):.1f}%" if ofcom.get('superfast_pct') is not None else 'N/A', "% of premises with superfast coverage"],
        ["No decent broadband", f"{ofcom.get('no_decent_pct', 'N/A'):.1f}%" if ofcom.get('no_decent_pct') is not None else 'N/A', "% with speeds below 10Mbps / 1Mbps"],
        ["Full fibre take-up", f"{ofcom.get('full_fibre_takeup_pct', 'N/A'):.1f}%" if ofcom.get('full_fibre_takeup_pct') is not None else 'N/A', "% of businesses on full fibre connections"],
        ["Avg monthly data usage", f"{ofcom.get('avg_data_usage_gb', 'N/A')} GB" if ofcom.get('avg_data_usage_gb') is not None else 'N/A', "Average monthly usage per connection"],
    ]
    story.append(data_table(["Metric", "Value", "Notes"], conn_rows, [65*mm, 35*mm, 70*mm]))
    story.append(Paragraph("Data: Ofcom Connected Nations, July 2024. Local authority level — campus-specific provision may differ. On-site survey recommended.", styles["caveat"]))
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph("Mobile Coverage", styles["h3"]))
    if mob_score is not None:
        story.append(score_bar_table("Mobile Coverage Score", mob_score, "Green" if mob_score >= 70 else "Amber" if mob_score >= 40 else "Red", width=170*mm))
    story.append(Spacer(1, 3*mm))
    mob_rows = [
        ["Indoor 4G (all operators)", f"{ofcom.get('indoor_4g_pct', 'N/A'):.1f}%" if ofcom.get('indoor_4g_pct') is not None else 'N/A'],
        ["Outdoor 4G (all operators)", f"{ofcom.get('outdoor_4g_pct', 'N/A'):.1f}%" if ofcom.get('outdoor_4g_pct') is not None else 'N/A'],
        ["Outdoor 5G (all operators)", f"{ofcom.get('outdoor_5g_pct', 'N/A'):.1f}%" if ofcom.get('outdoor_5g_pct') is not None else 'N/A'],
        ["Indoor voice (all operators)", f"{ofcom.get('indoor_voice_pct', 'N/A'):.1f}%" if ofcom.get('indoor_voice_pct') is not None else 'N/A'],
    ]
    story.append(data_table(["Coverage Metric", "Coverage %"], mob_rows, [100*mm, 70*mm]))
    story.append(Spacer(1, 5*mm))

def build_companies_section(story, companies, park, styles):
    story.append(Paragraph(f"Registered Companies at Postcode ({park.get('postcode','')})", styles["h2"]))
    if not companies:
        story.append(Paragraph("Companies House data not available (API key not configured or no results found).", styles["body"]))
        story.append(Spacer(1, 5*mm))
        return
    active = [c for c in companies if c.get("company_status", "").lower() == "active"]
    sectors = classify_companies(companies)
    story.append(Paragraph(f"<b>Results found:</b> {len(companies)} companies · <b>Active:</b> {len(active)} · <b>Sector profile:</b> {', '.join(f'{k} ({v})' for k, v in sectors.items()) or 'Mixed'}", styles["body"]))
    story.append(Spacer(1, 3*mm))
    rows = []
    for c in companies[:15]:
        rows.append([
            c.get("title", ""),
            c.get("company_status", "").capitalize(),
            c.get("date_of_creation", "")[:4] if c.get("date_of_creation") else "",
            ", ".join((c.get("sic_codes") or [])[:2]),
        ])
    if rows:
        story.append(data_table(["Company Name", "Status", "Inc.", "SIC Codes"], rows, [75*mm, 25*mm, 20*mm, 50*mm]))
    story.append(Spacer(1, 5*mm))

def build_intelligence_section(story, flags, opportunities, styles):
    story.append(PageBreak())
    story.append(Paragraph("Intelligence Flags", styles["h2"]))
    if not flags:
        story.append(Paragraph("No significant intelligence flags identified for this park location.", styles["body"]))
    for flag_title, flag_detail in flags:
        story.append(Paragraph(flag_title, styles["flag"]))
        story.append(Paragraph(flag_detail, styles["flagbody"]))
        story.append(Spacer(1, 3*mm))

    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("Commercial Opportunities", styles["h2"]))
    if not opportunities:
        story.append(Paragraph("No specific opportunities identified. Consider on-site survey for detailed assessment.", styles["body"]))
    for i, opp in enumerate(opportunities, 1):
        story.append(Paragraph(f"{i}.  {opp}", styles["opp"]))
        story.append(Spacer(1, 2*mm))

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("Recommended Next Steps", styles["h2"]))
    steps = [
        ["1", "On-site connectivity audit", "Commission independent survey to validate LA-level Ofcom data with campus-specific measurements"],
        ["2", "Tenant engagement", "Map individual tenant connectivity requirements — particularly research-intensive and compliance-sensitive occupiers"],
        ["3", "Park director briefing", "Present findings to park management as conversation opener — position as research service, not sales approach"],
        ["4", "WiredScore benchmarking", "Assess campus against WiredScore certification criteria to establish competitive baseline"],
    ]
    story.append(data_table(["", "Action", "Description"], steps, [10*mm, 50*mm, 110*mm]))

def generate_park_pdf(park, ofcom, companies):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=15*mm, rightMargin=15*mm,
                             topMargin=15*mm, bottomMargin=20*mm,
                             title=f"Intelligence Report: {park['name']}")
    styles = get_styles()
    story = []

    build_park_header(story, park, styles)
    build_park_profile_table(story, park, styles)
    story.append(HRFlowable(width="100%", thickness=1, color=MGREY, spaceAfter=4*mm))
    build_connectivity_section(story, ofcom, styles)
    story.append(HRFlowable(width="100%", thickness=1, color=MGREY, spaceAfter=4*mm))
    build_companies_section(story, companies, park, styles)

    flags = generate_flags(park, ofcom) if ofcom else []
    ops = generate_opportunities(park, ofcom or {}, companies or [])
    build_intelligence_section(story, flags, ops, styles)

    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(
        f"Generated: {datetime.datetime.now().strftime('%d %B %Y %H:%M')} · Data: Ofcom Connected Nations July 2024 · Companies House API · INTERNAL USE ONLY",
        styles["small"]
    ))

    doc.build(story)
    buf.seek(0)
    return buf

# ─── PDF GENERATION: AREA / CLUSTER / REGION REPORT ──────────────────────────
def generate_area_pdf(area_label, parks_list, all_ofcom_results, report_title):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=15*mm, rightMargin=15*mm,
                             topMargin=15*mm, bottomMargin=20*mm,
                             title=report_title)
    styles = get_styles()
    story = []

    header_data = [[
        Paragraph(report_title, styles["title"]),
        Paragraph(f"{len(parks_list)} parks profiled  ·  {area_label}", styles["subtitle"]),
        Paragraph(f"Generated {datetime.datetime.now().strftime('%d %B %Y')}", styles["subtitle"]),
    ]]
    t = Table([header_data], colWidths=[180*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("PADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))

    story.append(Paragraph("Area Summary", styles["h2"]))
    scored = [(p, all_ofcom_results.get(p["id"]), score_connectivity(all_ofcom_results.get(p["id"]) or {})[0])
              for p in parks_list]
    with_scores = [(p, o, s) for p, o, s in scored if s is not None]
    if with_scores:
        avg_score = round(sum(s for _, _, s in with_scores) / len(with_scores))
        green = sum(1 for _, _, s in with_scores if s >= 70)
        amber = sum(1 for _, _, s in with_scores if 40 <= s < 70)
        red   = sum(1 for _, _, s in with_scores if s < 40)
        summary_rows = [
            ["Parks in area", str(len(parks_list)), "Average connectivity score", f"{avg_score}/100"],
            ["Green RAG", str(green), "Amber RAG", str(amber)],
            ["Red RAG", str(red), "Parks with Ofcom data", str(len(with_scores))],
        ]
        body_s = ParagraphStyle("sb", fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#2C2C2C"))
        key_s = ParagraphStyle("sk", fontSize=9, fontName="Helvetica-Bold", textColor=NAVY)
        tbl = Table(
            [[Paragraph(r[0], key_s), Paragraph(r[1], body_s), Paragraph(r[2], key_s), Paragraph(r[3], body_s)] for r in summary_rows],
            colWidths=[55*mm, 30*mm, 55*mm, 30*mm]
        )
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, MGREY),
            ("BACKGROUND", (0, 0), (0, -1), LGREY), ("BACKGROUND", (2, 0), (2, -1), LGREY),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(tbl)
    story.append(Spacer(1, 6*mm))

    story.append(Paragraph("Connectivity Comparison — All Parks (Ranked)", styles["h2"]))
    ranked = sorted(scored, key=lambda x: (x[2] is None, -(x[2] or 0)))

    comp_rows = []
    for rank, (park, ofcom, conn_score) in enumerate(ranked, 1):
        rag = score_connectivity(ofcom or {})[1] if ofcom else "No data"
        ff = f"{ofcom.get('full_fibre_pct', 0):.0f}%" if ofcom else "—"
        gig = f"{ofcom.get('gigabit_pct', 0):.0f}%" if ofcom else "—"
        g4 = f"{ofcom.get('indoor_4g_pct', 0):.0f}%" if ofcom else "—"
        g5 = f"{ofcom.get('outdoor_5g_pct', 0):.0f}%" if ofcom else "—"
        score_str = f"{conn_score}/100" if conn_score is not None else "—"
        comp_rows.append([str(rank), park["name"][:28], park.get("local_authority","")[:18],
                          score_str, rag, ff, gig, g4, g5])

    story.append(data_table(
        ["#", "Park", "Local Authority", "Score", "RAG", "FF%", "Gig%", "4G%", "5G%"],
        comp_rows,
        [8*mm, 48*mm, 32*mm, 17*mm, 16*mm, 13*mm, 13*mm, 12*mm, 12*mm]
    ))
    story.append(Paragraph("Data: Ofcom Connected Nations July 2024 · Local authority level · On-site survey recommended for campus-specific accuracy.", styles["caveat"]))
    story.append(Spacer(1, 6*mm))

    all_ops = {}
    for park in parks_list:
        ofcom = all_ofcom_results.get(park["id"]) or {}
        park_ops = generate_opportunities(park, ofcom, [])
        for op in park_ops:
            all_ops[op] = all_ops.get(op, 0) + 1

    if all_ops:
        story.append(Paragraph("Most Common Commercial Opportunities Across Area", styles["h2"]))
        sorted_ops = sorted(all_ops.items(), key=lambda x: -x[1])
        op_rows = [[str(v), op] for op, v in sorted_ops[:10]]
        story.append(data_table(["Parks", "Opportunity"], op_rows, [20*mm, 150*mm]))
        story.append(Spacer(1, 6*mm))

    story.append(PageBreak())
    story.append(Paragraph("Individual Park Summaries", styles["h2"]))

    for park in parks_list:
        ofcom = all_ofcom_results.get(park["id"]) or {}
        conn_score, conn_rag = score_connectivity(ofcom)
        mob_score = score_mobile(ofcom)
        flags = generate_flags(park, ofcom) if ofcom else []
        ops = generate_opportunities(park, ofcom, [])

        ps = ParagraphStyle("pname", fontSize=11, fontName="Helvetica-Bold", textColor=WHITE)
        ps2 = ParagraphStyle("ploc", fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#BDD7EE"))
        park_hdr = Table([[Paragraph(park["name"], ps)], [Paragraph(f"{park.get('location','')} · {park.get('sector','')[:60]}", ps2)]],
                         colWidths=[170*mm])
        park_hdr.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TEAL), ("PADDING", (0, 0), (-1, -1), 7)]))
        story.append(KeepTogether([
            park_hdr,
            Spacer(1, 2*mm),
        ]))

        cs_str = f"{conn_score}/100 [{conn_rag}]" if conn_score is not None else "No Ofcom data"
        ms_str = f"{mob_score}/100" if mob_score is not None else "—"
        mini_rows = [
            ["Connectivity Score", cs_str, "Mobile Score", ms_str],
            ["Full Fibre %", f"{ofcom.get('full_fibre_pct',0):.0f}%" if ofcom else "—",
             "5G Outdoor %", f"{ofcom.get('outdoor_5g_pct',0):.0f}%" if ofcom else "—"],
            ["Sector", park.get("sector","")[:40], "Operator", park.get("operator","")[:35]],
            ["Status", park.get("status",""), "Tenants", park.get("tenants","")],
        ]
        key_s2 = ParagraphStyle("mk", fontSize=8, fontName="Helvetica-Bold", textColor=NAVY)
        val_s2 = ParagraphStyle("mv", fontSize=8, fontName="Helvetica", textColor=colors.HexColor("#2C2C2C"))
        mini_t = Table(
            [[Paragraph(r[0], key_s2), Paragraph(str(r[1]), val_s2), Paragraph(r[2], key_s2), Paragraph(str(r[3]), val_s2)] for r in mini_rows],
            colWidths=[40*mm, 45*mm, 40*mm, 45*mm]
        )
        mini_t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, MGREY),
            ("BACKGROUND", (0, 0), (0, -1), LGREY), ("BACKGROUND", (2, 0), (2, -1), LGREY),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(mini_t)

        if flags:
            flag_text = "  ·  ".join(f[0] for f in flags[:3])
            story.append(Paragraph(f"Flags: {flag_text}", ParagraphStyle("fl", fontSize=8, fontName="Helvetica", textColor=AMBER, spaceBefore=3)))
        if ops:
            story.append(Paragraph(f"Top opportunity: {ops[0]}", ParagraphStyle("op1", fontSize=8, fontName="Helvetica", textColor=NAVY, spaceBefore=2)))
        story.append(Spacer(1, 5*mm))

    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=MGREY))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        f"Generated: {datetime.datetime.now().strftime('%d %B %Y %H:%M')} · Data: Ofcom Connected Nations July 2024 · INTERNAL USE ONLY",
        styles["small"]
    ))

    doc.build(story)
    buf.seek(0)
    return buf

# ─── MAIN UI ──────────────────────────────────────────────────────────────────
st.title("🔬 UK Science Parks Intelligence")
st.markdown("*National prospecting tool — digital infrastructure profiling for science & innovation parks*")

with st.sidebar:
    st.header("⚙️ Settings")
    ch_api_key = st.secrets.get("CH_API_KEY", "") if hasattr(st, "secrets") else ""
    if not ch_api_key:
        ch_api_key = st.text_input("Companies House API Key", type="password",
                                    help="Free key from developer.company-information.service.gov.uk")
    else:
        st.success("✓ Companies House API key loaded")

    st.divider()
    st.markdown("**About**")
    st.markdown(f"🏢 **{sum(len(c['parks']) for r in parks_data['regions'] for c in r['clusters'])} parks** indexed")
    st.markdown(f"🗺️ **{len(parks_data['regions'])} regions** · **{sum(len(r['clusters']) for r in parks_data['regions'])} clusters**")
    st.markdown("📡 Ofcom Connected Nations (Jul 2024)")
    st.markdown("🏛️ Companies House API (live)")

st.divider()

region_map = {r["name"]: r for r in parks_data["regions"]}

col1, col2, col3 = st.columns(3)

with col1:
    region_options = ["— Select a Region —"] + [r["name"] for r in parks_data["regions"]]
    selected_region_name = st.selectbox("1️⃣ Select Region", region_options)

if selected_region_name == "— Select a Region —":
    st.info("👆 Select a region to begin, then drill down to a cluster and park — or generate an area-wide report.")
    st.stop()

selected_region = region_map[selected_region_name]

with col2:
    cluster_options = ["All clusters in this region"] + [c["name"] for c in selected_region["clusters"]]
    selected_cluster_name = st.selectbox("2️⃣ Select Cluster", cluster_options)

all_clusters_mode = selected_cluster_name == "All clusters in this region"

if not all_clusters_mode:
    selected_cluster = next(c for c in selected_region["clusters"] if c["name"] == selected_cluster_name)
    parks_in_scope = selected_cluster["parks"]
else:
    parks_in_scope = [p for c in selected_region["clusters"] for p in c["parks"]]

with col3:
    if all_clusters_mode:
        park_options = ["All parks in region"]
        park_label = f"All parks — {selected_region_name}"
    else:
        park_options = [f"All parks in {selected_cluster_name}"] + [p["name"] for p in parks_in_scope]
        park_label = None
    selected_park_name = st.selectbox("3️⃣ Select Park", park_options)

all_parks_mode = selected_park_name.startswith("All parks")

if not all_parks_mode:
    selected_park = next(p for p in parks_in_scope if p["name"] == selected_park_name)
    region_name = selected_region_name
    cluster_name = selected_cluster_name if not all_clusters_mode else next(
        c["name"] for c in selected_region["clusters"] if any(p["id"] == selected_park["id"] for p in c["parks"])
    )
    selected_park["_region"] = region_name
    selected_park["_cluster"] = cluster_name

st.divider()

# ─── SINGLE PARK MODE ─────────────────────────────────────────────────────────
if not all_parks_mode:
    park = selected_park
    st.subheader(f"🏢 {park['name']}")
    subcols = st.columns(4)
    subcols[0].metric("Region", selected_region_name.split("&")[0].strip()[:20])
    subcols[1].metric("Cluster", cluster_name[:22])
    subcols[2].metric("Location", park.get("location","")[:20])
    subcols[3].metric("Sector", (park.get("sector","")[:22] or "—"))

    if st.button("🔍 Generate Intelligence Report", type="primary", use_container_width=True):
        with st.spinner("Pulling data..."):
            ofcom = get_ofcom(park.get("local_authority",""))
            companies = get_companies(park.get("postcode",""), ch_api_key) if ch_api_key else []
            conn_score, conn_rag = score_connectivity(ofcom)
            mob_score = score_mobile(ofcom)
            flags = generate_flags(park, ofcom) if ofcom else []
            ops = generate_opportunities(park, ofcom or {}, companies)

        m1, m2, m3, m4 = st.columns(4)
        rag_icon = {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}.get(conn_rag, "⚪")
        m1.metric("Connectivity Score", f"{conn_score}/100 {rag_icon}" if conn_score else "No data")
        m2.metric("Mobile Score", f"{mob_score}/100" if mob_score else "No data")
        m3.metric("Companies found", len(companies) if companies else "—")
        m4.metric("Opportunities", len(ops))

        st.divider()
        left, right = st.columns(2)

        with left:
            st.markdown("**📡 Connectivity Profile**")
            if ofcom:
                st.progress(min(1.0, (conn_score or 0)/100), text=f"Score: {conn_score}/100 [{conn_rag}]")
                conn_display = {
                    "Full Fibre %": f"{ofcom.get('full_fibre_pct',0):.1f}%",
                    "Gigabit %": f"{ofcom.get('gigabit_pct',0):.1f}%",
                    "Superfast %": f"{ofcom.get('superfast_pct',0):.1f}%",
                    "No decent BB": f"{ofcom.get('no_decent_pct',0):.1f}%",
                    "FF Take-up": f"{ofcom.get('full_fibre_takeup_pct',0):.1f}%",
                    "Avg data use": f"{ofcom.get('avg_data_usage_gb',0)} GB/mo",
                }
                for k, v in conn_display.items():
                    st.text(f"  {k}: {v}")
                st.markdown("**📱 Mobile**")
                mob_display = {
                    "Indoor 4G": f"{ofcom.get('indoor_4g_pct',0):.1f}%",
                    "Outdoor 4G": f"{ofcom.get('outdoor_4g_pct',0):.1f}%",
                    "Outdoor 5G": f"{ofcom.get('outdoor_5g_pct',0):.1f}%",
                    "Indoor voice": f"{ofcom.get('indoor_voice_pct',0):.1f}%",
                }
                for k, v in mob_display.items():
                    st.text(f"  {k}: {v}")
            else:
                st.warning("Ofcom data not available for this local authority.")

        with right:
            st.markdown("**🏢 Park Profile**")
            st.text(f"  Operator: {park.get('operator','—')[:45]}")
            st.text(f"  Sector:   {park.get('sector','—')[:45]}")
            st.text(f"  Status:   {park.get('status','—')}")
            st.text(f"  Tenants:  {park.get('tenants','—')}")
            st.text(f"  LA:       {park.get('local_authority','—')}")
            if park.get("notes"):
                st.caption(park["notes"][:200])
            if companies:
                active = [c for c in companies if c.get("company_status","").lower()=="active"]
                st.markdown(f"**🏛️ Companies House** — {len(companies)} found, {len(active)} active")
                with st.expander(f"View companies ({min(15,len(companies))})"):
                    for c in companies[:15]:
                        st.text(f"• {c.get('title','')} [{c.get('company_status','').capitalize()}]")

        if flags:
            st.divider()
            st.markdown("**⚠️ Intelligence Flags**")
            for title, detail in flags:
                st.warning(f"**{title}** — {detail}")

        if ops:
            st.divider()
            st.markdown("**💼 Commercial Opportunities**")
            for i, op in enumerate(ops, 1):
                st.info(f"{i}. {op}")

        st.divider()
        with st.spinner("Building PDF..."):
            pdf_buf = generate_park_pdf(park, ofcom, companies)

        fname = f"{park['name'].replace(' ','_').replace('/','_')}_intelligence_report.pdf"
        st.download_button("📥 Download Intelligence Report (PDF)", pdf_buf, file_name=fname,
                            mime="application/pdf", use_container_width=True, type="primary")

# ─── AREA / MULTI-PARK MODE ───────────────────────────────────────────────────
else:
    if all_clusters_mode:
        area_label = selected_region_name
        report_title = f"Digital Infrastructure Report: {selected_region_name}"
    else:
        area_label = f"{selected_cluster_name}, {selected_region_name}"
        report_title = f"Digital Infrastructure Report: {selected_cluster_name}"

    parks_list = parks_in_scope
    for park in parks_list:
        park["_region"] = selected_region_name
        if not all_clusters_mode:
            park["_cluster"] = selected_cluster_name
        else:
            for c in selected_region["clusters"]:
                if any(p["id"] == park["id"] for p in c["parks"]):
                    park["_cluster"] = c["name"]

    st.subheader(f"📊 Area Report: {area_label}")
    st.markdown(f"**{len(parks_list)} parks** will be profiled across this {'region' if all_clusters_mode else 'cluster'}.")

    with st.expander(f"View all {len(parks_list)} parks in scope", expanded=False):
        for p in parks_list:
            st.text(f"  • {p['name']} — {p.get('location','')} ({p.get('local_authority','')})")

    if st.button(f"🔍 Generate {area_label} Area Report", type="primary", use_container_width=True):
        with st.spinner(f"Pulling Ofcom data for {len(parks_list)} parks..."):
            all_ofcom = {}
            for park in parks_list:
                la = park.get("local_authority","")
                if la:
                    all_ofcom[park["id"]] = get_ofcom(la)
                else:
                    all_ofcom[park["id"]] = {}

        with_data = [(p, all_ofcom.get(p["id"])) for p in parks_list if all_ofcom.get(p["id"])]
        scored = [(p, o, score_connectivity(o)[0]) for p, o in with_data]
        scored_valid = [(p, o, s) for p, o, s in scored if s is not None]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Parks profiled", len(parks_list))
        if scored_valid:
            avg = round(sum(s for _,_,s in scored_valid)/len(scored_valid))
            m2.metric("Avg connectivity score", f"{avg}/100")
            m3.metric("Parks with Ofcom data", len(scored_valid))
            greens = sum(1 for _,_,s in scored_valid if s >= 70)
            m4.metric("Green RAG", f"{greens}/{len(scored_valid)}")
        else:
            m2.metric("Avg connectivity score", "—")
            m3.metric("Parks with Ofcom data", "0")
            m4.metric("Green RAG", "—")

        st.divider()

        st.markdown("**📡 Connectivity Comparison — Ranked**")
        ranked = sorted(scored_valid, key=lambda x: -x[2])
        for park, ofcom, conn_score in ranked:
            rag = score_connectivity(ofcom)[1]
            rag_icon = {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}.get(rag, "⚪")
            ff = f"{ofcom.get('full_fibre_pct',0):.0f}%"
            g5 = f"{ofcom.get('outdoor_5g_pct',0):.0f}%"
            col_a, col_b, col_c, col_d, col_e = st.columns([3, 1.5, 1.2, 1.2, 1.5])
            col_a.text(park["name"][:38])
            col_b.markdown(f"{rag_icon} **{conn_score}/100**")
            col_c.text(f"FF: {ff}")
            col_d.text(f"5G: {g5}")
            col_e.text(park.get("location",""))

        no_data_parks = [p for p in parks_list if not all_ofcom.get(p["id"])]
        if no_data_parks:
            with st.expander(f"{len(no_data_parks)} parks without Ofcom data match"):
                for p in no_data_parks:
                    st.text(f"  • {p['name']} (LA: {p.get('local_authority','')})")

        st.divider()

        all_ops = {}
        for park in parks_list:
            ofcom = all_ofcom.get(park["id"]) or {}
            for op in generate_opportunities(park, ofcom, []):
                all_ops[op] = all_ops.get(op, 0) + 1
        if all_ops:
            st.markdown("**💼 Top Opportunities Across Area**")
            for op, count in sorted(all_ops.items(), key=lambda x: -x[1])[:6]:
                st.info(f"**{count} parks** — {op}")

        st.divider()

        with st.spinner("Building area report PDF..."):
            pdf_buf = generate_area_pdf(area_label, parks_list, all_ofcom, report_title)

        safe_name = area_label.replace(" ","_").replace("&","and").replace("–","_").replace("/","_")
        fname = f"{safe_name}_area_report.pdf"
        st.download_button(
            f"📥 Download {area_label} Area Report (PDF)",
            pdf_buf, file_name=fname, mime="application/pdf",
            use_container_width=True, type="primary"
        )

        st.divider()
        st.markdown("**🔎 Drill into individual parks from this area**")
        st.info("Use the selectors above to pick a specific park and generate a detailed individual report.")
