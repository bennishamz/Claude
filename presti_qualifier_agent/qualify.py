"""
Presti Account Qualifier
Knowledge-first approach with scraping fallback.
100-point scoring: revenue (40) + vertical (30) + catalog (30).
"""
import csv
import re
import os
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import urllib3
import warnings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

INPUT_PATH = os.environ.get("INPUT_PATH", "input.csv")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "output/results.csv")
SCRAPING_ENABLED = os.environ.get("SCRAPING_ENABLED", "true").lower() == "true"
PLAYWRIGHT_ENABLED = os.environ.get("PLAYWRIGHT_ENABLED", "false").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}
TIMEOUT = (5, 25)
PROBE_TIMEOUT = (5, 10)
MIN_USEFUL_TEXT = 200

# ─── Ecommerce platform signatures ───────────────────────────────────────────

ECOM_PLATFORMS = {
    "Shopify": [r"cdn\.shopify\.com", r"myshopify\.com"],
    "Magento": [r"mage/cookies", r"Magento_", r"/static/version"],
    "WooCommerce": [r"woocommerce", r"wc-ajax"],
    "Salesforce Commerce": [r"demandware\.net", r"dw/image/"],
    "BigCommerce": [r"bigcommerce\.com", r"stencil-utils"],
    "PrestaShop": [r"prestashop", r"/modules/ps_"],
    "Shopware": [r"shopware"],
    "Vtex": [r"vtex\.com", r"vteximg"],
    "SAP Commerce": [r"hybris", r"sap-commerce"],
}

# ─── Vertical keyword lists ──────────────────────────────────────────────────

TIER1_VERTICALS = {
    "home/furniture": ["furniture", "home decor", "mattress", "bedding", "bath", "rugs", "curtains", "blinds", "lighting"],
    "home improvement": ["home improvement", "hardware", "tools", "power tools", "plumbing", "electrical", "building materials", "diy", "renovation", "flooring", "tile", "paint"],
    "consumer electronics": ["electronics", "consumer electronics", "appliance", "audio", "video", "speaker", "headphone", "tv", "camera", "monitor"],
    "sporting goods": ["sporting goods", "sports", "fitness", "golf", "tennis", "cycling", "bicycle", "running", "athletic", "hunting", "fishing", "tactical"],
    "pet care": ["pet", "pet care", "pet food", "pet supplies", "dog", "cat food"],
    "kitchenware": ["kitchen", "kitchenware", "cookware", "tableware"],
    "toys": ["toys", "games"],
    "outdoor/garden": ["outdoor", "camping", "hiking", "garden", "lawn", "patio", "grill", "bbq"],
    "automotive parts": ["auto parts", "car parts", "automotive", "tire", "wheel"],
}

TIER2_VERTICALS = {
    "fashion/apparel": ["fashion", "apparel", "clothing", "dress", "t-shirt", "jeans", "handbag"],
    "beauty/cosmetics": ["beauty", "cosmetics", "skincare", "makeup", "fragrance", "perfume"],
    "eyewear/optical": ["eyewear", "glasses", "lenses", "optical", "sunglasses"],
    "jewelry/watches": ["jewelry", "watches", "rings", "necklace", "bracelet", "diamond"],
    "footwear": ["footwear", "shoes", "boots", "sandals", "sneakers"],
}

DISQUALIFY_KEYWORDS = [
    "insurance", "banking", "mortgage", "financial services",
    "saas", "cloud platform", "enterprise software",
    "consulting", "advisory", "professional services",
    "alcohol", "spirits", "wine", "beer", "brewery", "distillery",
    "hotel", "travel", "airline", "cruise",
]

# ─── Continent mapping ───────────────────────────────────────────────────────

COUNTRY_TO_CONTINENT = {
    "USA": "North America", "Canada": "North America", "Mexico": "North America",
    "Mexique": "North America",
    "UK": "Europe", "Royaume-Uni": "Europe", "France": "Europe",
    "Germany": "Europe", "Allemagne": "Europe", "Italy": "Europe", "Italie": "Europe",
    "Spain": "Europe", "Espagne": "Europe", "Netherlands": "Europe", "Pays-Bas": "Europe",
    "Belgium": "Europe", "Switzerland": "Europe", "Suisse": "Europe",
    "Sweden": "Europe", "Suède": "Europe", "Denmark": "Europe", "Norway": "Europe",
    "Finland": "Europe", "Austria": "Europe", "Poland": "Europe",
    "Czech Republic": "Europe", "Ireland": "Europe", "Irlande": "Europe",
    "Portugal": "Europe", "Greece": "Europe", "Hungary": "Europe",
    "Luxembourg": "Europe", "Lithuania": "Europe", "Latvia": "Europe",
    "Estonia": "Europe", "Turkey": "Europe", "Monaco": "Europe",
    "Liechtenstein": "Europe", "Slovenia": "Europe", "Slovakia": "Europe",
    "Croatia": "Europe", "Romania": "Europe", "Bulgaria": "Europe",
    "China": "Asia", "Chine": "Asia", "Japan": "Asia", "South Korea": "Asia",
    "Corée du Sud": "Asia", "India": "Asia", "Singapore": "Asia",
    "Hong Kong": "Asia", "Taiwan": "Asia", "Thailand": "Asia",
    "Indonesia": "Asia", "Malaysia": "Asia", "Philippines": "Asia",
    "Israel": "Middle East", "Israël": "Middle East", "UAE": "Middle East",
    "Saudi Arabia": "Middle East", "Kuwait": "Middle East",
    "Australia": "Oceania", "New Zealand": "Oceania",
    "Brazil": "South America", "Brésil": "South America",
    "Colombia": "South America", "Colombie": "South America",
    "Argentina": "South America", "Chile": "South America",
    "South Africa": "Africa", "Egypt": "Africa", "Morocco": "Africa",
    "El Salvador": "Central America", "Costa Rica": "Central America",
}


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def create_session():
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], connect=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def fetch(url, session, verify_ssl=True, timeout=None):
    t = timeout or TIMEOUT
    try:
        r = session.get(url, timeout=t, allow_redirects=True, verify=verify_ssl)
        r.raise_for_status()
        return r, BeautifulSoup(r.text, "html.parser")
    except requests.exceptions.SSLError:
        if verify_ssl:
            return fetch(url, session, verify_ssl=False, timeout=t)
        return None, None
    except Exception:
        if verify_ssl:
            try:
                r = session.get(url, timeout=t, allow_redirects=True, verify=False)
                r.raise_for_status()
                return r, BeautifulSoup(r.text, "html.parser")
            except Exception:
                return None, None
        return None, None


def get_page_text(soup):
    if not soup:
        return ""
    copy = BeautifulSoup(str(soup), "html.parser")
    for tag in copy(["script", "style", "noscript"]):
        tag.decompose()
    return copy.get_text(separator=" ", strip=True).lower()


# ─── Playwright helpers (optional) ───────────────────────────────────────────

_browser = None


def get_browser():
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _browser = pw.chromium.launch(headless=True)
    return _browser


def dismiss_popups(page):
    selectors = [
        "button:has-text('Accept')", "button:has-text('Accept All')",
        "button:has-text('Accepter')", "button:has-text('Tout accepter')",
        "button:has-text('OK')", "button:has-text('Got it')",
        "button:has-text('Close')", "button:has-text('Allow')",
        "[id*='cookie'] button", "[class*='cookie'] button",
        "[id*='consent'] button", "[id*='onetrust'] button",
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def fetch_with_playwright(url, timeout=20000):
    browser = get_browser()
    try:
        page = browser.new_page()
        page.set_extra_http_headers({"User-Agent": HEADERS["User-Agent"]})
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2000)
        dismiss_popups(page)
        page.wait_for_timeout(2000)
        raw = page.content()
        page.close()
        return raw, BeautifulSoup(raw, "html.parser")
    except Exception:
        try:
            page.close()
        except Exception:
            pass
        return None, None


def detect_ecom_from_rendered_dom(url):
    browser = get_browser()
    try:
        page = browser.new_page()
        page.set_extra_http_headers({"User-Agent": HEADERS["User-Agent"]})
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        dismiss_popups(page)
        page.wait_for_timeout(1500)
        cart_selectors = [
            "[aria-label*='cart' i]", "[aria-label*='bag' i]",
            "[aria-label*='basket' i]", "[aria-label*='panier' i]",
            "[class*='cart-icon' i]", "[class*='minicart' i]",
            "a[href*='/cart']", "a[href*='/basket']",
            "nav a:has-text('Shop')", "nav a:has-text('Boutique')",
        ]
        for sel in cart_selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    page.close()
                    return "PASS", f"DOM: {sel}"
            except Exception:
                continue
        page.close()
    except Exception:
        try:
            page.close()
        except Exception:
            pass
    return "DEPRIORITIZE", None


# ─── Scraping-based detection ────────────────────────────────────────────────

def detect_ecom_platform(raw_html):
    for platform, patterns in ECOM_PLATFORMS.items():
        for pattern in patterns:
            if re.search(pattern, raw_html, re.I):
                return platform
    return None


def detect_ecommerce_from_html(raw_html, soup):
    platform = detect_ecom_platform(raw_html)
    if platform:
        return "PASS", platform
    cart_patterns = [
        r"add.to.cart", r"add.to.bag", r"add.to.basket",
        r"shopping.?cart", r"checkout", r"/cart", r"/basket",
        r"buy.now", r"add_to_cart", r"cart\.js", r"minicart",
        r"data-action.*cart", r"pdp-add-to-cart",
    ]
    for p in cart_patterns:
        if re.search(p, raw_html, re.I):
            return "PASS", f"pattern: {p}"
    price_patterns = [
        r'"price"\s*:\s*[\d".]', r"itemprop=['\"]price['\"]",
        r"product-price", r"sale-price",
    ]
    for p in price_patterns:
        if re.search(p, raw_html, re.I):
            return "PASS", f"price: {p}"
    return "DEPRIORITIZE", None


def classify_vertical_scraping(text):
    text_lower = text.lower()
    best_t1 = ("UNCLEAR", 0)
    for vert, keywords in TIER1_VERTICALS.items():
        c = sum(1 for kw in keywords if kw in text_lower)
        if c > best_t1[1]:
            best_t1 = (vert, c)
    best_t2 = ("UNCLEAR", 0)
    for vert, keywords in TIER2_VERTICALS.items():
        c = sum(1 for kw in keywords if kw in text_lower)
        if c > best_t2[1]:
            best_t2 = (vert, c)
    disq = sum(1 for kw in DISQUALIFY_KEYWORDS if kw in text_lower)
    if best_t1[1] >= 1:
        return best_t1[0], "KEEP"
    if best_t2[1] >= 1:
        return best_t2[0], "DEPRIORITIZE"
    if disq >= 1:
        return "non-target", "DISQUALIFY"
    return "UNCLEAR", "UNCLEAR"


def estimate_catalog_scraping(base_url, session, soups, raw_htmls):
    count_patterns = [
        r'(\d[\d,\.\s]*)\s*(?:products?|items?|results?|produits?|produkte?)',
        r'(?:of|sur|von)\s+(\d[\d,\.\s]*)',
    ]
    for soup in soups:
        if not soup:
            continue
        text = get_page_text(soup)
        for pattern in count_patterns:
            m = re.search(pattern, text, re.I)
            if m:
                cleaned = m.group(1).replace(",", "").replace(".", "").replace("\u00a0", "").strip()
                try:
                    n = int(cleaned)
                    if 0 < n < 1_000_000:
                        return _classify_count(n)
                except ValueError:
                    pass
    for raw in raw_htmls:
        for p in [r'"totalProducts?"\s*:\s*(\d+)', r'"nbHits"\s*:\s*(\d+)',
                  r'"totalResults"\s*:\s*(\d+)']:
            m = re.search(p, raw, re.I)
            if m:
                try:
                    n = int(m.group(1))
                    if n > 5:
                        return _classify_count(n)
                except ValueError:
                    pass
    for sm_path in ["/sitemap.xml", "/sitemap_index.xml"]:
        resp, _ = fetch(urljoin(base_url, sm_path), session, timeout=PROBE_TIMEOUT)
        if resp:
            try:
                root = ET.fromstring(resp.text)
                ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
                product_re = re.compile(r'/products?/|/p/|/item/|/pdp/', re.I)
                count = sum(1 for u in root.findall(f".//{ns}url/{ns}loc")
                            if u.text and product_re.search(u.text))
                if count > 0:
                    return _classify_count(count)
            except ET.ParseError:
                pass
    return "UNKNOWN", "UNKNOWN"


def _classify_count(n):
    if n >= 1000:
        return "LARGE", str(n)
    if n >= 100:
        return "MEDIUM", str(n)
    return "SMALL", str(n)


# ─── Scoring ─────────────────────────────────────────────────────────────────

def parse_revenue_billions(estimated_revenue, revenue_range):
    s = (estimated_revenue or "").lower().replace(",", "").replace(" ", "")
    for pattern in [r'([\d.]+)\s*b', r'([\d.]+)\s*milliard']:
        m = re.search(pattern, s)
        if m:
            return float(m.group(1))
    m = re.search(r'([\d.]+)\s*m', s)
    if m:
        return float(m.group(1)) / 1000
    return 1.5 if revenue_range == "above_1b" else 0.7


def revenue_points(rev_b):
    if rev_b >= 50:
        return 40
    if rev_b >= 10:
        return 35
    if rev_b >= 5:
        return 30
    if rev_b >= 2:
        return 25
    if rev_b >= 1:
        return 20
    return 10


def vertical_points(verdict):
    return {"KEEP": 30, "DEPRIORITIZE": 15}.get(verdict, 0)


def catalog_points(size):
    return {"LARGE": 30, "MEDIUM": 15, "SMALL": 5}.get(size, 0)


def tier_label(score):
    if score >= 80:
        return "Tier 1"
    if score >= 60:
        return "Tier 2"
    if score >= 40:
        return "Tier 3"
    return "Tier 4"


def continent_for(country):
    return COUNTRY_TO_CONTINENT.get(country, "")


# ─── Qualification ───────────────────────────────────────────────────────────

def qualify_company(row, session):
    company = row["company"]
    website = row.get("website", "")

    vertical = row.get("known_vertical", "").strip()
    verdict = row.get("known_verdict", "").strip()
    ecom = row.get("known_ecommerce", "").strip()
    catalog = row.get("known_catalog_size", "").strip()
    notes = []

    if SCRAPING_ENABLED and (not vertical or not ecom or not catalog):
        if not website.startswith("http"):
            website = "https://" + website
        base_url = website.rstrip("/")
        home_resp, home_soup = fetch(base_url, session)
        home_raw = home_resp.text.lower() if home_resp else ""
        home_text = get_page_text(home_soup)

        if PLAYWRIGHT_ENABLED and (not home_soup or len(home_text) < MIN_USEFUL_TEXT):
            pw_raw, pw_soup = fetch_with_playwright(base_url)
            if pw_soup:
                home_soup, home_raw = pw_soup, (pw_raw or "").lower()
                home_text = get_page_text(home_soup)
                notes.append("Playwright used")

        if home_soup:
            if not vertical or not verdict:
                v, vd = classify_vertical_scraping(home_text)
                vertical = vertical or v
                verdict = verdict or vd
            if not ecom:
                e_status, _ = detect_ecommerce_from_html(home_raw, home_soup)
                if e_status == "DEPRIORITIZE" and PLAYWRIGHT_ENABLED:
                    e_status, _ = detect_ecom_from_rendered_dom(base_url)
                ecom = e_status
            if not catalog or catalog == "UNKNOWN":
                cat_size, _ = estimate_catalog_scraping(
                    base_url, session, [home_soup], [home_raw])
                if cat_size != "UNKNOWN":
                    catalog = cat_size
        else:
            notes.append("Site inaccessible")

    vertical = vertical or "UNCLEAR"
    verdict = verdict or "UNCLEAR"
    ecom = ecom or "DEPRIORITIZE"
    catalog = catalog or "UNKNOWN"

    if ecom != "PASS" or verdict == "DISQUALIFY":
        return _build_result(row, vertical, verdict, ecom, catalog,
                             "DISQUALIFIED", 0, notes)

    rev_b = parse_revenue_billions(
        row.get("estimated_revenue", ""), row.get("revenue_range", ""))
    rp = revenue_points(rev_b)
    vp = vertical_points(verdict)
    cp = catalog_points(catalog)
    total = rp + vp + cp

    return _build_result(row, vertical, verdict, ecom, catalog,
                         tier_label(total), total, notes, rp, vp, cp)


def _build_result(row, vertical, verdict, ecom, catalog,
                  fit_score, points, notes, rp=0, vp=0, cp=0):
    return {
        "vertical": vertical,
        "vertical_verdict": verdict,
        "has_ecommerce": ecom,
        "catalog_size": catalog,
        "fit_score": fit_score,
        "fit_points": str(points),
        "revenue_pts": str(rp),
        "vertical_pts": str(vp),
        "catalog_pts": str(cp),
        "tier": fit_score,
        "continent": continent_for(row.get("country", "")),
        "notes": "; ".join(notes),
        "qualified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_fields = reader.fieldnames
        rows = list(reader)

    total = len(rows)
    print(f"Qualifying {total} companies...\n")

    output_fields = input_fields + [
        "vertical", "vertical_verdict", "has_ecommerce", "catalog_size",
        "fit_score", "fit_points", "revenue_pts", "vertical_pts", "catalog_pts",
        "tier", "continent", "notes", "qualified_at",
    ]

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=output_fields).writeheader()

    session = create_session()
    scores = {}

    for i, row in enumerate(rows, 1):
        print(f"▶ [{i}/{total}] {row['company']}", flush=True)
        result = qualify_company(row, session)
        out_row = dict(row)
        out_row.update(result)
        with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=output_fields).writerow(out_row)
        tier = result["tier"]
        print(f"   → {tier} ({result['fit_points']}pts) | {result['vertical']} | "
              f"ecom={result['has_ecommerce']} | catalog={result['catalog_size']}")
        scores[tier] = scores.get(tier, 0) + 1

    print(f"\n{'='*60}\nDone! Results → {OUTPUT_PATH}\n")
    for k in ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "DISQUALIFIED"]:
        print(f"  {k:<15} {scores.get(k, 0):>5}")
    print(f"  {'TOTAL':<15} {total:>5}")


if __name__ == "__main__":
    main()
