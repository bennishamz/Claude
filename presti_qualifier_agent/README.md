# Presti Account Qualifier

B2B account qualification agent for [Presti](https://presti.ai) — a platform for AI-generated product visuals. Uses a **knowledge-first approach** to classify companies by vertical, ecommerce activity, and catalog size, with web scraping as optional fallback.

## How it works

### Knowledge-first methodology

The agent prioritizes pre-filled knowledge columns (`known_vertical`, `known_verdict`, `known_ecommerce`, `known_catalog_size`) over scraping. This is more reliable because modern JS-heavy sites (React SPAs, Cloudflare-protected) are difficult to scrape accurately.

**Recommended workflow:**
1. Use Claude to classify companies by vertical, ecommerce, and catalog size
2. Feed the enriched CSV to `qualify.py` for scoring
3. Optionally enable scraping (`SCRAPING_ENABLED=true`) to fill gaps

### Scoring: 100 points

| Criterion | Details | Points |
|---|---|---|
| **Revenue** | >$50B=40, >$10B=35, >$5B=30, >$2B=25, >$1B=20, $500M-$1B=10 | 0-40 |
| **Vertical** | Tier 1 (KEEP)=30, Tier 2 (DEPRIORITIZE)=15 | 0-30 |
| **Catalog** | LARGE (1000+)=30, MEDIUM (100-999)=15, SMALL (<100)=5 | 0-30 |

**Hard filters** (→ DISQUALIFIED):
- No ecommerce
- Non-target vertical (DISQUALIFY)

**Tiers:**
- **Tier 1** (80-100 pts): Top priority — large revenue + priority vertical + large catalog
- **Tier 2** (60-79 pts): High value — big fashion/beauty groups, or mid-size tier 1 verticals
- **Tier 3** (40-59 pts): Good fit — $500M-$1B tier 2, or mid-size with medium catalogs
- **Tier 4** (<40 pts): Opportunistic — small catalogs, niche brands

### Vertical tiers

| Tier | Verdict | Verticals |
|---|---|---|
| **Tier 1** | KEEP | Home/furniture, home improvement, consumer electronics, sporting goods, pet care, kitchenware, toys, outdoor/garden, automotive parts |
| **Tier 2** | DEPRIORITIZE | Fashion/apparel, beauty/cosmetics, footwear, eyewear, jewelry/watches, department stores, grocery (non-food), pharmacy (parapharma) |
| **Tier 3** | DISQUALIFY | B2B industrial, SaaS, insurance/banking, spirits/alcohol, travel/hotels, media/publishing, healthcare B2B |

## Scripts

| Script | Purpose |
|---|---|
| `qualify.py` | Main qualifier — reads input, applies scoring, writes output |
| `requalify_playwright.py` | Re-evaluates low-scoring companies using Playwright headless browser |
| `retry_errors.py` | Retries inaccessible sites with SSL bypass and URL variants |

## Installation

```bash
pip3 install requests beautifulsoup4

# Optional: Playwright for JS-heavy sites
pip3 install playwright
python3 -m playwright install chromium
```

## Usage

### 1. Prepare input

Create `input.csv` with these columns:

```csv
company,website,revenue_range,estimated_revenue,country,known_vertical,known_verdict,known_ecommerce,known_catalog_size
IKEA,https://www.ikea.com,above_1b,~€50B+,Sweden,home/furniture,KEEP,PASS,LARGE
Adidas,https://www.adidas.com,above_1b,~$25B,Germany,sporting goods,KEEP,PASS,LARGE
AMIRI,https://www.amiri.com,500m_to_1b,~$500M+,USA,fashion/apparel,DEPRIORITIZE,PASS,MEDIUM
```

Minimum required columns: `company`, `website`. All other columns are optional but improve accuracy.

### 2. Run qualification

```bash
# Knowledge-only mode (fastest, most accurate if columns are pre-filled)
SCRAPING_ENABLED=false python3 qualify.py

# With scraping fallback for missing fields
python3 qualify.py

# With Playwright for JS-heavy sites
PLAYWRIGHT_ENABLED=true python3 qualify.py

# Custom paths
INPUT_PATH=my_input.csv OUTPUT_PATH=my_output.csv python3 qualify.py
```

### 3. Re-evaluate (optional)

```bash
python3 requalify_playwright.py   # Re-scores low-tier companies
python3 retry_errors.py           # Retries inaccessible sites
```

## Output

`output/results.csv` contains all input columns plus:

| Column | Description |
|---|---|
| `vertical` | Detected industry vertical |
| `vertical_verdict` | KEEP / DEPRIORITIZE / DISQUALIFY |
| `has_ecommerce` | PASS / DEPRIORITIZE |
| `catalog_size` | LARGE / MEDIUM / SMALL / UNKNOWN |
| `fit_score` | Tier 1 / Tier 2 / Tier 3 / Tier 4 / DISQUALIFIED |
| `fit_points` | Score out of 100 |
| `revenue_pts` | Revenue component (0-40) |
| `vertical_pts` | Vertical component (0-30) |
| `catalog_pts` | Catalog component (0-30) |
| `tier` | Same as fit_score |
| `continent` | Auto-derived from country |
| `notes` | Additional context |
| `qualified_at` | ISO 8601 timestamp |
