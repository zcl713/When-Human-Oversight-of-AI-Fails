# Open questions

- Number of households, trading days and questions per day. Team first discussed 100 x 100; the
  recommended alternative is ~500-1,000 households over ~120-250 shared days, ~40-80 questions per day
  (exposure is a household trait, so runs from one household are not independent). `config/default.yaml`
  holds the recommended values.
- Failure-rate placeholders per Likelihood score (1 = 0.5%, 2 = 1%, 3 = 3%, 4 = 8%, 5 = 15% per exposed run).
- Does Columbia provide WRDS and CRSP access?
- Which capacities K to test (default 5% and 10%).
- Tool inventory and what counts as a "wrong tool" (confirm with Lopa at LPL).
- Which checks to build and how risk is computed (not scaffolded yet). Intent-ambiguity: independent classifier vs rule.
- Exact December 2026 deliverable date.
- Optional later: 200-300 real LLM runs against the mock tools to check injected error signatures look realistic.
