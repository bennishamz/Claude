# Presti Account Qualifier

## Rôle
Tu es un agent de qualification B2B. Pour chaque entreprise dans input.csv,
tu évalues si elle est un bon fit pour Presti (plateforme de génération de
visuels produit par IA).

## Méthodologie

L'agent utilise une approche **knowledge-first** : la classification verticale,
la détection ecommerce et l'estimation du catalogue sont d'abord remplies à
partir des connaissances du modèle, puis vérifiées/complétées par scraping si
nécessaire. Cette approche est plus fiable que le scraping seul car beaucoup de
sites modernes (SPAs, React, Cloudflare) sont difficiles à scraper.

## Stack technique
- Python 3.9+
- `requests` + `beautifulsoup4` pour le scraping HTTP
- `playwright` pour les sites JS-heavy (fallback)
- `csv` (built-in) pour l'I/O

## Workflow

### 1. Lire input.csv
Colonnes attendues : `company`, `website` (+ autres colonnes conservées).
Colonnes optionnelles pré-remplies : `known_vertical`, `known_verdict`,
`known_ecommerce`, `known_catalog_size`, `revenue_range`, `estimated_revenue`,
`country`.

### 2. Phase Knowledge (classify_knowledge.py)
Pour chaque entreprise, utiliser les connaissances du modèle pour remplir :

**Vertical** (3 tiers) :
- **KEEP** (tier 1, priorité haute) : home/furniture, home improvement, consumer
  electronics, sporting goods, pet care, kitchenware, toys, outdoor/garden,
  automotive parts, home appliances
- **DEPRIORITIZE** (tier 2, viable mais priorité basse) : fashion/apparel,
  beauty/cosmetics, footwear, eyewear/optical, jewelry/watches, department stores,
  grocery (vend aussi du non-alimentaire), pharmacy/parapharmacie (beauty, wellness)
- **DISQUALIFY** (tier 3, non pertinent) : B2B industriel pur, SaaS, consulting,
  spirits/alcool, travel/hotels, insurance/banking, media/publishing sans produits
  physiques, food/beverages pur, healthcare B2B (dispositifs médicaux sans ecom)

**eCommerce** :
- **PASS** = vend en ligne via son propre site (add-to-cart, panier)
- **DEPRIORITIZE** = pas de vente directe (site corporate, marketplace-only, brand-only)

**Taille catalogue** :
- **LARGE** = 1000+ produits
- **MEDIUM** = 100-999 produits
- **SMALL** = <100 produits
- **N/A** = pas applicable (pas d'ecommerce)

### 3. Phase Scraping (qualify.py) — optionnel, pour vérification/complétion
Si les colonnes `known_*` sont vides, le scraping tente de déterminer les critères :
- Scraping homepage, page catégorie (PLP), page produit (PDP)
- Détection de plateforme ecommerce (Shopify, Magento, WooCommerce, etc.)
- Inspection DOM rendu via Playwright pour les sites JS-heavy
- Estimation catalogue via sitemaps, compteurs de résultats, variables JS

### 4. Scoring sur 100 points

**Hard filters** (= DISQUALIFIED si non rempli) :
- Pas d'ecommerce → DISQUALIFIED
- Vertical DISQUALIFY → DISQUALIFIED

**Points** (pour les qualifiés) :

| Critère | Détail | Points |
|---|---|---|
| Revenue | >$50B = 40, >$10B = 35, >$5B = 30, >$2B = 25, >$1B = 20, $500M-$1B = 10 | 0-40 |
| Vertical | KEEP = 30, DEPRIORITIZE = 15 | 0-30 |
| Catalogue | LARGE = 30, MEDIUM = 15, SMALL = 5 | 0-30 |

**Tiers** :

| Tier | Score | Description |
|---|---|---|
| Tier 1 | 80-100 | Top priority — gros CA + vertical prioritaire + gros catalogue |
| Tier 2 | 60-79 | High value — grands groupes fashion/beauty, ou mid-size en tier 1 |
| Tier 3 | 40-59 | Good fit — $500M-$1B en tier 2, ou mid-size catalogue moyen |
| Tier 4 | <40 | Opportunistic — petits catalogues, niches |

### 5. Output : output/results.csv

Colonnes : toutes les colonnes input + `vertical`, `vertical_verdict`,
`has_ecommerce`, `catalog_size`, `fit_score`, `fit_points`, `revenue_pts`,
`vertical_pts`, `catalog_pts`, `tier`, `continent`

### 6. Mapping Continent
Ajoute automatiquement une colonne `continent` à partir du pays :
- Europe, North America, South America, Asia, Oceania, Middle East, Africa,
  Central America

## Règles
- Écris dans le CSV après chaque entreprise (mode incrémental)
- Log terminal : "▶ [3/47] Acme Corp"
- Si site inaccessible et pas de données knowledge → DISQUALIFIED
- Revenue non renseigné → utiliser 10 points (floor $500M-$1B)
- Grocery avec ecommerce → DEPRIORITIZE (pas DISQUALIFY) : ils vendent du non-alimentaire
- Pharmacy avec ecommerce → DEPRIORITIZE : parapharmacie, beauty, wellness
