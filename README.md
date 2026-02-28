# UK Science Parks Intelligence Tool

National prospecting tool for digital infrastructure intelligence across UK science and innovation parks.

## Coverage
- **111 parks** across **11 regions** and **32 clusters**
- Regions: London & South East, South West, Oxford-Cambridge Arc, East Midlands, West Midlands, North West, Yorkshire & Humber, North East, Scotland, Wales, Northern Ireland

## Features
- **Three-level selection**: Region → Cluster → Individual park
- **Area reports**: Select all parks in a cluster or entire region — get ranked connectivity table, aggregate opportunities, and individual summaries
- **Individual park reports**: Full connectivity profile, Companies House tenant analysis, intelligence flags, commercial opportunities
- **PDF download**: Both area reports and individual reports generate professional PDFs

## Data Sources
- Ofcom Connected Nations (July 2024) — broadband and mobile coverage at local authority level
- Companies House API (free, 600 req/5 min) — active companies registered at park postcodes
- UKSPA, Wikipedia, individual park websites (February 2026) — park reference data

## Deployment

### Streamlit Cloud (recommended)
1. Fork this repository
2. Go to share.streamlit.io → Create app → select this repo
3. Set main file: `app.py`
4. In App Settings → Secrets, add: `CH_API_KEY = "your-companies-house-api-key"`
5. Deploy

### Local
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Password
Default: `sciparks2026` — change in line 12 of app.py

## Updating the Park Database
Edit `uk_science_parks.json`. Structure:
```json
{
  "regions": [{
    "id": "region_id",
    "name": "Region Name",
    "clusters": [{
      "id": "cluster_id", 
      "name": "Cluster Name",
      "parks": [{
        "id": "park_id",
        "name": "Park Name",
        "location": "City, County",
        "county": "County",
        "local_authority": "LA name (must match Ofcom dataset)",
        "postcode": "AB1 2CD",
        "sector": "Life Sciences, Biotech",
        "tenants": "100+",
        "operator": "Operator name",
        "status": "Established",
        "notes": "Description",
        "website": "https://..."
      }]
    }]
  }]
}
```

## Notes
- Ofcom data matches on `local_authority` field — accuracy depends on exact name match with Ofcom dataset
- Companies House results are registered address lookups — not all tenants will be registered at the park postcode
- Area reports do not query Companies House (to avoid API rate limits across many parks simultaneously)
