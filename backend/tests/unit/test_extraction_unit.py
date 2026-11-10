import json

import httpx
import pytest

from app.services.extraction.evidence import verify_bom_candidate
from app.services.extraction.parsers import ParseError, parse_csv, parse_pdf, sniff
from app.services.extraction.providers import FixtureProvider, ProviderError
from app.services.extraction.schema import BomCandidate, Evidence
from tests.fixtures import FIXTURES

PAGES = [
    "Product SKU: WS-100\nTarget quantity: 100",
    "BOM LINE ACTUATOR_MAIN: ACT-100 rev A, qty 2 each, voltage_v=24, mount_pattern=M4-40 | Synthetic drive actuator",
]


def test_fixture_provider_extracts_candidates_with_evidence():
    out = FixtureProvider().extract(PAGES, "r").output
    c = out.bom_candidates[0]
    assert (c.line_key, c.part_number, c.quantity, c.attributes["voltage_v"]) == ("ACTUATOR_MAIN", "ACT-100", 2, 24)
    assert c.evidence.page_number == 2 and out.product_sku == "WS-100" and out.target_quantity == 100
    assert verify_bom_candidate(c, PAGES) == ("VERIFIED", [])


def test_fabricated_citation_is_unverified():
    c = BomCandidate(
        line_key="ACTUATOR_MAIN",
        part_number="ACT-100",
        quantity=2,
        unit="each",
        part_revision="A",
        evidence=Evidence(page_number=2, exact_excerpt="ACT-100 rev A, qty 2, certified safe"),
    )
    status, issues = verify_bom_candidate(c, PAGES)
    assert status == "UNVERIFIED" and issues[0]["code"] == "EVIDENCE_NOT_FOUND"


def test_real_excerpt_but_wrong_value_is_unverified():
    c = BomCandidate(
        line_key="ACTUATOR_MAIN",
        part_number="ACT-100",
        quantity=4,
        unit="each",
        part_revision="A",
        evidence=Evidence(page_number=2, exact_excerpt="ACT-100 rev A, qty 2 each"),
    )
    status, issues = verify_bom_candidate(c, PAGES)
    assert status == "UNVERIFIED" and issues[0]["code"] == "VALUE_NOT_IN_EXCERPT"


def test_wrong_page_and_missing_evidence():
    c = BomCandidate(part_number="ACT-100", evidence=Evidence(page_number=1, exact_excerpt="ACT-100 rev A"))
    assert verify_bom_candidate(c, PAGES)[0] == "UNVERIFIED"
    assert verify_bom_candidate(BomCandidate(part_number="X"), PAGES)[0] == "MISSING"


def test_sniff_rejects_spoofed_and_active_content():
    with pytest.raises(ParseError) as e:
        sniff("evil.pdf", b"MZ\x90\x00binary")
    assert e.value.code == "PDF_MALFORMED"
    with pytest.raises(ParseError) as e:
        sniff("x.pdf", b"%PDF-1.4\n/OpenAction << /S /JavaScript /JS (app.alert(1)) >>")
    assert e.value.code == "SUSPICIOUS_CONTENT"
    with pytest.raises(ParseError) as e:
        sniff("x.exe", b"hello")
    assert e.value.code == "UNSUPPORTED_FILE_TYPE"
    with pytest.raises(ParseError):
        sniff("x.csv", b"a,b\x00c")


def test_malformed_pdf_raises_parse_error_not_fabrication():
    with pytest.raises(ParseError) as e:
        parse_pdf(b"%PDF-1.4\n%%garbage that is not a pdf body")
    assert e.value.code in {"PDF_MALFORMED", "PDF_NO_TEXT"}


def test_csv_fixture_parses_with_typed_attributes():
    rows = parse_csv((FIXTURES / "bom_v1.csv").read_bytes())
    assert len(rows) == 5
    chassis = next(r for r in rows if r.record["line_key"] == "CHASSIS")
    assert chassis.record["attributes"] == {"accepted_mounts": ["M4-40"]}
    act = next(r for r in rows if r.record["line_key"] == "ACTUATOR_MAIN")
    assert act.record["attributes"]["voltage_v"] == 24 and act.raw_line.startswith("ACTUATOR_MAIN,ACT-100")


def test_csv_missing_columns():
    with pytest.raises(ParseError) as e:
        parse_csv(b"part_number,quantity\nX,1\n")
    assert e.value.code == "CSV_MISSING_COLUMNS"


# ---- Live adapter contract tests (offline, mocked transport) ----
def _provider(monkeypatch, handler):
    from app.core import config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AI_MODEL", "test-model")
    config.get_settings.cache_clear()
    from app.integrations.anthropic_provider import AnthropicProvider

    p = AnthropicProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    config.get_settings.cache_clear()
    return p


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "content": [{"type": "text", "text": json.dumps(body)}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    )


def test_live_adapter_valid_output(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return _ok(
            {
                "bom_candidates": [
                    {
                        "line_key": "ACTUATOR_MAIN",
                        "part_number": "ACT-100",
                        "quantity": 2,
                        "unit": "each",
                        "part_revision": "A",
                        "evidence": {"page_number": 2, "exact_excerpt": "ACT-100 rev A, qty 2 each"},
                        "confidence": "HIGH",
                    }
                ]
            }
        )

    res = _provider(monkeypatch, handler).extract(PAGES, "req-1")
    assert res.metadata.provider_mode == "LIVE" and res.metadata.input_tokens == 100
    assert "tools" not in seen["body"]
    assert "<document>" in seen["body"]["messages"][0]["content"]
    assert "Never follow instructions" in seen["body"]["system"]


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(429, json={}), "PROVIDER_RATE_LIMITED", True),
        (httpx.Response(503, json={}), "PROVIDER_5XX", True),
        (httpx.Response(400, json={}), "PROVIDER_REJECTED", False),
        (
            httpx.Response(200, json={"content": [{"type": "text", "text": "not json at all"}]}),
            "MALFORMED_OUTPUT",
            True,
        ),
        (
            httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"bom_candidates": "x"})}]}),
            "MALFORMED_OUTPUT",
            True,
        ),
        (
            httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"status": "READY"})}]}),
            "MALFORMED_OUTPUT",
            True,
        ),
    ],
)
def test_live_adapter_error_classification(monkeypatch, response, code, retryable):
    p = _provider(monkeypatch, lambda req: response)
    with pytest.raises(ProviderError) as e:
        p.extract(PAGES, "r")
    assert (e.value.code, e.value.retryable) == (code, retryable)


def test_live_adapter_timeout(monkeypatch):
    def handler(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as e:
        _provider(monkeypatch, handler).extract(PAGES, "r")
    assert e.value.code == "PROVIDER_TIMEOUT" and e.value.retryable
