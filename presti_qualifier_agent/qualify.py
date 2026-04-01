"""
Presti Account Qualifier
Scrapes company websites and evaluates fit for Presti (AI product visual generation).
"""
import csv
import re
import os
import time
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import urllib3

# Suppress SSL warnings since we use verify=False as fallback
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INPUT_PATH = "/Users/celinecheminal/presti-qualifier/input.csv"
OUTPUT_PATH = "/Users/celinecheminal/presti-qualifier/output/results.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
TIMEOUT = (5, 25)  # (connect_timeout, read_timeout)

# --- Vertical classification ---
KEEP_KEYWORDS = [
    "furniture", "home decor", "home improvement", "hardware", "lighting",
    "electronics", "consumer electronics", "appliance", "audio", "video",
    "sporting goods", "sports", "fitness", "outdoor", "camping", "hiking",
    "golf", "golf equipment", "golf clubs", "golf balls",
    "tennis", "tennis racket",
    "cycling", "bicycle", "bike",
    "hunting", "fishing", "angling",
    "pet", "pet care", "pet food", "pet supplies",
    "kitchen", "kitchenware", "cookware", "tableware",
    "toys", "games", "garden", "lawn", "patio", "grill", "bbq",
    "tools", "power tools", "plumbing", "electrical", "building materials",
    "mattress", "bedding", "bath", "rugs", "curtains", "blinds",
    "diy", "renovation", "flooring",
]
DISQUALIFY_KEYWORDS = [
    "fashion", "apparel", "clothing", "dress", "shoes", "sneakers",
    "grocery", "food retail", "supermarket", "fresh food",
    "t-shirt", "jeans", "handbag", "jewelry", "cosmetics", "beauty",
]


def create_session():
    """Create a requests session with retry logic."""
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,  # 0.5s, 1s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        connect=1,  # Only 1 connect retry
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def fetch(url, session, verify_ssl=True, timeout=None):
    """Fetch a URL with retries and SSL fallback. Returns (response, soup) or (None, None)."""
    t = timeout or TIMEOUT
    try:
        r = session.get(url, timeout=t, allow_redirects=True, verify=verify_ssl)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        return r, soup
    except requests.exceptions.SSLError:
        if verify_ssl:
            return fetch(url, session, verify_ssl=False, timeout=t)
        return None, None
    except Exception:
        if verify_ssl:
            try:
                r = session.get(url, timeout=t, allow_redirects=True, verify=False)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                return r, soup
            except Exception:
                return None, None
        return None, None


# Shorter timeout for fallback probes (search URLs, sitemaps)
PROBE_TIMEOUT = (5, 10)


def get_page_text(soup):
    """Extract visible text from soup, preserving the original soup object."""
    if not soup:
        return ""
    # Work on a copy so we don't destroy the soup for later use
    from copy import copy
    soup_copy = BeautifulSoup(str(soup), "html.parser")
    for tag in soup_copy(["script", "style", "noscript"]):
        tag.decompose()
    return soup_copy.get_text(separator=" ", strip=True).lower()


def find_category_page(soup, base_url, session):
    """Try to find a category/PLP page from navigation links."""
    if not soup:
        return None, None
    cat_patterns = re.compile(
        r"(categor|product|shop|collect|catalog|all-product|browse|department)",
        re.I,
    )
    links = soup.find_all("a", href=True)
    candidates = []
    for link in links:
        href = link.get("href", "")
        text = link.get_text(strip=True).lower()
        if cat_patterns.search(href) or cat_patterns.search(text):
            full_url = urljoin(base_url, href)
            if urlparse(full_url).netloc == urlparse(base_url).netloc:
                candidates.append(full_url)
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > 3:
            break
        resp, s = fetch(url, session)
        if s:
            return resp, s
    return None, None


def find_product_page(soup, base_url, session):
    """Try to find a product detail page (PDP)."""
    if not soup:
        return None, None
    pdp_patterns = re.compile(
        r"(/product/|/p/|/item/|/dp/|/pdp/|-p-|/products/[^/]+$)", re.I
    )
    links = soup.find_all("a", href=True)
    candidates = []
    for link in links:
        href = link.get("href", "")
        if pdp_patterns.search(href):
            full_url = urljoin(base_url, href)
            if urlparse(full_url).netloc == urlparse(base_url).netloc:
                candidates.append(full_url)
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > 3:
            break
        resp, s = fetch(url, session)
        if s:
            return resp, s
    return None, None


def classify_vertical(text):
    """Return (vertical_name, verdict)."""
    text_lower = text.lower()

    keep_score = sum(1 for kw in KEEP_KEYWORDS if kw in text_lower)
    disqualify_score = sum(1 for kw in DISQUALIFY_KEYWORDS if kw in text_lower)

    # KEEP takes priority when both match — avoids misclassifying sporting goods
    # sites that happen to mention "shoes" or "clothing" as accessories.
    if keep_score >= 2:
        vertical_scores = {
            "home/furniture": ["furniture", "home decor", "mattress", "bedding", "bath", "rugs", "curtains", "blinds", "lighting"],
            "home improvement": ["home improvement", "hardware", "tools", "power tools", "plumbing", "electrical", "building materials", "diy", "renovation", "flooring"],
            "consumer electronics": ["electronics", "consumer electronics", "appliance", "audio", "video"],
            "sporting goods": ["sporting goods", "sports", "fitness", "golf", "golf equipment", "golf clubs", "golf balls", "tennis", "tennis racket", "cycling", "bicycle", "bike", "hunting", "fishing", "angling"],
            "pet care": ["pet", "pet care", "pet food", "pet supplies"],
            "kitchenware": ["kitchen", "kitchenware", "cookware", "tableware"],
            "toys": ["toys", "games"],
            "outdoor/garden": ["outdoor", "camping", "hiking", "garden", "lawn", "patio", "grill", "bbq"],
        }
        best_vertical = "UNCLEAR"
        best_count = 0
        for vert, keywords in vertical_scores.items():
            c = sum(1 for kw in keywords if kw in text_lower)
            if c > best_count:
                best_count = c
                best_vertical = vert
        return best_vertical, "KEEP"

    if disqualify_score > keep_score and disqualify_score >= 2:
        if any(kw in text_lower for kw in ["fashion", "apparel", "clothing", "dress", "shoes", "sneakers", "t-shirt", "jeans", "handbag", "jewelry", "cosmetics", "beauty"]):
            return "fashion/apparel", "DISQUALIFY"
        return "grocery/food retail", "DISQUALIFY"

    if keep_score == 1:
        return "UNCLEAR", "KEEP"

    return "UNCLEAR", "UNCLEAR"


def check_physical_products(text, soup):
    """Check if the site sells physical/tangible products."""
    if not soup:
        return "FAIL"
    product_signals = ["add to cart", "add to bag", "buy now", "in stock", "out of stock",
                       "shipping", "delivery", "free delivery", "price", "€", "$", "£",
                       "sku", "quantity", "weight", "dimensions"]
    service_signals = ["saas", "subscription software", "api access", "cloud platform",
                       "consulting service", "professional services"]
    product_count = sum(1 for s in product_signals if s in text.lower())
    service_count = sum(1 for s in service_signals if s in text.lower())

    if product_count >= 2:
        return "PASS"
    if service_count >= 2 and product_count == 0:
        return "FAIL"
    if soup.find(attrs={"itemtype": re.compile(r"schema.org/Product", re.I)}):
        return "PASS"
    if product_count >= 1:
        return "PASS"
    return "FAIL"


def check_ecommerce(text, soup, all_soups):
    """Check for active ecommerce (cart, buy buttons)."""
    ecom_signals = ["add to cart", "add to bag", "add to basket", "buy now",
                    "checkout", "shopping cart", "view cart", "my cart", "cart("]
    for s in all_soups:
        if not s:
            continue
        page_text = get_page_text(s)
        if any(sig in page_text for sig in ecom_signals):
            return "PASS"
        cart_elements = s.find_all(attrs={"class": re.compile(r"cart|basket|bag", re.I)})
        if cart_elements:
            return "PASS"
        buttons = s.find_all(["button", "a"], string=re.compile(r"add to (cart|bag|basket)|buy now", re.I))
        if buttons:
            return "PASS"
        forms = s.find_all("form", action=re.compile(r"cart|basket|checkout", re.I))
        if forms:
            return "PASS"
    return "DEPRIORITIZE"


def _parse_count(num_str):
    """Parse a number string like '1,234' or '1.234' into int, or None."""
    cleaned = num_str.replace(",", "").replace(".", "").replace("\u00a0", "").strip()
    try:
        num = int(cleaned)
        if 0 < num < 1_000_000:
            return num
    except ValueError:
        pass
    return None


def _classify_count(num):
    """Classify a product count into LARGE/MEDIUM/SMALL."""
    if num >= 1000:
        return "LARGE", str(num)
    elif num >= 100:
        return "MEDIUM", str(num)
    else:
        return "SMALL", str(num)


COUNT_PATTERNS = [
    r'(\d[\d,\.\s]*)\s*(?:products?|items?|results?|articles?|produits?|références?|produkte?|artikelen?)',
    r'(?:of|sur|von|van|de)\s+(\d[\d,\.\s]*)\s*(?:products?|items?|results?)?',
    r'(?:showing|affichage|anzeige).*?(?:of|sur|von)\s+(\d[\d,\.\s]*)',
    r'(\d[\d,\.\s]*)\s*(?:résultats?|Ergebnisse|resultaten|treffer)',
    r'(?:found|trouvé)\s+(\d[\d,\.\s]*)',
]


def _search_text_for_count(text):
    """Search text for product count patterns. Returns (size, raw) or None."""
    for pattern in COUNT_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            num = _parse_count(match.group(1))
            if num:
                return _classify_count(num)
    return None


def _count_sitemap_products(base_url, session):
    """Try to count product URLs in sitemap.xml."""
    sitemap_urls = [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
    ]
    product_url_count = 0
    product_patterns = re.compile(r'/product[s]?/|/p/|/item/|/pdp/|-p-|/dp/', re.I)

    for sitemap_url in sitemap_urls:
        resp, _ = fetch(sitemap_url, session, timeout=PROBE_TIMEOUT)
        if not resp:
            continue
        try:
            root = ET.fromstring(resp.text)
            # Handle namespace
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"

            # Check if this is a sitemap index
            sub_sitemaps = root.findall(f".//{ns}sitemap/{ns}loc")
            if sub_sitemaps:
                # Look for product-specific sitemaps
                for sm in sub_sitemaps[:3]:  # Limit to 3 sub-sitemaps
                    sm_url = sm.text.strip() if sm.text else ""
                    if re.search(r'product|catalog', sm_url, re.I):
                        sm_resp, _ = fetch(sm_url, session, timeout=PROBE_TIMEOUT)
                        if sm_resp:
                            try:
                                sm_root = ET.fromstring(sm_resp.text)
                                urls = sm_root.findall(f".//{ns}url/{ns}loc")
                                product_url_count += len(urls)
                            except ET.ParseError:
                                pass
                if product_url_count > 0:
                    return _classify_count(product_url_count)

            # Regular sitemap — count product-like URLs
            urls = root.findall(f".//{ns}url/{ns}loc")
            for u in urls:
                if u.text and product_patterns.search(u.text):
                    product_url_count += 1
            if product_url_count > 0:
                return _classify_count(product_url_count)
        except ET.ParseError:
            continue

    return None


def _count_jsonld_products(soups):
    """Look for JSON-LD structured data with product counts."""
    for soup in soups:
        if not soup:
            continue
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                # Handle both single objects and arrays
                items = data if isinstance(data, list) else [data]
                for item in items:
                    # ItemList with numberOfItems
                    if item.get("@type") == "ItemList":
                        n = item.get("numberOfItems")
                        if n and isinstance(n, (int, float)) and n > 0:
                            return _classify_count(int(n))
                        elems = item.get("itemListElement", [])
                        if len(elems) > 0:
                            return _classify_count(len(elems))
                    # CollectionPage or SearchResultsPage
                    if item.get("@type") in ("CollectionPage", "SearchResultsPage"):
                        main = item.get("mainEntity", {})
                        if isinstance(main, dict):
                            n = main.get("numberOfItems")
                            if n and isinstance(n, (int, float)) and n > 0:
                                return _classify_count(int(n))
                    # OfferCatalog
                    if item.get("@type") == "OfferCatalog":
                        n = item.get("numberOfItems")
                        if n and isinstance(n, (int, float)) and n > 0:
                            return _classify_count(int(n))
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
    return None


def _count_product_links(soups, base_url):
    """Count unique links containing /product/ or /p/ as last-resort estimate."""
    product_link_re = re.compile(r'/products?/[^/]+|/p/[^/]+|/item/[^/]+|/pdp/', re.I)
    domain = urlparse(base_url).netloc
    unique_urls = set()
    for soup in soups:
        if not soup:
            continue
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            full = urljoin(base_url, href)
            if urlparse(full).netloc == domain and product_link_re.search(full):
                unique_urls.add(full)
    if len(unique_urls) > 0:
        return _classify_count(len(unique_urls))
    return None


def estimate_catalog_size(cat_soup, base_url, session, all_soups):
    """Try to estimate catalog size using multiple strategies."""

    # Strategy 1: Parse count from category page text
    if cat_soup:
        text = get_page_text(cat_soup)
        result = _search_text_for_count(text)
        if result:
            return result

    # Strategy 2: Try JSON-LD structured data in all fetched pages
    result = _count_jsonld_products(all_soups)
    if result:
        return result

    # Strategy 3: Try multiple search URL patterns (short timeout, stop after first hit)
    search_paths = [
        "/search?q=*",
        "/search?q=a",
        "/catalogsearch/result/?q=a",
        "/recherche?q=a",
        "/?s=a",
        "/suche?q=a",
        "/zoeken?q=a",
    ]
    for path in search_paths:
        search_url = urljoin(base_url, path)
        _, search_soup = fetch(search_url, session, timeout=PROBE_TIMEOUT)
        if search_soup:
            search_text = get_page_text(search_soup)
            result = _search_text_for_count(search_text)
            if result:
                return result
            result = _count_jsonld_products([search_soup])
            if result:
                return result

    # Strategy 4: Parse sitemap.xml for product URLs
    result = _count_sitemap_products(base_url, session)
    if result:
        return result

    # Strategy 5: Count product-like links across all scraped pages
    result = _count_product_links(all_soups, base_url)
    if result:
        return result

    return "UNKNOWN", "UNKNOWN"


def check_product_images(pdp_soup):
    """Check for product images on PDP."""
    if not pdp_soup:
        return "DEPRIORITIZE"
    imgs = pdp_soup.find_all("img", src=True)
    image_patterns = re.compile(
        r"(product|media|catalog|cdn.*product|image.*product|/p/|/i/|upload)", re.I
    )
    for img in imgs:
        src = img.get("src", "") + " " + img.get("data-src", "")
        if image_patterns.search(src):
            return "PASS"
    for img in imgs:
        for attr in ["srcset", "data-srcset", "data-zoom-image", "data-large"]:
            val = img.get(attr, "")
            if image_patterns.search(val):
                return "PASS"
    pictures = pdp_soup.find_all("picture")
    for pic in pictures:
        sources = pic.find_all("source")
        for source in sources:
            srcset = source.get("srcset", "")
            if image_patterns.search(srcset):
                return "PASS"
    return "DEPRIORITIZE"


def compute_fit_score(vertical_verdict, catalog_size, has_ecommerce):
    """Compute A/B/C/DISQUALIFIED fit score."""
    if vertical_verdict == "DISQUALIFY":
        return "DISQUALIFIED"

    criteria_met = 0
    if vertical_verdict == "KEEP":
        criteria_met += 1
    if catalog_size == "LARGE":
        criteria_met += 1
    if has_ecommerce == "PASS":
        criteria_met += 1

    if criteria_met == 3:
        return "A"
    elif criteria_met == 2:
        return "B"
    else:
        return "C"


def qualify_company(company, website, session):
    """Run full qualification for one company. Returns dict of results."""
    notes = []
    result = {
        "vertical": "",
        "vertical_verdict": "",
        "sells_physical_products": "",
        "has_ecommerce": "",
        "catalog_size": "",
        "catalog_size_raw": "",
        "has_product_images": "",
        "fit_score": "ERROR",
        "notes": "",
        "qualified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Normalize URL
    if not website.startswith("http"):
        website = "https://" + website
    base_url = website.rstrip("/")

    # Fetch homepage
    home_resp, home_soup = fetch(base_url, session)
    if not home_soup:
        # Try http fallback
        alt_url = base_url.replace("https://", "http://")
        home_resp, home_soup = fetch(alt_url, session)
        if not home_soup:
            result["notes"] = "Site inaccessible"
            return result

    home_text = get_page_text(home_soup)

    # Find category page
    cat_resp, cat_soup = find_category_page(home_soup, base_url, session)
    cat_text = get_page_text(cat_soup) if cat_soup else ""

    # Find product page (from category page first, then homepage)
    pdp_resp, pdp_soup = None, None
    if cat_soup:
        pdp_resp, pdp_soup = find_product_page(cat_soup, base_url, session)
    if not pdp_soup:
        pdp_resp, pdp_soup = find_product_page(home_soup, base_url, session)
    pdp_text = get_page_text(pdp_soup) if pdp_soup else ""

    combined_text = " ".join([home_text, cat_text, pdp_text])
    all_soups = [home_soup, cat_soup, pdp_soup]

    # 1. Vertical
    vertical, verdict = classify_vertical(combined_text)
    result["vertical"] = vertical
    result["vertical_verdict"] = verdict

    # 2. Physical products
    result["sells_physical_products"] = check_physical_products(combined_text, home_soup)

    # 3. eCommerce
    result["has_ecommerce"] = check_ecommerce(combined_text, home_soup, all_soups)

    # 4. Catalog size (pass all_soups for JSON-LD + link counting)
    catalog_size, catalog_raw = estimate_catalog_size(cat_soup, base_url, session, all_soups)
    result["catalog_size"] = catalog_size
    result["catalog_size_raw"] = catalog_raw

    # 5. Product images
    result["has_product_images"] = check_product_images(pdp_soup)

    # Fit score
    result["fit_score"] = compute_fit_score(verdict, catalog_size, result["has_ecommerce"])

    # Notes
    if not cat_soup:
        notes.append("No category page found")
    if not pdp_soup:
        notes.append("No PDP found")
    if catalog_size == "UNKNOWN":
        notes.append("Could not determine catalog size")
    result["notes"] = "; ".join(notes)

    return result


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_fields = reader.fieldnames
        rows = list(reader)

    total = len(rows)
    print(f"Qualifying {total} companies...\n")

    output_fields = input_fields + [
        "vertical", "vertical_verdict", "sells_physical_products",
        "has_ecommerce", "catalog_size", "catalog_size_raw",
        "has_product_images", "fit_score", "notes", "qualified_at",
    ]

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()

    session = create_session()

    for i, row in enumerate(rows, 1):
        company = row["company"]
        website = row["website"]
        print(f"▶ [{i}/{total}] {company}", flush=True)

        result = qualify_company(company, website, session)

        out_row = dict(row)
        out_row.update(result)

        with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=output_fields)
            writer.writerow(out_row)

        score = result["fit_score"]
        print(f"   → {score} | {result['vertical']} | catalog={result['catalog_size']}")

    # Summary
    print(f"\n{'='*50}")
    print(f"Done! Results written to {OUTPUT_PATH}")

    scores = {"A": 0, "B": 0, "C": 0, "DISQUALIFIED": 0, "ERROR": 0}
    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s = row.get("fit_score", "ERROR")
            scores[s] = scores.get(s, 0) + 1
    for k, v in sorted(scores.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
