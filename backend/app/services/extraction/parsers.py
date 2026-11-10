"""Deterministic file parsing: content sniffing, CSV BOM parsing, PDF page text extraction."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

CSV_REQUIRED = {"line_key", "part_number", "part_revision", "quantity", "unit"}
CSV_CORE = {"line_key", "part_number", "part_revision", "description", "quantity", "unit"}
NUMERIC_ATTRS = {"voltage_v", "output_voltage_v", "input_voltage_v", "torque_nm", "current_a"}
LIST_ATTRS = {"accepted_mounts"}
SUSPICIOUS_PDF_TOKENS = (b"/JavaScript", b"/JS ", b"/Launch", b"/EmbeddedFile", b"/OpenAction /JS")


class ParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sniff(filename: str, data: bytes) -> str:
    """Return 'pdf' or 'csv' based on extension AND content; raise ParseError otherwise."""
    lower = filename.lower()
    if data.startswith(b"%PDF-"):
        if not lower.endswith(".pdf"):
            raise ParseError("EXTENSION_MISMATCH", "PDF content with non-.pdf extension")
        for tok in SUSPICIOUS_PDF_TOKENS:
            if tok in data:
                raise ParseError("SUSPICIOUS_CONTENT", "PDF contains active content (scripts/launch/embedded files)")
        return "pdf"
    if lower.endswith(".csv"):
        if b"\x00" in data:
            raise ParseError("UNSUPPORTED_FILE_TYPE", "CSV contains binary data")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ParseError("UNSUPPORTED_FILE_TYPE", "CSV must be UTF-8 text")
        return "csv"
    if lower.endswith(".pdf"):
        raise ParseError("PDF_MALFORMED", "File has .pdf extension but is not a PDF")
    raise ParseError("UNSUPPORTED_FILE_TYPE", "Only text-readable PDF and CSV files are accepted")


@dataclass(frozen=True)
class CsvRow:
    row_number: int
    raw_line: str
    record: dict[str, Any]


def _convert_attr(name: str, value: str) -> Any:
    if name in LIST_ATTRS:
        return [v.strip() for v in value.split(";") if v.strip()]
    if name in NUMERIC_ATTRS:
        try:
            num = float(value)
            return int(num) if num.is_integer() else num
        except ValueError:
            return value  # leave as string; BOM validation flags ATTRIBUTE_TYPE_INVALID
    return value


def parse_csv(data: bytes) -> list[CsvRow]:
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ParseError("CSV_EMPTY", "CSV has no header row")
    headers = {h.strip() for h in reader.fieldnames if h}
    missing = CSV_REQUIRED - headers
    if missing:
        raise ParseError("CSV_MISSING_COLUMNS", f"CSV missing required columns: {sorted(missing)}")
    rows: list[CsvRow] = []
    for idx, raw in enumerate(reader, start=2):
        clean = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
        if not any(clean.values()):
            continue
        record: dict[str, Any] = {k: clean.get(k, "") for k in CSV_CORE}
        record["attributes"] = {k: _convert_attr(k, v) for k, v in clean.items() if k not in CSV_CORE and v != ""}
        rows.append(CsvRow(idx, lines[idx - 1] if idx - 1 < len(lines) else "", record))
    if not rows:
        raise ParseError("CSV_EMPTY", "CSV contains no data rows")
    return rows


def parse_pdf(data: bytes, max_pages: int = 50) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ParseError("PDF_ENCRYPTED", "Encrypted PDFs are not supported")
        if len(reader.pages) > max_pages:
            raise ParseError("PDF_TOO_MANY_PAGES", f"PDF exceeds {max_pages} pages")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise ParseError("PDF_MALFORMED", f"PDF could not be parsed: {type(e).__name__}")
    if not pages:
        raise ParseError("PDF_MALFORMED", "PDF has no pages")
    if sum(len(p.strip()) for p in pages) < 20:
        raise ParseError("PDF_NO_TEXT", "No extractable text; scanned/image-only PDFs are out of scope for this demo")
    return pages


_WS = re.compile(r"\s+")
_TRANSLATE = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def normalize_text(s: str) -> str:
    return _WS.sub(" ", s.translate(_TRANSLATE)).strip().lower()
