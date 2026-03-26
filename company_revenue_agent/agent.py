import json
import anthropic
from config import CLAUDE_MODEL, ANTHROPIC_API_KEY
from tools.web_search import search_company_revenue
from tools.linkedin import search_linkedin_employees, REVENUE_PER_EMPLOYEE

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TOOLS = [
    {
        "name": "search_company_revenue",
        "description": (
            "Search the web for a company's annual revenue. "
            "Returns search results with snippets containing revenue figures. "
            "Use this as the first step to find revenue data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "The name of the company to search for.",
                }
            },
            "required": ["company_name"],
        },
    },
    {
        "name": "search_linkedin_employees",
        "description": (
            "Search for a company's LinkedIn page to find the employee count. "
            "Use this as a FALLBACK when revenue data cannot be found directly. "
            "The employee count can be used to estimate revenue using industry "
            "revenue-per-employee benchmarks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "The name of the company to search on LinkedIn.",
                }
            },
            "required": ["company_name"],
        },
    },
    {
        "name": "classify_revenue",
        "description": (
            "Classify a company into a revenue range based on your analysis. "
            "Call this tool once you have enough information to make a determination."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "The company name.",
                },
                "revenue_range": {
                    "type": "string",
                    "enum": ["below_500m", "500m_to_1b", "above_1b"],
                    "description": (
                        "The revenue range: "
                        "'below_500m' for < $500M, "
                        "'500m_to_1b' for $500M-$1B, "
                        "'above_1b' for > $1B."
                    ),
                },
                "estimated_revenue": {
                    "type": "string",
                    "description": "The estimated revenue figure (e.g. '$2.5B', '~$300M').",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Confidence level in the classification.",
                },
                "source": {
                    "type": "string",
                    "enum": ["direct_revenue_data", "employee_estimate"],
                    "description": "Whether the classification came from direct revenue data or employee-based estimation.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of how the revenue range was determined.",
                },
            },
            "required": [
                "company_name",
                "revenue_range",
                "estimated_revenue",
                "confidence",
                "source",
                "reasoning",
            ],
        },
    },
]

SYSTEM_PROMPT = """\
You are a company revenue assessment agent. Your job is to determine a company's \
annual revenue range using a two-step approach:

**Step 1 - Direct Revenue Search:**
Use `search_company_revenue` to find the company's actual reported revenue.

**Step 2 - LinkedIn Fallback (only if Step 1 fails):**
If you cannot find reliable revenue data from Step 1, use `search_linkedin_employees` \
to find the company's employee count on LinkedIn, then estimate revenue using these \
industry benchmarks (revenue per employee):
- Technology/Software: $350K-$400K
- Consulting/Professional Services: $250K
- Financial Services: $500K
- Manufacturing: $300K
- Retail: $200K
- Healthcare: $250K
- Energy: $600K
- Default (unknown industry): $300K

**Step 3 - Classification:**
Once you have enough data, use `classify_revenue` to assign the company to one of:
- `below_500m`: Annual revenue below $500 million
- `500m_to_1b`: Annual revenue between $500 million and $1 billion
- `above_1b`: Annual revenue above $1 billion

Be rigorous. Prefer recent fiscal year data. State your confidence level honestly.\
"""


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool call and return the result as a string."""
    if tool_name == "search_company_revenue":
        result = search_company_revenue(tool_input["company_name"])
    elif tool_name == "search_linkedin_employees":
        result = search_linkedin_employees(tool_input["company_name"])
    elif tool_name == "classify_revenue":
        # Classification is the final output — just return it as-is
        return json.dumps(tool_input)
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    return json.dumps(result)


def assess_company(company_name: str) -> dict:
    """Run the agent loop for a single company and return the classification."""
    messages = [
        {"role": "user", "content": f"Assess the revenue range for: {company_name}"}
    ]

    while True:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Check if the model wants to use tools
        if response.stop_reason == "tool_use":
            # Process all tool calls in the response
            assistant_content = response.content
            tool_results = []

            for block in assistant_content:
                if block.type == "tool_use":
                    tool_result = execute_tool(block.name, block.input)

                    # If this is the classify_revenue call, we have our answer
                    if block.name == "classify_revenue":
                        return block.input

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_result,
                    })

            # Feed tool results back to continue the loop
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        else:
            # Model stopped without classifying — extract any text response
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            return {
                "company_name": company_name,
                "revenue_range": "unknown",
                "reasoning": " ".join(text_parts) if text_parts else "Agent could not determine revenue.",
                "confidence": "low",
                "source": "none",
            }
