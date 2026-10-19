"""HW4 strategies: direct, think-and-vote, and bounded tool use."""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from tools import FIELDS, calculate, lookup

load_dotenv()
MODEL = "gpt-5.6-luna"
MAX_ROUNDS = 12
CORPUS_PATH = Path(__file__).resolve().parent / "data" / "corpus.jsonl"

TOOLS = [
    {"type": "function", "function": {
        "name": "lookup",
        "description": ("Retrieve one current numeric fact for a district. Uses 2025 district "
                        "profiles rather than archived 2019 records, applies correction "
                        "notices, converts water use to cubic metres, and returns source IDs. "
                        "Look up each fact needed; never guess a number."),
        "parameters": {"type": "object", "properties": {
            "district": {"type": "string", "description": "Exact district name"},
            "field": {"type": "string", "enum": list(FIELDS)}},
            "required": ["district", "field"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "calculate",
        "description": "Safely compute arithmetic with numbers, parentheses, +, -, *, /. Use for ratios, differences, percentages and unit conversions.",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "Numeric arithmetic expression"}},
            "required": ["expression"], "additionalProperties": False}}},
]

ANSWER_RE = re.compile(r"ANSWER:\s*([^|\n]+?)\s*\|\s*([-+]?\d[\d,]*(?:\.\d+)?)", re.I)


def parse_answer(text):
    matches = list(ANSWER_RE.finditer(text or ""))
    if not matches:
        return None
    match = matches[-1]
    try:
        return {"name": match.group(1).strip(), "value": float(match.group(2).replace(",", ""))}
    except ValueError:
        return None


def answer_format(decimals):
    return ("Finish with a separate final line exactly in this format: "
            "ANSWER: <district name or yes/no> | <number>. "
            f"Round the final number to exactly {decimals} decimal place(s), "
            "using the requested unit. No unit after the number on the ANSWER line. "
            "Keep full precision in intermediate arithmetic. "
            "Never use or request any ground-truth answer.")


def _usage(response):
    usage = response.usage
    return (usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0)


def _complete(client, messages, **kwargs):
    # Chat Completions with function tools requires reasoning_effort='none' for this model.
    return client.chat.completions.create(model=MODEL, messages=messages,
                                          reasoning_effort="none", **kwargs)


def _base_record(q, strategy):
    return {"question_id": q["id"], "question": q["question"],
            "strategy": strategy, "model": MODEL, "answer": None,
            "parsed": None, "unanswered": True, "rounds": 0,
            "input_tokens": 0, "output_tokens": 0, "total_seconds": 0.0}


def _corpus_text():
    with CORPUS_PATH.open(encoding="utf-8") as f:
        docs = [json.loads(line) for line in f if line.strip()]
    return "\n\n".join(f"[{d['id']}] {d['title']}\n{d['text']}" for d in docs)


def _one_sample(client, q, corpus, think=False):
    instruction = ("Answer using only the provided fictional documents. "
                   "Prefer current profiles over archived records and apply later corrections. "
                   "Convert units correctly. ")
    if think:
        instruction += "Reason step by step and check the arithmetic. "
    instruction += answer_format(q["decimals"])
    messages = [{"role": "system", "content": instruction},
                {"role": "user", "content": f"DOCUMENTS:\n{corpus}\n\nQUESTION:\n{q['question']}"}]
    start = time.perf_counter()
    response = _complete(client, messages)
    duration = time.perf_counter() - start
    inp, out = _usage(response)
    content = response.choices[0].message.content or ""
    return {"answer": content, "parsed": parse_answer(content),
            "input_tokens": inp, "output_tokens": out, "seconds": duration}


def run_s1(q, client=None):
    client = client or OpenAI()
    record = _base_record(q, "S1")
    sample = _one_sample(client, q, _corpus_text())
    record.update(sample)
    record["total_seconds"] = sample.pop("seconds")
    record["rounds"] = 1
    record["unanswered"] = record["parsed"] is None
    return record


def run_s2(q, client=None):
    client = client or OpenAI()
    record = _base_record(q, "S2")
    corpus = _corpus_text()
    start = time.perf_counter()
    samples = [_one_sample(client, q, corpus, think=True) for _ in range(5)]
    record["samples"] = samples
    record["rounds"] = 5
    record["input_tokens"] = sum(s["input_tokens"] for s in samples)
    record["output_tokens"] = sum(s["output_tokens"] for s in samples)
    record["total_seconds"] = time.perf_counter() - start
    # Vote on the complete normalized answer, not on name alone.
    votes = []
    for s in samples:
        parsed = s["parsed"]
        if parsed:
            votes.append((parsed["name"].casefold(), round(parsed["value"], q["decimals"])))
    if votes:
        winner, count = Counter(votes).most_common(1)[0]
        record["parsed"] = {"name": winner[0], "value": winner[1]}
        record["answer"] = f"ANSWER: {winner[0]} | {winner[1]:.{q['decimals']}f}"
        record["votes_for_winner"] = count
        record["unanswered"] = False
    return record


def _dispatch(name, raw_arguments):
    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Arguments must be a JSON object")
        if name == "lookup":
            if set(arguments) != {"district", "field"}:
                raise ValueError("lookup requires district and field only")
            if not all(isinstance(arguments[k], str) for k in arguments):
                raise ValueError("lookup arguments must be strings")
            return lookup(**arguments)
        if name == "calculate":
            if set(arguments) != {"expression"} or not isinstance(arguments["expression"], str):
                raise ValueError("calculate requires a string expression only")
            return calculate(**arguments)
        return {"error": f"Unknown tool: {name}"}
    except (ValueError, TypeError) as exc:
        return {"error": f"Invalid tool arguments: {exc}"}
    except Exception as exc:
        return {"error": f"Tool execution failed: {type(exc).__name__}"}


def run_s3(q, client=None, vague=False):
    client = client or OpenAI()
    record = _base_record(q, "S3-vague" if vague else "S3")
    record["tool_calls"] = []
    tools = json.loads(json.dumps(TOOLS))
    if vague:
        tools[0]["function"]["description"] = "Looks things up."
    messages = [
        {"role": "system", "content": (
            "Answer questions about Nine Rivers Region using lookup for facts and "
            "calculate for arithmetic. No documents are provided in this prompt. "
            "Do not guess facts. " + answer_format(q["decimals"]))},
        {"role": "user", "content": q["question"]}
    ]
    start = time.perf_counter()
    for round_no in range(1, MAX_ROUNDS + 1):
        response = _complete(client, messages, tools=tools)
        record["rounds"] = round_no
        inp, out = _usage(response)
        record["input_tokens"] += inp
        record["output_tokens"] += out
        message = response.choices[0].message
        # Preserve assistant tool-call message including original IDs/arguments.
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            record["answer"] = message.content or ""
            record["parsed"] = parse_answer(record["answer"])
            record["unanswered"] = record["parsed"] is None
            break
        for call in message.tool_calls:
            t0 = time.perf_counter()
            result = _dispatch(call.function.name, call.function.arguments)
            duration = time.perf_counter() - t0
            record["tool_calls"].append({
                "round": round_no, "id": call.id, "name": call.function.name,
                "arguments": call.function.arguments, "result": result,
                "seconds": duration})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    # A tool-call message on the final permitted round has no subsequent model answer.
    # It is deliberately marked unanswered; all issued tool calls have still been answered.
    record["total_seconds"] = time.perf_counter() - start
    record["hit_round_limit"] = record["unanswered"] and record["rounds"] == MAX_ROUNDS
    return record


def grade(record, q):
    parsed = record.get("parsed")
    if parsed is None:
        return False
    # One last-digit tolerance, as allowed in the assignment.
    tolerance = 10 ** (-int(q["decimals"])) + 1e-9
    return (parsed["name"].casefold() == q["answer"].casefold()
            and abs(parsed["value"] - float(q["value"])) <= tolerance)
