"""
Retry ERROR companies using Playwright with aggressive settings:
- ignore SSL errors
- real Chrome UA
- 10s wait after load
- try www and non-www variants
"""
import csv
import re
import json
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

CSV_PATH = "/Users/celinecheminal/presti-qualifier/output/results.csv"
PAGE_TIMEOUT = 30000

KEEP_KEYWORDS = [
    "furniture", "home decor", "home improvement", "hardware", "lighting",
    "electronics", "consumer electronics", "appliance", "audio", "video",
    "sporting goods", "sports", "fitness", "outdoor", "camping", "hiking",
    "golf", "golf equipment", "golf clubs", "golf balls",
    "tennis", "tennis racket", "cycling", "bicycle", "bike",
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
    "ajouter au panier", "in den warenkorb",
]


def parse_count(s):
    cleaned = s.replace(",", "").replace(".", "").replace("\u00a0", "").replace(" ", "").strip()
    try:
        n = int(cleaned)
        return n if 0 < n < 1_000_000 else None
    except ValueError:
        return None


def classify_count(n):
    if n >= 1000: return "LARGE", str(n)
    elif n >= 100: return "MEDIUM", str(n)
    else: return "SMALL", str(n)


def search_text_for_count(text):
    for pat in COUNT_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            n = parse_count(m.group(1))
            if n:
                return classify_count(n)
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
        best, best_c = "UNCLEAR", 0
        for vert, kws in vertical_scores.items():
            c = sum(1 for kw in kws if kw in text_lower)
            if c > best_c:
                best_c = c
                best = vert
        return best, "KEEP"

    if disqualify_score > keep_score and disqualify_score >= 2:
        if any(kw in text_lower for kw in ["fashion", "apparel", "clothing", "dress", "shoes", "sneakers", "t-shirt", "jeans", "handbag", "jewelry", "cosmetics", "beauty"]):
            return "fashion/apparel", "DISQUALIFY"
        return "grocery/food retail", "DISQUALIFY"

    if keep_score == 1:
        return "UNCLEAR", "KEEP"
    return "UNCLEAR", "UNCLEAR"


def compute_fit_score(verdict, catalog_size, ecom):
    if verdict == "DISQUALIFY":
        return "DISQUALIFIED"
    c = (verdict == "KEEP") + (catalog_size == "LARGE") + (ecom == "PASS")
    return ["C", "C", "B", "A"][c]


def url_variants(website):
    """Generate www and non-www variants for both https and http."""
    base = website.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    parsed = urlparse(base)
    host = parsed.netloc

    variants = []
    if host.startswith("www."):
        no_www = host[4:]
        variants.append(f"https://{host}{parsed.path}")
        variants.append(f"https://{no_www}{parsed.path}")
        variants.append(f"http://{host}{parsed.path}")
        variants.append(f"http://{no_www}{parsed.path}")
    else:
        variants.append(f"https://{host}{parsed.path}")
        variants.append(f"https://www.{host}{parsed.path}")
        variants.append(f"http://{host}{parsed.path}")
        variants.append(f"http://www.{host}{parsed.path}")
    return variants


def safe_goto(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(10000)  # 10s for JS rendering
        return True
    except Exception:
        return False


def get_text(page):
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''").lower()
    except Exception:
        return ""


def find_and_goto_category(page, base_url):
    try:
        cat_re = re.compile(r"categor|product|shop|collect|catalog|all-product|browse|department", re.I)
        links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href, text: a.innerText.trim().toLowerCase()
            })).filter(l => l.href && !l.href.startsWith('javascript'))
        }""")
        domain = urlparse(base_url).netloc
        for link in links:
            href, text = link.get("href", ""), link.get("text", "")
            if urlparse(href).netloc != domain:
                continue
            if cat_re.search(href) or cat_re.search(text):
                if safe_goto(page, href):
                    return True
    except Exception:
        pass
    return False


def find_and_goto_pdp(page, base_url):
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
    try:
        text = get_text(page)
        if any(sig in text for sig in ECOM_SIGNALS):
            return True
        return page.evaluate("""() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            const signals = ['add-to-cart','addtocart','add_to_cart','cart-icon',
                           'shopping-cart','cart-count','minicart','mini-cart',
                           'btn-cart','cart-button'];
            return signals.some(s => html.includes(s));
        }""")
    except Exception:
        return False


def check_product_images_pw(page):
    try:
        return page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            const pattern = /product|media|catalog|cdn.*product|upload/i;
            return imgs.some(img => {
                const src = (img.src||'') + ' ' + (img.dataset.src||'') + ' ' + (img.srcset||'');
                return pattern.test(src);
            });
        }""")
    except Exception:
        return False


def count_product_links(page, base_url):
    try:
        domain = urlparse(base_url).netloc
        links = page.evaluate("""() => {
            return Array.from(new Set(
                Array.from(document.querySelectorAll('a[href]')).map(a => a.href)
                    .filter(h => h && !h.startsWith('javascript'))
            ));
        }""")
        product_re = re.compile(r'/products?/[^/]+|/p/[^/]+|/item/[^/]+|/pdp/', re.I)
        count = sum(1 for h in links if urlparse(h).netloc == domain and product_re.search(h))
        if count > 0:
            return classify_count(count)
    except Exception:
        pass
    return None


def evaluate(page, company, website):
    """Try all URL variants, then scrape. Returns updated fields dict or None."""
    variants = url_variants(website)
    base_url = None

    for url in variants:
        print(f"     trying {url}...", flush=True)
        if safe_goto(page, url):
            base_url = url.rstrip("/")
            print(f"     ✓ loaded {page.url}", flush=True)
            break
    else:
        return None

    notes_parts = []
    home_text = get_text(page)
    all_text = home_text
    ecom_found = check_ecommerce_pw(page)
    has_images = False
    catalog_result = None

    home_url = page.url

    # Category page
    if find_and_goto_category(page, base_url):
        cat_text = get_text(page)
        all_text += " " + cat_text
        catalog_result = search_text_for_count(cat_text)
        if not ecom_found:
            ecom_found = check_ecommerce_pw(page)

        cat_url = page.url
        if find_and_goto_pdp(page, base_url):
            pdp_text = get_text(page)
            all_text += " " + pdp_text
            if not ecom_found:
                ecom_found = check_ecommerce_pw(page)
            has_images = check_product_images_pw(page)

        if not catalog_result:
            safe_goto(page, cat_url)
            catalog_result = count_product_links(page, base_url)
    else:
        notes_parts.append("No category page found")
        if find_and_goto_pdp(page, base_url):
            pdp_text = get_text(page)
            all_text += " " + pdp_text
            if not ecom_found:
                ecom_found = check_ecommerce_pw(page)
            has_images = check_product_images_pw(page)

    # Search fallback for catalog size
    if not catalog_result:
        for path in ["/search?q=*", "/search?q=a", "/catalogsearch/result/?q=a", "/?s=a"]:
            if safe_goto(page, urljoin(base_url, path)):
                catalog_result = search_text_for_count(get_text(page))
                if catalog_result:
                    break

    # Product link count fallback
    if not catalog_result:
        safe_goto(page, home_url)
        catalog_result = count_product_links(page, base_url)

    catalog_size = catalog_result[0] if catalog_result else "UNKNOWN"
    catalog_raw = catalog_result[1] if catalog_result else "UNKNOWN"
    vertical, verdict = classify_vertical(all_text)
    has_ecommerce = "PASS" if ecom_found else "DEPRIORITIZE"
    has_product_images = "PASS" if has_images else "DEPRIORITIZE"

    product_signals = ["add to cart", "add to bag", "buy now", "in stock", "out of stock",
                       "shipping", "delivery", "free delivery", "price", "€", "$", "£",
                       "sku", "quantity", "weight", "dimensions"]
    sells_physical = "PASS" if sum(1 for s in product_signals if s in all_text) >= 1 else "FAIL"

    fit_score = compute_fit_score(verdict, catalog_size, has_ecommerce)

    if catalog_size == "UNKNOWN":
        notes_parts.append("Could not determine catalog size")

    return {
        "vertical": vertical,
        "vertical_verdict": verdict,
        "sells_physical_products": sells_physical,
        "has_ecommerce": has_ecommerce,
        "catalog_size": catalog_size,
        "catalog_size_raw": catalog_raw,
        "has_product_images": has_product_images,
        "fit_score": fit_score,
        "notes": "; ".join(notes_parts) + " [Playwright retry]" if notes_parts else "Playwright retry",
        "qualified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    targets = [(i, r) for i, r in enumerate(rows) if r["fit_score"] == "ERROR"]
    total = len(targets)
    print(f"Retrying {total} ERROR companies with Playwright...\n")

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
            print(f"▶ [{idx}/{total}] {company} ({website})", flush=True)

            page = context.new_page()
            try:
                result = evaluate(page, company, website)
                if result:
                    for k, v in result.items():
                        rows[row_idx][k] = v
                    print(f"   → {result['fit_score']} | {result['vertical']} | cat={result['catalog_size']} | ecom={result['has_ecommerce']} *** RESOLVED")
                else:
                    print(f"   → still ERROR (all URL variants failed)")
            except Exception as e:
                print(f"   → still ERROR: {e}")
            finally:
                page.close()

        browser.close()

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{'='*50}")
    scores = {}
    for r in rows:
        s = r["fit_score"]
        scores[s] = scores.get(s, 0) + 1
    for k, v in sorted(scores.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
