import requests
from config import SERPAPI_API_KEY


def search_linkedin_employees(company_name: str) -> dict:
    """Search for a company's LinkedIn page via Google to find employee count."""
    query = f"{company_name} site:linkedin.com/company employees"
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "num": 5,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        results = []

        if "knowledge_graph" in data:
            results.append({
                "source": "knowledge_graph",
                "content": str(data["knowledge_graph"]),
            })

        for item in data.get("organic_results", [])[:5]:
            results.append({
                "source": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })

        return {"success": True, "query": query, "results": results}

    except requests.RequestException as e:
        return {"success": False, "error": str(e)}


# Revenue-per-employee heuristics by industry (in USD)
REVENUE_PER_EMPLOYEE = {
    "technology": 400_000,
    "software": 350_000,
    "consulting": 250_000,
    "financial_services": 500_000,
    "manufacturing": 300_000,
    "retail": 200_000,
    "healthcare": 250_000,
    "energy": 600_000,
    "default": 300_000,
}
