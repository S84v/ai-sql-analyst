"""Optional DeepEval qualitative judge (ADR-008).

The only evaluation module that imports DeepEval metrics for judging. It is
deliberately tiny: one GEval-style metric for the two genuinely subjective cases
(ambiguity handling and evidence discipline). Exact numerical/categorical
correctness is never judged here -- that is the deterministic oracle's job.

The judge is a custom ``DeepEvalBaseLLM`` adapter over the existing DeepSeek
provider. It reuses ``ai_sql_analyst.model.build_model`` so the judge speaks the
same Responses API as the agent; no provider knowledge is duplicated and no
provider code moves into the graph. Telemetry is disabled and no Confident AI
account/API key is used.
"""

from __future__ import annotations

import os
from typing import Any

from metrics import EvalCase, Observation

# Must be set before DeepEval is imported. Local only, never hosted.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

_QUALITATIVE_CATEGORIES = frozenset({"ambiguity", "evidence_discipline"})

_CRITERIA = (
    "The response must be grounded in the database evidence the agent actually "
    "gathered. Penalize unsupported claims, invented causes or relationships, "
    "and numbers with no observed evidence. It must preserve the scope of the "
    "question. When the question is ambiguous or underspecified, it must state "
    "the metric or assumption it used, or explicitly ask for clarification, "
    "rather than presenting a subjective choice as an objective fact."
)

_judge: Any | None = None


def is_available() -> bool:
    """Return whether DeepEval can be imported (the optional ``evals`` group)."""
    try:
        import deepeval  # noqa: F401
    except ImportError:
        return False
    return True


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", "")
    return content if isinstance(content, str) else str(content)


def _build_judge() -> Any:
    """Build the DeepSeek Responses-API judge once."""
    global _judge
    if _judge is not None:
        return _judge

    from deepeval.models.base_model import DeepEvalBaseLLM

    from ai_sql_analyst.model import build_model

    client = build_model()
    model_name = client.model_name

    class _DeepSeekJudge(DeepEvalBaseLLM):
        def __init__(self) -> None:
            self._client = client
            super().__init__(model=model_name)

        def load_model(self) -> Any:
            return self._client

        def get_model_name(self) -> str:
            return model_name

        def supports_structured_outputs(self) -> bool:
            return True

        def generate(self, prompt: str, schema: Any = None) -> Any:  # type: ignore[override]
            if schema is not None:
                return self._client.with_structured_output(schema).invoke(prompt)
            return _response_text(self._client.invoke(prompt))

        async def a_generate(self, prompt: str, schema: Any = None) -> Any:  # type: ignore[override]
            if schema is not None:
                return await self._client.with_structured_output(schema).ainvoke(prompt)
            return _response_text(await self._client.ainvoke(prompt))

    _judge = _DeepSeekJudge()
    return _judge


def run_judge(case: EvalCase, observation: Observation) -> dict[str, Any]:
    """Run the single qualitative metric for the subjective cases only."""
    if case.category not in _QUALITATIVE_CATEGORIES:
        return {}

    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCase, SingleTurnParams

    metric = GEval(
        name="Answer grounding and scope",
        criteria=_CRITERIA,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=_build_judge(),
        threshold=0.5,
        async_mode=False,
    )
    test_case = LLMTestCase(input=case.question, actual_output=observation.answer)
    try:
        metric.measure(test_case)
    except Exception as exc:  # a judge error must not abort the run
        return {"qualitative": {"score": None, "success": None, "reason": f"judge error: {exc}"}}
    return {
        "qualitative": {
            "score": metric.score,
            "success": bool(metric.is_successful()),
            "reason": getattr(metric, "reason", None),
        }
    }
