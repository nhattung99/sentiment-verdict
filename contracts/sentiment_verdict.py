# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *

import json
from dataclasses import dataclass


def _addr_str(a: Address) -> str:
    try:
        return a.as_hex
    except Exception:
        return str(a)


def _to_address(val) -> Address:
    if isinstance(val, Address):
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str.startswith("0x"):
            val_str = "0x" + val_str
        return Address(val_str)
    return Address(val)


def _is_http_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _page_text(res) -> str:
    body = ""
    try:
        if isinstance(res, dict):
            body = res.get("body", "")
        elif hasattr(res, "body"):
            body = res.body
        else:
            body = str(res)
    except Exception:
        body = ""
    if not isinstance(body, str):
        body = str(body)
    return body


def _clamp_int(val, lo: int, hi: int, default: int) -> int:
    try:
        n = int(val)
    except Exception:
        return default
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n


# Offset: stored_value = actual_score + 100
# actual_score -100 -> stored as bigint(0)
# actual_score    0 -> stored as bigint(100)
# actual_score +100 -> stored as bigint(200)
_SCORE_OFFSET = 100

_TIERS = (
    "VERY_BULLISH",
    "BULLISH",
    "NEUTRAL",
    "BEARISH",
    "VERY_BEARISH",
    "UNSCOREABLE",
)

_SOURCE_SENTIMENTS = (
    "VERY_BULLISH",
    "BULLISH",
    "NEUTRAL",
    "BEARISH",
    "VERY_BEARISH",
    "UNSCOREABLE",
    "UNREADABLE",
)

_LIST_FILTERS = (
    "",
    "PENDING",
    "VERY_BULLISH",
    "BULLISH",
    "NEUTRAL",
    "BEARISH",
    "VERY_BEARISH",
    "UNSCOREABLE",
)


def _align_score(tier: str, score: int) -> int:
    # Keep the stored score inside the tier band the leader claimed.
    if tier == "VERY_BULLISH" and score < 61:
        score = 75
    if tier == "BULLISH" and (score < 21 or score > 60):
        score = 40
    if tier == "NEUTRAL" and (score < -20 or score > 20):
        score = 0
    if tier == "BEARISH" and (score > -21 or score < -60):
        score = -40
    if tier == "VERY_BEARISH" and score > -61:
        score = -75
    if tier == "UNSCOREABLE":
        score = 0
    return score


def _actual_score(stored) -> int:
    return int(stored) - _SCORE_OFFSET


@allow_storage
@dataclass
class SentimentReport:
    submitter: Address
    asset_name: str
    context_note: str
    source_urls: DynArray[str]
    status: str
    tier: str
    # OFFSET STORAGE: actual score + 100 (range 0-200)
    # e.g. actual=-50 stored as bigint(50)
    sentiment_score_offset: bigint
    confidence: bigint
    sources_read: bigint
    reasoning: str
    source_breakdown: str


class Contract(gl.Contract):
    reports: TreeMap[str, SentimentReport]
    next_id: bigint
    owner: Address
    # Cache: asset_name (lowercased) -> latest report_id
    asset_cache: TreeMap[str, str]

    def __init__(self):
        self.owner = _to_address(gl.message.sender_address)
        self.next_id = bigint(1)

    def _report_dict(self, report_id: str, report: SentimentReport) -> dict:
        urls = []
        for i in range(len(report.source_urls)):
            urls.append(str(report.source_urls[i]))
        return {
            "report_id": report_id,
            "submitter": _addr_str(report.submitter),
            "asset_name": report.asset_name,
            "context_note": report.context_note,
            "source_urls": urls,
            "status": report.status,
            "tier": report.tier,
            "sentiment_score": _actual_score(report.sentiment_score_offset),
            "confidence": int(report.confidence),
            "sources_read": int(report.sources_read),
            "reasoning": report.reasoning,
            "source_breakdown": report.source_breakdown,
        }

    @gl.public.write
    def submit_reading(
        self,
        asset_name: str,
        context_note: str,
        source_urls: DynArray[str],
    ) -> str:
        if not isinstance(asset_name, str):
            asset_name = str(asset_name)
        if not isinstance(context_note, str):
            context_note = str(context_note)

        if not asset_name or len(asset_name.strip()) == 0:
            raise gl.vm.UserError("asset_name cannot be empty")
        if len(asset_name) > 50:
            raise gl.vm.UserError("asset_name cannot exceed 50 characters")
        if len(context_note) > 200:
            raise gl.vm.UserError("context_note cannot exceed 200 characters")

        num_urls = len(source_urls)
        if num_urls < 3:
            raise gl.vm.UserError("At least 3 source URLs required")
        if num_urls > 5:
            raise gl.vm.UserError("Maximum 5 source URLs")

        urls = []
        for i in range(num_urls):
            url = source_urls[i]
            if not isinstance(url, str):
                url = str(url)
            if not _is_http_url(url):
                raise gl.vm.UserError("Invalid URL")
            if url in urls:
                raise gl.vm.UserError("Duplicate URL")
            urls.append(url)

        report_id = str(self.next_id)
        self.next_id += bigint(1)

        report = SentimentReport(
            submitter=_to_address(gl.message.sender_address),
            asset_name=asset_name,
            context_note=context_note,
            source_urls=urls,
            status="PENDING",
            tier="",
            sentiment_score_offset=bigint(_SCORE_OFFSET),
            confidence=bigint(0),
            sources_read=bigint(0),
            reasoning="",
            source_breakdown="[]",
        )
        self.reports[report_id] = report
        self.asset_cache[asset_name.lower()] = report_id
        return report_id

    @gl.public.write
    def score_sentiment(self, report_id: str) -> None:
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")

        report = self.reports[report_id]
        if report.status != "PENDING":
            raise gl.vm.UserError("not PENDING")

        source_urls_list = []
        for i in range(len(report.source_urls)):
            source_urls_list.append(str(report.source_urls[i]))
        asset_name_cap = report.asset_name
        context_note_cap = report.context_note

        def leader_fn():
            readable_count = 0
            try:
                source_results = []
                for url in source_urls_list:
                    try:
                        res = gl.nondet.web.render(url)
                        body = _page_text(res)
                        if body and len(body.strip()) > 30:
                            source_results.append(
                                "Source [" + url + "]:\n" + body[:3000]
                            )
                            readable_count += 1
                        else:
                            source_results.append(
                                "Source [" + url + "]: (empty or too short)"
                            )
                    except Exception as e:
                        source_results.append(
                            "Source ["
                            + url
                            + "]: (fetch failed: "
                            + str(e)
                            + ")"
                        )

                context_text = context_note_cap
                if not context_text:
                    context_text = "General market sentiment"

                prompt = f"""You are an expert crypto market sentiment analyst on GenLayer.
Your job is to measure the current market sentiment for a crypto asset
by reading multiple independent public sources and aggregating them into
a single consensus sentiment score.

Asset Being Analyzed: {asset_name_cap}
Context: {context_text}

Public sentiment sources fetched on-chain:
{chr(10).join(source_results)}

Instructions:
1. For each source that returned readable content, assess the sentiment
   expressed towards the asset on a scale of -100 to +100:
   - Extremely bearish / panic / very negative: -100 to -61
   - Bearish / pessimistic / negative: -60 to -21
   - Neutral / mixed / uncertain: -20 to +20
   - Bullish / optimistic / positive: +21 to +60
   - Extremely bullish / euphoric / very positive: +61 to +100
2. Aggregate the per-source scores into a single overall sentiment score
   (-100 to +100). Weight all readable sources equally.
3. Determine the tier from the aggregated score:
   - VERY_BULLISH:  +61 to +100
   - BULLISH:       +21 to +60
   - NEUTRAL:       -20 to +20
   - BEARISH:       -60 to -21
   - VERY_BEARISH:  -100 to -61
4. If fewer than 2 sources returned readable content, set tier to
   UNSCOREABLE and score to 0.

CRITICAL: sentiment_score must be an integer between -100 and +100.
No decimals, no fractions.

source_breakdown: array of objects for each source URL, including
unreadable ones. For unreadable sources, set score to 0 and
sentiment to "UNREADABLE".

Return ONLY raw JSON, no markdown, no backticks:
{{"tier": "VERY_BULLISH"|"BULLISH"|"NEUTRAL"|"BEARISH"|"VERY_BEARISH"|"UNSCOREABLE",
  "sentiment_score": <integer -100 to +100>,
  "confidence": <0-100>,
  "sources_read": <integer>,
  "reasoning": "<2-3 sentences summarizing overall sentiment signals>",
  "source_breakdown": [
    {{"url": "<source url>",
      "score": <integer -100 to +100>,
      "sentiment": "VERY_BULLISH"|"BULLISH"|"NEUTRAL"|"BEARISH"|"VERY_BEARISH"|"UNREADABLE",
      "signal": "<key observation from this source>"}}
  ]}}"""

                raw = gl.nondet.exec_prompt(prompt, response_format="json")

                try:
                    if isinstance(raw, dict):
                        parsed = raw
                    else:
                        cleaned = str(raw).strip()
                        if cleaned.startswith("```json"):
                            cleaned = cleaned[7:]
                        if cleaned.startswith("```"):
                            cleaned = cleaned[3:]
                        if cleaned.endswith("```"):
                            cleaned = cleaned[:-3]
                        parsed = json.loads(cleaned.strip())

                    tier = parsed.get("tier", "UNSCOREABLE")
                    if tier not in _TIERS:
                        tier = "UNSCOREABLE"

                    score = _clamp_int(
                        parsed.get("sentiment_score", 0), -100, 100, 0
                    )
                    score = _align_score(tier, score)

                    conf = _clamp_int(parsed.get("confidence", 0), 0, 100, 0)

                    try:
                        claimed_src = int(
                            parsed.get("sources_read", readable_count)
                        )
                        src_read = claimed_src
                        if src_read < 0:
                            src_read = 0
                        if src_read > len(source_urls_list):
                            src_read = len(source_urls_list)
                    except Exception:
                        src_read = readable_count

                    # A model cannot claim more readable pages than were fetched.
                    if src_read > readable_count:
                        src_read = readable_count

                    # Fewer than 2 readable sources cannot support a score.
                    if readable_count < 2 or src_read < 2:
                        tier = "UNSCOREABLE"
                        score = 0
                        src_read = readable_count

                    raw_breakdown = parsed.get("source_breakdown", [])
                    if not isinstance(raw_breakdown, list):
                        raw_breakdown = []
                    clean_breakdown = []
                    for item in raw_breakdown:
                        if isinstance(item, dict):
                            s_score = _clamp_int(
                                item.get("score", 0), -100, 100, 0
                            )
                            s_sentiment = str(item.get("sentiment", "NEUTRAL"))
                            if s_sentiment not in _SOURCE_SENTIMENTS:
                                s_sentiment = "NEUTRAL"
                            clean_breakdown.append({
                                "url": str(item.get("url", "")),
                                "score": s_score,
                                "sentiment": s_sentiment,
                                "signal": str(item.get("signal", "")),
                            })

                    return {
                        "tier": tier,
                        "sentiment_score": score,
                        "confidence": conf,
                        "sources_read": src_read,
                        "reasoning": str(parsed.get("reasoning", "")),
                        "source_breakdown": json.dumps(clean_breakdown),
                    }
                except Exception as e:
                    return {
                        "tier": "UNSCOREABLE",
                        "sentiment_score": 0,
                        "confidence": 0,
                        "sources_read": readable_count,
                        "reasoning": "Parse error: " + str(e),
                        "source_breakdown": "[]",
                    }
            except Exception as e:
                return {
                    "tier": "UNSCOREABLE",
                    "sentiment_score": 0,
                    "confidence": 0,
                    "sources_read": readable_count,
                    "reasoning": "Parse error: " + str(e),
                    "source_breakdown": "[]",
                }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            lp = leader_res.calldata
            if not isinstance(lp, dict):
                return False

            leader_tier = lp.get("tier")
            leader_score = lp.get("sentiment_score")
            leader_conf = lp.get("confidence")

            if leader_tier not in _TIERS:
                return False
            try:
                ls = int(leader_score)
                lc = int(leader_conf)
                if not (-100 <= ls <= 100):
                    return False
                if not (0 <= lc <= 100):
                    return False
            except Exception:
                return False

            try:
                my_result = leader_fn()
            except Exception:
                return False
            if not isinstance(my_result, dict):
                return False

            # Tier must match exactly. Tier is the market signal category.
            if my_result.get("tier") != leader_tier:
                return False

            # Score tolerance: +/- 10. Sentiment varies more across LLM runs
            # than price or reputation, so the band is wider than +/- 5.
            try:
                ms = int(my_result.get("sentiment_score", 0))
                if abs(ms - ls) > 10:
                    return False
            except Exception:
                return False

            try:
                mc = int(my_result.get("confidence", 0))
                if not (0 <= mc <= 100):
                    return False
            except Exception:
                return False

            def _band(c):
                if c < 35:
                    return 1
                elif c < 80:
                    return 2
                else:
                    return 3

            return _band(mc) == _band(lc)

        ruling = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        result_data = ruling.calldata if hasattr(ruling, "calldata") else ruling
        if not isinstance(result_data, dict):
            result_data = {
                "tier": "UNSCOREABLE",
                "sentiment_score": 0,
                "confidence": 0,
                "sources_read": 0,
                "reasoning": "Invalid nondet result",
                "source_breakdown": "[]",
            }

        result_tier = result_data.get("tier", "UNSCOREABLE")
        if result_tier not in _TIERS:
            result_tier = "UNSCOREABLE"

        result_score = _clamp_int(
            result_data.get("sentiment_score", 0), -100, 100, 0
        )
        if result_tier == "UNSCOREABLE":
            result_score = 0

        conf = _clamp_int(result_data.get("confidence", 0), 0, 100, 0)
        src_read = _clamp_int(
            result_data.get("sources_read", 0), 0, len(source_urls_list), 0
        )

        breakdown_raw = result_data.get("source_breakdown", "[]")
        if isinstance(breakdown_raw, list):
            breakdown_raw = json.dumps(breakdown_raw)
        elif not isinstance(breakdown_raw, str):
            breakdown_raw = "[]"

        stored_offset = result_score + _SCORE_OFFSET
        if stored_offset < 0:
            stored_offset = 0
        if stored_offset > 200:
            stored_offset = 200

        report.tier = result_tier
        report.sentiment_score_offset = bigint(stored_offset)
        report.confidence = bigint(conf)
        report.sources_read = bigint(src_read)
        report.reasoning = str(result_data.get("reasoning", ""))
        report.source_breakdown = breakdown_raw
        if result_tier == "UNSCOREABLE" or result_tier == "":
            report.status = "UNSCOREABLE"
        else:
            report.status = "SCORED"
        self.reports[report_id] = report

    @gl.public.write
    def invalidate_report(self, report_id: str) -> None:
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")

        sender = _to_address(gl.message.sender_address)
        if sender != self.owner:
            raise gl.vm.UserError("Only owner")

        report = self.reports[report_id]
        if report.status != "PENDING":
            raise gl.vm.UserError("not PENDING")

        report.status = "UNSCOREABLE"
        report.reasoning = "Invalidated by admin"
        self.reports[report_id] = report

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")
        return json.dumps(self._report_dict(report_id, self.reports[report_id]))

    @gl.public.view
    def get_sentiment(self, report_id: str) -> str:
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")

        report = self.reports[report_id]
        if report.status == "PENDING":
            raise gl.vm.UserError("Not yet scored")

        res = {
            "report_id": report_id,
            "asset_name": report.asset_name,
            "tier": report.tier,
            "sentiment_score": _actual_score(report.sentiment_score_offset),
            "confidence": int(report.confidence),
            "sources_read": int(report.sources_read),
            "reasoning": report.reasoning,
            "source_breakdown": report.source_breakdown,
        }
        return json.dumps(res)

    @gl.public.view
    def get_latest(self, asset_name: str) -> str:
        if not isinstance(asset_name, str):
            asset_name = str(asset_name)
        key = asset_name.lower()
        if key not in self.asset_cache:
            return json.dumps({"found": False})
        report_id = self.asset_cache[key]
        if report_id not in self.reports:
            return json.dumps({"found": False})
        return json.dumps(self._report_dict(report_id, self.reports[report_id]))

    @gl.public.view
    def list_reports(self, tier_filter: str) -> str:
        if tier_filter not in _LIST_FILTERS:
            raise gl.vm.UserError("Invalid filter")

        results = []
        limit = int(self.next_id)
        for i in range(1, limit):
            report_id = str(i)
            if report_id not in self.reports:
                continue
            report = self.reports[report_id]
            include = False
            if tier_filter == "":
                include = True
            elif tier_filter == "PENDING":
                include = report.status == "PENDING"
            elif tier_filter == "UNSCOREABLE":
                include = (
                    report.status == "UNSCOREABLE"
                    or report.tier == "UNSCOREABLE"
                )
            else:
                include = report.tier == tier_filter
            if include:
                results.append({
                    "report_id": report_id,
                    "asset_name": report.asset_name,
                    "status": report.status,
                    "tier": report.tier,
                    "sentiment_score": _actual_score(
                        report.sentiment_score_offset
                    ),
                    "confidence": int(report.confidence),
                })
        return json.dumps(results)

    @gl.public.view
    def get_count(self) -> int:
        return int(self.next_id) - 1
