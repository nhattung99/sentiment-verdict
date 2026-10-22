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
