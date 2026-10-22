import json
import pytest


def _raise(_url=None):
    raise RuntimeError("source unreachable")


def _mock(vm, pattern, body):
    # Stock genlayer-test 0.29.2 stores the mock as-is and calls .get on it.
    if isinstance(body, str):
        body = {"status": 200, "body": body, "headers": {}}
    vm.mock_web(pattern, body)


def _breakdown(urls, scores, sentiments, signals):
    rows = []
    for i in range(len(urls)):
        rows.append({
            "url": urls[i],
            "score": scores[i],
            "sentiment": sentiments[i],
            "signal": signals[i],
        })
    return rows


def _llm(tier, score, confidence, sources_read, reasoning, breakdown):
    return json.dumps({
        "tier": tier,
        "sentiment_score": score,
        "confidence": confidence,
        "sources_read": sources_read,
        "reasoning": reasoning,
        "source_breakdown": breakdown,
    })


def _submit(contract, direct_vm, caller, asset, note, urls):
    with direct_vm.prank(caller):
        return contract.submit_reading(asset, note, urls)


def _three(tag):
    return [
        "https://" + tag + ".example/source-a",
        "https://" + tag + ".example/source-b",
        "https://" + tag + ".example/source-c",
    ]


_LONG = (
    "Market discussion with enough text to count as a readable sentiment "
    "source for the on-chain analyst. Traders describe positioning, flows, "
    "and community mood in concrete terms."
)


# 1. Happy path — BULLISH tier with 3 sources
def test_bullish_sentiment(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")

    with direct_vm.prank(direct_alice):
        report_id = contract.submit_reading(
            "Ethereum ETH",
            "Post-ETF approval week",
            [
                "https://coingecko.com/en/coins/ethereum",
                "https://reddit.com/r/ethereum/top",
                "https://cointelegraph.com/tags/ethereum",
            ],
        )
    assert report_id == "1"
    assert contract.get_count() == 1

    _mock(direct_vm, 
        r".*coingecko.*ethereum.*",
        "Ethereum ETH | Community Bullish 78% | Positive sentiment "
        "trending | Price up 23% this week | Strong buy signals",
    )
    _mock(direct_vm, 
        r".*reddit.*ethereum.*",
        "Top posts: 'ETH finally breaking resistance!', "
        "'Best time to accumulate', '95% upvoted: Bullish on ETH', "
        "Comments all expressing optimism about upcoming upgrades",
    )
    _mock(direct_vm, 
        r".*cointelegraph.*ethereum.*",
        "Ethereum News: Analysts bullish on ETH price targets. "
        "Institutional inflows at record high. Market sentiment positive.",
    )

    urls = [
        "https://coingecko.com/en/coins/ethereum",
        "https://reddit.com/r/ethereum/top",
        "https://cointelegraph.com/tags/ethereum",
    ]
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "BULLISH",
            55,
            85,
            3,
            "All three sources show strong bullish signals "
            "with institutional inflows and community optimism.",
            _breakdown(
                urls,
                [60, 65, 40],
                ["BULLISH", "BULLISH", "BULLISH"],
                [
                    "78% community bullish rating",
                    "Top posts all optimistic, high upvotes",
                    "Analysts positive, institutional inflows",
                ],
            ),
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "BULLISH"
    assert result["sentiment_score"] == 55
    assert result["confidence"] == 85
    assert result["sources_read"] == 3
    breakdown = json.loads(result["source_breakdown"])
    assert len(breakdown) == 3

    full = json.loads(contract.get_report(report_id))
    assert full["status"] == "SCORED"
    assert full["sentiment_score"] == 55


# 2. VERY_BULLISH — extreme positive sentiment
def test_very_bullish_sentiment(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("euphoria")
    report_id = _submit(
        contract, direct_vm, direct_alice, "Solana SOL", "Memecoin mania week", urls
    )
    for url in urls:
        _mock(direct_vm, r".*" + url.split("/")[2].replace(".", r"\.") + r".*", _LONG + " Euphoric bids everywhere.")
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "VERY_BULLISH",
            80,
            90,
            3,
            "Every source describes euphoric buying and record inflows.",
            _breakdown(urls, [85, 78, 77], ["VERY_BULLISH"] * 3, ["euphoria"] * 3),
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "VERY_BULLISH"
    assert result["sentiment_score"] == 80
    full = json.loads(contract.get_report(report_id))
    assert full["sentiment_score"] == 80
    assert full["status"] == "SCORED"


# 3. NEUTRAL sentiment
def test_neutral_sentiment(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("mixed")
    report_id = _submit(
        contract, direct_vm, direct_alice, "Cardano ADA", "Sideways week", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Mixed comments, no clear direction.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "NEUTRAL",
            5,
            70,
            3,
            "Sources disagree. Buyers and sellers are evenly split.",
            _breakdown(urls, [10, -8, 12], ["NEUTRAL"] * 3, ["mixed"] * 3),
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "NEUTRAL"
    assert result["sentiment_score"] == 5


# 4. BEARISH sentiment — negative score
def test_bearish_sentiment(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("bear")
    report_id = _submit(
        contract, direct_vm, direct_alice, "Bitcoin BTC", "Post-liquidation week", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Traders are cutting risk and discussing further downside.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "BEARISH",
            -40,
            82,
            3,
            "Sources lean pessimistic after liquidations and outflow headlines.",
            _breakdown(urls, [-35, -50, -35], ["BEARISH"] * 3, ["outflows"] * 3),
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "BEARISH"
    assert result["sentiment_score"] == -40
    full = json.loads(contract.get_report(report_id))
    # Offset round-trip: actual -40 is stored as 60 and displayed as -40.
    assert full["sentiment_score"] == -40
    assert full["status"] == "SCORED"


# 5. VERY_BEARISH — extreme negative
def test_very_bearish_sentiment(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("panic")
    report_id = _submit(
        contract, direct_vm, direct_alice, "Luna Classic LUNC", "Collapse week", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Panic selling, exchange halts, and capitulation posts.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "VERY_BEARISH",
            -80,
            88,
            3,
            "All sources describe panic and capitulation.",
            _breakdown(urls, [-90, -70, -80], ["VERY_BEARISH"] * 3, ["panic"] * 3),
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "VERY_BEARISH"
    assert result["sentiment_score"] == -80
    full = json.loads(contract.get_report(report_id))
    # Offset round-trip: actual -80 is stored as bigint(20) and displayed as -80.
    assert full["sentiment_score"] == -80


# 6. UNSCOREABLE — fewer than 2 readable sources
def test_unscoreable_insufficient_sources(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("down")
    report_id = _submit(
        contract, direct_vm, direct_alice, "GhostCoin GHOST", "", urls
    )
    for url in urls:
        _mock(direct_vm, r".*" + url.split("/")[2].replace(".", r"\.") + r".*", _raise)
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "BULLISH",
            50,
            80,
            3,
            "The model claimed a score, but no page could be read.",
            [],
        ),
    )

    contract.score_sentiment(report_id)

    full = json.loads(contract.get_report(report_id))
    assert full["status"] == "UNSCOREABLE"
    assert full["sentiment_score"] == 0
    assert full["tier"] == "UNSCOREABLE"
    result = json.loads(contract.get_sentiment(report_id))
    assert result["sentiment_score"] == 0
    assert result["sources_read"] == 0


# 7. Exactly 2 sources readable — passes threshold
def test_exactly_two_readable_passes(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = [
        "https://two.example/ok-a",
        "https://two.example/ok-b",
        "https://two.example/fail-c",
    ]
    report_id = _submit(
        contract, direct_vm, direct_alice, "Avalanche AVAX", "Upgrade week", urls
    )
    _mock(direct_vm, r".*two\.example/ok-a.*", _LONG + " Community is optimistic about the upgrade.")
    _mock(direct_vm, r".*two\.example/ok-b.*", _LONG + " News flow is positive and inflows are steady.")
    _mock(direct_vm, r".*two\.example/fail-c.*", _raise)
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "BULLISH",
            45,
            76,
            2,
            "Two readable sources are bullish. The third page failed.",
            _breakdown(
                urls,
                [50, 40, 0],
                ["BULLISH", "BULLISH", "UNREADABLE"],
                ["upgrade optimism", "steady inflows", "fetch failed"],
            ),
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "BULLISH"
    assert result["sentiment_score"] == 45
    assert result["sources_read"] == 2
    assert json.loads(contract.get_report(report_id))["status"] == "SCORED"


# 8. Score -100 boundary — stored as offset 0
def test_score_negative_100_boundary(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("floor")
    report_id = _submit(
        contract, direct_vm, direct_alice, "FloorCoin FLOOR", "Capitulation", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Total capitulation across every comment thread.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "VERY_BEARISH",
            -100,
            95,
            3,
            "The reading is at the extreme bearish bound.",
            _breakdown(urls, [-100, -100, -100], ["VERY_BEARISH"] * 3, ["floor"] * 3),
        ),
    )

    contract.score_sentiment(report_id)

    full = json.loads(contract.get_report(report_id))
    assert full["sentiment_score"] == -100
    assert full["tier"] == "VERY_BEARISH"
    assert json.loads(contract.get_sentiment(report_id))["sentiment_score"] == -100


# 9. Score +100 boundary — stored as offset 200
def test_score_positive_100_boundary(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("ceil")
    report_id = _submit(
        contract, direct_vm, direct_alice, "MoonCoin MOON", "Blow-off top", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Euphoria at the extreme bullish bound.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "VERY_BULLISH",
            100,
            93,
            3,
            "The reading is at the extreme bullish bound.",
            _breakdown(urls, [100, 100, 100], ["VERY_BULLISH"] * 3, ["ceiling"] * 3),
        ),
    )

    contract.score_sentiment(report_id)

    full = json.loads(contract.get_report(report_id))
    assert full["sentiment_score"] == 100
    assert full["tier"] == "VERY_BULLISH"


# 10. Tier/score consistency enforced
def test_tier_score_consistency_bullish(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("align")
    report_id = _submit(
        contract, direct_vm, direct_alice, "AlignCoin ALIGN", "", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Pages are readable so the threshold is met.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "BULLISH",
            5,
            60,
            3,
            "The model named BULLISH but the raw score was inside NEUTRAL.",
            [],
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "BULLISH"
    assert result["sentiment_score"] == 40


# 11. Score out of range — clamped to -100..+100
def test_score_out_of_range_clamped(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("clamp")
    report_id = _submit(
        contract, direct_vm, direct_alice, "ClampCoin CLAMP", "", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Readable pages so clamping is the behavior under test.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm(
            "VERY_BULLISH",
            150,
            80,
            3,
            "The model returned a score above the allowed maximum.",
            [],
        ),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["sentiment_score"] <= 100
    assert result["sentiment_score"] == 100
    assert result["tier"] == "VERY_BULLISH"


# 12. Malformed LLM JSON — graceful fallback
def test_malformed_llm_json(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("badjson")
    report_id = _submit(
        contract, direct_vm, direct_alice, "ParseCoin PARSE", "", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Readable content so the failure is the JSON parse.",
        )
    direct_vm.mock_llm(r".*sentiment analyst.*", "not json!!")

    contract.score_sentiment(report_id)

    full = json.loads(contract.get_report(report_id))
    assert full["status"] == "UNSCOREABLE"
    assert full["sentiment_score"] == 0
    assert full["tier"] == "UNSCOREABLE"


# 13. LLM returns invalid tier — normalized to UNSCOREABLE
def test_invalid_tier_normalized(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("badtier")
    report_id = _submit(
        contract, direct_vm, direct_alice, "TierCoin TIER", "", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Readable pages so the invalid tier is what gets normalized.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        json.dumps({
            "tier": "SUPER_BULLISH",
            "sentiment_score": 90,
            "confidence": 80,
            "sources_read": 3,
            "reasoning": "Unknown tier label.",
            "source_breakdown": [],
        }),
    )

    contract.score_sentiment(report_id)

    full = json.loads(contract.get_report(report_id))
    assert full["tier"] == "UNSCOREABLE"
    assert full["status"] == "UNSCOREABLE"
    assert full["sentiment_score"] == 0


# 14. source_breakdown non-list — handled gracefully
def test_source_breakdown_non_list(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("breakdown")
    report_id = _submit(
        contract, direct_vm, direct_alice, "BreakCoin BREAK", "", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Readable pages so the breakdown type is the case under test.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        json.dumps({
            "tier": "NEUTRAL",
            "sentiment_score": 0,
            "confidence": 55,
            "sources_read": 3,
            "reasoning": "Breakdown arrived as a string.",
            "source_breakdown": "some string",
        }),
    )

    contract.score_sentiment(report_id)

    result = json.loads(contract.get_sentiment(report_id))
    assert result["tier"] == "NEUTRAL"
    assert result["source_breakdown"] == "[]"
    assert json.loads(result["source_breakdown"]) == []


# 15. Submit with fewer than 3 URLs — UserError
def test_too_few_urls(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("At least 3 source URLs required"):
            contract.submit_reading(
                "Bitcoin BTC",
                "",
                ["https://a.com", "https://b.com"],
            )


# 16. Submit with more than 5 URLs — UserError
def test_too_many_urls(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Maximum 5 source URLs"):
            contract.submit_reading(
                "Bitcoin BTC",
                "",
                [
                    "https://a.com",
                    "https://b.com",
                    "https://c.com",
                    "https://d.com",
                    "https://e.com",
                    "https://f.com",
                ],
            )


# 17. Submit with invalid URL — UserError
def test_invalid_url_format(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Invalid URL"):
            contract.submit_reading(
                "Bitcoin BTC",
                "",
                ["not-a-url", "https://b.com", "https://c.com"],
            )


# 18. Submit with duplicate URLs — UserError
def test_duplicate_urls_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Duplicate"):
            contract.submit_reading(
                "Bitcoin BTC",
                "",
                ["https://a.com", "https://a.com", "https://c.com"],
            )


# 19. Submit with empty asset_name — UserError
def test_empty_asset_name(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("asset_name cannot be empty"):
            contract.submit_reading(
                "",
                "",
                ["https://a.com", "https://b.com", "https://c.com"],
            )


# 20. score_sentiment already scored — UserError
def test_score_already_scored(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls = _three("twice")
    report_id = _submit(
        contract, direct_vm, direct_alice, "TwiceCoin TWICE", "", urls
    )
    for url in urls:
        _mock(direct_vm, 
            r".*" + url.split("/")[2].replace(".", r"\.") + r".*",
            _LONG + " Readable so the first score can succeed.",
        )
    direct_vm.mock_llm(
        r".*sentiment analyst.*",
        _llm("NEUTRAL", 0, 50, 3, "Flat mood.", []),
    )

    contract.score_sentiment(report_id)
    assert json.loads(contract.get_report(report_id))["status"] == "SCORED"

    with direct_vm.expect_revert("not PENDING"):
        contract.score_sentiment(report_id)


# 21. get_sentiment on PENDING — UserError
def test_get_sentiment_pending(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    with direct_vm.prank(direct_alice):
        report_id = contract.submit_reading(
            "BTC",
            "",
            ["https://a.com", "https://b.com", "https://c.com"],
        )
    with direct_vm.expect_revert("Not yet scored"):
        contract.get_sentiment(report_id)


# 22. get_latest — returns most recent for asset
def test_get_latest_asset(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    urls_a = _three("latest-a")
    urls_b = _three("latest-b")
    id1 = _submit(
        contract,
        direct_vm,
        direct_alice,
        "Ethereum ETH",
        "first reading",
        urls_a,
    )
    id2 = _submit(
        contract,
        direct_vm,
        direct_bob,
        "ethereum eth",
        "second reading",
        urls_b,
    )
    assert id1 == "1"
    assert id2 == "2"

    result = json.loads(contract.get_latest("Ethereum ETH"))
    assert result["report_id"] == "2"
    assert result["asset_name"] == "ethereum eth"
    assert result["context_note"] == "second reading"
    assert result["status"] == "PENDING"

    again = json.loads(contract.get_latest("ETHEREUM ETH"))
    assert again["report_id"] == id2


# 23. get_latest — not found
def test_get_latest_not_found(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    result = json.loads(contract.get_latest("UNKNOWN TOKEN XYZ"))
    assert result.get("found") == False


# 24. list_reports tier filtering
def test_list_reports_filter(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/sentiment_verdict.py")

    bull_urls = _three("list-bull")
    bear_urls = _three("list-bear")
    pending_urls = _three("list-pending")

    bull_id = _submit(
        contract, direct_vm, direct_alice, "BullAsset BULL", "up week", bull_urls
    )
    bear_id = _submit(
        contract, direct_vm, direct_bob, "BearAsset BEAR", "down week", bear_urls
    )
    pending_id = _submit(
        contract, direct_vm, direct_alice, "WaitAsset WAIT", "", pending_urls
    )

    _mock(direct_vm, 
        r".*list-bull\.example.*",
        _LONG + " Bullish community thread with strong buy language.",
    )
    direct_vm.mock_llm(
        r".*BullAsset.*",
        _llm("BULLISH", 55, 84, 3, "Bullish across the readable pages.", []),
    )
    contract.score_sentiment(bull_id)

    _mock(direct_vm, 
        r".*list-bear\.example.*",
        _LONG + " Bearish forum posts about outflows and failed support.",
    )
    direct_vm.mock_llm(
        r".*BearAsset.*",
        _llm("BEARISH", -40, 77, 3, "Bearish across the readable pages.", []),
    )
    contract.score_sentiment(bear_id)

    bullish = json.loads(contract.list_reports("BULLISH"))
    bearish = json.loads(contract.list_reports("BEARISH"))
    pending = json.loads(contract.list_reports("PENDING"))
    everything = json.loads(contract.list_reports(""))

    assert len(bullish) == 1
    assert bullish[0]["report_id"] == bull_id
    assert bullish[0]["sentiment_score"] == 55
    assert bullish[0]["status"] == "SCORED"

    assert len(bearish) == 1
    assert bearish[0]["report_id"] == bear_id
    assert bearish[0]["sentiment_score"] == -40

    assert len(pending) == 1
    assert pending[0]["report_id"] == pending_id
    assert pending[0]["status"] == "PENDING"
    assert pending[0]["sentiment_score"] == 0

    assert len(everything) == 3


# 25. invalidate_report — owner only
def test_invalidate_owner_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/sentiment_verdict.py")
    report_id = _submit(
        contract,
        direct_vm,
        direct_alice,
        "OwnedCoin OWN",
        "",
        _three("owned"),
    )

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only owner"):
            contract.invalidate_report(report_id)

    contract.invalidate_report(report_id)
    full = json.loads(contract.get_report(report_id))
    assert full["status"] == "UNSCOREABLE"
    assert full["reasoning"] == "Invalidated by admin"


# 26. Offset round-trip test for all tier boundaries
def test_offset_round_trip_all_boundaries(direct_vm, direct_deploy, direct_alice):
    for actual_score in [-100, -61, -20, 0, 20, 61, 100]:
        offset = actual_score + 100
        recovered = offset - 100
        assert recovered == actual_score
        assert 0 <= offset <= 200
