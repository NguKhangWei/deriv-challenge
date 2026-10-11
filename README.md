# Support Ticket Triage Pipeline

A small, deterministic service that classifies support tickets, retrieves help-center
articles, and drafts grounded **notes for the support agent** with a confidence score,
an escalation signal and safety flags.

## Quick start

Requires Python 3.10+.

```bash
pip install -r requirements.txt          # pydantic only
python main.py                           # writes results.json, debug_report.json, debug_report.md
python validate.py                       # checks the artifacts; exits non-zero on failure
```

Tests (optional):

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

### Optional LLM mode (Gemini)

The default mode is fully offline and needs no secrets. Gemini is an **optional fallback
layer** on top of the offline pipeline:

```bash
export GEMINI_API_KEY=...                # PowerShell: $env:GEMINI_API_KEY="..."
export GEMINI_MODEL=gemini-2.5-flash     # optional
python main.py --mode llm
```

- If no key is set, `--mode llm` prints a notice and runs offline.
- The classifier asks Gemini **only when the rules are unsure**. The model can't override
  the trading-advice detector, and its output is limited to the allowed labels.
- Gemini writes the agent notes **only for grounded tickets**. Each line must cite a
  retrieved article and pass the grounding guard. Otherwise the extractive draft is
  used, and the reason is recorded in the debug report.
- Refusals, rejections and fallbacks always use fixed templates and never reach the model.
- Ticket text is redacted (card numbers, emails, phone numbers) and placed inside
  delimiters before it is sent to Gemini.
- Responses are cached under `.cache/llm/`, keyed by a hash of the prompt, so reruns
  give the same output.

## Pipeline

```
LOAD_DATA -> INDEX_KB -> CLASSIFY_INTENT -> RETRIEVE_CONTEXT -> GENERATE_REPLY -> VALIDATE_OUTPUT -> WRITE_RESULTS
```

| Stage | Module | What it does |
|---|---|---|
| LOAD_DATA | `pipeline/loader.py` | Schema-checks inputs. Fails fast on malformed JSON or duplicate IDs. |
| INDEX_KB | `pipeline/retrieve.py`, `runner.py` | Builds the BM25 index. Maps each article to an intent using the same rules (no hardcoded IDs). |
| CLASSIFY_INTENT | `pipeline/screen.py`, `classify.py` | Screens input (empty, language, injection, scope, account-specific). Then: trading-advice detector, then weighted lexicon with margin-based certainty. |
| RETRIEVE_CONTEXT | `pipeline/retrieve.py` | BM25 on ticket text only, with title boost, stemming and synonyms. Returns 0–3 hits above an absolute and a relative score cutoff. Ties break by ID. |
| GENERATE_REPLY | `pipeline/policy.py`, `generate.py`, `guard.py` | Chooses a route. Builds the draft. Checks grounding. Computes confidence and escalation. |
| VALIDATE_OUTPUT | `pipeline/validation.py` | Re-checks every result independently. A failing result is replaced with a safe, escalated fallback. |
| WRITE_RESULTS | `pipeline/report.py` | Atomic writes of `results.json`, `debug_report.json` and `debug_report.md`. |

The classifier, retriever and generator are `Protocol`s injected in `build_pipeline()`
(`pipeline/runner.py`), so each can be swapped on its own. For example, an embedding
retriever can replace BM25 without touching generation.

### Routes

| Route | When | Reply | Escalate |
|---|---|---|---|
| grounded | Known intent and relevant KB hits | KB sentences quoted verbatim with `[A#]` citations, plus cautions | Per the rules below |
| refusal | Trading advice or prediction request (checked before the language label) | Fixed polite refusal backed by the policy article | No, unless the ticket also raises a support issue |
| fallback | In-domain, but no relevant article (or `intent=other`) | "No relevant KB article", with `retrieved_articles: []` | Yes |
| fallback | Unsupported language | Route to an agent who speaks the language | Yes |
| reject | Off-topic (no domain terms) | Direct rejection | No |
| reject | Prompt-injection phrasing | Rejection, with a warning not to act on embedded instructions | Yes (security review) |
| reject | Another person's account, or bulk data about other users | Rejection: only the verified account holder can be served | Yes (security review) |
| reject | Empty message | Asks the client to describe the issue | No |

`reply_draft` is written for the **support agent**. The KB is written in that voice,
so quoting it verbatim keeps every claim traceable. Refusals and rejections also include
a suggested client-facing reply.

### Confidence and escalation (`pipeline/policy.py`)

For grounded answers, confidence is calculated as:

`0.35·classifier certainty + 0.30·retrieval strength + 0.20·intent/article agreement + 0.15·grounding coverage`

A grounded ticket is escalated when any of these hold:
- confidence < 0.55
- two intents score closely, or the top article doesn't match the intent
- the question needs the client's own records (`account_specific_request`)
- the topic is policy-sensitive (withdrawals)
- the draft isn't fully supported by the retrieved articles
- the draft contains an unsupported claim
- the question asks about a topic the retrieved articles don't cover, such as fees or limits

Every decision records its reasons, which appear in the debug report.

### Safety flags

`advice_request`, `insufficient_grounding`, `policy_sensitive`, `low_confidence`,
`account_specific_request`, `conflicting_signals`, `unsupported_claim`,
`unsupported_language`, `out_of_scope`, `prompt_injection_suspected`, `invalid_input`,
`input_truncated`, `validation_failed`, `third_party_request`, `sensitive_data_shared`.

### Security screening (`pipeline/screen.py`, `pipeline/text.py`)

- **Obfuscation is undone before any rule runs.** This covers full-width text,
  zero-width characters, Cyrillic and Greek look-alike letters, curly quotes and
  s p a c e d - o u t words, so "ignore" spelled with a Cyrillic "о" is still detected.
- **Prompt injection is rejected and escalated.** This includes indirect attempts:
  asking for the instructions or guidelines, "admin mode", fake `</ticket>` tags, and
  "new instructions: …".
- **Requests about other people's data are rejected and escalated.** This covers
  another person's account ("my husband's balance"), other users' IDs, and bulk
  requests ("list all clients…").
- **Questions about the client's own records or account actions are escalated.** These
  include "what email is on my account", "approve/unlock/skip verification on my…".
  The pipeline never invents account facts.
- **Credentials and card data shared in a ticket are flagged.** The draft tells the
  agent not to repeat them, and they are redacted from the debug report and from
  anything sent to Gemini.
- **Blocked tickets never reach the LLM.** Rejected or refused tickets aren't sent to
  the classifier or the generator, and text beyond the 4,000-character cap is never sent.
- **Ticket text isn't echoed into `reply_draft`.** In `debug_report.md` it is
  HTML-escaped and `|`-escaped.

### Grounding guard (`pipeline/guard.py`)

The guard runs in two places: on the draft during generation, and again on the final
text during validation.
- **Line support:** every non-template line must cite a retrieved article, and at least
  60% of its content terms must appear in a single sentence of that article.
- **Unsupported claims:** a pattern scan catches:
  - promised outcomes ("will be credited/approved")
  - guarantees
  - account facts ("your account shows…", "I can see…")
  - market predictions
  - any timeframe not stated in the sources
  - absolute wording not in the sources ("always", "never", "immediately"), which can
    flip a KB sentence's meaning

  The KB gives no processing times, so replies stay vague.

## Outputs

- `results.json`: one object per ticket, in input order, following the required schema.
- `debug_report.md` / `debug_report.json`: for each ticket, the predicted intent and
  classifier scores, retrieved article titles and scores, the route and generator, the
  confidence breakdown, and why it was or wasn't escalated.

## Validation

`python validate.py` checks:
- the required files exist and the JSON is valid
- there is exactly one result per ticket, in order
- each result has the exact schema keys and passes the pydantic model
- article IDs are valid
- every trading-advice request is classified, refused and flagged
- the per-result safety rules hold
- two fresh offline runs are identical
- `results.json` matches a fresh run (when it was produced offline)

The checks don't depend on ticket IDs, so they still apply if `tickets.json` is replaced.

The tests in `tests/` are run with `python -m pytest -q`:

| File | Covers |
|---|---|
| `test_pipeline.py` | Required cases (T4 refused, T1 → A1, schema), traceability, reproducibility |
| `test_ambiguous_intents.py` | Paraphrases, typos and slang; tickets with two topics; advice mixed with a support issue; indirect advice requests; trading words that aren't advice; questions about topics the KB doesn't cover |
| `test_security.py` | Obvious and obfuscated injection; false positives on harmless wording; third-party and bulk data requests; requests for account data or actions; pressure tactics; leaks of secrets and card data; markup injection into the report; adversarial LLM output; LLM isolation |
| `test_irrelevant_requests.py` | Off-topic rejection; empty or content-free input; support questions the KB doesn't cover go to fallback (never rejected, never a weak match) |
| `test_robustness.py` | Language labels and non-English text; Unicode and whitespace; keyword stuffing; truncation; malformed input files; independence from KB and ticket order |
| `test_guard.py`, `test_llm.py`, `test_edge_cases.py` | Unit tests for the guard, validator, loader and Gemini client |

The LLM layer is tested with fake clients, so no network or API key is needed.

## Known limitations

- The rules and lexicon target this domain and KB size. A larger KB would call for
  embedding retrieval, which can be dropped in through the `Retriever` protocol.
- The grounding check is lexical. It catches invented specifics and promises, but not
  every subtle paraphrase that changes the meaning.
- Only English is processed. Other languages are escalated by design. Non-English text
  labelled `en` is caught by a small function-word heuristic, not a full language
  detector.
- Screening uses patterns, so it raises the cost of injection and data-fishing attempts
  but doesn't make them impossible. That is why rejected tickets are escalated and the
  LLM only ever sees screened, redacted text.
