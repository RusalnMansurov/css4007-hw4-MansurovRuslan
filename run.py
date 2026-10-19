"""Run and save HW4 experiments. Test a few questions before --full."""
import argparse
import json
import statistics
import time
from pathlib import Path

from openai import OpenAI
from strategies import MODEL, grade, run_s1, run_s2, run_s3

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"


def load_questions():
    return json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Run all 20 questions")
    parser.add_argument("--runs", type=int, default=1, choices=(1, 2))
    parser.add_argument("--ids", nargs="*", default=["Q-09"], help="Question IDs for a pilot")
    parser.add_argument("--strategies", nargs="+", choices=("S1", "S2", "S3"), default=["S3"])
    parser.add_argument("--vague", action="store_true", help="Use vague lookup description (S3 only)")
    parser.add_argument("--run-label", default=None, help="Unique label for output files")
    args = parser.parse_args()
    questions = load_questions()
    if not args.full:
        questions = [q for q in questions if q["id"] in args.ids]
    if not questions:
        parser.error("No matching questions")
    if args.vague and args.strategies != ["S3"]:
        parser.error("--vague requires --strategies S3")
    label = args.run_label or time.strftime("%Y%m%d_%H%M%S")
    print(f"Model: {MODEL}; questions: {len(questions)}; strategies: {args.strategies}; runs: {args.runs}")
    print("Grading: case-insensitive exact name, numeric tolerance one unit in last decimal place.")
    print("Settings: Chat Completions, reasoning_effort=none; S2 k=5; S3 max_rounds=12.")
    client = OpenAI()
    for run_number in range(1, args.runs + 1):
        for strategy in args.strategies:
            key = strategy + ("_vague" if args.vague else "")
            folder = OUTPUTS / label / f"run_{run_number}" / key
            records = []
            for q in questions:
                print(f"{key} run {run_number}: {q['id']} ...", flush=True)
                try:
                    if strategy == "S1":
                        result = run_s1(q, client)
                    elif strategy == "S2":
                        result = run_s2(q, client)
                    else:
                        result = run_s3(q, client, vague=args.vague)
                    result["correct"] = grade(result, q)
                    result["error"] = None
                except Exception as exc:
                    result = {"question_id": q["id"], "strategy": key, "answer": None,
                              "parsed": None, "unanswered": True, "correct": False,
                              "error": f"{type(exc).__name__}: {exc}", "input_tokens": 0,
                              "output_tokens": 0, "total_seconds": 0, "measurement_incomplete": True}
                records.append(result)
                save_json(folder / f"{q['id']}.json", result)
                print(f"  correct={result['correct']}, answer={result.get('answer')!r}")
            summary = {
                "run": run_number, "strategy": key, "questions": len(records),
                "correct": sum(bool(r["correct"]) for r in records),
                "unanswered": sum(bool(r["unanswered"]) for r in records),
                "input_tokens": sum(r["input_tokens"] for r in records),
                "output_tokens": sum(r["output_tokens"] for r in records),
                "median_seconds_per_question": statistics.median(r["total_seconds"] for r in records),
                "total_seconds": sum(r["total_seconds"] for r in records),
                "cost_usd": None,  # Fill from verified API pricing / actual billing.
                "per_question": {r["question_id"]: r["correct"] for r in records}
            }
            save_json(folder / "summary.json", summary)
            print(json.dumps(summary, indent=2))
    print(f"All logs saved in: {OUTPUTS / label}")


if __name__ == "__main__":
    main()
