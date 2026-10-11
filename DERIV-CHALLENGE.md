## Problem Statement
Build a small, runnable AI service that processes customer support tickets and produces a structured response draft.

The service should do three things:
1. classify the ticket intent,
2. retrieve the most relevant help-center snippets,
3. generate a grounded reply draft with a confidence flag and escalation signal.

This challenge is designed for an AI Engineer and focuses on practical implementation with clear requirements. The goal is not a perfect model. The goal is a clean, testable pipeline that separates retrieval, prompting, and validation, and that behaves safely on common edge cases.

Assume the product context is a digital platform with account, payments, and trading-related help content. You do not need any company-specific knowledge. Use only the provided local files.

---

## Input / Sample Data
Your solution must read these files from disk:

### `tickets.json`
```json
[
  {
    "id": "T1",
    "message": "I deposited by bank card two hours ago but my balance still shows zero. What should I check?",
    "language": "en"
  },
  {
    "id": "T2",
    "message": "I forgot my password and I am not receiving the reset email.",
    "language": "en"
  },
  {
    "id": "T3",
    "message": "Why was my withdrawal declined after verification?",
    "language": "en"
  },
  {
    "id": "T4",
    "message": "Can you tell me which asset will go up today so I can make profit?",
    "language": "en"
  },
  {
    "id": "T5",
    "message": "My account was locked after too many login attempts. What do I do now?",
    "language": "en"
  }
]
```

### `kb_articles.json`
```json
[
  {
    "article_id": "A1",
    "title": "Card deposit processing times",
    "body": "Card deposits are usually instant, but may be delayed by issuer checks, network issues, or pending review. Ask the client to confirm successful payment, check transaction history, and wait up to the documented processing window before escalating."
  },
  {
    "article_id": "A2",
    "title": "Password reset troubleshooting",
    "body": "If the client does not receive a reset email, confirm the registered email address, check spam folders, wait a few minutes, and retry. Repeated failed attempts may trigger temporary rate limits."
  },
  {
    "article_id": "A3",
    "title": "Withdrawal review and declines",
    "body": "Withdrawals may be declined due to incomplete verification, mismatched payment method details, compliance review, or account restrictions. Support should not promise approval times without checking account status."
  },
  {
    "article_id": "A4",
    "title": "Account lock after failed sign-in attempts",
    "body": "For security reasons, repeated failed sign-in attempts can temporarily lock the account. Advise the client to wait for the cooldown period or use the secure recovery flow. Avoid sharing account-specific details in unsecured channels."
  },
  {
    "article_id": "A5",
    "title": "No investment or trading advice",
    "body": "Support agents must not provide market predictions, profit guarantees, or asset recommendations. Such requests should be politely declined and redirected to general educational resources if available."
  }
]
```

### Required output schema per ticket
```json
{
  "ticket_id": "string",
  "intent": "deposit_issue | password_reset | withdrawal_issue | account_lock | trading_advice_request | other",
  "retrieved_articles": ["article_id"],
  "reply_draft": "string",
  "grounded": true,
  "confidence": 0.0,
  "needs_human_escalation": false,
  "safety_flags": ["string"]
}
```

---

## MUST COMPLETE

### 1. Build a runnable pipeline
Implement a pipeline with clear stages in code:
```text
LOAD_DATA -> INDEX_KB -> CLASSIFY_INTENT -> RETRIEVE_CONTEXT -> GENERATE_REPLY -> VALIDATE_OUTPUT -> WRITE_RESULTS
```

The evaluator should be able to run your solution from a clean checkout.

### 2. Intent classification
Classify each ticket into one of the required intent labels.

You may use rules, embeddings, an LLM, or a hybrid approach. If you use an LLM, keep the output constrained to the allowed labels.

### 3. Retrieval
Retrieve the most relevant knowledge-base articles for each ticket.

Requirements:
- return the top 1 to 3 article IDs,
- use the ticket text as the query,
- keep retrieval separate from reply generation,
- make retrieval deterministic for the same inputs.

A simple TF-IDF, BM25-style approach, or embeddings-based search is acceptable.

### 4. Grounded response generation
Generate a reply draft that is grounded in the retrieved articles.

Requirements:
- the draft must answer the user’s question directly,
- it must not invent account-specific facts,
- it must avoid promises about payment or withdrawal outcomes,
- it must politely refuse trading advice or profit-seeking requests,
- it must be possible to trace the answer back to retrieved content.

If you use an LLM, the prompt must include the retrieved snippets and instruct the model not to answer beyond them.

### 5. Output validation
Validate each result before writing it.

Checks must include:
- required fields exist,
- `intent` is one of the allowed labels,
- `retrieved_articles` contains only known article IDs,
- `confidence` is between 0 and 1,
- trading advice requests are refused,
- unsupported claims trigger either a safety flag or escalation.

### 6. Basic tests
Include at least a few tests or a validation script that checks the pipeline on the sample data.

At minimum, verify:
- T4 is refused as trading advice,
- T1 retrieves the deposit article,
- output JSON matches the required schema.

---

## SHOULD ATTEMPT

### 7. Confidence and escalation logic
Add simple confidence logic based on retrieval quality, classifier certainty, or rule coverage.

Examples:
- low retrieval score,
- conflicting signals,
- no relevant article found,
- policy-sensitive issue like withdrawal review.

Use that logic to set `needs_human_escalation`.

### 8. Safety flags
Add specific safety flags such as:
- `advice_request`
- `insufficient_grounding`
- `policy_sensitive`
- `low_confidence`
- `account_specific_request`

### 9. Explainability artifact
Write a small debug artifact showing, for each ticket:
- predicted intent,
- retrieved article titles,
- why the ticket was escalated or not.

---

## STRETCH

### 10. Swap-friendly model boundary
Structure the code so retrieval and generation components can be swapped independently.

For example:
- rule-based classifier now, LLM classifier later,
- TF-IDF retrieval now, embedding retrieval later,
- mock generator now, hosted model later.

### 11. Lightweight offline fallback
Provide a non-LLM fallback so the pipeline still runs if no model key is available.

---

## Required Artifacts or Expected Outcome
Your repository should produce:
- `tickets.json`
- `kb_articles.json`
- `results.json`
- `debug_report.json` or `debug_report.md`
- `README.md`
- `validate.py` or test files

Each entry in `results.json` must follow the required schema.

---

## Validation Requirements
Include a single command to run the solution, for example:
```bash
python main.py
```

Include a single command to validate it, for example:
```bash
python validate.py
```

The validation should check:
- files exist,
- JSON is valid,
- every ticket has one result,
- required schema is respected,
- T4 is safely refused,
- retrieved article IDs are valid,
- outputs are reproducible for the same inputs.

---

## Tools
Python is required.

Suggested libraries:
- `json`
- `pydantic` or `jsonschema`
- `scikit-learn` for TF-IDF retrieval
- `pytest` if you prefer tests

You may use any LLM provider, but your solution must also be runnable without secrets by providing a fallback path.

---

## Technical Constraints
- Use only the provided local sample files.
- Do not require external APIs for knowledge retrieval.
- Keep retrieval and generation as separate steps.
- Do not hardcode full final answers per ticket.
- Do not provide trading recommendations or market predictions.
- Keep the implementation bounded and production-minded: simple structure, clear interfaces, and basic validation matter more than heavy infrastructure.
- The evaluator may replace the sample tickets with similar ones using the same schema, so avoid logic that depends on exact ticket IDs.