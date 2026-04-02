# Account Research Script

Automated account discovery tool that finds customers, partners, and related companies for a given target company by scraping its web presence, review sites, Google, and LinkedIn.

## Purpose

Given a company name and website URL, the script identifies all publicly visible accounts (customers, partners, case study subjects) and outputs a deduplicated CSV. Designed for competitive intelligence and ecosystem mapping.

## Website URL Verification

When the agent finds or assigns a website URL for a company, it **must** follow this verification process:

### 1. Search for the official website
- Search for `"{company_name} official website"` to find the correct URL.
- Do not rely on memory or assumptions — always verify via search.

### 2. Cross-check ownership
- Confirm the URL actually belongs to the target company:
  - The company name appears in the page title, header, or domain.
  - The site content matches the company's known industry and description.
- If the domain doesn't clearly match the company, reject it.

### 3. Handle homonyms (companies sharing the same name)
- When multiple companies share the same name (e.g. "Bestway", "Zenith", "Summit"), **always prefer the ecommerce / retail / manufacturer version**.
- Never select a travel agency, professional services firm, or unrelated B2B company when a retail/ecommerce/manufacturer match exists.
- If ambiguity remains, include a note explaining which entity was selected and why.

### 4. Assign confidence level
- Include `website_confidence` in the output for every URL:
  - **HIGH** — Domain contains company name, page title/header confirms identity, industry matches.
  - **MEDIUM** — Domain is plausible but doesn't contain the company name, or minor ambiguity exists.
  - **LOW** — URL found but ownership is uncertain, or multiple same-name companies make disambiguation difficult.

### 5. Never use unverified URLs
- **Never** include a URL in the output that doesn't clearly belong to the target company.
- If no confident match is found, set the website field to `null` and `website_confidence = LOW` with a note explaining why.

## Data Sources

The script scrapes 8 categories of sources, in order:

1. **Homepage logos & names** -- Selenium renders the full page, scrolls to trigger lazy-loaded content, clicks through carousel next buttons (supports Slick, Swiper, and generic carousels), then extracts company names from image `alt` text, `title` attributes, and filenames in sections matching customer/partner/logo keywords.

2. **Subpages** -- Auto-detects links from the homepage nav matching keywords (customer, case study, partner, about, press, integrations, ecosystem, marketplace, etc.), visits each, and applies the same logo/text extraction as Step 1.

3. **Customer story pages & testimonials** -- Discovers all individual customer story / case study URLs from listing pages (handles pagination and "load more" buttons). Visits each and extracts company names from testimonial blocks (`testimonial-card`, `blockquote`, `<figure>/<figcaption>`, quote-related CSS classes). Parses structured attribution (`<strong>` for company, `author-info` containers) and falls back to URL slug derivation when no structured company name is found.

4. **Google searches** -- Runs 3 queries via `googlesearch-python`:
   - `"[company]" customer OR "case study" OR "powered by" OR "uses" OR "partner"`
   - `"[company]" filetype:pdf`
   - `"[company]" site:linkedin.com`
   Parses result pages for "[Company] uses [target]" and similar patterns.

5. **G2 reviews** -- Visits `g2.com/products/[slug]/reviews`, extracts reviewer company names from review cards.

6. **Capterra reviews** -- Visits `capterra.com/reviews/[slug]`, extracts "at Company" patterns from reviewer info.

7. **TrustRadius reviews** -- Visits `trustradius.com/products/[slug]/reviews`, extracts company names from review elements.

8. **LinkedIn company posts** -- Attempts to scroll through the company's LinkedIn post feed (up to 2 years back), extracting partner/client mentions via regex patterns (partnership announcements, testimonials, integration mentions). Requires authentication; skips gracefully if blocked.

## Installation

```bash
cd ~/account_research
pip3 install -r requirements.txt
```

Dependencies: `selenium`, `beautifulsoup4`, `requests`, `googlesearch-python`, `webdriver-manager`, `lxml`. ChromeDriver is auto-installed via `webdriver-manager`.

## Usage

```bash
# Basic usage
python3 account_research.py "Akeneo" "akeneo.com"

# Custom output file
python3 account_research.py "Salsify" "salsify.com" --output salsify_results.csv

# Visible browser (useful for LinkedIn login)
python3 account_research.py "Akeneo" "akeneo.com" --no-headless
```

Output defaults to `<company_name>_accounts.csv` in the current directory.

To generate a customers-only file (excluding partner rows):
```bash
head -1 akeneo_accounts.csv > akeneo_accounts_customers_only.csv
grep -iv 'technology partner\|solution partner' akeneo_accounts.csv | tail -n +2 >> akeneo_accounts_customers_only.csv
```

## Output Format

CSV with columns:
- `account_name` -- Deduplicated company name (Title Case)
- `domain` -- Domain if detectable from image source URL
- `detection_source` -- Where the account was found (e.g. `homepage`, `subpage: activation`, `testimonial: customer story: kitwave`, `Google search`, `G2 review`)
- `detected_company` -- The company that was researched (input)
- `linkedin_url` -- Official LinkedIn company page URL (format: `https://www.linkedin.com/company/...`). To find it, search `"{account_name} LinkedIn"` and take the first official company page result. If no official page is found, set to `null`.
- `hq_country` -- Country where the company is headquartered, in English (e.g. `France`, `United States`, `Germany`). Extract from the company website (About/Contact page), Wikipedia, or LinkedIn profile. If not determinable, set to `null`.

Accounts appearing in multiple sources get one row per source.

## Fixes and Improvements Made During Development

### Carousel & logo detection
- Selenium scrolls full page and clicks carousel next buttons (up to 12 clicks per carousel) to reveal all slides before parsing
- Added `logo-carousel`, `logo-grid`, `logos-block`, `logo-card` to section detection keywords
- Ancestor-walk depth of 5 levels to match images nested inside carousel containers

### Image filename cleaning
- Retina suffixes (`@2x`, `@2x-1`) are stripped before name extraction, not rejected -- this was the main bug causing missed logos (e.g. `logo-assa-abloy@2x-1.png` was being dropped entirely)
- Trailing image variant numbers stripped (`logo-staples-2` becomes `Staples`)
- Dimension patterns stripped (`1200x1200`, `1920x1080`)
- File extensions removed before processing

### Noise filtering (`clean_name`)
- Tracking pixel/analytics domains blocked (ZoomInfo, Google Analytics, HubSpot, Segment, etc.) -- images from these domains are skipped entirely
- Hex IDs and base64 fragments rejected
- Generic marketing phrases rejected (50+ patterns: "learn more", "get started", "cookie", etc.)
- UI/page element names rejected ("partners hero", "key benefits", "thumbnail", "webinar", etc.)
- Single-word generic terms blocked ("biscuit", "ads", etc.)
- Sentences (>6 words) and strings ending with periods rejected
- Raw filenames with timestamps/long digit sequences rejected

### Testimonial extraction
- Detects testimonial blocks via CSS class patterns (`testimonial-card`, `quote-card`, `review-card`, etc.) and `<blockquote>` elements
- Extracts company names from structured attribution: `<strong>` tags inside author containers, `.company`/`.org` class elements, `<cite>` elements
- Falls back to "at Company" regex pattern in free text
- Falls back to URL slug for customer-story pages when no structured attribution exists
- Person-name filter (`_looks_like_person_name`) prevents storing human names as companies -- checks trailing commas, "First Last" capitalization patterns, and absence of company indicator words (Inc, LLC, GmbH, Electric, Store, etc.)

### Subpage handling
- Clean labels derived from URL path (not raw nav link text which was often garbled)
- Homepage duplicate visits eliminated
- Customer story listing pages paginated (clicks "load more", follows `rel=next` links)
- Already-visited URLs tracked to avoid redundant scraping

## Known Limitations

- **LinkedIn requires authentication** -- Without a logged-in session, LinkedIn redirects to a login page. Use `--no-headless` to log in manually in the browser window, or the script skips LinkedIn gracefully with a warning.
- **Google search rate limits** -- `googlesearch-python` can get rate-limited by Google. The script adds 2-second delays between queries but may still get blocked on repeated runs. Consider SerpAPI for production use.
- **Review site URL slugs are guessed** -- G2/Capterra/TrustRadius URLs are constructed from the company name slug. If the actual slug differs (e.g. `akeneo-pim` instead of `akeneo`), the page will 404 and that source is skipped.
- **Review site anti-scraping** -- G2, Capterra, and TrustRadius actively block headless browsers. Reviewer company extraction may return 0 results even when reviews exist.
- **Testimonial patterns are site-specific** -- The structured extraction works well for common CMS patterns (WordPress, HubSpot) but may miss custom-built testimonial components. The URL-slug fallback helps compensate.
- **Name cleaning is heuristic** -- Some false positives (generic image filenames) or false negatives (legitimate company names that look like common words) may slip through. The filters were tuned on akeneo.com and may need adjustment for other sites.
- **No JavaScript-rendered review content** -- Review sites that load reviews via client-side AJAX after initial page load may not have their content captured even with Selenium scrolling.
