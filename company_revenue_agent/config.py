import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

REVENUE_RANGES = {
    "below_500m": "Below $500M",
    "500m_to_1b": "$500M - $1B",
    "above_1b": "Above $1B",
}

CLAUDE_MODEL = "claude-sonnet-4-6"
