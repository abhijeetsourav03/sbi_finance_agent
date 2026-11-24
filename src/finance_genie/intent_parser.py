
import json
from datetime import date

import requests

from .metric_registry import METRICS


OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:4b"


def parse_intent(question: str) -> dict:
    today = date.today().isoformat()
    allowed_metrics = ", ".join(METRICS.keys())

    system_prompt = f"""
You are a financial query intent parser.

Today is {today}.

Convert the user's question into one JSON object.

Allowed metrics:
{allowed_metrics}

Required JSON fields:
- metric: one of the allowed metrics
- start_date: YYYY-MM-DD
- end_date: YYYY-MM-DD
- category: string or null

Rules:
1. Return JSON only.
2. Do not calculate financial amounts.
3. Do not invent transaction records.
4. Interpret relative dates using today's date.
5. "Last month" means the previous calendar month.
6. "This month" means the current calendar month to date.
7. If the metric or period is unclear, return
   an error field explaining what is missing.
8. Use null for category when no category is specified.
9. Never generate Python or SQL code.

Example:
Question: How much did I spend in October 2026?

Response:
{{
  "metric": "total_spending",
  "start_date": "2026-10-01",
  "end_date": "2026-10-31",
  "category": null
}}
"""

    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": question,
            },
        ],
        "options": {
            "temperature": 0,
            "num_ctx": 2048,
            "num_predict": 180,
        },
    }

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=120,
        )
        response.raise_for_status()

        result = response.json()
        content = result["message"]["content"]

        return json.loads(content)

    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Ollama request failed: {exc}"
        ) from exc

    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            "Ollama returned an invalid JSON response"
        ) from exc

