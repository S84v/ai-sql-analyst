"""Single opt-in entry point for the live agent evaluation (ADR-008).

Drives the real DeepSeek agent once per golden case against the live PostgreSQL
database, captures the run with DeepEval's native LangGraph ``CallbackHandler``,
applies the deterministic Olist metrics, optionally adds the one qualitative
DeepEval judge, writes a gitignored JSON report, and exits non-zero when a
required deterministic check fails.

Usage (from ``backend/``)::

    uv sync --group evals                  # install DeepEval (optional, opt-in)
    uv run python evals/run_evals.py        # full live evaluation
    uv run python evals/run_evals.py --no-judge
    uv run python evals/run_evals.py --case unique_buyers --case repeat_customers
    uv run python evals/run_evals.py --validate    # dataset only; no DB/model
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as a script (``python evals/run_evals.py``).
EVALS_DIR = Path(__file__).resolve().parent
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import metrics  # noqa: E402  (path bootstrap must run first)
from metrics import CaseResult, EvalCase, Observation, ToolCall  # noqa: E402

REPORTS_DIR = EVALS_DIR / "reports"
REPO_ROOT = EVALS_DIR.parents[1]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Opt-in live evaluation of the Olist agent.")
    parser.add_argument("--case", action="append", default=[], help="only run this case id (repeatable)")
    parser.add_argument("--no-judge", action="store_true", help="skip the qualitative DeepEval judge")
    parser.add_argument("--report", type=Path, default=None, help="report path (default: evals/reports/run-<ts>.json)")
    parser.add_argument("--recursion-limit", type=int, default=25, help="LangGraph recursion limit per case")
    parser.add_argument("--validate", action="store_true", help="validate the dataset and exit without a DB/model")
    return parser.parse_args(argv)


def _select(cases: list[EvalCase], wanted: list[str]) -> list[EvalCase]:
    if not wanted:
        return cases
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case id(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in wanted]


# ---------------------------------------------------------------------------
# Framework capture (DeepEval native LangGraph callback)
# ---------------------------------------------------------------------------


def _parse_tool_output(output: Any) -> dict[str, Any] | None:
    """Parse a captured tool output into the structured run_sql payload."""
    if isinstance(output, dict):
        return output
    content = getattr(output, "content", output)
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _extract_tool_calls(handler: Any) -> list[ToolCall]:
    """Read tool calls from DeepEval's captured LangGraph trace.

    DeepEval evicts the finished trace from its manager, so the runner reads the
    trace captured on the handler object. This is evaluation-only code pinned to
    the locked DeepEval version (ADR-008).
    """
    trace = getattr(handler, "_trace", None)
    calls = getattr(trace, "tools_called", None) or []
    records: list[ToolCall] = []
    for call in calls:
        args = call.input_parameters if isinstance(call.input_parameters, dict) else {}
        records.append(ToolCall(name=call.name, args=dict(args), result=_parse_tool_output(call.output)))
    return records


def _message_text(message: Any) -> str:
    text = getattr(message, "text", None)
    return text.strip() if isinstance(text, str) else ""


def _invoke_agent(graph: Any, question: str, recursion_limit: int) -> Observation:
    """Invoke the real agent once, capturing the run via DeepEval's callback."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.errors import GraphRecursionError

    from deepeval.integrations.langchain import CallbackHandler

    handler = CallbackHandler(name="olistiq-eval")
    answer = ""
    recursion_hit = False
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=question)]},
            config={"callbacks": [handler], "recursion_limit": recursion_limit},
        )
        final = next(
            (m for m in reversed(result.get("messages", [])) if isinstance(m, AIMessage) and not m.tool_calls),
            None,
        )
        answer = _message_text(final) if final is not None else ""
    except GraphRecursionError:
        # Exhausting the tool loop is itself a robustness result, not a crash.
        recursion_hit = True
    return Observation(
        answer=answer,
        tools=_extract_tool_calls(handler),
        recursion_limit_hit=recursion_hit,
    )


# ---------------------------------------------------------------------------
# Running and reporting
# ---------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    cases = _select(metrics.load_cases(), args.case)
    if args.validate:
        _print_validation(cases)
        return 0

    judge = None
    if not args.no_judge:
        import judge as judge_module

        if judge_module.is_available():
            judge = judge_module
        else:
            print("DeepEval not installed; continuing without the judge (see evals/README.md).")

    from ai_sql_analyst.model import build_model

    from ai_sql_analyst.agent import build_agent

    agent = build_agent(build_model())

    results: list[CaseResult] = []
    for case in cases:
        try:
            before = metrics.db_fingerprint() if "no_mutation" in case.checks else None
            observation = _invoke_agent(agent, case.question, args.recursion_limit)
            after = metrics.db_fingerprint() if "no_mutation" in case.checks else None
            result = metrics.evaluate_case(case, observation, before, after)
        except Exception as exc:  # one bad case must not lose the whole report
            result = metrics.evaluate_case(case, Observation(answer="", tools=[]))
            result.checks.append(metrics.Check("agent_completed", False, f"runner error: {exc}"))
        if judge is not None:
            result.judge = judge.run_judge(case, result.observation)
        results.append(result)
        _print_result(result)

    report = _build_report(results, judge_enabled=judge is not None)
    report_path = args.report or REPORTS_DIR / f"run-{report['metadata']['timestamp'].replace(':', '-')}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _print_summary(report, report_path)
    return 0 if report["summary"]["deterministic_failed"] == 0 else 1


def _print_validation(cases: list[EvalCase]) -> None:
    categories: dict[str, int] = {}
    for case in cases:
        categories[case.category] = categories.get(case.category, 0) + 1
    print(f"dataset OK: {len(cases)} cases")
    for category, count in sorted(categories.items()):
        print(f"  {category}: {count}")
    print(f"dataset sha256: {metrics.dataset_hash()}")


def _print_result(result: CaseResult) -> None:
    status = "PASS" if result.deterministic_pass else "FAIL"
    failed = [check.name for check in result.checks if not check.passed]
    detail = f" failed={','.join(failed)}" if failed else ""
    judge = result.judge.get("qualitative", {})
    judge_text = f" [quality={judge.get('score')}]" if judge else ""
    print(f"{status:4} {result.case.id:28} {result.case.category:20} {result.failure_class or '-':18}{detail}{judge_text}")


def _build_report(results: list[CaseResult], judge_enabled: bool) -> dict[str, Any]:
    failure_classes: dict[str, int] = {}
    for result in results:
        if result.failure_class:
            failure_classes[result.failure_class] = failure_classes.get(result.failure_class, 0) + 1
    passed = sum(1 for result in results if result.deterministic_pass)
    return {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_head": metrics.git_head(REPO_ROOT),
            "dataset_hash": metrics.dataset_hash(),
            "case_count": len(results),
            "judge_enabled": judge_enabled,
        },
        "summary": {
            "total": len(results),
            "deterministic_passed": passed,
            "deterministic_failed": len(results) - passed,
            "failure_classes": failure_classes,
        },
        "cases": [_result_dict(result) for result in results],
    }


def _result_dict(result: CaseResult) -> dict[str, Any]:
    case, observation = result.case, result.observation
    sql = [
        {
            "sql": tool.args.get("sql"),
            "ok": (tool.result or {}).get("ok"),
            "row_count": (tool.result or {}).get("row_count"),
            "truncated": (tool.result or {}).get("truncated"),
            "error_kind": ((tool.result or {}).get("error") or {}).get("kind"),
        }
        for tool in observation.tools
        if tool.name == "run_sql"
    ]
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "deterministic_pass": result.deterministic_pass,
        "failure_class": result.failure_class,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks],
        "oracle": {
            "sql": case.oracle_sql,
            "columns": result.oracle_columns,
            "rows": _jsonable(result.oracle_rows),
            "error": result.oracle_error,
        },
        "observation": {
            "answer": observation.answer,
            "tool_calls": [{"name": t.name, "args": _jsonable(t.args)} for t in observation.tools],
            "sql": sql,
            "tool_error_count": observation.tool_error_count,
            "truncated": observation.saw_truncation,
            "recursion_limit_hit": observation.recursion_limit_hit,
        },
        "judge": result.judge,
        "notes": case.notes,
    }


def _jsonable(value: Any) -> Any:
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _print_summary(report: dict[str, Any], report_path: Path) -> None:
    summary = report["summary"]
    print()
    print(f"deterministic: {summary['deterministic_passed']}/{summary['total']} passed")
    if summary["failure_classes"]:
        print("failure classes: " + ", ".join(f"{k}={v}" for k, v in sorted(summary["failure_classes"].items())))
    print(f"report: {report_path}")


def main() -> None:
    raise SystemExit(_run(_parse_args()))


if __name__ == "__main__":
    main()
