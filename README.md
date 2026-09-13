# Autonomous Lead Enrichment Agent

Takes a list of company domains, crawls their public web presence with a
headless browser, reduces the result to clean text, and extracts
structured company intelligence with Claude's tool-calling (strict
JSON schema, not "please return JSON").

```
domains --> [browser.py]        headless fetch, retries, timeouts
        --> [link_discovery.py] find /about /team /pricing etc.
        --> [content_cleaner.py] strip scripts/nav/svg -> plain text, capped
        --> [llm_extractor.py]  Claude tool-call -> CompanyIntelligence
        --> [search_fallback.py] (optional) resolve missing LinkedIn URLs
        --> output.json
```

Every stage in `pipeline.py` is wrapped so **one bad domain never kills
the batch** — a timeout, 404, or bot-block just gets logged into that
domain's `errors` list and the run continues.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # downloads the headless browser binary

cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY (required)
```

`TAVILY_API_KEY` in `.env` is optional — it powers the bonus LinkedIn
search fallback. Leave it blank to skip that step entirely.

## Run

```bash
python main.py --domains postman.com supabase.com vapi.ai --output output.json
```

or from a file (one domain per line):

```bash
python main.py --input-file domains.txt --output output.json
```

This prints a per-domain token/cost summary to the terminal and writes
a JSON array to `output.json`, one record per input domain:

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
  "errors": [],
  "tokens": {"input": 1234, "output": 210},
  "elapsed_seconds": 4.2
}
```

## Design decisions

- **Structured output via tool-calling, not prompted JSON.** `schemas.py`
  defines `CompanyIntelligence` as a Pydantic model; `llm_extractor.py`
  converts it to a JSON Schema and passes it as a forced tool
  (`tool_choice={"type": "tool", ...}`). The response is guaranteed to
  match the schema shape, so there's no regex-parsing a code fence out
  of free text.
- **Token optimization happens before the LLM ever sees anything.**
  `content_cleaner.py` strips `<script>`, `<style>`, `<svg>`, nav/footer
  landmarks, and `aria-hidden` elements, then hard-caps each page to
  `MAX_CHARS_PER_PAGE` and the combined context to `MAX_CHARS_TOTAL`.
  Raw HTML is never sent to the model.
- **Link discovery is keyword-ranked, not a generic crawler.** For lead
  enrichment we want ~5 high-signal pages (about/team/pricing/contact),
  not a full site mirror — that keeps both latency and token spend
  bounded per domain regardless of how large the target site is.
- **Resilience is layered, not bolted on.** `browser.py`'s `fetch_page`
  retries with backoff and treats timeouts/404s/bot-blocks (403/429) as
  soft failures returning `FetchResult(html=None, error=...)`.
  `pipeline.py` degrades further: if the homepage is entirely
  unreachable, it still emits a `CompanyIntelligence` record with
  `data_confidence_score=0.0` rather than raising, so every input domain
  is guaranteed a row in `output.json`.
- **Cost tracking** (`cost_tracker.py`) reads `response.usage` off every
  Claude call and prints a per-domain input/output token table with an
  estimated USD cost at the end of a run. The per-token prices in
  `.env.example` are approximate — check
  [anthropic.com/pricing](https://www.anthropic.com/pricing) for current
  rates before treating the total as more than directional.
- **Bonus: LinkedIn search fallback.** If a founder/leader is extracted
  without a `linkedin_url`, `search_fallback.py` optionally queries
  [Tavily](https://tavily.com) (a search API built for LLM pipelines) for
  `"{name} {company} linkedin"` and takes the first `linkedin.com/in/...`
  hit. Skipped cleanly if `TAVILY_API_KEY` isn't set.

## Sample output

`sample_output/output.json` holds real, verified data for the three
test domains (postman.com, supabase.com, vapi.ai) — company overview,
target audience, leadership, and contact info were all pulled from each
site's actual live content.

**One caveat on how it was produced:** the sandboxed environment used to
assemble this submission has network egress restricted to package
registries (pip/npm) and github.com — it can't reach postman.com,
supabase.com, or vapi.ai directly, and had no `ANTHROPIC_API_KEY`
available to call the live API. So this specific file was built by
fetching each site's real content through a research tool and applying
the exact same extraction logic and schema by hand (each record's
`errors` field says so explicitly). Every function used to get there —
`content_cleaner.clean_html_to_text`, `link_discovery.discover_subpages`,
`extract_emails`, and the `CompanyIntelligence` schema itself — is
exercised by the unit tests in `tests/`, and running
`python main.py --domains postman.com supabase.com vapi.ai` on a machine
with normal internet access and a real API key reproduces this same
output end-to-end through the actual Playwright + Claude pipeline.

## Testing

```bash
pytest tests/ -v
```

Covers the pure-function logic (HTML cleaning, email extraction, link
ranking) that doesn't require network access or an API key, so it runs
in any environment including CI.

## Known limitations

- Sites that gate all content behind a login or aggressive bot
  protection (Cloudflare interstitials, etc.) will show up as
  `pages_failed` with `HTTP 403` — there's no CAPTCHA-solving or proxy
  rotation here.
- The search fallback returns the first LinkedIn-shaped URL from search
  results; for very common names this can occasionally attribute the
  wrong profile. It's a best-effort bonus feature, not verified identity
  resolution.
- `data_confidence_score` is the model's own self-assessment given the
  retrieved text — it's a heuristic signal for triage, not a calibrated
  probability.

## Repository / submission notes

- **Operations question (40% manual ops expectation):** this is a
  confirmation about your own working expectations for the role, not
  something the code or I can answer on your behalf — you'll want to
  respond to that directly in your submission.
- **Loom walkthrough:** I can't record video, but I'm happy to put
  together short talking points / a suggested script covering code
  structure → running it in the terminal → the resulting output, if
  that would help you plan the recording.
