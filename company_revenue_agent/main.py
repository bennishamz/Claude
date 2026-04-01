#!/usr/bin/env python3
"""
Company Revenue Assessment Agent

Assess a company's revenue range using web search + LinkedIn employee fallback.

Usage:
    # Single company
    python main.py "Apple"

    # Multiple companies
    python main.py "Apple" "Stripe" "Datadog"

    # From a file (one company per line)
    python main.py --file companies.txt

    # Output as JSON
    python main.py --json "Apple" "Stripe"
"""

import argparse
import json
import sys

from config import ANTHROPIC_API_KEY, SERPAPI_API_KEY, REVENUE_RANGES
from agent import assess_company


RANGE_LABELS = {
    "below_500m": "Below $500M",
    "500m_to_1b": "$500M - $1B",
    "above_1b": "Above $1B",
}


def print_result(result: dict, index: int | None = None):
    """Pretty-print a single assessment result."""
    prefix = f"[{index}] " if index is not None else ""
    company = result.get("company_name", "Unknown")
    range_key = result.get("revenue_range", "unknown")
    range_label = RANGE_LABELS.get(range_key, range_key)
    estimated = result.get("estimated_revenue", "N/A")
    confidence = result.get("confidence", "N/A")
    source = result.get("source", "N/A")
    reasoning = result.get("reasoning", "")

    print(f"\n{prefix}{company}")
    print(f"  Revenue Range : {range_label}")
    print(f"  Estimated     : {estimated}")
    print(f"  Confidence    : {confidence}")
    print(f"  Source        : {source}")
    print(f"  Reasoning     : {reasoning}")


def main():
    parser = argparse.ArgumentParser(
        description="Assess company revenue ranges using AI agent."
    )
    parser.add_argument(
        "companies",
        nargs="*",
        help="One or more company names to assess.",
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="Path to a text file with one company name per line.",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output results as JSON.",
    )
    args = parser.parse_args()

    # Collect company names
    companies = list(args.companies) if args.companies else []
    if args.file:
        with open(args.file) as f:
            companies.extend(
                line.strip() for line in f if line.strip()
            )

    if not companies:
        parser.print_help()
        sys.exit(1)

    # Validate API keys
    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set. See .env.example", file=sys.stderr)
        sys.exit(1)
    if not SERPAPI_API_KEY:
        print("Error: SERPAPI_API_KEY not set. See .env.example", file=sys.stderr)
        sys.exit(1)

    print(f"Assessing {len(companies)} company(ies)...\n")

    results = []
    for i, company in enumerate(companies, 1):
        print(f"[{i}/{len(companies)}] Analyzing {company}...")
        result = assess_company(company)
        results.append(result)

        if not args.json:
            print_result(result, i)

    if args.json:
        print(json.dumps(results, indent=2))

    # Print summary table for batch mode
    if len(companies) > 1 and not args.json:
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"{'Company':<30} {'Revenue Range':<20} {'Confidence'}")
        print("-" * 60)
        for r in results:
            name = r.get("company_name", "?")[:29]
            rng = RANGE_LABELS.get(r.get("revenue_range", ""), "Unknown")
            conf = r.get("confidence", "?")
            print(f"{name:<30} {rng:<20} {conf}")


if __name__ == "__main__":
    main()
