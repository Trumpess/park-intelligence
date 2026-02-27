import streamlit as st
import json
import os
import requests
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib.units import inch, cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import io

# ─── CONFIG ──────────────────────────────────────────────────────────────────
COMPANY_NAME = "Digital Infrastructure Report"
REPORT_SUBTITLE = "Oxford–Cambridge Arc: Science & Innovation Parks"
PASSWORD = "arcreport2026"

st.set_page_config(
    page_title="Arc Parks Intelligence – Science & Innovation",
    page_icon="🔬",
    layout="wide"
)

# ─── AUTHENTICATION ───────────────────────────────────────────────────────────
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return True
    st.markdown("## 🔬 Arc Parks Intelligence Tool")
    st.markdown("Oxford–Cambridge Science & Innovation Parks — Digital Infrastructure Profiler")
    pwd = st.text_input("Enter access password", type="password")
    if st.button("Login"):
        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    return False

# ─── LOAD DATA ────────────────────────────────────────────────────────────────
@st.cache_data
def load_parks():
    path = os.path.join(os.path.dirname(__file__), "parks_data.json")
    with open(path) as f:
        return json.load(f)

@st.cache_data
def load_area_data():
    path = os.path.join(os.path.dirname(__file__), "area_data.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

# ─── OFCOM LOOKUP ─────────────────────────────────────────────────────────────
def get_connectivity(local_authority, area_data):
    """Match park's local authority to Ofcom/VOA area data."""
    if not area_data:
        return None
    key = local_authority.strip().upper()
    # Try direct match first
    if key in area_data:
        return area_data[key]
    # Try partial match
    for k, v in area_data.items():
        if key in k or k in key:
            return v
    return None

# ─── COMPANIES HOUSE API ──────────────────────────────────────────────────────
def get_companies_house_data(postcode, ch_api_key):
    """Query Companies House for companies registered at this postcode."""
    if not ch_api_key or not postcode:
        return None
    try:
        url = f"https://api.company-information.service.gov.uk/search/companies"
        params = {"q": postcode, "items_per_page": 20}
        resp = requests.get(url, params=params, auth=(ch_api_key, ""), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            # Filter to active companies only
            active = [i for i in items if i.get("company_status") == "active"]
            return {
                "total_results": data.get("total_results", 0),
                "active_companies": len(active),
                "companies": active[:10],
                "sic_codes": list(set([
                    sic for c in active
                    for sic in c.get("sic_codes", [])
                ]))[:15]
            }
    except Exception as e:
        return None
    return None

def classify_sic_codes(sic_codes):
    """Map SIC codes to sector labels."""
    sectors = []
    sic_map = {
        ("7211","7219","7220","7230"): "Research & Development",
        ("6201","6202","6209","6311","6312"): "Software & IT Services",
        ("2100","2110","2120"): "Pharmaceuticals",
        ("3210","3220","3250","3290"): "Medical Devices / Electronics",
        ("7111","7112","7120"): "Engineering & Technical Consultancy",
        ("3511","3512","3513","3514"): "Energy Technology",
        ("6110","6120","6130","6190"): "Telecommunications",
        ("6410","6420","6430","6491","6499"): "Financial Services / Investment",
        ("8010","8020","8030"): "Security Services",
        ("7010","7022"): "Management Consultancy",
    }
    for code_group, label in sic_map.items():
        for sic in sic_codes:
            if any(sic.startswith(c[:3]) for c in code_group):
                if label not in sectors:
                    sectors.append(label)
    return sectors if sectors else ["Technology & Science"]

# ─── CONNECTIVITY SCORING ─────────────────────────────────────────────────────
def score_connectivity(conn):
    """Score connectivity 0-100 and return RAG status."""
    if not conn:
        return None, None, "Unknown"
    c = conn.get("connectivity", {})
    score = 0
    score += min(c.get("full_fibre_pct", 0), 40)         # max 40 pts
    score += min(c.get("gigabit_pct", 0) * 0.3, 20)      # max 20 pts
    score += min(c.get("superfast_pct", 0) * 0.2, 20)    # max 20 pts
    score += max(0, 20 - c.get("no_decent_pct", 0) * 2)  # max 20 pts
    score = min(int(score), 100)
    if score >= 70:
        rag = "Green"
    elif score >= 40:
        rag = "Amber"
    else:
        rag = "Red"
    return score, c, rag

def mobile_score(conn):
    """Score mobile coverage 0-100."""
    if not conn:
        return None, None
    m = conn.get("mobile", {})
    if not m:
        return None, None
    score = 0
    score += min(m.get("indoor_4g_all_operators_pct", 0) * 0.4, 40)
    score += min(m.get("outdoor_5g_all_operators_pct", 0) * 0.4, 40)
    score += min(m.get("indoor_voice_all_operators_pct", 0) * 0.2, 20)
    return min(int(score), 100), m

# ─── OPPORTUNITY ANALYSIS ─────────────────────────────────────────────────────
def generate_opportunities(park, conn_score, conn_data, mob_score, mob_data, ch_data):
    """Generate a list of specific opportunities based on data."""
    opps = []
    flags = []

    if conn_data:
        ff = conn_data.get("full_fibre_pct", 0)
        gig = conn_data.get("gigabit_pct", 0)
        no_decent = conn_data.get("no_decent_pct", 0)
        takeup = conn_data.get("ff_takeup_pct", 0)

        if ff < 60:
            flags.append(f"Only {ff}% of premises in this local authority have full fibre availability — below the threshold expected for a science and innovation campus")
            opps.append("Campus-wide full fibre upgrade: current availability suggests significant infrastructure gap vs peer parks")
        if gig < 50:
            flags.append(f"Gigabit-capable coverage at {gig}% — likely insufficient for research data transmission demands")
            opps.append("Gigabit connectivity upgrade: research organisations typically require symmetrical gigabit+ for data-intensive work")
        if takeup < 30 and ff > 60:
            flags.append(f"Full fibre take-up at only {takeup}% despite {ff}% availability — significant uptake gap")
            opps.append("Connectivity migration programme: infrastructure exists but tenants are not yet on full fibre — opportunity for managed migration")
        if no_decent > 2:
            flags.append(f"{no_decent}% of premises have no decent broadband — notable gap in coverage for a science park location")
            opps.append("Last-mile connectivity: coverage gaps exist that may affect specific buildings or zones within the park")

    if mob_data:
        in4g = mob_data.get("indoor_4g_all_operators_pct", 100)
        out5g = mob_data.get("outdoor_5g_all_operators_pct", 0)
        if in4g < 80:
            flags.append(f"Indoor 4G coverage across all operators at {in4g}% — below acceptable threshold for research campuses")
            opps.append("Indoor mobile coverage: current 4G indoor coverage may be insufficient for campus-wide mobile working")
        if out5g < 40:
            flags.append(f"Outdoor 5G coverage at {out5g}% — 5G readiness is low for a cutting-edge innovation campus")
            opps.append("5G readiness: outdoor 5G coverage is limited — early-mover advantage for a park deploying private 5G network")

    # Sector-specific based on park's own description
    sector = park.get("sector", "").lower()
    if "genomics" in sector or "biomedical" in sector or "life science" in sector.lower():
        opps.append("High-bandwidth data infrastructure: genomics and life science research generates large datasets requiring dedicated high-speed links to cloud platforms and partner institutions")
        flags.append("Life sciences sector: data-intensive research requires resilient, high-bandwidth connectivity with low latency for instrument connectivity and remote collaboration")
    if "space" in sector or "energy" in sector or "defence" in sector.lower():
        opps.append("Secure, segregated network architecture: space, energy and defence-adjacent tenants require network isolation and enhanced cybersecurity posture")
        flags.append("Sensitive sector: space/energy/defence tenants have elevated network security requirements including potential ITAR/export control compliance")
    if "incubator" in park.get("status", "").lower() or "early" in park.get("notes", "").lower():
        opps.append("Start-up connectivity packages: incubator-stage companies require flexible, scalable connectivity that grows with them — avoid over-commitment on long contracts")

    # Size-based
    if park.get("size_sqft") and park["size_sqft"] > 500000:
        opps.append("Campus-scale managed network: at this scale, a dedicated managed campus network with centralised monitoring is significantly more cost-effective than per-tenant provision")

    if ch_data and ch_data.get("active_companies", 0) > 20:
        opps.append(f"Tenant community connectivity: {ch_data['active_companies']} active companies identified in this postcode — opportunity for a campus-wide connectivity programme covering all tenants")

    if not opps:
        opps.append("Connectivity review: baseline assessment suggests infrastructure meets minimum standards but further on-site survey recommended to identify specific tenant-level gaps")

    return opps, flags

# ─── PDF GENERATION ───────────────────────────────────────────────────────────
def generate_pdf(park, area_conn, conn_score, conn_data, conn_rag,
                 mob_score_val, mob_data, ch_data, opportunities, flags):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=2*cm
    )

    # Styles
    styles = getSampleStyleSheet()
    NAVY = colors.HexColor("#1F4E79")
    TEAL = colors.HexColor("#2E74B5")
    LGREY = colors.HexColor("#F5F7FA")
    MGREY = colors.HexColor("#E0E7EF")
    RED = colors.HexColor("#C00000")
    AMBER = colors.HexColor("#E36C09")
    GREEN = colors.HexColor("#375623")

    def style(name, **kwargs):
        s = ParagraphStyle(name, **kwargs)
        return s

    title_style = style("T", fontSize=22, fontName="Helvetica-Bold",
                        textColor=NAVY, spaceAfter=4, leading=26)
    sub_style = style("Sub", fontSize=12, fontName="Helvetica",
                      textColor=TEAL, spaceAfter=12, leading=16)
    h2_style = style("H2", fontSize=13, fontName="Helvetica-Bold",
                     textColor=NAVY, spaceAfter=6, spaceBefore=14, leading=17)
    h3_style = style("H3", fontSize=10, fontName="Helvetica-Bold",
                     textColor=TEAL, spaceAfter=4, spaceBefore=8, leading=13)
    body_style = style("B", fontSize=9, fontName="Helvetica",
                       textColor=colors.black, spaceAfter=4, leading=13)
    small_style = style("Sm", fontSize=8, fontName="Helvetica",
                        textColor=colors.HexColor("#595959"), spaceAfter=3, leading=11)
    flag_style = style("Fl", fontSize=9, fontName="Helvetica",
                       textColor=colors.HexColor("#7B0000"), spaceAfter=5, leading=13,
                       leftIndent=10)
    opp_style = style("Op", fontSize=9, fontName="Helvetica",
                      textColor=colors.HexColor("#1A3A2A"), spaceAfter=5, leading=13,
                      leftIndent=10)
    footer_style = style("Ft", fontSize=7.5, fontName="Helvetica",
                         textColor=colors.HexColor("#888888"), alignment=TA_CENTER)

    def hr(thickness=0.5, color=MGREY):
        return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=8, spaceBefore=4)

    def kv_table(rows, col1_w=5.5*cm, col2_w=11.5*cm):
        data = []
        for label, value in rows:
            data.append([
                Paragraph(f"<b>{label}</b>", small_style),
                Paragraph(str(value) if value else "—", body_style)
            ])
        t = Table(data, colWidths=[col1_w, col2_w])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (0,-1), LGREY),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [LGREY, colors.white]),
            ("GRID", (0,0), (-1,-1), 0.3, MGREY),
        ]))
        return t

    def score_bar_table(label, score, rag, width=17*cm):
        if score is None:
            return Paragraph(f"<b>{label}:</b> Data not available for this area", body_style)
        rag_colors = {"Green": GREEN, "Amber": AMBER, "Red": RED}
        bar_color = rag_colors.get(rag, TEAL)
        bar_width = max(0.5, (score / 100) * 13)
        data = [[
            Paragraph(f"<b>{label}</b>", small_style),
            Paragraph(f"<b>{score}/100</b>  <font color='#{bar_color.hexval()[2:]}'>({rag})</font>", body_style),
        ]]
        t = Table(data, colWidths=[5.5*cm, 11.5*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (0,0), LGREY),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("GRID", (0,0), (-1,-1), 0.3, MGREY),
        ]))
        return t

    content = []

    # ── HEADER ──
    content.append(Paragraph(park["name"], title_style))
    content.append(Paragraph(f"{park['location']}  ·  {park['zone']}  ·  {park['postcode']}", sub_style))
    content.append(hr(1.5, NAVY))
    content.append(Spacer(1, 6))

    # ── PARK PROFILE ──
    content.append(Paragraph("Park Profile", h2_style))
    profile_rows = [
        ("Zone", park.get("zone", "")),
        ("Location", park.get("location", "")),
        ("County", park.get("county", "")),
        ("Postcode", park.get("postcode", "")),
        ("Sector Focus", park.get("sector", "")),
        ("Tenants / Scale", park.get("tenants", "")),
        ("Operator", park.get("operator", "")),
        ("Status", park.get("status", "")),
    ]
    if park.get("size_sqft"):
        profile_rows.append(("Size (sq ft)", f"{park['size_sqft']:,}"))
    if park.get("website"):
        profile_rows.append(("Website", park.get("website", "")))
    content.append(kv_table(profile_rows))
    if park.get("notes"):
        content.append(Spacer(1, 6))
        content.append(Paragraph(f"<i>{park['notes']}</i>", small_style))
    content.append(Spacer(1, 8))

    # ── CONNECTIVITY PROFILE ──
    content.append(hr())
    content.append(Paragraph("Connectivity Profile", h2_style))
    content.append(Paragraph(
        f"<i>Source: Ofcom Connected Nations dataset, local authority: {park.get('local_authority', 'Unknown')}. "
        f"Data reflects fixed broadband availability across the local authority area. "
        f"Campus-specific provision may differ — on-site survey recommended.</i>",
        small_style
    ))
    content.append(Spacer(1, 6))

    if conn_data:
        ff = conn_data.get("full_fibre_pct", 0)
        gig = conn_data.get("gigabit_pct", 0)
        sup = conn_data.get("superfast_pct", 0)
        no_dec = conn_data.get("no_decent_pct", 0)
        takeup = conn_data.get("ff_takeup_pct", 0)
        usage = conn_data.get("avg_data_usage_gb", 0)

        conn_rows = [
            ("Full Fibre available", f"{ff}% of premises"),
            ("Gigabit-capable", f"{gig}% of premises"),
            ("Superfast (30Mbps+)", f"{sup}% of premises"),
            ("No decent broadband", f"{no_dec}% of premises"),
            ("Full Fibre take-up", f"{takeup}% of available premises"),
            ("Avg monthly data usage", f"{usage} GB per premises"),
        ]
        content.append(score_bar_table("Connectivity Score", conn_score, conn_rag))
        content.append(Spacer(1, 4))
        content.append(kv_table(conn_rows))
    else:
        content.append(Paragraph("Connectivity data not available for this local authority. On-site survey required.", body_style))

    content.append(Spacer(1, 8))

    # ── MOBILE COVERAGE ──
    content.append(hr())
    content.append(Paragraph("Mobile Coverage", h2_style))
    if mob_data:
        mob_rows = [
            ("Indoor 4G (all operators)", f"{mob_data.get('indoor_4g_all_operators_pct', 0)}% of premises"),
            ("Outdoor 4G (all operators)", f"{mob_data.get('outdoor_4g_all_operators_pct', 0)}% of premises"),
            ("Outdoor 5G (all operators)", f"{mob_data.get('outdoor_5g_all_operators_pct', 0)}% of premises"),
            ("Indoor voice (all operators)", f"{mob_data.get('indoor_voice_all_operators_pct', 0)}% of premises"),
        ]
        content.append(score_bar_table("Mobile Score", mob_score_val,
                                       "Green" if (mob_score_val or 0) >= 70
                                       else "Amber" if (mob_score_val or 0) >= 40 else "Red"))
        content.append(Spacer(1, 4))
        content.append(kv_table(mob_rows))
    else:
        content.append(Paragraph("Mobile coverage data not available.", body_style))

    content.append(Spacer(1, 8))

    # ── TENANT COMPANIES ──
    if ch_data:
        content.append(hr())
        content.append(Paragraph("Registered Companies at Postcode", h2_style))
        content.append(Paragraph(
            f"<i>Source: Companies House API. Active companies registered at or near {park['postcode']}. "
            f"Not all tenant companies will be registered at this postcode.</i>",
            small_style
        ))
        content.append(Spacer(1, 6))

        ch_summary_rows = [
            ("Total results found", str(ch_data.get("total_results", 0))),
            ("Active companies", str(ch_data.get("active_companies", 0))),
        ]
        sectors_found = classify_sic_codes(ch_data.get("sic_codes", []))
        if sectors_found:
            ch_summary_rows.append(("Sector profile", ", ".join(sectors_found)))
        content.append(kv_table(ch_summary_rows))

        companies = ch_data.get("companies", [])
        if companies:
            content.append(Spacer(1, 6))
            content.append(Paragraph("Sample Active Companies", h3_style))
            co_data = [["Company Name", "Status", "Incorporated", "SIC Codes"]]
            for co in companies[:8]:
                co_data.append([
                    co.get("title", "")[:45],
                    co.get("company_status", "").capitalize(),
                    co.get("date_of_creation", "—"),
                    ", ".join(co.get("sic_codes", []))[:30]
                ])
            co_table = Table(co_data, colWidths=[6.5*cm, 2.5*cm, 2.5*cm, 5.5*cm])
            co_table.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), NAVY),
                ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE", (0,0), (-1,-1), 8),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [LGREY, colors.white]),
                ("GRID", (0,0), (-1,-1), 0.3, MGREY),
                ("LEFTPADDING", (0,0), (-1,-1), 5),
                ("TOPPADDING", (0,0), (-1,-1), 3),
                ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
            ]))
            content.append(co_table)
        content.append(Spacer(1, 8))

    # ── INTELLIGENCE FLAGS ──
    content.append(PageBreak())
    content.append(Paragraph("Infrastructure Intelligence Flags", h2_style))
    content.append(Paragraph(
        "The following observations are drawn from public data analysis and sector knowledge. "
        "They are intended to inform commercial conversations, not as definitive technical assessments.",
        body_style
    ))
    content.append(Spacer(1, 6))
    if flags:
        for flag in flags:
            content.append(Paragraph(f"⚠  {flag}", flag_style))
    else:
        content.append(Paragraph("No significant infrastructure flags identified from available data.", body_style))

    content.append(Spacer(1, 10))

    # ── OPPORTUNITIES ──
    content.append(hr())
    content.append(Paragraph("Commercial Opportunities", h2_style))
    content.append(Paragraph(
        "Based on data analysis and sector context, the following managed services and connectivity "
        "opportunities are identified for this park:",
        body_style
    ))
    content.append(Spacer(1, 6))
    for i, opp in enumerate(opportunities, 1):
        content.append(Paragraph(f"{i}.  {opp}", opp_style))

    content.append(Spacer(1, 10))

    # ── RECOMMENDED NEXT STEPS ──
    content.append(hr())
    content.append(Paragraph("Recommended Next Steps", h2_style))
    steps = [
        ("1. On-site connectivity survey", "Request permission to conduct a physical survey of campus infrastructure. This will validate Ofcom data at building level and identify specific upgrade opportunities."),
        ("2. Tenant engagement", "Propose a complimentary digital readiness review for tenant companies — this creates value for the park operator and opens parallel conversations with individual tenants."),
        ("3. Park director briefing", "Present this profile to the park director or facilities manager as a conversation starter. Frame around infrastructure gaps vs. peer parks."),
        ("4. Connectivity benchmarking", "Commission a WiredScore pre-assessment to benchmark the park against certification criteria — a useful hook for parks with ambitions to attract premium tenants."),
    ]
    steps_data = [[Paragraph(f"<b>{t}</b>", small_style), Paragraph(d, body_style)] for t, d in steps]
    steps_table = Table(steps_data, colWidths=[5*cm, 12*cm])
    steps_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [LGREY, colors.white]),
        ("GRID", (0,0), (-1,-1), 0.3, MGREY),
    ]))
    content.append(steps_table)

    content.append(Spacer(1, 16))
    content.append(hr(0.3, colors.HexColor("#CCCCCC")))
    content.append(Paragraph(
        f"Report generated: {datetime.now().strftime('%d %B %Y, %H:%M')}  ·  "
        f"Oxford–Cambridge Arc Digital Infrastructure Intelligence  ·  Internal Use Only  ·  "
        f"Data sources: Ofcom Connected Nations, Companies House, UKSPA. "
        f"Connectivity data is area-level (local authority) and may not reflect campus-specific provision.",
        footer_style
    ))

    doc.build(content)
    buffer.seek(0)
    return buffer

# ─── MAIN APP ─────────────────────────────────────────────────────────────────
def main():
    if not check_password():
        return

    parks = load_parks()
    area_data = load_area_data()

    st.markdown("## 🔬 Arc Parks Intelligence Tool")
    st.markdown("*Oxford–Cambridge Science & Innovation Parks — Digital Infrastructure Profiler*")
    st.divider()

    # Sidebar
    with st.sidebar:
        st.markdown("### ⚙️ Settings")
        ch_api_key = st.text_input(
            "Companies House API Key",
            type="password",
            help="Free key from developer.company-information.service.gov.uk — enables tenant company lookup"
        )
        if not ch_api_key:
            st.info("Add a Companies House API key to enable tenant company data")
        st.markdown("---")
        st.markdown("**About this tool**")
        st.markdown(
            "Automatically profiles science and innovation parks along the Oxford–Cambridge Arc using "
            "public data sources. No manual input required from the park."
        )
        st.markdown("---")
        st.markdown("**Data sources**")
        st.markdown("• Ofcom Connected Nations (July 2024)")
        st.markdown("• Valuation Office Agency (March 2025)")
        st.markdown("• Companies House API (live)")

    # Zone filter
    zones = sorted(set(p["zone"] for p in parks))
    zone_filter = st.selectbox("Filter by Zone", ["All Zones"] + zones)

    filtered = parks if zone_filter == "All Zones" else [p for p in parks if p["zone"] == zone_filter]

    # Park selector
    park_names = [p["name"] for p in filtered]
    selected_name = st.selectbox(
        f"Select Park ({len(filtered)} parks in view)",
        park_names,
        help="Select a park to automatically generate its intelligence profile"
    )

    park = next(p for p in parks if p["name"] == selected_name)

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"### {park['name']}")
        st.markdown(f"**{park['location']}  ·  {park['postcode']}**")
        st.markdown(f"*{park['zone']}*")
    with col2:
        generate_btn = st.button("🔍 Generate Intelligence Report", type="primary", use_container_width=True)

    if generate_btn:
        with st.spinner("Pulling data from Ofcom, Companies House and park database..."):

            # Get connectivity data
            area_conn = get_connectivity(park["local_authority"], area_data)
            conn_score, conn_data, conn_rag = score_connectivity(area_conn)
            mob_score_val, mob_data = mobile_score(area_conn)

            # Get Companies House data
            ch_data = get_companies_house_data(park["postcode"], ch_api_key) if ch_api_key else None

            # Generate opportunities
            opportunities, flags = generate_opportunities(
                park, conn_score, conn_data, mob_score_val, mob_data, ch_data
            )

        # ── DISPLAY RESULTS ──
        st.divider()
        st.markdown(f"## Intelligence Report: {park['name']}")

        # Top metrics row
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            score_display = f"{conn_score}/100" if conn_score is not None else "N/A"
            st.metric("Connectivity Score", score_display,
                      delta=conn_rag if conn_score else None,
                      delta_color="normal" if conn_rag == "Green" else "inverse")
        with m2:
            mob_display = f"{mob_score_val}/100" if mob_score_val is not None else "N/A"
            st.metric("Mobile Score", mob_display)
        with m3:
            if ch_data:
                st.metric("Active Companies Found", ch_data.get("active_companies", 0))
            else:
                st.metric("Companies House", "Key required")
        with m4:
            st.metric("Opportunities Identified", len(opportunities))

        st.markdown("---")

        # Two column layout
        left, right = st.columns([1, 1])

        with left:
            st.markdown("#### 📡 Connectivity Profile")
            if area_conn and conn_data:
                ff = conn_data.get("full_fibre_pct", 0)
                gig = conn_data.get("gigabit_pct", 0)
                sup = conn_data.get("superfast_pct", 0)
                no_dec = conn_data.get("no_decent_pct", 0)
                takeup = conn_data.get("ff_takeup_pct", 0)
                usage = conn_data.get("avg_data_usage_gb", 0)

                rag_emoji = {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}.get(conn_rag, "⚪")
                st.markdown(f"**Local Authority:** {park['local_authority']}")
                st.markdown(f"{rag_emoji} **Overall: {conn_rag}** ({conn_score}/100)")
                st.progress(ff / 100, text=f"Full Fibre available: {ff}%")
                st.progress(gig / 100, text=f"Gigabit-capable: {gig}%")
                st.progress(sup / 100, text=f"Superfast 30Mbps+: {sup}%")
                st.markdown(f"**No decent broadband:** {no_dec}% | **Full fibre take-up:** {takeup}% | **Avg usage:** {usage} GB/month")
            else:
                st.info("Connectivity data not found for this local authority. Add area_data.json from the commercial property app.")

            if mob_data:
                st.markdown("#### 📱 Mobile Coverage")
                mob_rag = "🟢" if (mob_score_val or 0) >= 70 else "🟡" if (mob_score_val or 0) >= 40 else "🔴"
                st.markdown(f"{mob_rag} **Mobile Score: {mob_score_val}/100**")
                st.progress(mob_data.get("indoor_4g_all_operators_pct", 0) / 100,
                            text=f"Indoor 4G (all operators): {mob_data.get('indoor_4g_all_operators_pct', 0)}%")
                st.progress(mob_data.get("outdoor_5g_all_operators_pct", 0) / 100,
                            text=f"Outdoor 5G (all operators): {mob_data.get('outdoor_5g_all_operators_pct', 0)}%")

        with right:
            st.markdown("#### 🏢 Park Profile")
            st.markdown(f"**Sector:** {park['sector']}")
            st.markdown(f"**Tenants:** {park['tenants']}")
            st.markdown(f"**Operator:** {park['operator']}")
            st.markdown(f"**Status:** {park['status']}")
            if park.get("size_sqft"):
                st.markdown(f"**Size:** {park['size_sqft']:,} sq ft")
            st.markdown(f"*{park['notes']}*")

            if ch_data:
                st.markdown("#### 🏛 Companies House")
                st.markdown(f"**Active companies at postcode:** {ch_data.get('active_companies', 0)}")
                sectors_found = classify_sic_codes(ch_data.get("sic_codes", []))
                if sectors_found:
                    st.markdown(f"**Sector profile:** {', '.join(sectors_found)}")
                companies = ch_data.get("companies", [])
                if companies:
                    with st.expander(f"View {min(len(companies), 8)} sample companies"):
                        for co in companies[:8]:
                            st.markdown(f"• **{co.get('title','')}** — {co.get('company_status','').capitalize()} ({co.get('date_of_creation','—')})")

        st.markdown("---")

        # Flags and opportunities
        fcol, ocol = st.columns([1, 1])
        with fcol:
            st.markdown("#### ⚠️ Intelligence Flags")
            if flags:
                for flag in flags:
                    st.warning(flag)
            else:
                st.success("No significant infrastructure flags from available data")

        with ocol:
            st.markdown("#### 💡 Commercial Opportunities")
            for i, opp in enumerate(opportunities, 1):
                st.info(f"**{i}.** {opp}")

        st.markdown("---")

        # PDF download
        st.markdown("#### 📄 Download Report")
        pdf_buf = generate_pdf(
            park, area_conn, conn_score, conn_data, conn_rag,
            mob_score_val, mob_data, ch_data, opportunities, flags
        )
        safe_name = park["name"].replace(" ", "_").replace("/", "-")[:40]
        st.download_button(
            label="⬇️ Download PDF Intelligence Report",
            data=pdf_buf,
            file_name=f"Arc_Parks_{safe_name}_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True
        )

    else:
        # Park preview before generating
        st.divider()
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**Zone**")
            st.markdown(park["zone"])
            st.markdown("**County**")
            st.markdown(park["county"])
        with col_b:
            st.markdown("**Sector**")
            st.markdown(park["sector"])
            st.markdown("**Tenants**")
            st.markdown(park["tenants"])
        with col_c:
            st.markdown("**Operator**")
            st.markdown(park["operator"])
            st.markdown("**Status**")
            st.markdown(park["status"])
        st.markdown(f"*{park['notes']}*")
        st.info("👆 Click **Generate Intelligence Report** to pull all data and create the PDF")

    # Footer: all parks table
    st.divider()
    with st.expander("📋 View all 35 Arc parks in database"):
        import pandas as pd
        df = pd.DataFrame([{
            "Park": p["name"],
            "Zone": p["zone"],
            "Postcode": p["postcode"],
            "Sector": p["sector"][:50],
            "Status": p["status"][:40]
        } for p in parks])
        st.dataframe(df, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    main()
