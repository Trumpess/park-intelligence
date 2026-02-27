# Arc Parks Intelligence Tool

**Oxford–Cambridge Science & Innovation Parks — Digital Infrastructure Profiler**

An internal prospecting tool that automatically generates digital infrastructure intelligence reports for science and innovation parks along the Oxford–Cambridge Arc. No input required from the target park — it runs entirely on public data.

## What it does

Select any of the 35 parks in the database. The app automatically pulls:

- **Ofcom connectivity data** — full fibre availability, gigabit coverage, superfast %, no-decent-broadband %, full fibre take-up, average monthly data usage (matched to the park's local authority)
- **Mobile coverage** — indoor/outdoor 4G and 5G across all operators
- **Companies House data** — active companies registered at the park's postcode, SIC code sector profiling (requires free CH API key)

It then:
- Scores connectivity (0–100) with Red/Amber/Green RAG status
- Generates specific infrastructure flags based on the data
- Identifies commercial opportunities tailored to the park's sector and scale
- Produces a branded PDF report ready to use as outreach material

## Files

| File | Purpose |
|------|---------|
| `app.py` | Main Streamlit application |
| `parks_data.json` | Database of 35 Arc parks with postcodes, operators, sectors, local authorities |
| `area_data.json` | Ofcom + VOA data for all UK local authorities (copy from commercial property app) |
| `requirements.txt` | Python dependencies |

## Setup

### 1. Clone / upload to GitHub

Upload all four files to a GitHub repository.

### 2. Deploy on Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Connect to your GitHub repo
3. Set main file path: `app.py`
4. Deploy

### 3. Set password

The default password is `arcreport2026`. To change it, edit line 10 of `app.py`:
```python
PASSWORD = "your-new-password"
```

### 4. Companies House API key

Get a free key at [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk). Enter it in the sidebar when using the app. Free tier: 600 requests per 5 minutes.

### 5. area_data.json

This file is shared with the commercial property app. Copy it from that repository into this one. Without it, connectivity scores will show as unavailable but the rest of the app functions normally.

## Parks in the database

35 parks across 6 geographic clusters, west to east:

- **Oxford Cluster** — ARC Oxford, Oxford Science Park, Oxford North, Begbroke, Oxford Technology Park, BioEscalator
- **Oxfordshire** — Milton Park, Harwell Campus, Abingdon Science Park, Culham Campus, Bicester Motion
- **Buckinghamshire & MK** — Cranfield Technology Park, Silverstone Technology Cluster
- **Hertfordshire** — Stevenage Bioscience Catalyst, Elevate Quarter, BioPark Hertfordshire
- **Cambridge North** — Cambridge Science Park, St John's Innovation Centre, Peterhouse Technology Park, Cambridge Research Park, Melbourn Science Park, TusPark
- **Cambridge South** — Cambridge Biomedical Campus, Babraham, Granta Park, Wellcome Genome Campus, Chesterford Research Park, Haverhill Research Park, Cambourne Park, Unity Campus, South Cambridge Science Centre, The Mill Scitech Park

## Adding parks

Add new entries to `parks_data.json` following the existing format. The critical fields are `postcode` (drives Companies House lookup) and `local_authority` (drives Ofcom data matching).

## Data sources

- Ofcom Connected Nations, July 2024
- Valuation Office Agency, March 2025  
- Companies House API (live, free)
- Park data manually curated from UKSPA, Oxford Calling, Cambridge& and individual park websites, February 2026
