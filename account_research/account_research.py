#!/usr/bin/env python3
"""
Account Research Script
-----------------------
Given a company name and website URL, discovers customers, partners, and accounts
mentioned across the company's web presence, review sites, LinkedIn, and Google.

Usage:
    python account_research.py "Salsify" "salsify.com"
    python account_research.py "Salsify" "salsify.com" --output results.csv
"""

import argparse
import csv
import logging
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    StaleElementReferenceException,
)
from webdriver_manager.chrome import ChromeDriverManager

try:
    from googlesearch import search as google_search
except ImportError:
    google_search = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("account_research")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUBPAGE_KEYWORDS = [
    "customer", "clients", "case-stud", "casestud", "partner",
    "about", "press", "newsroom", "news", "success-stor", "testimonial",
    "integrat", "ecosystem", "marketplace",
    # Industry / solution / vertical pages
    "solution", "industr", "segment", "use-case", "vertical",
    "brand-manufactur", "distributor", "retailer", "b2b", "b2c",
    "fashion", "food", "beverage", "beauty", "electron", "automotive",
    "pim-for-", "pim-by-", "cpg",
]

# ---------------------------------------------------------------------------
# Known technology platforms / SaaS tools / SI partners (not end-user customers)
# ---------------------------------------------------------------------------
TECH_PARTNER_NAMES = {
    # E-commerce platforms
    "shopify", "bigcommerce", "big commerce", "magento", "adobe commerce",
    "woocommerce", "prestashop", "commercetools", "salesforce commerce",
    "vtex", "sap commerce", "hybris",
    # CRM / ERP / marketing
    "salesforce", "sap", "oracle", "microsoft", "hubspot", "marketo",
    "netsuite", "dynamics", "zoho",
    # DAM / content / PIM adjacent
    "bynder", "adobe", "adobe cc", "adobe creative", "figma", "canva",
    "contentful", "contentstack", "storyblok", "sitecore",
    # Cloud / infrastructure
    "aws", "amazon web services", "google cloud", "azure", "cloudflare",
    # Data / analytics / AI
    "google analytics", "google shopping", "segment", "amplitude",
    "snowflake", "databricks", "openai",
    # Marketplaces / channels
    "amazon", "ebay", "walmart marketplace", "alibaba", "zalando",
    "google shopping",
    # Integration / middleware
    "mulesoft", "boomi", "zapier", "workato", "celigo", "talend",
    "informatica", "jitterbit",
    # Search / personalization
    "algolia", "elasticsearch", "bloomreach", "coveo", "lucidworks",
    # Translation / localization
    "smartling", "transifex", "lokalise", "phrase",
    # CI / dev tools
    "github", "gitlab", "jira", "confluence", "slack",
}

# Sources that indicate a technology/SI partner, not an end-user customer
PARTNER_SOURCE_PATTERNS = [
    "solution partner", "technology partner", "become an akeneo partner",
    "become a partner", "partner program", "si partner",
    "integration partner", "app partner",
]

LOGO_SECTION_KEYWORDS = [
    "customer", "client", "partner", "trust", "loved by", "used by",
    "powering", "companies", "brand", "logo", "who use", "join",
    "work with", "built for", "chosen by",
    "logo-carousel", "logo-grid", "logo-wall", "logo-bar", "logo-strip",
    "logowall", "logogrid", "logos-block", "logo-card",
]

# Company-name noise words to filter out of logo alt texts
NOISE_WORDS = {
    "logo", "icon", "image", "img", "picture", "photo", "banner",
    "header", "footer", "arrow", "chevron", "close", "menu", "search",
    "play", "pause", "next", "prev", "previous", "slide", "dot",
    "background", "bg", "decoration", "separator", "divider",
}

# Review site URL templates
G2_URL = "https://www.g2.com/products/{slug}/reviews"
CAPTERRA_URL = "https://www.capterra.com/p/{slug}/reviews/"
TRUSTRADIUS_URL = "https://www.trustradius.com/products/{slug}/reviews"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def create_driver(headless: bool = True) -> webdriver.Chrome:
    """Create a Selenium Chrome driver."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(30)
    return driver


def scroll_page(driver: webdriver.Chrome, pause: float = 1.5, max_scrolls: int = 15):
    """Scroll the full page to trigger lazy-loaded content."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    # Scroll back to top
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)


def iterate_carousels(driver: webdriver.Chrome):
    """Click through carousel next buttons to reveal all slides."""
    carousel_selectors = [
        "button[aria-label*='next' i]",
        "button[aria-label*='Next' i]",
        ".slick-next",
        ".swiper-button-next",
        ".carousel-control-next",
        "[class*='carousel'] [class*='next']",
        "[class*='slider'] [class*='next']",
        "[data-direction='next']",
    ]
    for selector in carousel_selectors:
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, selector)
            for btn in buttons:
                if btn.is_displayed():
                    for _ in range(12):  # click up to 12 times per carousel
                        try:
                            btn.click()
                            time.sleep(0.8)
                        except (StaleElementReferenceException, WebDriverException):
                            break
        except Exception:
            continue


def clean_name(raw: str) -> str:
    """Clean a raw string into a plausible company name."""
    if not raw:
        return ""
    # Remove file extensions
    name = re.sub(r"\.(png|jpg|jpeg|svg|webp|gif|avif)$", "", raw, flags=re.I)
    # Strip retina suffixes BEFORE splitting (e.g. "logo-assa-abloy@2x-1" -> "logo-assa-abloy")
    name = re.sub(r"@\d+x(?:[- ]\d+)?$", "", name, flags=re.I)
    # Strip trailing image variant numbers (e.g. "staples-2" -> "staples")
    name = re.sub(r"[- ]\d{1,2}$", "", name)
    # Strip dimensions like "1200x1200"
    name = re.sub(r"[- ]?\d{3,4}[xX]\d{3,4}", "", name)
    # Strip domain suffixes (e.g. "fossil.com" -> "fossil", "nuxe.fr" -> "nuxe")
    name = re.sub(
        r"\.(com|fr|de|co|uk|eu|net|org|io|it|es|nl|be|ch|at|pt|se|dk|no|fi"
        r"|us|ca|au|nz|jp|cn|in|br|mx|za|ru|pl|cz|ie|co\.uk|com\.au|co\.nz)$",
        "", name, flags=re.I,
    )
    # Also strip domain suffixes that appear mid-string before other junk
    name = re.sub(
        r"\.(com|fr|de|co|uk|eu|net|org|io|it|es|nl)\b.*$",
        "", name, flags=re.I,
    )
    # Replace separators with spaces
    name = re.sub(r"[-_]+", " ", name)
    # Strip trailing TLD words (capitalized versions left after separator replacement)
    # e.g. "Fossil Com" -> "Fossil", "Nuxe Fr" -> "Nuxe"
    _TLD_WORDS = {
        "com", "fr", "de", "co", "uk", "eu", "net", "org", "io", "it",
        "es", "nl", "be", "ch", "at", "pt", "se", "dk", "no", "fi",
        "us", "ca", "au", "nz", "jp", "cn", "in", "br", "mx",
    }
    # Remove noise words
    tokens = name.split()
    tokens = [t for t in tokens if t.lower() not in NOISE_WORDS]
    # Strip trailing TLD tokens, but protect "& Co" / "And Co" patterns
    while tokens and tokens[-1].lower() in _TLD_WORDS:
        # Don't strip "Co" if preceded by "And" or "&" (e.g. "Tiffany And Co")
        if tokens[-1].lower() == "co" and len(tokens) >= 2 and tokens[-2].lower() in ("and", "&"):
            break
        tokens.pop()
    name = " ".join(tokens).strip()
    # Skip very short or very long results
    if len(name) < 2 or len(name) > 80:
        return ""
    # Skip if it's just numbers or a single common word
    if re.match(r"^\d+$", name):
        return ""
    # Skip hex IDs / tracking pixel identifiers (e.g. "60F82098Ea348500148A9D90")
    if re.match(r"^[0-9A-Fa-f]{16,}$", name.replace(" ", "")):
        return ""
    # Skip base64 image data fragments
    if re.search(r"[A-Za-z0-9+/]{20,}=+$", name.replace(" ", "")):
        return ""
    # Skip raw filenames with timestamps/IDs (e.g. "20210812 162428 Original")
    if re.search(r"\d{6,}", name.replace(" ", "")):
        return ""
    # Re-tokenize after stripping
    tokens = name.split()
    # Skip if it looks like a sentence (more than 6 words) — not a company name
    if len(tokens) > 6:
        return ""
    # Skip generic marketing / UI phrases
    lower = name.lower()
    generic_phrases = [
        "world class", "ecosystem", "all commerce", "learn more",
        "read more", "view all", "see all", "contact us", "get started",
        "sign up", "request demo", "book a demo", "schedule", "subscribe",
        "download", "watch now", "explore", "discover", "try free",
        "free trial", "pricing", "solutions", "resources", "our platform",
        "why choose", "how it works", "what we do", "who we are",
        "cookie", "privacy", "terms", "copyright", "all rights",
        "loading", "please wait", "submit", "accept", "decline",
        "back to", "become a", "find a", "join", "app store",
        "api doc", "certification", "documentation", "community",
        "customer story", "case study", "partner program",
        "powered by", "ai powered", "ai but", "apparel",
        "electronics", "high tech",
        "diamond", "gold", "silver", "bronze", "platinum",  # partner tiers
        "collection", "featured", "popular", "trending",
        "appointment reminder",
    ]
    if any(phrase in lower for phrase in generic_phrases):
        return ""
    # Skip training/certification/role labels and UI/page elements
    ui_noise = [
        "fundamentals", "specialist", "implementation",
        "technical integration", "help center", "pxm advisor",
        "sdm ", "isv partner", "solution partner",
        "partners hero", "partners find", "key benefits",
        "thumbnail", "webinar", "casestudy", "office hour",
        "spring release", "zoom ", "interview ",
        "hero ", "banner ", "cta ",
        "customerstory", "cas client",
        "webpage ", "newsletter", "email ",
        "web assets", "brand attributes", "card raise",
        "pim for ", "pim by ", "b2b ", "b2c ",
        "survey results",
        "improve site", "industry page",
        "pxm champion", "pxmas", "case stud",
        "customer case", "head of ",
        "day 1 ", "day 2 ", "day 3 ", "day 4 ", "day 5 ",
        "day 6 ", "day 7 ", "day 8 ", "day 9 ", "day 10",
        "day 11", "day 12",
    ]
    if any(phrase in lower for phrase in ui_noise):
        return ""
    # Skip if name equals a single common/generic word
    if lower in {"ads", "biscuit", "ad"}:
        return ""
    # Skip very short single-word names (likely not real companies)
    if len(tokens) == 1 and len(name) <= 2:
        return ""
    # Skip known noise entries
    if lower in {"midland scientist"}:
        return ""
    # Skip strings ending with period (likely sentences, not company names)
    if name.endswith("."):
        return ""
    return name.title()


TRACKING_DOMAINS = {
    "zoominfo.com", "ws.zoominfo.com", "googletagmanager.com",
    "google-analytics.com", "analytics.google.com", "facebook.com",
    "facebook.net", "fbcdn.net", "doubleclick.net", "googlesyndication.com",
    "hotjar.com", "hubspot.com", "hs-analytics.net", "hsforms.com",
    "marketo.com", "mktoresp.com", "pardot.com", "segment.com",
    "segment.io", "mixpanel.com", "amplitude.com", "heap.io",
    "heapanalytics.com", "intercom.io", "drift.com", "clearbit.com",
    "6sense.com", "demandbase.com", "bombora.com", "rollworks.com",
    "linkedin.com", "licdn.com", "twitter.com", "x.com",
    "cloudflare.com", "cloudflareinsights.com", "jsdelivr.net",
    "unpkg.com", "cdnjs.cloudflare.com", "googleapis.com",
    "gstatic.com", "gravatar.com", "wp.com", "wordpress.com",
    "w.org", "bootstrapcdn.com", "fontawesome.com",
    "sentry.io", "bugsnag.com", "datadoghq.com", "newrelic.com",
    "optimizely.com", "crazyegg.com", "fullstory.com",
    "mouseflow.com", "lucky-orange.com", "cookiebot.com",
    "onetrust.com", "trustarc.com", "cookielaw.org",
}


def extract_domain_from_url(url: str) -> str:
    """Try to extract a company domain from a URL."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Remove common prefixes
        host = re.sub(r"^(www\d?|cdn|assets|images|static|media)\.", "", host)
        return host
    except Exception:
        return ""


def is_tracking_domain(domain: str) -> bool:
    """Check if a domain belongs to a tracking/analytics service."""
    if not domain:
        return False
    for td in TRACKING_DOMAINS:
        if domain == td or domain.endswith("." + td):
            return True
    return False


def is_relevant_section(element, depth: int = 3) -> bool:
    """Check if an element or its ancestors look like a customer/partner section."""
    node = element
    for _ in range(depth):
        if node is None:
            break
        text = ""
        for attr in ["class", "id", "data-section", "aria-label"]:
            val = node.get(attr, "")
            if isinstance(val, list):
                val = " ".join(val)
            text += " " + val.lower()
        heading = node.find(re.compile(r"^h[1-6]$"))
        if heading:
            text += " " + heading.get_text().lower()
        if any(kw in text for kw in LOGO_SECTION_KEYWORDS):
            return True
        node = node.parent
    return False


# ---------------------------------------------------------------------------
# Fuzzy name normalization for deduplication
# ---------------------------------------------------------------------------
# Suffixes to strip when comparing names for deduplication
_DEDUP_SUFFIXES = [
    " inc", " llc", " ltd", " corp", " corporation", " co.",
    " group", " gmbh", " sa", " sas", " ag", " plc", " bv", " nv",
    " pty", " se", " spa", " srl", " kg", " ohg", " ab",
    " store", " stores", " shop", " online",
    " international", " intl",
]


def _normalize_for_dedup(name: str) -> str:
    """Normalize a company name for deduplication comparison."""
    n = name.lower().strip()
    # Remove punctuation
    n = re.sub(r"[.,;:!?'\"()\[\]{}]", "", n)
    # Normalize "& co" / "and co" variants (keep them — they're part of the name)
    n = re.sub(r"\s+&\s+co\b", " and co", n)
    # Strip known legal/commercial suffixes
    for suffix in _DEDUP_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    # Strip trailing TLD words
    _tld = {"com", "fr", "de", "co", "uk", "eu", "net", "org", "io", "it", "es", "nl"}
    parts = n.split()
    while parts and parts[-1] in _tld:
        parts.pop()
    n = " ".join(parts)
    # Also strip "group" / "gruppe" as trailing words for dedup
    for trail in (" group", " gruppe"):
        if n.endswith(trail):
            n = n[: -len(trail)].strip()
    # Collapse whitespace and remove all spaces for an additional comparison key
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _is_tech_partner(name: str, source: str) -> bool:
    """Return True if the account looks like a tech/SI partner, not a customer."""
    lower = name.lower().strip()
    # Check against known tech platform names
    if lower in TECH_PARTNER_NAMES or _normalize_for_dedup(name) in TECH_PARTNER_NAMES:
        return True
    # Check if the detection source is a partner page
    source_lower = source.lower()
    if any(pat in source_lower for pat in PARTNER_SOURCE_PATTERNS):
        return True
    return False


# ---------------------------------------------------------------------------
# Result store
# ---------------------------------------------------------------------------
class ResultStore:
    """Deduplicated account storage with fuzzy matching."""

    def __init__(self, detected_company: str):
        self.detected_company = detected_company
        self._accounts: dict[str, dict] = {}  # key = normalized name
        self._norm_to_key: dict[str, str] = {}  # normalized -> canonical key

    def _find_key(self, name: str):
        """Find existing key via exact or fuzzy match."""
        key = name.lower()
        if key in self._accounts:
            return key
        norm = _normalize_for_dedup(name)
        if norm in self._norm_to_key:
            return self._norm_to_key[norm]
        # Space-stripped comparison (catches "Sealedair" vs "Sealed Air")
        norm_nospace = norm.replace(" ", "")
        for existing_norm, existing_key in self._norm_to_key.items():
            existing_nospace = existing_norm.replace(" ", "")
            if norm_nospace == existing_nospace:
                return existing_key
        # Prefix-based substring match (catches "abb" vs "abb inbetween")
        for existing_norm, existing_key in self._norm_to_key.items():
            if len(norm) >= 3 and len(existing_norm) >= 3:
                if norm.startswith(existing_norm) or existing_norm.startswith(norm):
                    return existing_key
        return None

    def add(self, name: str, source: str, domain: str = ""):
        name = clean_name(name)
        if not name:
            return
        # Skip if name matches the target company
        target_lower = self.detected_company.lower()
        name_lower = name.lower()
        if name_lower in target_lower or target_lower in name_lower:
            return
        # Skip known tech/SI partners
        if _is_tech_partner(name, source):
            return
        norm = _normalize_for_dedup(name)
        existing_key = self._find_key(name)
        if existing_key:
            self._accounts[existing_key]["sources"].add(source)
            if domain and not self._accounts[existing_key]["domain"]:
                self._accounts[existing_key]["domain"] = domain
            # If new name is shorter/cleaner, update the display name
            if len(name) < len(self._accounts[existing_key]["account_name"]):
                self._accounts[existing_key]["account_name"] = name
        else:
            key = name_lower
            self._accounts[key] = {
                "account_name": name,
                "domain": domain,
                "sources": {source},
            }
            self._norm_to_key[norm] = key

    def rows(self) -> list[dict]:
        out = []
        for acc in sorted(self._accounts.values(), key=lambda a: a["account_name"]):
            out.append({
                "account_name": acc["account_name"],
                "domain": acc["domain"],
                "detection_source": ", ".join(sorted(acc["sources"])),
                "detected_company": self.detected_company,
            })
        return out

    @property
    def count(self) -> int:
        return len(self._accounts)


# ---------------------------------------------------------------------------
# 1. Homepage logo/name extraction
# ---------------------------------------------------------------------------
def extract_logos_from_page(driver: webdriver.Chrome, url: str, source_label: str, store: ResultStore):
    """Visit a URL with Selenium, scroll, handle carousels, extract logos & names."""
    log.info(f"  Visiting: {url}")
    try:
        driver.get(url)
        time.sleep(3)
    except (TimeoutException, WebDriverException) as e:
        log.warning(f"  Could not load {url}: {e}")
        return

    scroll_page(driver)
    iterate_carousels(driver)
    time.sleep(1)

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, "lxml")

    # --- Extract from images ---
    images = soup.find_all("img")
    for img in images:
        alt = img.get("alt", "").strip()
        title = img.get("title", "").strip()
        src = img.get("src", "") or img.get("data-src", "") or ""
        filename = os.path.basename(urlparse(src).path) if src else ""

        # Check if the image is in a relevant section
        if not is_relevant_section(img, depth=5):
            continue

        # Skip tracking pixels and analytics images
        domain = extract_domain_from_url(src)
        if is_tracking_domain(domain):
            continue

        # Try alt text first, then title, then filename
        for candidate in [alt, title, filename]:
            name = clean_name(candidate)
            if name:
                store.add(name, source_label, domain)
                break

    # --- Extract from text in customer/partner sections ---
    sections = soup.find_all(["section", "div", "ul"])
    for sec in sections:
        attrs_text = " ".join(
            str(sec.get(a, "")) for a in ["class", "id", "data-section", "aria-label"]
        ).lower()
        heading = sec.find(re.compile(r"^h[1-6]$"))
        heading_text = heading.get_text().lower() if heading else ""
        combined = attrs_text + " " + heading_text

        if not any(kw in combined for kw in LOGO_SECTION_KEYWORDS):
            continue

        # Look for company names in list items only (not all spans/paragraphs
        # which tend to capture navigational and descriptive text)
        for el in sec.find_all(["li"]):
            text = el.get_text(strip=True)
            # Company names in logo grids are typically short (1-4 words)
            word_count = len(text.split())
            if 2 < len(text) < 40 and word_count <= 4 and not re.search(r"<|>|http|@|copyright|\d{4}", text, re.I):
                store.add(text, source_label)

    # --- Extract from testimonials / quotes ---
    extract_testimonials(soup, source_label, store)

    log.info(f"  -> {store.count} accounts so far")


# ---------------------------------------------------------------------------
# 1b. Testimonial / quote extraction
# ---------------------------------------------------------------------------
def extract_testimonials(soup: BeautifulSoup, source_label: str, store: ResultStore):
    """Extract company and person names from testimonials, quotes, and blockquotes."""
    testimonial_count = 0

    # --- Strategy 1: testimonial-card pattern (Akeneo-style and common CMS patterns) ---
    card_selectors = [
        {"class": re.compile(r"testimonial", re.I)},
        {"class": re.compile(r"quote-card|quoteCard", re.I)},
        {"class": re.compile(r"review-card|reviewCard", re.I)},
        {"class": re.compile(r"customer-quote|customerQuote", re.I)},
        {"class": re.compile(r"voice-of-customer", re.I)},
    ]
    for selector in card_selectors:
        for card in soup.find_all(["div", "section", "article"], attrs=selector):
            _extract_attribution_from_block(card, source_label, store)
            testimonial_count += 1

    # --- Strategy 2: <blockquote> elements ---
    for bq in soup.find_all("blockquote"):
        # Skip if already inside a testimonial card (avoid double-counting)
        parent_classes = " ".join(bq.parent.get("class", [])) if bq.parent else ""
        if "testimonial" in parent_classes.lower():
            continue
        # Look for attribution in siblings or parent
        parent = bq.parent
        if parent:
            _extract_attribution_from_block(parent, source_label, store)
            testimonial_count += 1

    # --- Strategy 3: <figure> with <figcaption> (common quote pattern) ---
    for figure in soup.find_all("figure"):
        figcaption = figure.find("figcaption")
        if figcaption and figure.find("blockquote"):
            _extract_attribution_from_block(figure, source_label, store)
            testimonial_count += 1

    # --- Strategy 4: Quoted text with attribution patterns in generic sections ---
    # Look for elements containing curly/smart quotes followed by an attribution
    for el in soup.find_all(["div", "section"], class_=re.compile(
        r"quote|testimonial|review|feedback|endorsement|said|voice", re.I
    )):
        _extract_attribution_from_block(el, source_label, store)
        testimonial_count += 1

    if testimonial_count > 0:
        log.info(f"    Found {testimonial_count} testimonial blocks on this page")


def _extract_attribution_from_block(block, source_label: str, store: ResultStore):
    """Given a testimonial/quote block, extract the company name from attribution."""
    full_text = block.get_text(separator="\n", strip=True)

    company_name = ""

    # --- Strategy A: Look for structured attribution elements ---
    author_selectors = [
        {"class": re.compile(r"author|attribution|cite|byline|person|speaker", re.I)},
    ]

    for selector in author_selectors:
        for el in block.find_all(attrs=selector):
            classes = " ".join(el.get("class", [])).lower()
            text = el.get_text(strip=True)
            if not text or len(text) > 120:
                continue

            # Look inside author containers for company name
            # <strong> in attribution blocks typically = company name
            strong = el.find("strong")
            if strong:
                strong_text = strong.get_text(strip=True)
                if strong_text and len(strong_text) < 60:
                    company_name = strong_text
                    break

            # Look for elements with company/org in class name
            for child in el.find_all(attrs={"class": re.compile(r"company|org", re.I)}):
                child_text = child.get_text(strip=True)
                if child_text and len(child_text) < 60:
                    company_name = child_text
                    break

            # Parse "Name, Title at Company" from <p> elements
            for p in el.find_all("p"):
                p_text = p.get_text(strip=True)
                if p_text and len(p_text) < 100:
                    parsed = _parse_attribution_text(p_text)
                    if parsed:
                        company_name = company_name or parsed
        if company_name:
            break

    # --- Strategy B: Look for standalone company/org elements in the block ---
    if not company_name:
        for el in block.find_all(attrs={"class": re.compile(r"company|org(?!aniz)", re.I)}):
            text = el.get_text(strip=True)
            if text and 2 < len(text) < 60:
                company_name = text
                break

    # --- Strategy C: <cite> element ---
    if not company_name:
        cite = block.find("cite")
        if cite:
            parsed = _parse_attribution_text(cite.get_text(strip=True))
            if parsed:
                company_name = parsed

    # --- Strategy D: Parse full text for "at Company" patterns only ---
    # (Skip free-text fallback to avoid capturing person names)
    if not company_name:
        lines = [l.strip() for l in full_text.split("\n") if l.strip()]
        for line in lines:
            if len(line) > 120:
                continue
            if line.startswith(("\u201c", "\u00ab", '"', "\u2018", "\u201d")):
                continue
            # Only use the "at Company" pattern — most reliable for free text
            match = re.search(r"(?:\bat\b|@)\s+([A-Z][A-Za-z\s&.,']+?)(?:\s*$|[|,])", line)
            if match:
                company_name = match.group(1).strip().rstrip(".,;:")
                break

    # --- Strategy E: Derive company from customer-story URL slug only ---
    # Only applies to pages that are clearly about a specific customer
    if not company_name:
        for prefix in ("customer story: ", "subpage: "):
            if source_label.startswith(prefix):
                slug = source_label[len(prefix):]
                # Only use if the slug looks like it refers to a specific customer
                # (i.e., it's a customer-story subpage, not a generic industry/solution page)
                generic_page_keywords = {
                    "customer story", "customer stories", "about us", "press",
                    "resource center", "integrations", "activation",
                    "omnichannel activation", "px insights", "akeneo partners",
                    "become an akeneo partner", "solution partners",
                    "technology partners", "sell on retailers marketplaces",
                    "akeneo pim partners", "akeneo customer community",
                    "customer portal", "akeneo solutions", "akeneo use case",
                    "brand manufacturer", "distributor retailer",
                    "b2b manufacturing", "fashion", "pim for food and beverage",
                    "pim for automotive ecommerce", "pim for cpg", "pim by industry",
                    "expand to new segments", "supplier data onboarding solutions akeneo",
                    "retailers & distributors", "ai b2b industry",
                    "2024 b2b survey results report",
                }
                if slug and slug.lower() not in generic_page_keywords:
                    company_name = slug
                    break

    # Filter out person names before storing
    if company_name and _looks_like_person_name(company_name):
        company_name = ""

    # Store the company if found
    if company_name:
        source = f"testimonial: {source_label}"
        store.add(company_name, source)


def _looks_like_person_name(text: str) -> bool:
    """Return True if the text looks like a person's name rather than a company."""
    if not text:
        return False
    original = text
    text = text.strip().rstrip(",;:")

    # Person names with trailing comma (common in "Name, Title" that got cut)
    if original.strip().endswith(","):
        return True

    # Very common first-name patterns: 2-3 capitalized words, no company suffixes
    words = text.split()
    if len(words) < 2 or len(words) > 4:
        return False

    # If any word contains company-type indicators, it's not a person
    company_indicators = {
        "inc", "llc", "ltd", "corp", "co", "group", "gmbh", "sa", "sas", "ag",
        "plc", "bv", "nv", "pty", "international", "global", "digital",
        "solutions", "technologies", "technology", "systems", "services",
        "consulting", "labs", "studio", "studios", "media", "software",
        "network", "networks", "electric", "electronics", "foods", "food",
        "store", "stores", "equip", "equipment", "furniture", "brown",
    }
    lower_words = {w.lower() for w in words}
    if lower_words & company_indicators:
        return False

    # Check if it matches typical person name pattern (First Last or First Middle Last)
    # People names: all words capitalized or lowercase particles (van, de, den, von, etc.)
    name_particles = {"van", "de", "den", "der", "von", "di", "du", "le", "la", "el", "al"}
    significant_words = [w for w in words if w.lower() not in name_particles]
    if all(w[0].isupper() for w in significant_words if len(w) > 1):
        # Extra check: if all significant words look like proper names (Capitalized)
        if all(w[0].isupper() and w[1:].islower() for w in significant_words if len(w) > 1):
            return True
    return False


def _parse_attribution_text(text: str) -> str:
    """Parse an attribution line and return the company name if found.

    Common patterns:
        "Jane Doe, VP of Sales, Acme Corp"
        "Jane Doe, VP of Sales at Acme Corp"
        "Jane Doe | Acme Corp"
        "— Jane Doe, Acme Corp"
    """
    if not text or len(text) > 150:
        return ""

    # Remove leading dashes/emdashes
    text = re.sub(r"^[\u2014\u2013—–\-]+\s*", "", text)

    # Pattern: "at Company" or "@ Company"
    match = re.search(r"(?:\bat\b|@)\s+([A-Z][A-Za-z\s&.,']+?)(?:\s*$|[|])", text)
    if match:
        return match.group(1).strip().rstrip(".,;:")

    # Pattern: pipe separator "Name | Company"
    if "|" in text:
        parts = text.split("|")
        if len(parts) >= 2:
            candidate = parts[-1].strip()
            if 2 < len(candidate) < 60:
                return candidate

    # Pattern: comma-separated "Name, Title, Company" — take the last segment
    # But only if there are at least 2 commas (name, title, company)
    parts = [p.strip() for p in text.split(",")]
    if len(parts) >= 3:
        candidate = parts[-1].strip().rstrip(".,;:")
        # The last part should look like a company (capitalized, not a job title word)
        title_words = {"director", "manager", "vp", "ceo", "cto", "coo", "head", "lead",
                       "senior", "junior", "chief", "officer", "president", "engineer",
                       "analyst", "consultant", "specialist", "coordinator", "associate"}
        if candidate and not any(w in candidate.lower() for w in title_words):
            if 2 < len(candidate) < 60:
                return candidate

    # Pattern: "Name, Title" with 2 parts — can't reliably extract company
    return ""


# ---------------------------------------------------------------------------
# 2. Detect & visit subpages
# ---------------------------------------------------------------------------
def find_subpages(driver: webdriver.Chrome, base_url: str) -> list[tuple[str, str]]:
    """Find relevant subpage URLs from the homepage navigation."""
    log.info("Detecting relevant subpages...")
    try:
        driver.get(base_url)
        time.sleep(3)
    except (TimeoutException, WebDriverException):
        return []

    soup = BeautifulSoup(driver.page_source, "lxml")
    links = soup.find_all("a", href=True)
    found = []
    seen = set()

    for link in links:
        href = link["href"]
        text = link.get_text(strip=True).lower()
        href_lower = href.lower()

        if any(kw in href_lower or kw in text for kw in SUBPAGE_KEYWORDS):
            full_url = urljoin(base_url, href).rstrip("/")
            # Stay on same domain
            if urlparse(base_url).hostname not in (urlparse(full_url).hostname or ""):
                continue
            # Skip if it's just the homepage again
            if full_url.rstrip("/") == base_url.rstrip("/"):
                continue
            if full_url not in seen:
                seen.add(full_url)
                # Build a clean label from the URL path
                path_parts = [p for p in urlparse(full_url).path.strip("/").split("/") if p]
                path_label = path_parts[-1].replace("-", " ").replace("_", " ") if path_parts else ""
                label = path_label or text[:40]
                found.append((full_url, label))

    log.info(f"  Found {len(found)} relevant subpages")
    for url, label in found:
        log.info(f"    - {label}: {url}")
    return found


def scrape_subpages(driver: webdriver.Chrome, base_url: str, store: ResultStore) -> set:
    """Find and scrape all relevant subpages. Returns the set of visited URLs."""
    subpages = find_subpages(driver, base_url)
    visited = set()
    for url, label in subpages:
        source = f"subpage: {label}"
        extract_logos_from_page(driver, url, source, store)
        visited.add(url)
    return visited


# ---------------------------------------------------------------------------
# 2b. Discover and scrape all customer story pages for testimonials
# ---------------------------------------------------------------------------
STORY_URL_PATTERNS = [
    "customer-story", "customer-stories", "case-study", "case-studies",
    "success-story", "success-stories", "testimonial",
]


def discover_customer_story_pages(driver: webdriver.Chrome, base_url: str, already_visited: set) -> list[str]:
    """Find all individual customer story/case study page URLs via sitemap + listing pages."""
    log.info("Discovering individual customer story pages...")

    base_host = urlparse(base_url).hostname
    all_story_urls = set()

    # --- Source 1: Sitemap (most comprehensive) ---
    sitemap_urls_to_try = [
        f"{base_url.rstrip('/')}/sitemap.xml",
        f"{base_url.rstrip('/')}/sitemap_index.xml",
    ]
    # Also try www variant
    if "www." not in base_url:
        parsed = urlparse(base_url)
        sitemap_urls_to_try.append(f"{parsed.scheme}://www.{parsed.hostname}/sitemap.xml")

    all_sitemap_pages = []
    for sitemap_url in sitemap_urls_to_try:
        try:
            resp = requests.get(sitemap_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; AccountResearch/1.0)"
            })
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml-xml")
            # Handle sitemap index (contains sub-sitemaps)
            sub_sitemaps = [loc.text for loc in soup.find_all("loc")
                           if loc.text.endswith(".xml")]
            pages = [loc.text for loc in soup.find_all("loc")
                     if not loc.text.endswith(".xml")]

            for sub_url in sub_sitemaps:
                try:
                    sub_resp = requests.get(sub_url, timeout=10, headers={
                        "User-Agent": "Mozilla/5.0 (compatible; AccountResearch/1.0)"
                    })
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, "lxml-xml")
                        pages.extend(loc.text for loc in sub_soup.find_all("loc"))
                except Exception:
                    continue

            all_sitemap_pages.extend(pages)
            if pages:
                break  # Got a valid sitemap, no need to try others
        except Exception:
            continue

    for page_url in all_sitemap_pages:
        path = urlparse(page_url).path.lower()
        if any(p in path for p in STORY_URL_PATTERNS):
            path_parts = [p for p in path.strip("/").split("/") if p]
            if len(path_parts) >= 2:  # individual story, not just listing
                all_story_urls.add(page_url.rstrip("/"))

    log.info(f"  Found {len(all_story_urls)} customer story URLs from sitemap")

    # --- Source 2: Listing pages with Selenium (catches JS-rendered links) ---
    listing_patterns = [
        f"{base_url.rstrip('/')}/customer-story/",
        f"{base_url.rstrip('/')}/customer-stories/",
        f"{base_url.rstrip('/')}/case-studies/",
    ]
    if "www." not in base_url:
        parsed = urlparse(base_url)
        listing_patterns.append(f"{parsed.scheme}://www.{parsed.hostname}/customer-story/")

    for listing_url in listing_patterns:
        try:
            driver.get(listing_url)
            time.sleep(3)
        except (TimeoutException, WebDriverException):
            continue
        if "404" in driver.title.lower() or "not found" in driver.title.lower():
            continue

        scroll_page(driver, pause=1.5, max_scrolls=10)

        # Click "load more" / pagination buttons
        for _ in range(10):
            try:
                load_more_selectors = [
                    "button[class*='load-more']", "a[class*='load-more']",
                    "button[class*='loadMore']", "a[class*='loadMore']",
                    "[class*='pagination'] a:last-child", "a[rel='next']",
                    "button[class*='more']", ".load-more",
                ]
                clicked = False
                for sel in load_more_selectors:
                    btns = driver.find_elements(By.CSS_SELECTOR, sel)
                    for btn in btns:
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(2)
                            scroll_page(driver, pause=1, max_scrolls=3)
                            clicked = True
                            break
                    if clicked:
                        break
                if not clicked:
                    break
            except (StaleElementReferenceException, WebDriverException):
                break

        soup = BeautifulSoup(driver.page_source, "lxml")
        for link in soup.find_all("a", href=True):
            full_url = urljoin(listing_url, link["href"]).rstrip("/")
            if base_host not in (urlparse(full_url).hostname or ""):
                continue
            path = urlparse(full_url).path.lower()
            if any(p in path for p in STORY_URL_PATTERNS):
                path_parts = [p for p in path.strip("/").split("/") if p]
                if len(path_parts) >= 2:
                    all_story_urls.add(full_url)

    # Deduplicate across locales: prefer English, but keep locale-only stories
    # Group URLs by their slug (last path segment)
    slug_to_urls: dict = {}  # slug -> list of URLs
    for url in all_story_urls:
        path = urlparse(url).path.lower().strip("/")
        parts = [p for p in path.split("/") if p]
        slug = parts[-1] if parts else ""
        if not slug:
            continue
        if slug not in slug_to_urls:
            slug_to_urls[slug] = []
        slug_to_urls[slug].append(url)

    # For each slug, pick the English URL if available, otherwise the first locale URL
    deduped_urls = set()
    for slug, urls in slug_to_urls.items():
        english = [u for u in urls if not re.search(
            r"^/(de|fr|it|es|pt|nl|ja|zh|ko)/", urlparse(u).path.lower()
        )]
        if english:
            deduped_urls.add(english[0])
        else:
            # Story only exists in a non-English locale — keep it
            deduped_urls.add(urls[0])

    # Remove already-visited pages
    new_urls = deduped_urls - already_visited
    log.info(f"  Found {len(deduped_urls)} customer story pages total ({len(new_urls)} not yet visited)")
    return sorted(new_urls)


def scrape_customer_stories(driver: webdriver.Chrome, base_url: str, store: ResultStore, already_visited: set):
    """Discover all customer story pages and add customers from URL slugs.

    Uses sitemap + listing pages to find all story URLs, then extracts
    customer names directly from the URL slug (fast, no page visits needed).
    Only visits a small sample of pages for testimonial extraction.
    """
    story_urls = discover_customer_story_pages(driver, base_url, already_visited)

    # Add every customer name from the URL slug (no page visit needed)
    for url in story_urls:
        path_parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        slug = path_parts[-1] if path_parts else ""
        label = slug.replace("-", " ").replace("_", " ")
        source = f"customer story: {label}"
        store.add(label, source)

    log.info(f"  Added {len(story_urls)} customer story slugs")

    # Visit a sample of story pages for testimonial extraction (max 20)
    sample = story_urls[:20]
    if sample:
        log.info(f"  Visiting {len(sample)} story pages for testimonials...")
    for url in sample:
        path_parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        label = path_parts[-1].replace("-", " ").replace("_", " ") if path_parts else ""
        source = f"customer story: {label}"
        log.info(f"  Visiting: {url}")
        try:
            driver.get(url)
            time.sleep(2)
        except (TimeoutException, WebDriverException) as e:
            log.warning(f"  Could not load {url}: {e}")
            continue
        scroll_page(driver, pause=1, max_scrolls=5)
        soup = BeautifulSoup(driver.page_source, "lxml")
        extract_testimonials(soup, source, store)

    log.info(f"  -> {store.count} accounts so far")


# ---------------------------------------------------------------------------
# 2c. Discover and scrape industry / solution / vertical pages
# ---------------------------------------------------------------------------
INDUSTRY_URL_KEYWORDS = [
    "solution", "industr", "segment", "use-case", "vertical",
    "brand-manufactur", "distributor", "retailer",
    "b2b", "b2c", "fashion", "food", "beverage", "beauty",
    "electron", "automotive", "cpg", "pim-for-", "pim-by-",
    "manufactur",
]


def discover_industry_pages(driver: webdriver.Chrome, base_url: str, already_visited: set) -> list[str]:
    """Find industry/solution/vertical pages from the homepage and sitemap."""
    log.info("Discovering industry & solution pages...")
    base_host = urlparse(base_url).hostname
    found_urls = set()

    # 1. Collect from the homepage links
    try:
        driver.get(base_url)
        time.sleep(3)
    except (TimeoutException, WebDriverException):
        pass
    else:
        soup = BeautifulSoup(driver.page_source, "lxml")
        for link in soup.find_all("a", href=True):
            href_lower = link["href"].lower()
            if any(kw in href_lower for kw in INDUSTRY_URL_KEYWORDS):
                full_url = urljoin(base_url, link["href"]).rstrip("/")
                if base_host in (urlparse(full_url).hostname or ""):
                    found_urls.add(full_url)

    # 2. Try the sitemap for additional pages
    sitemap_urls = [
        f"{base_url.rstrip('/')}/sitemap.xml",
        f"{base_url.rstrip('/')}/sitemap_index.xml",
    ]
    for sitemap_url in sitemap_urls:
        try:
            resp = requests.get(sitemap_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; AccountResearch/1.0)"
            })
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml-xml")
            # Handle sitemap index (contains sub-sitemaps)
            sub_sitemaps = [loc.text for loc in soup.find_all("loc")
                           if loc.text.endswith(".xml")]
            pages = [loc.text for loc in soup.find_all("loc")
                     if not loc.text.endswith(".xml")]

            for sub_url in sub_sitemaps[:10]:
                try:
                    sub_resp = requests.get(sub_url, timeout=10)
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, "lxml-xml")
                        pages.extend(loc.text for loc in sub_soup.find_all("loc"))
                except Exception:
                    continue

            for page_url in pages:
                page_lower = page_url.lower()
                if any(kw in page_lower for kw in INDUSTRY_URL_KEYWORDS):
                    if base_host in (urlparse(page_url).hostname or ""):
                        found_urls.add(page_url.rstrip("/"))
        except Exception:
            continue

    # Remove already-visited and non-English locale pages (keep only /en/ or no locale)
    new_urls = set()
    for url in found_urls:
        if url in already_visited:
            continue
        path = urlparse(url).path.lower()
        # Skip localized pages (e.g. /de/, /fr/, /it/) — we already scrape English
        if re.search(r"^/(de|fr|it|es|pt|nl|ja|zh|ko)/", path):
            continue
        # Skip glossary, blog, events, webinar pages (not customer logo pages)
        if any(skip in path for skip in ["/glossary/", "/blog/", "/event", "/webinar"]):
            continue
        new_urls.add(url)

    log.info(f"  Found {len(new_urls)} industry/solution pages to scrape")
    return sorted(new_urls)


def scrape_industry_pages(driver: webdriver.Chrome, base_url: str, store: ResultStore, already_visited: set) -> set:
    """Discover and scrape industry/solution/vertical pages for customer logos."""
    urls = discover_industry_pages(driver, base_url, already_visited)
    visited = set()
    for url in urls:
        path_parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        label = path_parts[-1].replace("-", " ").replace("_", " ") if path_parts else url
        source = f"industry page: {label}"
        extract_logos_from_page(driver, url, source, store)
        visited.add(url)
    return visited


# ---------------------------------------------------------------------------
# 3. Google searches
# ---------------------------------------------------------------------------
def run_google_searches(company_name: str, store: ResultStore):
    """Run Google searches to find mentioned accounts."""
    log.info("Running Google searches...")

    if google_search is None:
        log.warning("  googlesearch-python not installed, skipping Google searches")
        return

    queries = [
        f'"{company_name}" customer OR "case study" OR "powered by" OR "uses" OR "partner"',
        f'"{company_name}" filetype:pdf',
        f'"{company_name}" site:linkedin.com',
    ]

    for query in queries:
        log.info(f"  Searching: {query}")
        try:
            results = list(google_search(query, num_results=20, lang="en"))
        except Exception as e:
            log.warning(f"  Google search failed: {e}")
            continue

        time.sleep(2)  # be polite

        for url in results:
            # Try to extract a company name from the URL or title
            domain = extract_domain_from_url(url)
            if domain:
                # Remove TLD
                name = domain.split(".")[0]
                name = clean_name(name)
                if name:
                    store.add(name, "Google search", domain)

        # Also try to fetch and parse the result pages for company mentions
        for url in results[:5]:  # limit to first 5 to avoid being blocked
            try:
                resp = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                })
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                title = soup.title.get_text(strip=True) if soup.title else ""
                # Look for company names near the target company mention
                text = soup.get_text(separator=" ")
                # Find sentences mentioning the company
                pattern = re.compile(
                    rf"([A-Z][A-Za-z\s&.]+?)\s+(?:uses?|chose|selected|partners?\s+with|"
                    rf"switched\s+to|implemented|deployed|adopted)\s+{re.escape(company_name)}",
                    re.I,
                )
                for match in pattern.finditer(text):
                    candidate = match.group(1).strip()
                    store.add(candidate, "Google search")

                pattern2 = re.compile(
                    rf"{re.escape(company_name)}\s+(?:customer|client|partner)\s+([A-Z][A-Za-z\s&.]+?)[\.,;]",
                    re.I,
                )
                for match in pattern2.finditer(text):
                    candidate = match.group(1).strip()
                    store.add(candidate, "Google search")

            except Exception:
                continue

    log.info(f"  -> {store.count} accounts so far")


# ---------------------------------------------------------------------------
# 4-6. Review sites (G2, Capterra, TrustRadius)
# ---------------------------------------------------------------------------
def scrape_review_site(driver: webdriver.Chrome, url: str, site_name: str, company_name: str, store: ResultStore):
    """Scrape reviewer company names from a review site."""
    log.info(f"Scraping {site_name}: {url}")
    try:
        driver.get(url)
        time.sleep(4)
    except (TimeoutException, WebDriverException) as e:
        log.warning(f"  Could not load {site_name}: {e}")
        return

    scroll_page(driver, pause=2, max_scrolls=8)
    soup = BeautifulSoup(driver.page_source, "lxml")

    reviewers_found = 0

    if site_name == "G2":
        # G2 reviewer info is typically in review cards
        for review in soup.find_all(["div", "section"], class_=re.compile(r"review|Review")):
            # Look for company/org info
            for el in review.find_all(["span", "div", "p"], class_=re.compile(r"company|org|business|firm", re.I)):
                text = el.get_text(strip=True)
                if text:
                    store.add(text, "G2 review")
                    reviewers_found += 1
            # Also look for "at Company" patterns in titles
            for el in review.find_all(["span", "div"], string=re.compile(r"\bat\b")):
                text = el.get_text(strip=True)
                match = re.search(r"at\s+([A-Z][A-Za-z\s&.]+)", text)
                if match:
                    store.add(match.group(1).strip(), "G2 review")
                    reviewers_found += 1

    elif site_name == "Capterra":
        for review in soup.find_all(["div", "section"], class_=re.compile(r"review|Review")):
            for el in review.find_all(["span", "div", "p"]):
                text = el.get_text(strip=True)
                # "Company Name" or "at Company" patterns
                match = re.search(r"(?:at|@)\s+([A-Z][A-Za-z\s&.]+)", text)
                if match:
                    store.add(match.group(1).strip(), "Capterra review")
                    reviewers_found += 1

    elif site_name == "TrustRadius":
        for review in soup.find_all(["div", "section"], class_=re.compile(r"review|Review")):
            for el in review.find_all(["span", "div", "a"]):
                cls = " ".join(el.get("class", []))
                if re.search(r"company|org|employer", cls, re.I):
                    text = el.get_text(strip=True)
                    if text:
                        store.add(text, "TrustRadius review")
                        reviewers_found += 1

    # Generic fallback: look for "at <Company>" patterns in all review text
    if reviewers_found == 0:
        all_text = soup.get_text(separator="\n")
        for match in re.finditer(r"(?:at|@)\s+([A-Z][A-Za-z\s&.,]+?)(?:\s*[|\n\r,.])", all_text):
            candidate = match.group(1).strip().rstrip(".,")
            if 2 < len(candidate) < 60:
                store.add(candidate, f"{site_name} review")
                reviewers_found += 1

    log.info(f"  Found {reviewers_found} reviewer companies on {site_name}")
    log.info(f"  -> {store.count} accounts so far")


def scrape_g2(driver: webdriver.Chrome, company_name: str, store: ResultStore):
    slug = company_name.lower().replace(" ", "-")
    url = G2_URL.format(slug=slug)
    scrape_review_site(driver, url, "G2", company_name, store)


def scrape_capterra(driver: webdriver.Chrome, company_name: str, store: ResultStore):
    # Capterra slugs are harder to guess; try a search-based approach
    slug = company_name.lower().replace(" ", "-")
    # Try direct URL first
    url = f"https://www.capterra.com/reviews/{slug}"
    scrape_review_site(driver, url, "Capterra", company_name, store)


def scrape_trustradius(driver: webdriver.Chrome, company_name: str, store: ResultStore):
    slug = company_name.lower().replace(" ", "-")
    url = TRUSTRADIUS_URL.format(slug=slug)
    scrape_review_site(driver, url, "TrustRadius", company_name, store)


# ---------------------------------------------------------------------------
# 7. LinkedIn company posts
# ---------------------------------------------------------------------------
def scrape_linkedin(driver: webdriver.Chrome, company_name: str, store: ResultStore):
    """Attempt to scrape LinkedIn company posts for client/partner mentions."""
    log.info("Scraping LinkedIn company posts...")
    slug = company_name.lower().replace(" ", "-")
    url = f"https://www.linkedin.com/company/{slug}/posts/"

    try:
        driver.get(url)
        time.sleep(5)
    except (TimeoutException, WebDriverException) as e:
        log.warning(f"  Could not load LinkedIn: {e}")
        return

    # Check if we're blocked / need login
    page_text = driver.page_source.lower()
    if "sign in" in page_text and "join now" in page_text:
        log.warning(
            "  LinkedIn requires authentication. To use LinkedIn scraping:\n"
            "    1. Log into LinkedIn in Chrome\n"
            "    2. Export your cookies and load them into the Selenium session\n"
            "    Or run this script with --no-headless to log in manually.\n"
            "  Skipping LinkedIn for now."
        )
        return

    # Scroll through posts
    post_count = 0
    stop_scrolling = False
    for scroll_round in range(20):
        if stop_scrolling:
            break
        scroll_page(driver, pause=2, max_scrolls=3)
        soup = BeautifulSoup(driver.page_source, "lxml")

        posts = soup.find_all(["div", "article"], class_=re.compile(r"feed|post|update", re.I))
        if not posts:
            # Try a more generic approach
            posts = soup.find_all("div", {"data-urn": True})

        for post in posts:
            text = post.get_text(separator=" ", strip=True)

            # Check for date - stop if older than 2 years
            date_match = re.search(r"(\d{1,2}[yY])\s*ago", text)
            if date_match:
                years = int(date_match.group(1).rstrip("yY"))
                if years >= 2:
                    log.info("  Reached posts older than 2 years, stopping")
                    stop_scrolling = True
                    break

            # Check for partner/client mentions
            partner_patterns = [
                r"partner(?:ship|ed|ing)?\s+with\s+([A-Z][A-Za-z\s&.]+)",
                r"customer\s+(?:story|spotlight|success).*?([A-Z][A-Za-z\s&.]+)",
                r"case\s+study.*?([A-Z][A-Za-z\s&.]+)",
                r"proud\s+to\s+(?:announce|work\s+with)\s+([A-Z][A-Za-z\s&.]+)",
                r"welcome\s+([A-Z][A-Za-z\s&.]+)\s+(?:as|to)",
                r"congratulations?\s+(?:to\s+)?([A-Z][A-Za-z\s&.]+)",
                r"testimonial.*?([A-Z][A-Za-z\s&.]+)",
                r"integrat(?:ion|ed|es?)\s+with\s+([A-Z][A-Za-z\s&.]+)",
            ]

            for pattern in partner_patterns:
                for match in re.finditer(pattern, text, re.I):
                    candidate = match.group(1).strip().rstrip(".,;:!")
                    store.add(candidate, "LinkedIn post")
                    post_count += 1

    log.info(f"  Found {post_count} mentions on LinkedIn")
    log.info(f"  -> {store.count} accounts so far")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def run(company_name: str, website: str, output_file: str, headless: bool = True):
    """Run the full account research pipeline."""
    # Normalize website URL
    if not website.startswith("http"):
        website = f"https://{website}"
    base_url = website.rstrip("/")

    store = ResultStore(company_name)

    log.info(f"{'=' * 60}")
    log.info(f"Account Research: {company_name} ({base_url})")
    log.info(f"{'=' * 60}")

    driver = None
    try:
        log.info("Initializing browser...")
        driver = create_driver(headless=headless)

        # Step 1: Homepage
        log.info("\n--- Step 1: Homepage logos & names ---")
        extract_logos_from_page(driver, base_url, "homepage", store)

        # Step 2: Subpages
        log.info("\n--- Step 2: Subpages ---")
        visited = scrape_subpages(driver, base_url, store)
        visited.add(base_url)

        # Step 2b: Customer story pages (testimonials)
        log.info("\n--- Step 2b: Customer story pages & testimonials ---")
        scrape_customer_stories(driver, base_url, store, visited)

        # Step 2c: Industry / solution / vertical pages
        log.info("\n--- Step 2c: Industry & solution pages ---")
        industry_visited = scrape_industry_pages(driver, base_url, store, visited)
        visited.update(industry_visited)

        # Step 3: Google searches
        log.info("\n--- Step 3: Google searches ---")
        run_google_searches(company_name, store)

        # Step 4: G2
        log.info("\n--- Step 4: G2 reviews ---")
        scrape_g2(driver, company_name, store)

        # Step 5: Capterra
        log.info("\n--- Step 5: Capterra reviews ---")
        scrape_capterra(driver, company_name, store)

        # Step 6: TrustRadius
        log.info("\n--- Step 6: TrustRadius reviews ---")
        scrape_trustradius(driver, company_name, store)

        # Step 7: LinkedIn
        log.info("\n--- Step 7: LinkedIn ---")
        scrape_linkedin(driver, company_name, store)

    except KeyboardInterrupt:
        log.info("\nInterrupted by user. Saving partial results...")
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()

    # Step 8: Output
    log.info(f"\n--- Step 8: Export results ---")
    rows = store.rows()
    log.info(f"Total unique accounts found: {store.count}")
    log.info(f"Total rows (with multiple sources): {len(rows)}")

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "account_name", "domain", "detection_source", "detected_company"
        ])
        writer.writeheader()
        writer.writerows(rows)

    log.info(f"Results saved to: {output_file}")
    log.info("Done!")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Automated account research for a given company."
    )
    parser.add_argument("company_name", help='Company name (e.g. "Salsify")')
    parser.add_argument("website", help='Company website (e.g. "salsify.com")')
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output CSV file path (default: <company_name>_accounts.csv)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible mode (useful for LinkedIn login)",
    )
    args = parser.parse_args()

    output = args.output or f"{args.company_name.lower().replace(' ', '_')}_accounts.csv"

    run(
        company_name=args.company_name,
        website=args.website,
        output_file=output,
        headless=not args.no_headless,
    )


if __name__ == "__main__":
    main()
