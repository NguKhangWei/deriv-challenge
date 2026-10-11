# Debug report

Mode: `offline` | classifier: `rules` | retriever: `bm25` | generator: `extractive`

| Ticket | Intent | Retrieved articles | Route | Confidence | Escalate | Why |
|---|---|---|---|---|---|---|
| T1 | deposit_issue | A1 – Card deposit processing times | grounded | 0.92 | no | confident grounded answer (confidence 0.92) with no policy or account-specific risk |
| T2 | password_reset | A2 – Password reset troubleshooting | grounded | 0.94 | no | confident grounded answer (confidence 0.94) with no policy or account-specific risk |
| T3 | withdrawal_issue | A3 – Withdrawal review and declines | grounded | 0.90 | yes | answer requires the client&#x27;s own account records; policy-sensitive topic (withdrawal_issue) |
| T4 | trading_advice_request | A5 – No investment or trading advice | refusal | 0.95 | no | trading-advice request refused per policy; refusal is fixed, so no escalation |
| T5 | account_lock | A4 – Account lock after failed sign-in attempts | grounded | 0.94 | no | confident grounded answer (confidence 0.94) with no policy or account-specific risk |

## Per-ticket detail

### T1

- Message: &#x27;I deposited by bank card two hours ago but my balance still shows zero. What should I check?&#x27;
- Classifier (rules): deposit_issue, certainty 1.0; top intent deposit_issue=5.0, runner-up account_lock=0
- Retrieval scores: A1 (Card deposit processing times) = 5.2246
- Generator: extractive
- Grounding coverage: 1.0
- Confidence breakdown: {'classifier': 1.0, 'retrieval': 0.7232, 'agreement': 1.0, 'grounding': 1.0}
- Flags: []

### T2

- Message: &#x27;I forgot my password and I am not receiving the reset email.&#x27;
- Classifier (rules): password_reset, certainty 1.0; top intent password_reset=9.0, runner-up account_lock=0
- Retrieval scores: A2 (Password reset troubleshooting) = 7.7774
- Generator: extractive
- Grounding coverage: 1.0
- Confidence breakdown: {'classifier': 1.0, 'retrieval': 0.7954, 'agreement': 1.0, 'grounding': 1.0}
- Flags: []

### T3

- Message: &#x27;Why was my withdrawal declined after verification?&#x27;
- Classifier (rules): withdrawal_issue, certainty 0.95; top intent withdrawal_issue=4.5, runner-up account_lock=0
- Retrieval scores: A3 (Withdrawal review and declines) = 5.2873
- Generator: extractive
- Grounding coverage: 1.0
- Confidence breakdown: {'classifier': 0.95, 'retrieval': 0.7255, 'agreement': 1.0, 'grounding': 1.0}
- Flags: ['account_specific_request', 'policy_sensitive']

### T4

- Message: &#x27;Can you tell me which asset will go up today so I can make profit?&#x27;
- Classifier (rules:advice): trading_advice_request, certainty 1.0; matched a trading-advice / prediction request pattern
- Retrieval scores: A5 (No investment or trading advice) = 3.0224
- Generator: template:refusal
- Grounding coverage: 1.0
- Confidence breakdown: fixed for this route
- Flags: ['advice_request']

### T5

- Message: &#x27;My account was locked after too many login attempts. What do I do now?&#x27;
- Classifier (rules): account_lock, certainty 1.0; top intent account_lock=6.0, runner-up deposit_issue=0
- Retrieval scores: A4 (Account lock after failed sign-in attempts) = 7.3358
- Generator: extractive
- Grounding coverage: 1.0
- Confidence breakdown: {'classifier': 1.0, 'retrieval': 0.7858, 'agreement': 1.0, 'grounding': 1.0}
- Flags: []

