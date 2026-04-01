# Presti Account Qualifier

B2B account qualification agent for [Presti](https://presti.ai) — a platform for AI-generated product visuals. Given a list of companies, the agent scrapes their websites and evaluates whether they are a good fit based on vertical, catalog size, ecommerce activity, and product imagery.

## How it works

1. **`prepare_input.py`** — Extracts companies from an enriched Excel file (filters by `revenue_status`) and writes `input.csv`
2. **`qualify.py`** — Main qualifier. For each company, scrapes the homepage, category pages, and product pages using `requests` + `BeautifulSoup`. Evaluates 5 criteria and assigns a fit score (A/B/C/DISQUALIFIED/ERROR)
3. **`requalify_playwright.py`** — Re-evaluates companies scored C with unknown catalog size using a Playwright headless browser to render JS-heavy pages
4. **`retry_errors.py`** — Retries ERROR companies (inaccessible sites) using Playwright with SSL bypass, URL variants (www/non-www, https/http), and extended wait times

## Qualification criteria

| Criterion | Values |
|---|---|
| **Vertical** | KEEP (home/furniture, electronics, sporting goods, pet care, kitchenware, toys, outdoor/garden) · DISQUALIFY (fashion, grocery, automotive, spirits, pharmacy) |
| **Physical products** | PASS / FAIL |
| **eCommerce active** | PASS (cart/buy button detected) · DEPRIORITIZE |
| **Catalog size** | LARGE (1000+) · MEDIUM (100-999) · SMALL (<100) · UNKNOWN |
| **Product images** | PASS (product image URLs detected) · DEPRIORITIZE |

## Fit score

- **A** = vertical KEEP + catalog LARGE + eCommerce PASS
- **B** = 2 of 3 criteria met
- **C** = 1 or 0 criteria met
- **DISQUALIFIED** = vertical in disqualify list
- **ERROR** = site inaccessible

## Installation

```bash
pip3 install requests beautifulsoup4 openpyxl

# For Playwright-based re-evaluation (optional)
pip3 install playwright
python3 -m playwright install chromium
```

## Usage

### 1. Prepare input

Create an `input.csv` with at minimum `company` and `website` columns:

```csv
company,website
Intersport,https://www.intersport.fr
Staples,https://www.staples.com
Carhartt,https://www.carhartt.com
```

Or extract from an Excel file by editing `prepare_input.py` to point to your source file.

### 2. Run qualification

```bash
python3 qualify.py
```

Results are written incrementally to `output/results.csv`.

### 3. Re-evaluate JS-heavy sites (optional)

```bash
python3 requalify_playwright.py   # Re-scores C/UNKNOWN companies
python3 retry_errors.py           # Retries ERROR companies
```

## Output

`output/results.csv` contains all input columns plus:

| Column | Description |
|---|---|
| `vertical` | Detected industry vertical |
| `vertical_verdict` | KEEP / DISQUALIFY / UNCLEAR |
| `sells_physical_products` | PASS / FAIL |
| `has_ecommerce` | PASS / DEPRIORITIZE |
| `catalog_size` | LARGE / MEDIUM / SMALL / UNKNOWN |
| `catalog_size_raw` | Raw product count if detected |
| `has_product_images` | PASS / DEPRIORITIZE |
| `fit_score` | A / B / C / DISQUALIFIED / ERROR |
| `notes` | Additional context |
| `qualified_at` | ISO 8601 timestamp |
