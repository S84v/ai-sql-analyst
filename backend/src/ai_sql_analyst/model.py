"""DeepSeek model construction for the provider-neutral agent boundary (ADR-005).

Provider-specific knowledge lives here and only here: the DeepSeek Responses API
model ID, base URL, reasoning effort, and the API-key environment variable.
``agent.py`` receives the resulting ``ChatOpenAI`` as a plain ``BaseChatModel``
and remains provider-neutral.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# model.py lives at <repo>/backend/src/ai_sql_analyst/model.py.
REPO_ROOT = Path(__file__).resolve().parents[3]

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_REASONING_EFFORT = "high"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"


def build_model() -> ChatOpenAI:
    """Build the DeepSeek ``deepseek-flash`` model via the Responses API.

    Configuration is read at call time, never at import time, so importing this
    module (and running the test suite) needs no secret. The Responses API is
    selected explicitly; ``output_version="responses/v1"`` is kept so structured
    response items remain available to the later progress/streaming milestone.

    ``store`` is deliberately left unset and ``use_previous_response_id`` is not
    used: DeepSeek's Responses API is stateless, and leaving ``store`` unset lets
    LangChain replay reasoning items across the tool loop, which DeepSeek
    requires (ADR-005).
    """
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get(DEEPSEEK_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"Missing required environment variable: {DEEPSEEK_API_KEY_ENV}"
        )
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": DEEPSEEK_REASONING_EFFORT},
    )
