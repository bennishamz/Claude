# Account Research Script

Automated account discovery tool that finds customers, partners, and related companies for a given target company by scraping its web presence, review sites, Google, and LinkedIn.

## What it does

Given a company name and website URL, the script:

1. **Scrapes the homepage** — renders with Selenium, scrolls to trigger lazy-loaded content, clicks through logo carousels, and extracts company names from image alt text, titles, and filenames
2. **Visits all relevant subpages** — auto-detects links to Customers, Case Studies, Partners, About, Press, Integrations pages and scrapes each one
3. **Extracts testimonials** — discovers all customer story pages, parses testimonial/quote blocks for company attribution (structured and free-text)
4. **Runs Google searches** — queries for customers, case studies, PDFs, and LinkedIn mentions
5. **Scrapes review sites** — collects reviewer company names from G2, Capterra, and TrustRadius
6. **Scans LinkedIn posts** — scrolls the company's post feed (up to 2 years) for partner/client mentions

All results are deduplicated by account name and exported to a single CSV.

## Installation

```bash
cd account_research
pip3 install -r requirements.txt
```

Requires Python 3.9+ and Chrome browser. ChromeDriver is auto-installed via `webdriver-manager`.

## Usage

```bash
# Basic usage
python3 account_research.py "Akeneo" "akeneo.com"

# Custom output file
python3 account_research.py "Salsify" "salsify.com" --output salsify_results.csv

# Visible browser (useful for LinkedIn login)
python3 account_research.py "Akeneo" "akeneo.com" --no-headless
```

Output defaults to `<company_name>_accounts.csv`.

## Output format

| Column | Description |
|--------|-------------|
| `account_name` | Deduplicated company name |
| `domain` | Domain if detectable from image source URL |
| `detection_source` | Where the account was found (e.g. `homepage`, `subpage: activation`, `testimonial: customer story: kitwave`, `G2 review`) |
| `detected_company` | The company that was researched (input) |

## Known limitations

- **LinkedIn** requires manual authentication — use `--no-headless` to log in, or the script skips gracefully
- **Google search** may rate-limit on repeated runs (2s delay between queries)
- **Review site slugs** are guessed from the company name — may 404 if the actual slug differs
- **Review sites** actively block headless browsers — may return 0 results even when reviews exist
- **Testimonial extraction** is heuristic — tuned for common CMS patterns (WordPress, HubSpot) but may miss custom components
