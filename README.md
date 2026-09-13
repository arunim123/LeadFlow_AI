# Autonomous Lead Enrichment Agent

Takes a list of company domains and, for each one, runs an autonomous
Claude agent that explores the company's public website — deciding for
itself which pages are worth reading — and ends by submitting a
structured company intelligence record via a strict tool schema.

```
                    ┌──────────────────────────────────────────┐
                    │              agent.py (per domain)         │
domain ──────────►  │  loop: Claude picks a tool each turn        │  ──► DomainRunResult
                    │   • visit_page   → browser.py + content_    │
                    │                    cleaner.py + link_        │
                    │                    discovery.py (fetch,      │
                    │                    strip, rank links)        │
                    │   • search_web   → tools.py (Tavily, optional)│
                    │   • submit_intelligence → schemas.py          │
                    │                    (validated, self-repairs   │
                    │                    on failure)                │
                    └──────────────────────────────────────────┘
                                       │
                    pipeline.py fans this out across all input
                    domains concurrently (bounded by DOMAIN_CONCURRENCY)
```

This is deliberately **not** a fixed "fetch homepage → grep for /about →
one LLM call" pipeline. Claude gets three tools and a step budget, and
decides turn-by-turn where to look — the same way a human researcher
would click through a site rather than following a hard-coded list of
paths.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium         # downloads the headless browser binary

cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY (required)
```

`TAVILY_API_KEY` in `.env` is optional — it powers the `search_web` tool
the agent can use to resolve a founder's LinkedIn URL when it's not on
the company's own site. Leave it blank and the agent is simply told
that tool is unavailable and proceeds without it.

## Run

```bash
python main.py --domains postman.com supabase.com vapi.ai --output output.json
```

```bash
python main.py --input-file domains.txt --output output.json --concurrency 5
```

or with Docker (no local Python/Playwright setup needed):

```bash
docker build -t lead-enrichment-agent .
docker run --rm -e ANTHROPIC_API_KEY=sk-ant-... \
  -v "$(pwd)/output:/app/output" \
  lead-enrichment-agent --domains postman.com supabase.com vapi.ai --output /app/output/output.json
```

Each run prints a per-domain token/cost table and writes a JSON array,
one record per input domain:

```json
{
  "domain": "postman.com",
  "intelligence": {
    "company_overview": "...",
    "target_audience": "...",
    "contact_points": ["info@postman.com"],
    "leadership": [{"name": "...", "role": "...", "linkedin_url": "...", "source": "site"}],
    "data_confidence_score": 0.85
  },
  "pages_fetched": ["..."],
  "pages_failed": ["..."],
  "tool_calls": 3,
  "errors": [],
  "tokens": {"input": 3120, "output": 480}
}
```

## Architecture

| Module | Responsibility |
|---|---|
| `src/agent.py` | The per-domain tool-calling loop: calls Claude, executes whatever tools it asks for, self-repairs invalid submissions, forces a final answer if the step budget runs out. |
| `src/tools.py` | The three tool schemas (`visit_page`, `search_web`, `submit_intelligence`) and their execution logic. |
| `src/browser.py` | Playwright fetch with retries, UA rotation, stealth patching, classified errors (`blocked`/`not_found`/`timeout`/`server_error`), and a disk-cache check. Exposed behind a `PageFetcher` protocol so tests never need a real browser. |
| `src/content_cleaner.py` | Strips scripts/styles/svg/nav/footer and hard-caps text length — raw HTML never reaches the model. |
| `src/link_discovery.py` | Extracts same-domain links with anchor text, ranked (not filtered) by keyword relevance, for the agent to reason over. |
| `src/llm_client.py` | Async Anthropic client wrapper with retry/backoff on rate limits (honors `Retry-After`) and transient 5xxs. Exposed behind an `LLMClient` protocol for testability. |
| `src/cache.py` | Disk cache for fetched pages, keyed by URL hash — cost/latency optimization only, safe to delete. |
| `src/pipeline.py` | Fans the agent out across all input domains concurrently, bounded by `DOMAIN_CONCURRENCY`; one domain's unhandled exception never affects the rest. |
| `src/cost_tracker.py` | Token usage → estimated USD cost, printed as a summary table. |
| `src/schemas.py` | The Pydantic models — `CompanyIntelligence` doubles as the `submit_intelligence` tool's JSON Schema. |

## Design decisions

- **A real agentic loop, not a scripted pipeline.** The model itself
  decides which pages to visit (via `visit_page`) and when it has
  enough to stop (via `submit_intelligence`). A site that calls its
  team page "The Humans of Acme" instead of `/about` still gets found,
  because the agent is reasoning over link text, not matching a fixed
  keyword list. Multiple tool calls requested in the same turn (e.g.
  "visit /about and /pricing at once") run concurrently.
- **Structured output via forced tool-calling**, not prompted JSON —
  `CompanyIntelligence`'s Pydantic schema *is* the `submit_intelligence`
  tool's `input_schema`, so a response is guaranteed to match the shape.
- **Self-repair on bad submissions.** If `submit_intelligence`'s
  arguments fail Pydantic validation, that's returned to the model as a
  normal tool error (`is_error: true`) with the validation message —
  the same mechanism as any other failed tool call — and the model gets
  a chance to resubmit corrected data in the same run, rather than the
  domain just failing.
- **Token optimization happens before the LLM ever sees anything.**
  `content_cleaner.py` strips `<script>`, `<style>`, `<svg>`, nav/footer
  landmarks, and `aria-hidden` elements, then hard-caps each page's text.
- **Layered resilience.** `browser.py` retries with backoff and
  classifies failures (`blocked`, `not_found`, `timeout`,
  `server_error`) instead of one generic error string. `agent.py`
  treats an LLM outage, a bad tool call, or an exhausted step budget as
  recoverable — the run always ends with *some* `CompanyIntelligence`
  record, even in the worst case (`data_confidence_score: 0.0` with the
  reason in `errors`). `pipeline.py` adds a second safety net around
  each domain's agent run for the batch as a whole.
- **Rate-limit aware.** `llm_client.py` catches `RateLimitError`
  specifically, honors the API's `Retry-After` header when present, and
  backs off exponentially on transient 5xxs — separate from the
  browser-level retry logic, since these are different failure modes.
- **Anti-bot resilience without pretending to be something else.**
  User-agent rotation and `playwright-stealth` patch the generic
  headless-browser fingerprints that some WAFs block by default — this
  is about not getting false-positived for automating a normal page
  load of public marketing content, not about evading access controls
  on non-public data. There's no CAPTCHA-solving or proxy rotation here.
- **Disk cache** (`src/cache.py`) keyed by URL hash so re-running the
  same domain during development doesn't re-pay for the same fetch.
  Purely a cost/latency optimization — delete `.cache/` any time.
- **Cost tracking** reads `response.usage` off every Claude call
  (accumulated across all turns in a domain's agent run, including the
  forced-finalization call if one happens) and prints a per-domain
  input/output token table with an estimated USD cost. The per-token
  prices in `.env.example` are approximate — check
  [anthropic.com/pricing](https://www.anthropic.com/pricing) for
  current rates.
- **Bonus: search as an agent tool, not a bolted-on post-step.** Rather
  than always running a fixed "look up LinkedIn for anyone missing one"
  pass after extraction, `search_web` is just another tool the agent
  can reach for mid-run, when *it* decides a name is worth looking up.
  Skipped cleanly (with a clear message back to the model) if
  `TAVILY_API_KEY` isn't set.

## Sample output

`sample_output/output.json` holds real, verified data for the three
test domains (postman.com, supabase.com, vapi.ai) — company overview,
target audience, leadership, and contact info were all pulled from each
site's actual live content, including a genuine `search_web`-style
resolution of Vapi CEO Jordan Dearsley's LinkedIn URL (marked
`"source": "search"`).



## Testing

```bash
pytest tests/ -v
```

- `tests/test_content_cleaner.py` — pure-function coverage of HTML
  cleaning, email extraction, and link ranking.
- `tests/test_agent.py` — the agent's control-flow loop, using a fake
  `PageFetcher` and a scripted fake `LLMClient` (no network, no API key,
  no real browser needed): a normal multi-step research run, an
  off-domain `visit_page` rejection, self-repair after an invalid
  `submit_intelligence` call, forced finalization when the step budget
  runs out (plus same-URL caching within a run), and survival of a
  simulated total LLM outage.

CI (`.github/workflows/ci.yml`) runs this same suite on every push —
no secrets or live network access required, so it stays green without
any repo configuration.

## Known limitations

- No CAPTCHA-solving or proxy rotation — a site behind an aggressive
  challenge page will show up in `pages_failed` with `error_type:
  "blocked"`.
- `search_web` returns the first LinkedIn-shaped URL from search
  results; for very common names this can occasionally attribute the
  wrong profile. Best-effort, not verified identity resolution.
- `data_confidence_score` is the model's own self-assessment given what
  it actually retrieved — a heuristic signal for triage, not a
  calibrated probability.
- No `robots.txt` checking yet — a reasonable next addition alongside
  the existing per-domain concurrency bound, which already keeps the
  agent from hammering any single site.


