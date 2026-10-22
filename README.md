# SentimentVerdict

SentimentVerdict dies without GenLayer: no EVM contract can read a Reddit crypto thread and a CoinGecko community page on-chain, aggregate sentiment signals from multiple independent sources, and produce a multi-validator consensus sentiment score — only GenLayer's AI validators can fetch live sentiment data during consensus and deliver a verifiable on-chain sentiment oracle.

## Problem

Traders, protocols, and prediction markets need the market mood for a token at a specific moment. Existing sentiment indices are centralized (one company's algorithm), opaque (no verifiable methodology), and easy to manipulate. A market that settles on sentiment has no neutral reading it can point to.

## Solution

SentimentVerdict accepts an asset name, a short context note, and 3 to 5 public source URLs. Validators fetch those pages on-chain, score each source, and store one Sentiment Score from -100 to +100 plus a tier. The result is a multi-source sentiment oracle with a permanent on-chain record.

Remove `web.render` and the LLM and this contract is only a registry of whatever score the submitter typed in. The AI sentiment aggregation is the product.

## How it works

1. **Submit.** Anyone calls `submit_reading(asset_name, context_note, source_urls)`. The report is stored as `PENDING` and `asset_cache` points at this report id for that asset. A later submit for the same asset (case-insensitive) replaces the cache entry. The method returns the report id (`"1"`, `"2"`, ...).
2. **Score.** Anyone calls `score_sentiment(report_id)`. Validators fetch every source and agree on a tier and a score.
3. **Read the result.** `get_sentiment(report_id)` returns the tier, the signed score, confidence, how many sources were readable, the reasoning, and the per-source breakdown. `get_report(report_id)` returns the full record.
4. **Latest.** `get_latest(asset_name)` returns the newest report for that asset, or `{"found": false}` when the name has never been submitted.

The contract owner can call `invalidate_report(report_id)` on a `PENDING` record. That sets status to `UNSCOREABLE` and reasoning to `Invalidated by admin`.

## Score range

The score runs from **-100** (extreme bearish) to **+100** (extreme bullish). View methods return that signed integer. The contract never stores a negative `bigint`.

## Tier guide

| Tier | Score |
| --- | --- |
| `VERY_BULLISH` | +61 to +100 |
| `BULLISH` | +21 to +60 |
| `NEUTRAL` | -20 to +20 |
| `BEARISH` | -60 to -21 |
| `VERY_BEARISH` | -100 to -61 |

`UNSCOREABLE` means the reading could not be made. Status becomes `UNSCOREABLE` and the displayed score is `0`. A scored tier sets status to `SCORED`.

`get_sentiment` reverts with `Not yet scored` while status is `PENDING`.

## Storage design

GenVM storage keeps the score as a non-negative `bigint` with an offset of 100:

| Actual score | Stored `sentiment_score_offset` |
| --- | --- |
| -100 | 0 |
| -40 | 60 |
| 0 | 100 |
| +55 | 155 |
| +100 | 200 |

`stored = actual + 100`. View methods convert back with `actual = int(stored) - 100`. Per-source scores live inside the `source_breakdown` JSON string as signed integers from -100 to +100. They are not separate `bigint` fields.

## Minimum readable sources

A non-`UNSCOREABLE` tier requires at least 2 sources whose fetched body is longer than 30 characters. Zero or one readable page forces `UNSCOREABLE`, score 0, even if the model claims a higher `sources_read`. The stored `sources_read` cannot exceed the number of pages that actually returned readable text.

## Consistency rules

Before a result is proposed to consensus, `leader_fn`:

- clamps `sentiment_score` to -100..+100
- snaps a score that falls outside its tier band back into that band (`VERY_BULLISH` -> 75, `BULLISH` -> 40, `NEUTRAL` -> 0, `BEARISH` -> -40, `VERY_BEARISH` -> -75)
- forces `UNSCOREABLE` and score 0 when fewer than 2 sources were readable
- drops an unknown tier string to `UNSCOREABLE`
- clamps each `source_breakdown` score to -100..+100, and stores `[]` when the breakdown is not a list

## Consensus design

`score_sentiment` runs inside `gl.vm.run_nondet_unsafe`. Each URL is fetched in its own try/except. A failed fetch becomes text in the prompt. `leader_fn` always returns a dict and never reads contract storage.

Validators do not accept a result because the JSON parsed. Each validator fetches the sources again, runs the analysis again, and accepts the leader only when the decision means the same thing. Two validators that reach different decisions do not both pass. A leader result of `BEARISH` is rejected when the other validator returns `BULLISH`, `NEUTRAL`, `VERY_BULLISH`, `VERY_BEARISH`, or `UNSCOREABLE`, even if both payloads are valid JSON.

- **Tier exact match.** `VERY_BULLISH`, `BULLISH`, `NEUTRAL`, `BEARISH`, `VERY_BEARISH`, and `UNSCOREABLE` must agree exactly. Tier is the market signal category.
- **Score tolerance +/- 10.** Sentiment naturally varies more across LLM runs than a price or a reputation score, so two validators can differ by up to 10 points and still accept the leader. The tolerance is wider than the +/- 5 used by other contracts. A gap larger than 10 is a different reading and is rejected.
- **Confidence bands.** Confidence is not compared as an exact integer. The bands are 0-34, 35-79, and 80-100. Both sides must land in the same band. Reasoning text is not compared.

## Use cases

- Prediction market settlement oracle for a sentiment outcome
- Automated trading signal that reads a signed score and a tier
- DAO governance check before a treasury or listing vote
- DeFi protocol risk scoring when community and news mood turn sharply bearish

## Supported sources

Public pages the contract can fetch during consensus, including:

- CoinGecko community stats
- CoinMarketCap community or news pages
- Reddit threads
- Twitter/X search result pages
- News aggregators
- Crypto forum discussions

Each call needs 3 to 5 `http://` or `https://` URLs. Duplicate URLs are rejected.

## Edge cases

- A failed fetch is caught per URL and written into the prompt. The call still finishes.
- An empty or very short page is marked `(empty or too short)` and does not count as readable.
- Fewer than 2 readable sources forces `UNSCOREABLE` and score 0.
- Malformed model JSON falls back to `UNSCOREABLE`, score 0, confidence 0, and `source_breakdown` `[]`.
- An unknown tier string is normalized to `UNSCOREABLE`.
- A score outside -100..+100 is clamped before it is aligned to the tier and before the offset is stored.
- A tier whose score sits outside that tier's band is corrected to the band default.
- A `source_breakdown` value that is not a list is stored as `[]`. Per-item scores are clamped to -100..+100.
- `asset_cache` keeps the latest report id for the lowercased asset name. `get_latest` returns that report, or `{"found": false}` when the name is new.
- Only the owner can invalidate a `PENDING` report. A scored report cannot be invalidated or scored again.

## Public API

### Write methods

- `submit_reading(asset_name, context_note, source_urls) -> str`
- `score_sentiment(report_id) -> None` (permissionless)
- `invalidate_report(report_id) -> None` (owner only, `PENDING` only)

### View methods

- `get_report(report_id) -> str` — full record; `sentiment_score` is the signed integer
- `get_sentiment(report_id) -> str` — tier, signed score, confidence, sources, reasoning, breakdown
- `get_latest(asset_name) -> str` — latest report, or `{"found": false}`
- `list_reports(tier_filter) -> str` — `""`, `PENDING`, a tier name, or `UNSCOREABLE`
- `get_count() -> int`

## Deploy on GenLayer Studio (studionet)

1. Open [GenLayer Studio](https://studio.genlayer.com) and create an Intelligent Contract.
2. Paste `contracts/sentiment_verdict.py`. The file header must stay the first lines (`v0.2.16` and the `py-genlayer` dependency). Do not add a frontend.
3. Select network **studionet**.
4. Deploy. The constructor takes no arguments and sets `owner` to the deployer.
5. Call `submit_reading(asset_name, context_note, source_urls)` with 3 to 5 public URLs.
6. Call `score_sentiment(report_id)`. Then read `get_sentiment(report_id)` or `get_latest(asset_name)`.

## Run tests

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
gltest tests/
```

## Deployment

- **CONTRACT_ADDRESS:** `0x542C3ee7854100c60D0faBdE82549C335Ce13DD0`
- **NETWORK:** studionet
- **Explorer:** https://explorer-studio.genlayer.com/address/0x542C3ee7854100c60D0faBdE82549C335Ce13DD0

### Real on-chain read

A read-only call against this address on studionet returned:

- `get_count()` -> `0`
- `get_latest("Ethereum ETH")` -> `{"found": false}`
- `list_reports("")` -> `[]`

No sentiment reading is stored on studionet yet. The worked example below is the expected result from the local test `test_bullish_sentiment`, not a receipt from this address.

### Worked example (expected output, not a live studionet verdict)

Input to `submit_reading`:

```text
asset_name:   Ethereum ETH
context_note: Post-ETF approval week
source_urls:
  https://coingecko.com/en/coins/ethereum
  https://reddit.com/r/ethereum/top
  https://cointelegraph.com/tags/ethereum
```

The CoinGecko page shows a 78% community bullish rating, the Reddit thread is optimistic about upgrades, and the Cointelegraph page reports institutional inflows. Expected `get_sentiment` after `score_sentiment`:

```json
{
  "report_id": "1",
  "asset_name": "Ethereum ETH",
  "tier": "BULLISH",
  "sentiment_score": 55,
  "confidence": 85,
  "sources_read": 3,
  "reasoning": "All three sources show strong bullish signals with institutional inflows and community optimism.",
  "source_breakdown": "[{\"url\": \"https://coingecko.com/en/coins/ethereum\", \"score\": 60, \"sentiment\": \"BULLISH\", \"signal\": \"78% community bullish rating\"}, {\"url\": \"https://reddit.com/r/ethereum/top\", \"score\": 65, \"sentiment\": \"BULLISH\", \"signal\": \"Top posts all optimistic, high upvotes\"}, {\"url\": \"https://cointelegraph.com/tags/ethereum\", \"score\": 40, \"sentiment\": \"BULLISH\", \"signal\": \"Analysts positive, institutional inflows\"}]"
}
```

`sentiment_score` 55 is the signed score. It is stored as `bigint(155)` (`55 + 100`). Status on `get_report` is `SCORED`.
