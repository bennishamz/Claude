"""
Re-evaluate companies with fit_score=C and catalog_size=UNKNOWN using Playwright
to render JS-heavy pages and extract catalog size, ecommerce signals, and vertical info.
Updates results.csv in place.
"""
import csv
import re
import json
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

CSV_PATH = "/Users/celinecheminal/presti-qualifier/output/results.csv"

# Reuse keyword lists from qualify.py
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

COUNT_PATTERNS = [
    r'(\d[\d,\.\s]*)\s*(?:products?|items?|results?|articles?|produits?|références?|produkte?|artikelen?)',
    r'(?:of|sur|von|van|de)\s+(\d[\d,\.\s]*)\s*(?:products?|items?|results?)?',
    r'(?:showing|affichage|anzeige).*?(?:of|sur|von)\s+(\d[\d,\.\s]*)',
    r'(\d[\d,\.\s]*)\s*(?:résultats?|Ergebnisse|resultaten|treffer)',
    r'(?:found|trouvé)\s+(\d[\d,\.\s]*)',
]

ECOM_SIGNALS = [
    "add to cart", "add to bag", "add to basket", "buy now",
    "checkout", "shopping cart", "view cart", "my cart",
    "ajouter au panier", "in den warenkorb", "añadir al carrito",
]

PAGE_TIMEOUT = 20000  # 20s per page load


def parse_count(num_str):
    cleaned = num_str.replace(",", "").replace(".", "").replace("\u00a0", "").replace(" ", "").strip()
    try:
        num = int(cleaned)
        if 0 < num < 1_000_000:
            return num
    except ValueError:
        pass
    return None


def classify_count(num):
    if num >= 1000:
        return "LARGE", str(num)
    elif num >= 100:
        return "MEDIUM", str(num)
    else:
        return "SMALL", str(num)


def search_text_for_count(text):
    for pattern in COUNT_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            num = parse_count(match.group(1))
            if num:
                return classify_count(num)
    return None


def classify_vertical(text):
    text_lower = text.lower()
    keep_score = sum(1 for kw in KEEP_KEYWORDS if kw in text_lower)
    disqualify_score = sum(1 for kw in DISQUALIFY_KEYWORDS if kw in text_lower)

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


def compute_fit_score(vertical_verdict, catalog_size, has_ecommerce):
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


def safe_goto(page, url):
    """Navigate to URL, return True if successful."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Give JS a moment to render dynamic content
        page.wait_for_timeout(2000)
        return True
    except (PwTimeout, Exception):
        return False


def get_text(page):
    """Get all visible text from the page."""
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''").lower()
    except Exception:
        return ""


def get_html(page):
    """Get full page HTML."""
    try:
        return page.content()
    except Exception:
        return ""


def find_and_goto_category(page, base_url):
    """Find a category/PLP link and navigate to it. Returns True if found."""
    try:
        cat_re = re.compile(r"categor|product|shop|collect|catalog|all-product|browse|department", re.I)
        links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.trim().toLowerCase()
            })).filter(l => l.href && !l.href.startsWith('javascript'))
        }""")
        domain = urlparse(base_url).netloc
        for link in links:
            href = link.get("href", "")
            text = link.get("text", "")
            if urlparse(href).netloc != domain:
                continue
            if cat_re.search(href) or cat_re.search(text):
                if safe_goto(page, href):
                    return True
    except Exception:
        pass
    return False


def find_and_goto_pdp(page, base_url):
    """Find a PDP link and navigate to it. Returns True if found."""
    try:
        links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => a.href)
                .filter(h => h && !h.startsWith('javascript'))
        }""")
        pdp_re = re.compile(r'/products?/[^/]+|/p/[^/]+|/item/|/dp/|/pdp/|-p-', re.I)
        domain = urlparse(base_url).netloc
        tried = 0
        for href in links:
            if urlparse(href).netloc != domain:
                continue
            if pdp_re.search(href):
                if safe_goto(page, href):
                    return True
                tried += 1
                if tried >= 3:
                    break
    except Exception:
        pass
    return False


def check_ecommerce_pw(page):
    """Check for ecommerce signals in the rendered page."""
    try:
        text = get_text(page)
        if any(sig in text for sig in ECOM_SIGNALS):
            return True
        # Check for cart-related elements in DOM
        result = page.evaluate("""() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            const signals = ['add-to-cart', 'addtocart', 'add_to_cart', 'cart-icon',
                           'shopping-cart', 'cart-count', 'minicart', 'mini-cart',
                           'btn-cart', 'cart-button'];
            return signals.some(s => html.includes(s));
        }""")
        return result
    except Exception:
        return False


def check_product_images_pw(page):
    """Check for product images on current page."""
    try:
        result = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            const pattern = /product|media|catalog|cdn.*product|upload/i;
            return imgs.some(img => {
                const src = (img.src || '') + ' ' + (img.dataset.src || '') + ' ' + (img.srcset || '');
                return pattern.test(src);
            });
        }""")
        return result
    except Exception:
        return False


def extract_jsonld_counts(page):
    """Extract product counts from JSON-LD structured data."""
    try:
        scripts = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                .map(s => s.textContent);
        }""")
        for script_text in scripts:
            try:
                data = json.loads(script_text)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "ItemList":
                        n = item.get("numberOfItems")
                        if n and isinstance(n, (int, float)) and n > 0:
                            return classify_count(int(n))
                        elems = item.get("itemListElement", [])
                        if len(elems) > 0:
                            return classify_count(len(elems))
                    if item.get("@type") in ("CollectionPage", "SearchResultsPage"):
                        main = item.get("mainEntity", {})
                        if isinstance(main, dict):
                            n = main.get("numberOfItems")
                            if n and isinstance(n, (int, float)) and n > 0:
                                return classify_count(int(n))
                    if item.get("@type") == "OfferCatalog":
                        n = item.get("numberOfItems")
                        if n and isinstance(n, (int, float)) and n > 0:
                            return classify_count(int(n))
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass
    return None


def count_product_links(page, base_url):
    """Count unique product-like links on the page."""
    try:
        domain = urlparse(base_url).netloc
        links = page.evaluate("""() => {
            return Array.from(new Set(
                Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h && !h.startsWith('javascript'))
            ));
        }""")
        product_re = re.compile(r'/products?/[^/]+|/p/[^/]+|/item/[^/]+|/pdp/', re.I)
        count = sum(1 for href in links if urlparse(href).netloc == domain and product_re.search(href))
        if count > 0:
            return classify_count(count)
    except Exception:
        pass
    return None


def evaluate_company(page, company, website):
    """Full Playwright-based evaluation. Returns dict of updated fields."""
    notes = []
    base_url = website.rstrip("/")
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    # --- Navigate to homepage ---
    if not safe_goto(page, base_url):
        return None  # Keep existing data

    home_text = get_text(page)
    all_text = home_text
    ecom_found = check_ecommerce_pw(page)
    has_images = False

    # --- Find and visit category page ---
    cat_text = ""
    # Save homepage URL to go back
    home_url = page.url
    if find_and_goto_category(page, base_url):
        cat_text = get_text(page)
        all_text += " " + cat_text

        # Try to get catalog size from category page
        catalog_result = search_text_for_count(cat_text)
        if not catalog_result:
            catalog_result = extract_jsonld_counts(page)

        if not ecom_found:
            ecom_found = check_ecommerce_pw(page)

        # --- Find PDP from category page ---
        cat_url = page.url
        if find_and_goto_pdp(page, base_url):
            pdp_text = get_text(page)
            all_text += " " + pdp_text
            if not ecom_found:
                ecom_found = check_ecommerce_pw(page)
            has_images = check_product_images_pw(page)

            if not catalog_result:
                catalog_result = extract_jsonld_counts(page)

        # If no catalog size yet, try search pages
        if not catalog_result:
            search_paths = [
                "/search?q=*", "/search?q=a",
                "/catalogsearch/result/?q=a",
                "/recherche?q=a", "/?s=a",
            ]
            for path in search_paths:
                search_url = urljoin(base_url, path)
                if safe_goto(page, search_url):
                    search_text = get_text(page)
                    catalog_result = search_text_for_count(search_text)
                    if not catalog_result:
                        catalog_result = extract_jsonld_counts(page)
                    if catalog_result:
                        break

        # Last resort: count product links on category page
        if not catalog_result:
            safe_goto(page, cat_url)
            catalog_result = count_product_links(page, base_url)
    else:
        notes.append("No category page found")
        # Try search directly
        catalog_result = None
        search_paths = [
            "/search?q=*", "/search?q=a",
            "/catalogsearch/result/?q=a",
            "/recherche?q=a", "/?s=a",
        ]
        for path in search_paths:
            search_url = urljoin(base_url, path)
            if safe_goto(page, search_url):
                search_text = get_text(page)
                catalog_result = search_text_for_count(search_text)
                if not catalog_result:
                    catalog_result = extract_jsonld_counts(page)
                if not ecom_found:
                    ecom_found = check_ecommerce_pw(page)
                if catalog_result:
                    break

        # Try PDP from homepage
        safe_goto(page, home_url)
        if find_and_goto_pdp(page, base_url):
            pdp_text = get_text(page)
            all_text += " " + pdp_text
            if not ecom_found:
                ecom_found = check_ecommerce_pw(page)
            has_images = check_product_images_pw(page)

        # Count product links on homepage
        if not catalog_result:
            safe_goto(page, home_url)
            catalog_result = count_product_links(page, base_url)

    # --- Compute results ---
    catalog_size = catalog_result[0] if catalog_result else "UNKNOWN"
    catalog_raw = catalog_result[1] if catalog_result else "UNKNOWN"

    vertical, vertical_verdict = classify_vertical(all_text)
    has_ecommerce = "PASS" if ecom_found else "DEPRIORITIZE"
    has_product_images_val = "PASS" if has_images else "DEPRIORITIZE"

    # Physical products
    product_signals = ["add to cart", "add to bag", "buy now", "in stock", "out of stock",
                       "shipping", "delivery", "free delivery", "price", "€", "$", "£",
                       "sku", "quantity", "weight", "dimensions"]
    sells_physical = "PASS" if sum(1 for s in product_signals if s in all_text.lower()) >= 1 else "FAIL"

    fit_score = compute_fit_score(vertical_verdict, catalog_size, has_ecommerce)

    if catalog_size == "UNKNOWN":
        notes.append("Could not determine catalog size")

    return {
        "vertical": vertical,
        "vertical_verdict": vertical_verdict,
        "sells_physical_products": sells_physical,
        "has_ecommerce": has_ecommerce,
        "catalog_size": catalog_size,
        "catalog_size_raw": catalog_raw,
        "has_product_images": has_product_images_val,
        "fit_score": fit_score,
        "notes": "; ".join(notes) + " [Playwright re-eval]" if notes else "Playwright re-eval",
        "qualified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    # Read all rows
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    # Find targets
    targets = [(i, r) for i, r in enumerate(rows) if r["fit_score"] == "C" and r["catalog_size"] == "UNKNOWN"]
    total = len(targets)
    print(f"Re-evaluating {total} companies with Playwright...\n")

    updated = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            ignore_https_errors=True,
        )

        for idx, (row_idx, row) in enumerate(targets, 1):
            company = row["company"]
            website = row["website"]
            print(f"▶ [{idx}/{total}] {company}", flush=True)

            page = context.new_page()
            try:
                result = evaluate_company(page, company, website)
                if result:
                    old_score = row["fit_score"]
                    old_cat = row["catalog_size"]
                    # Update the row
                    for key, val in result.items():
                        rows[row_idx][key] = val
                    new_score = result["fit_score"]
                    new_cat = result["catalog_size"]
                    changed = ""
                    if old_score != new_score or old_cat != new_cat:
                        changed = f" *** CHANGED from {old_score}/cat={old_cat}"
                        updated += 1
                    print(f"   → {new_score} | {result['vertical']} | cat={new_cat} | ecom={result['has_ecommerce']}{changed}")
                else:
                    print(f"   → SKIP (site inaccessible)")
            except Exception as e:
                print(f"   → ERROR: {e}")
            finally:
                page.close()

        browser.close()

    # Write updated CSV
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{'='*50}")
    print(f"Done! {updated} companies updated in {CSV_PATH}")

    # Summary
    scores = {}
    for r in rows:
        s = r["fit_score"]
        scores[s] = scores.get(s, 0) + 1
    for k, v in sorted(scores.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
