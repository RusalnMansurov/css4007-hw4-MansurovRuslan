# HW4 — submission

**AI assistance:** I used ChatGPT (OpenAI) to help plan the decomposition, draft the tool schemas, and format Part A. I reviewed the source documents and used ChatGPT to help draft and review the code and report; all experimental measurements below come from my saved runs. I checked the tool tests and the analysis against the supplied logs.

---

## Part A — Plan on paper (Easy)

> Complete and commit this section before creating any `.py` files.

### A1 — The steps

| #  | Step                       | What goes in (types)                 | What comes out (types)                                                           | When it goes wrong                                                                      | Tool, loop, or model? |
| :- | :------------------------- | :----------------------------------- | :------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------- | :-------------------- |
| 1  | Parse question             | `question: str`                      | district names `list[str]`, requested metrics `list[str]`, comparison rule `str` | If the request is ambiguous, ask for clarification or mark unanswered                   | Model                 |
| 2  | Request facts              | `district: str`, `field: str`        | Tool request with JSON arguments                                                 | Missing field or district: try a corrected request                                      | Model                 |
| 3  | Validate and retrieve      | `district: str`, `field: str`        | `dict` with `district`, `field`, `value`, `unit`, `sources`                      | Unknown/misspelled/ambiguous name, unavailable field, unreadable data: structured error | `lookup` tool         |
| 4  | Resolve versions and units | Matched source text and field        | Corrected, unit-labelled value and source IDs                                    | Exclude archived records; apply later correction notices; reject unknown units          | `lookup` tool         |
| 5  | Plan arithmetic            | Fact dictionaries and question       | Arithmetic expression `str`                                                      | Missing or inconsistent values: request facts again                                     | Model                 |
| 6  | Evaluate expression        | `expression: str`                    | `dict` containing `result: float` or `error: str`                                | Reject unsafe syntax, division by zero, excessive magnitude or complexity               | `calculate` tool      |
| 7  | Compare and answer         | Calculated numbers, question         | `ANSWER: <name or yes/no> \| <number>` as a single plain-text line               | If no defensible result, mark unanswered instead of guessing                            | Model                 |
| 8  | Dispatch and log           | Conversation messages and tool calls | Updated messages, one response per call ID, timed log entries                    | Invalid JSON arguments: return tool error; stop at round limit                          | Loop                  |

**How the steps connect:**

`Question -> model parses -> lookup(district, field) -> validated/corrected facts -> model constructs expressions -> calculate(expression) -> model compares -> final ANSWER line.`

The loop surrounds these steps: it receives each model response, dispatches every requested tool call, appends the results, records elapsed time, and repeats until a final answer or a round limit is reached.

**What I am assuming:**

- Use the 2025 census for population, the 2025 fiscal year for budgets, calendar 2025 for household water use, and 2025/26 for school enrolment unless a question explicitly requests another period. Use 2020 census figures when calculating historical growth.
- NR-13 and NR-14 are archived 2019 profiles and must not override the current Tarnvale (NR-03) and Kelmarsh (NR-04) profiles.
- NR-16 corrects Brightwater's area in NR-05 from 912.4 to **921.4 km²**; correction notices take precedence.
- District matching must use complete names, not substrings: Ossbridge and Ossford are distinct. A typo may receive suggestions, but the tool must not silently substitute another district.
- Normalize water volumes when necessary: 1 megalitre = 1,000 cubic metres; 1 cubic metre = 1,000 litres. NR-08 reports water in cubic metres. Use NR-15's **2025** tariff when a question asks for household water cost.
- Keep full numeric precision for intermediate calculations and round only the final reported result to the precision required by the question. Do not give the model the reference answers from `questions.json`.
- Every successful lookup must identify the document(s) supporting the value. An unavailable fact is an error, not a fabricated zero.
- The tool schemas below are the exact interfaces planned for Part B. If implementation details change, explain them in Part B without rewriting this committed plan.

### A2 — The two tools

#### `lookup`

```json
{
  "type": "function",
  "function": {
    "name": "lookup",
    "description": "Find one factual field for an exact Nine Rivers district in the current profiles. Use this before calculations; the tool excludes archived profiles, applies correction notices, normalizes water volume to cubic metres, and returns source document IDs. Request separate fields separately. Never guess missing values or confuse similarly named districts.",
    "parameters": {
      "type": "object",
      "properties": {
        "district": {
          "type": "string",
          "description": "Full district name, e.g. Brightwater or Millbrook."
        },
        "field": {
          "type": "string",
          "enum": ["population_2020", "population_2025", "area_km2", "founded_year", "budget_2025_million_tenge", "water_2025_m3", "schools_2025_26", "pupils_2025_26", "paved_road_km", "water_tariff_2025_tenge_per_m3"],
          "description": "The specific fact to retrieve. The water tariff is region-wide and may be requested with any valid district."
        }
      },
      "required": ["district", "field"],
      "additionalProperties": false
    }
  }
}

```

| Case                               | What the tool returns                                                                                                                    |
| :--------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------- |
| Success                            | `{"district":"Brightwater","field":"area_km2","value":921.4,"unit":"km2","sources":["NR-05","NR-16"]}`                                   |
| Unknown/misspelled district        | `{"error":"Unknown district: Ossbrige","suggestions":["Ossbridge"]}`                                                                     |
| Similar names                      | Exact full-name matching; `Ossbridge` and `Ossford` each return their own record; an ambiguous prefix produces an error with suggestions |
| Archived record                    | Skip NR-13/NR-14, use the 2025 replacement profile; return an error if no current value exists                                           |
| Corrected record                   | Apply NR-16 over NR-05 and include both source IDs                                                                                       |
| Missing field or unreadable number | `{"error":"Field unavailable or invalid","detail":"Field unavailable or invalid"}`                                                                |
| Unsupported field or invalid input | `{"error":"Unsupported field or invalid arguments"}`                                                                                     |

**Worst thing it could do with strange or dangerous input:** An excessively long or adversarial search string could waste CPU or return an incorrect match if validation is weak. I will restrict accepted fields, limit input length and output size, use a fixed local corpus, avoid executing document content, and return structured errors rather than crashing. The JSON schema describes the request shape; the Python tool also validates arguments at runtime.

#### `calculate`

```json
{
  "type": "function",
  "function": {
    "name": "calculate",
    "description": "Safely evaluate a basic arithmetic expression using numeric constants, parentheses, +, -, *, and /. Use for all arithmetic, including rates, percentage changes, unit conversions, and differences. No variables, functions, attributes, or exponentiation are permitted. Returns a number or a structured error.",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {
          "type": "string",
          "description": "Arithmetic expression, e.g. (37203 / 758.6) - (44675 / 921.4)."
        }
      },
      "required": ["expression"],
      "additionalProperties": false
    }
  }
}

```

| Case                                                          | What the tool returns                          |
| :------------------------------------------------------------ | :--------------------------------------------- |
| Success                                                       | `{"result":4}` for expression `2 + 2`          |
| Division by zero                                              | `{"error":"Division by zero"}`                 |
| Unsafe expression, e.g. `__import__('os')` or `(1).__class__` | `{"error":"Unsupported expression"}`           |
| Exponentiation, e.g. `9**9**9`                                | `{"error":"Unsupported operator"}`             |
| Invalid syntax                                                | `{"error":"Invalid arithmetic expression"}`    |
| Nonnumeric constant (e.g. `"hello"`)                         | `{"error":"Unsupported expression"}`           |
| Excessively long expression or excessive intermediate result  | `{"error":"Expression exceeds safety limits"}` |

**Worst thing it could do with strange or dangerous input:** Without restrictions, a parser or evaluator could consume too much time or memory, or run arbitrary Python. I will parse with `ast`, explicitly allow only numeric constants, unary plus/minus and binary `+`, `-`, `*`, `/`, cap expression length, AST node count and intermediate magnitude, and reject all other node types. I will not use `eval` or `exec`. The JSON schema describes the request shape; the Python evaluator enforces the safety limits at runtime.

### A3 — Q-09 by hand

**Question:** Which district has the higher population density in 2025, Brightwater or Millbrook, and by how many residents per square kilometre?

The following is a **planned example conversation**, not a real API log. Each assistant tool call has an ID, and every tool response references that ID. For readability, this shows the message content and tool-call payloads; the actual API message objects may include additional metadata.

```json
[
  {
    "role": "user",
    "content": "Which district has the higher population density in 2025, Brightwater or Millbrook, and by how many residents per square kilometre?"
  },
  {
    "role": "assistant",
    "content": null,
    "tool_calls": [
      {"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\"district\":\"Brightwater\",\"field\":\"population_2025\"}"}},
      {"id":"call_2","type":"function","function":{"name":"lookup","arguments":"{\"district\":\"Brightwater\",\"field\":\"area_km2\"}"}},
      {"id":"call_3","type":"function","function":{"name":"lookup","arguments":"{\"district\":\"Millbrook\",\"field\":\"population_2025\"}"}},
      {"id":"call_4","type":"function","function":{"name":"lookup","arguments":"{\"district\":\"Millbrook\",\"field\":\"area_km2\"}"}}
    ]
  },
  {"role":"tool","tool_call_id":"call_1","content":"{\"district\":\"Brightwater\",\"field\":\"population_2025\",\"value\":44675,\"unit\":\"residents\",\"sources\":[\"NR-05\"]}"},
  {"role":"tool","tool_call_id":"call_2","content":"{\"district\":\"Brightwater\",\"field\":\"area_km2\",\"value\":921.4,\"unit\":\"km2\",\"sources\":[\"NR-05\",\"NR-16\"]}"},
  {"role":"tool","tool_call_id":"call_3","content":"{\"district\":\"Millbrook\",\"field\":\"population_2025\",\"value\":37203,\"unit\":\"residents\",\"sources\":[\"NR-11\"]}"},
  {"role":"tool","tool_call_id":"call_4","content":"{\"district\":\"Millbrook\",\"field\":\"area_km2\",\"value\":758.6,\"unit\":\"km2\",\"sources\":[\"NR-11\"]}"},
  {
    "role":"assistant",
    "content":null,
    "tool_calls":[
      {"id":"call_5","type":"function","function":{"name":"calculate","arguments":"{\"expression\":\"44675 / 921.4\"}"}},
      {"id":"call_6","type":"function","function":{"name":"calculate","arguments":"{\"expression\":\"37203 / 758.6\"}"}},
      {"id":"call_7","type":"function","function":{"name":"calculate","arguments":"{\"expression\":\"(37203 / 758.6) - (44675 / 921.4)\"}"}}
    ]
  },
  {"role":"tool","tool_call_id":"call_5","content":"{\"result\":48.48599956587801}"},
  {"role":"tool","tool_call_id":"call_6","content":"{\"result\":49.041655681518584}"},
  {"role":"tool","tool_call_id":"call_7","content":"{\"result\":0.5556561156405735}"},
  {"role":"assistant","content":"ANSWER: Millbrook | 0.6"}
]

```

**My arithmetic:**

- Brightwater: 44,675 residents (NR-05), corrected area 921.4 km² (NR-05 + NR-16). Density = `44675 / 921.4` residents/km².
- Millbrook: 37,203 residents and 758.6 km² (NR-11). Density = `37203 / 758.6` residents/km².
- Difference = `(37203 / 758.6) - (44675 / 921.4)` ≈ **0.56**, which rounds to **0.6** residents/km² (one decimal place).
- Therefore: `ANSWER: Millbrook | 0.6`.

### A4 — Why I split it this way

An alternative would be to create one large `answer_question(question)` tool that retrieves the data, chooses the formulas, calculates, and writes the answer itself. I did not choose that because it hides the distinction between retrieval and arithmetic, makes individual failures harder to diagnose, and gives the model less visibility into the evidence. Two small tools are easier to test independently, and their source IDs and arithmetic outputs can be inspected in the logs. This separation also makes it possible to evaluate whether tool use improves accuracy over direct prompting and voting.

---

## Part B — Build it and run it (Medium)

### What I changed from Part A while coding

The implementation follows the two-tool design in Part A. `lookup` returns numeric values, units, and document IDs from the local corpus; `calculate` evaluates restricted arithmetic expressions with Python's AST rather than `eval`. The actual Chat Completions interface uses `reasoning_effort="none"` because this model/endpoint combination does not support function tools with reasoning enabled. The model sometimes chooses a different order or grouping of tool calls than my handwritten example. The dispatcher handles JSON arguments, returns a response for each tool-call ID, and records errors and usage. The numeric grading tolerance is defined before the full runs.

### B2 — Tests

The original local tool test suite was run with `python -m pytest -q`, and **13 tests passed**. Six additional offline regression tests were added during final review. The tests exercise the local tools without making API calls. They include valid retrieval/arithmetic and error/safety cases. This result is evidence that the tested behaviors work; it does not prove every possible district, unit conversion, or adversarial expression is correct. For exact test-by-test coverage, see `tests/test_tools.py`. The test output from the final reviewed project is included below.

**Final local pytest output:**

```text
...................                                                      [100%]
19 passed
```

### B3 — The loop

- **Round limit, and why:** Maximum 12 API rounds per question, to avoid infinite tool-call loops and unbounded cost while permitting several lookup and calculation steps.
- **Where the logs are:** `outputs/20261019_172520/run_1/S3/Q-09.json` (and the corresponding per-question JSON files for both runs). The pilot and ablation logs are under `outputs/20261019_171911/`, `outputs/20261019_174138/`, and `outputs/20261019_175230/`.
- **Real Q-09 conversation compared with my A3 version, what is different:** In run 1, the model completed Q-09 in **4 rounds**, making **7 calls**: 4 `lookup` calls in round 1, 2 `calculate` calls in round 2, and 1 `calculate` call in round 3, followed by the final answer in round 4. It retrieved Brightwater population 44,675 and corrected area 921.4 km² (NR-05/NR-16), and Millbrook population 37,203 and area 758.6 km² (NR-11). The calculations returned 48.48599956587801, 49.041655681518584, and a difference of 0.5556561156405735. Final output: `ANSWER: Millbrook | 0.6`. The handwritten A3 version groups all three calculations in one assistant turn; the real run separated the final difference into an additional round. Real tool-call IDs were generated by the API rather than the illustrative `call_1` etc. Run-1 Q-09 used **2,817 input tokens**, **228 output tokens**, and **6.36 seconds**. No round limit was hit.

### B4 — Three strategies

**What I kept the same:** All strategies used the same 20 questions, the same model (`gpt-5.6-luna`), the same answer format, the same reference answers for grading, and the same grading tolerance. S1 received the full corpus directly in its prompt; S2 used five independently sampled answers and a vote; S3 accessed facts through `lookup` and performed arithmetic through `calculate`, without including the full corpus in the question prompt. All strategies were run twice. Chat Completions was used with `reasoning_effort="none"`.

**My rule for a correct answer** (written before the first full run): A response is considered correct if the final `ANSWER:` line contains the correct district/yes-no label (case-insensitive) and its numeric value differs from the reference value by no more than one unit in the last required decimal place (`10^(-decimals)`). Missing or unparseable answers are incorrect. This is a *tolerance-based* metric, not strict equality after rounding: for example, Q-09's 0.5, 0.6, and 0.7 can all pass when the reference is 0.6.

| Run | Strategy | Correct /20 | Unanswered | Input tokens | Output tokens | Median seconds per question | Total seconds | Cost ($) |
| :-- | :------- | ----------: | ---------: | -----------: | ------------: | --------------------------: | ------------: | -------: |
| 1 | S1 (direct) | 9/20 | 0 | 44,360 | 899 | 1.38 | 30.59 | N/A* |
| 1 | S2 (think-and-vote, k=5) | 16/20 | 0 | 222,700 | 11,108 | 9.72 | 195.49 | N/A* |
| 1 | S3 (tools) | 18/20 | 0 | 43,974 | 4,255 | 5.10 | 111.42 | N/A* |
| 2 | S1 (direct) | 12/20 | 0 | 44,360 | 1,056 | 1.51 | 29.95 | N/A* |
| 2 | S2 (think-and-vote, k=5) | 15/20 | 0 | 222,700 | 10,839 | 9.06 | 190.68 | N/A* |
| 2 | S3 (tools) | 19/20 | 0 | 45,078 | 4,336 | 5.00 | 100.35 | N/A* |

*Cost is N/A because the experimental summaries store `cost_usd: null`. No verified per-token pricing or billing export was supplied, so a dollar amount cannot be supported from these files alone. The recorded token counts are available for later cost calculation.*

**Per question (run 1):** ✓ = graded correct; ✗ = graded incorrect.

| Question | S1 | S2 | S3 |
| :------- | :-: | :-: | :-: |
| Q-01 | ✓ | ✓ | ✓ |
| Q-02 | ✓ | ✓ | ✓ |
| Q-03 | ✗ | ✓ | ✗ |
| Q-04 | ✗ | ✓ | ✓ |
| Q-05 | ✗ | ✗ | ✓ |
| Q-06 | ✗ | ✓ | ✓ |
| Q-07 | ✗ | ✓ | ✓ |
| Q-08 | ✓ | ✓ | ✓ |
| Q-09 | ✓ | ✓ | ✓ |
| Q-10 | ✓ | ✓ | ✓ |
| Q-11 | ✗ | ✓ | ✗ |
| Q-12 | ✓ | ✗ | ✓ |
| Q-13 | ✓ | ✓ | ✓ |
| Q-14 | ✗ | ✓ | ✓ |
| Q-15 | ✗ | ✗ | ✓ |
| Q-16 | ✓ | ✓ | ✓ |
| Q-17 | ✗ | ✓ | ✓ |
| Q-18 | ✗ | ✓ | ✓ |
| Q-19 | ✓ | ✓ | ✓ |
| Q-20 | ✗ | ✗ | ✓ |

**Summary across both runs:** S1 = 21/40 (52.5%), S2 = 31/40 (77.5%), S3 = 37/40 (92.5%). S1 is the fastest but least accurate. S2 improves accuracy at substantially higher token and time cost. S3 is the most accurate in this experiment, with intermediate runtime and fewer input tokens than S2. The two runs are repeated measurements on the same questions, not 40 independent questions.

---

## Part C — Explain and choose (Hard)

### C1 — Does the tool description matter?

I tested **five questions** (Q-03, Q-05, Q-09, Q-11, Q-14), comparing S3 in full run 1 with S3 using the vague `lookup` description `"Looks things up."`. All other configured settings were unchanged, but the responses were generated in separate stochastic runs, so differences cannot be attributed solely to the description.

| Question | Calls (informative) | Calls (vague) | Answer (informative) | Answer (vague) |
| :-- | --: | --: | :-- | :-- |
| Q-03 | 5 | 5 | Lindenmoor / 1949.2 (wrong) | Lindenmoor / 1949.2 (wrong) |
| Q-05 | 7 | 5 | Coldharbour / 14235 (correct) | Coldharbour / 14235 (correct) |
| Q-09 | 7 | 7 | Millbrook / 0.6 (correct) | Millbrook / 0.6 (correct) |
| Q-11 | 10 | 7 | Ashgrove / 2.59 (wrong) | Redfen / 53.51 (wrong) |
| Q-14 | 6 | 5 | Ashgrove / 4.9 (correct) | Ashgrove / 1771.3 (wrong) |
| **Total** | **35** | **29** | **3/5 correct** | **2/5 correct** |

**Changes in inputs:** Q-03 and Q-09 used the same four fact lookups and the same essential calculations in both conditions; Q-03 changed the syntactic arrangement of its calculation but still omitted `/365`. Q-05 used the same four lookups, but the informative version called `calculate` three times (two individual budget-per-person rates and the difference), while the vague version called it once for the difference. Q-11 used the same six lookups, but the informative version made four calculation calls and the vague version only one; both compared the wrong pair at some point. Q-14 used the same four lookups, but the informative version calculated two daily rates with `/365`, whereas the vague version calculated an annual difference without `/365`. Thus, fewer calls did not imply a better answer. Accuracy fell from 60% to 40% on this small sample, but stochastic variability and sample size prevent a causal conclusion.

**Log locations:** Informative: `outputs/20261019_172520/run_1/S3/`; vague: `outputs/20261019_174138/run_1/S3_vague/` for Q-03/Q-09/Q-11 and `outputs/20261019_175230/run_1/S3_vague/` for Q-05/Q-14.

### C2 — Three mistakes explained

**1. S1, run 1, Q-07 — contradictory comparison.** The model wrote: `Coldharbour: 52.45 km; Stonecairn: 33.95 km; difference: 18.50`, but then concluded `ANSWER: Stonecairn | 18.50`. The numeric difference was close, but the selected district contradicted its own evidence: Coldharbour has the larger road length per 1,000 residents. Without a structured comparison/check step, direct generation can fail even when the necessary numbers appear in the response.

**2. S2, run 1, Q-05 — voting did not fix the budget-per-resident error.** The voted result was `ANSWER: coldharbour | 14343`, which the grader marked incorrect. The correct label was Coldharbour, but the numeric difference was outside the permitted tolerance (the tool strategy returned approximately 14,235). Repeated sampling and majority voting can suppress isolated mistakes, but cannot reliably remove a shared arithmetic or interpretation error. The aggregate S2 result was 16/20 in run 1 and 15/20 in run 2, demonstrating that five votes are not a guarantee.

**3. S3, run 1, Q-03 — missing per-day conversion.** The model retrieved Lindenmoor water = 2,151,400 m³ and population = 29,051, plus Kelmarsh water = 1,739,000 m³ and population = 24,117. It sent `calculate` the expression `(2151400/29051-1739000/24117)*1000`, receiving approximately `1949.16`. This is a difference in litres per resident **per year**, not per day. The correct expression must also divide by 365, yielding approximately 5.3 litres per resident per day. The model answered `Lindenmoor | 1949.2`. The safe calculator executed its input correctly; the model supplied the wrong formula.

**Why tools help, but not perfectly:** A calculator prevents arithmetic execution errors for a *correct expression*, while a lookup tool supplies auditable source facts. Neither guarantees that the model chooses the correct comparison or units. The S3 logs show up to four model-response rounds: each new round resends the growing conversation, including prior tool requests and results, increasing cumulative token usage. A unit-aware formula check and a final label-versus-values check would reduce these observed failure modes.

### C3 — Which strategy I would use, and what would change it

I would use **S3 (tools)** for this district-question task. It scored **18/20** and **19/20** across the two runs, versus **9/20 and 12/20** for S1 and **16/20 and 15/20** for S2. The separation between fact retrieval and arithmetic makes the outputs auditable: I can see exactly which district records and expressions caused an error. S3 was slower than S1 but faster than S2 in total elapsed time in both runs. I would add unit-aware formula validation and an explicit comparison step before deployment, since the logs show that a safe calculator alone does not prevent wrong formulas.

I might choose **S1** if response time and low token usage mattered more than precision and occasional errors were acceptable, especially for simple questions. I might choose **S2** when tools were unavailable and a modest accuracy improvement justified a much larger token budget. If a larger, independently sampled evaluation showed that another strategy was more accurate or cheaper under the relevant constraints, I would change the recommendation.

### C4 — How sure I can be

These results provide **useful but limited evidence**. There are only **20 fixed questions**, reused in both runs, so the 40 graded answers per strategy are not 40 independent test items. There were only **two repetitions**, no confidence intervals, and no testing across other models, corpora, or question styles. Some answers fall inside the permitted last-decimal-place tolerance despite not matching the exact rounded reference (for example Q-09). The automated grader checks the final label and number, not whether the prose explanation contradicts the final line. Timings and token counts also depend on the endpoint and execution conditions. The ablation used only five questions and one run per condition, so its result is inconclusive.

I am confident that **S3 performed best on these recorded runs under the specified grader**, but not that it will always outperform the alternatives. A stronger study would use more diverse questions, repeated randomized trials, independent manual grading of borderline cases, explicit cost calculations from verified pricing, and a held-out set that tests corrections, archived documents, and unit conversions.

### Final questions

**1. When the model sends a `tool_calls` message, what has actually run so far?** Only the model inference has run: it has proposed function calls and generated argument strings. My Python dispatcher must validate and execute each call and return a corresponding tool message before the model can use the result.

**2. Why does `arguments` arrive as a string, and what goes wrong if you pass it straight into your function?** The API serializes function-call arguments as JSON text, not as Python keyword arguments. I must parse and validate it; otherwise the function may receive a string instead of the expected `district`/`field` or `expression`, causing incorrect behavior or errors.

**3. How many tokens do your two tool definitions add to every S3 request? Measure it.** **Exact API measurement unavailable in the supplied artifacts:** the saved logs include total input tokens per request sequence, not an isolated token count for the two definitions. The exact incremental cost requires a controlled token-usage measurement with and without the definitions, using the same model and otherwise identical messages. I will not infer an exact number from the total usage.

**4. How much more did S2 cost than S1?** Across two full runs, S2 consumed **445,400 input and 21,947 output tokens**, compared with S1's **88,720 input and 1,955 output tokens**. Thus S2 used about **5.02×** as many input tokens and **11.23×** as many output tokens; the input increase reflects five samples per question. **Dollar cost remains unverified** because the logs store `cost_usd: null` and no billing/pricing evidence was supplied.

**5. Which question needed the most rounds in S3, and why?** The maximum observed was **four rounds**, reached by Q-05, Q-09, Q-15 and Q-20 in run 1, and Q-07, Q-09, Q-11, Q-15 and Q-20 in run 2. There was therefore **a tie**, not a single longest question. Q-09, for example, used one round for four lookups, a second for density calculations, a third for the difference, and a fourth to produce the answer.

**6. What would you change in `lookup` if there were 100,000 documents?** I would index documents by district, field, version, and source ID rather than scanning the entire corpus on each call. I would preserve exact-name matching, archived-document filtering, correction precedence, bounded results, and provenance, and test the index for consistency with the small-corpus implementation.

**7. What stops your `calculate` from running dangerous code? Could the model still make it misbehave?** The tool parses expressions with `ast` and accepts only numeric constants and explicitly permitted arithmetic operators, rejecting attributes, calls, exponentiation, and unsafe syntax. Expression-size and magnitude limits reduce resource-exhaustion risks. The model can still send a mathematically valid but conceptually wrong formula, as it did on Q-03.

**8. For which kind of question did tools help most, and for which did they not help at all?** Tools were especially useful when several facts, corrected records, unit conversions, or multi-step arithmetic were needed: for example, S3 answered Q-05 correctly in both runs while S1 and S2 failed it in both. They did not improve Q-01, Q-02, Q-08, Q-09, Q-10, Q-13, Q-16, or Q-19 in run 1 because all three strategies already passed those questions. Q-03 and Q-14 also show that tool access alone does not prevent wrong unit-conversion formulas.


### Reproducibility and review notes

- The recorded full-run and ablation logs are historical evidence from the original implementation and were not regenerated after adding offline regression tests.
- No API key is included in this submission archive. A local `.env` file must be created from `.env.example` to rerun model experiments.
- Dollar cost and isolated API token overhead of tool definitions cannot be derived exactly from the existing logs alone; those values remain explicitly unverified rather than fabricated.
- The historical grader allows one unit of tolerance in the final required decimal place, which is more permissive than exact rounded equality.
