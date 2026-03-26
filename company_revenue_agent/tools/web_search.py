import requests
from config import SERPAPI_API_KEY


def search_company_revenue(company_name: str) -> dict:
    """Search the web for a company's revenue using SerpAPI (Google Search)."""
    query = f"{company_name} annual revenue"
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

        # Extract answer box if present (often has direct revenue figures)
        if "answer_box" in data:
            results.append({
                "source": "answer_box",
                "content": str(data["answer_box"]),
            })

        # Extract knowledge graph if present
        if "knowledge_graph" in data:
            results.append({
                "source": "knowledge_graph",
                "content": str(data["knowledge_graph"]),
            })

        # Extract top organic results
        for item in data.get("organic_results", [])[:5]:
            results.append({
                "source": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })

        return {"success": True, "query": query, "results": results}

    except requests.RequestException as e:
        return {"success": False, "error": str(e)}
