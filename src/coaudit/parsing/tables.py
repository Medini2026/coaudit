import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, FeatureNotFound, XMLParsedAsHTMLWarning

from coaudit.contracts import FilingBundle, TableKind, TableRef

SCALE_PATTERN = re.compile(r"in\s+(millions|thousands|billions)", re.IGNORECASE)
SCALE_VALUES = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}
TITLE_PATTERN = re.compile(
    r"balance sheets?|statements? of|results of operations|financial position|cash flows",
    re.IGNORECASE,
)
DIGITS_PATTERN = re.compile(r"\d+(\.\d+)?")
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
HEADER_ROWS_TO_SCAN = 6
DASH_CHARACTERS = {"-", "—", "–", "−"}
SKIPPED_SYMBOLS = {"$", ")", "(", "%", "$)", ")%", "$("}
STATEMENT_KINDS = (
    TableKind.BALANCE_SHEET,
    TableKind.INCOME_STATEMENT,
    TableKind.CASH_FLOW,
    TableKind.SEGMENT_NOTE,
)


@dataclass(frozen=True)
class ParsedRow:
    index: int
    label: str
    values: tuple[float, ...]
    raw_text: str


@dataclass(frozen=True)
class ParsedTable:
    table_id: str
    kind: TableKind
    title: str
    unit_scale: int
    rows: tuple[ParsedRow, ...]
    columns_ascending: bool = False


def clean_text(text: str) -> str:
    replaced = text.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", replaced).strip()


def parse_number(text: str) -> Optional[float]:
    cleaned = clean_text(text).replace("$", "").replace(" ", "")
    if not cleaned or cleaned in SKIPPED_SYMBOLS:
        return None
    if cleaned in DASH_CHARACTERS:
        return 0.0
    if cleaned.endswith("%"):
        return None
    negative = cleaned[0] in {"(", "-", "−"}
    digits = cleaned.strip("()-−").replace(",", "")
    if not DIGITS_PATTERN.fullmatch(digits):
        return None
    value = float(digits)
    return -value if negative else value


def parse_row(index: int, cells: list[str]) -> ParsedRow:
    texts = [clean_text(cell) for cell in cells]
    non_empty = [text for text in texts if text]
    label = ""
    values: list[float] = []
    for position, text in enumerate(non_empty):
        following = non_empty[position + 1] if position + 1 < len(non_empty) else ""
        number = parse_number(text)
        if number is not None:
            if following.startswith("%") or following.startswith(")%"):
                continue
            values.append(number)
            continue
        if text in SKIPPED_SYMBOLS:
            continue
        if not label:
            label = text
    raw_text = " | ".join(text for text in texts if text)
    return ParsedRow(index=index, label=label, values=tuple(values), raw_text=raw_text)


def detect_unit_scale(text: str) -> Optional[int]:
    match = SCALE_PATTERN.search(text)
    if match is None:
        return None
    return SCALE_VALUES[match.group(1).lower()]


def classify_table(rows: list[ParsedRow], context: str = "") -> TableKind:
    labels = [row.label.lower() for row in rows if row.label]

    def has(fragment: str) -> bool:
        return any(fragment in label for label in labels)

    def starts_with_any(prefixes: tuple[str, ...]) -> bool:
        return any(label.startswith(prefix) for label in labels for prefix in prefixes)

    if has("total assets") and (
        has("total liabilities") or has("total shareholders") or has("total stockholders")
    ):
        return TableKind.BALANCE_SHEET
    reports_operations = (
        has("operating activities")
        or has("net cash from operations")
        or has("cash from operations")
    )
    if reports_operations and has("investing"):
        return TableKind.CASH_FLOW
    reports_earnings = has("net income") or has("net earnings") or has("net loss")
    reports_sales = starts_with_any(
        (
            "total revenue",
            "revenue",
            "net sales",
            "total net sales",
            "net revenue",
            "net operating revenue",
        )
    )
    reports_tax_or_share = has("income tax") or has("per share")
    if (
        reports_earnings
        and reports_sales
        and reports_tax_or_share
        and not has("financing activities")
    ):
        return TableKind.INCOME_STATEMENT
    mentions_segment = "segment" in context.lower() or has("segment")
    has_total = any(label.startswith("total") for label in labels)
    if mentions_segment and has_total:
        return TableKind.SEGMENT_NOTE
    return TableKind.OTHER


def build_soup(html) -> BeautifulSoup:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        try:
            return BeautifulSoup(html, "lxml")
        except FeatureNotFound:
            return BeautifulSoup(html, "html.parser")


def nearest_context(element, limit: int = 8) -> list[str]:
    strings = []
    for text in element.find_all_previous(string=True, limit=limit * 6):
        cleaned = clean_text(str(text))
        if cleaned:
            strings.append(cleaned)
        if len(strings) >= limit:
            break
    return strings


def find_title(rows: list[ParsedRow], context_strings: list[str]) -> str:
    for text in context_strings:
        if TITLE_PATTERN.search(text) and len(text) < 120:
            return text
    for row in rows[:3]:
        if not row.values and row.label:
            return row.label
    return ""


def detect_ascending_columns(rows: list[ParsedRow]) -> bool:
    years: list[int] = []
    for row in rows[:HEADER_ROWS_TO_SCAN]:
        for match in YEAR_PATTERN.findall(row.raw_text):
            year = int(match)
            if not years or years[-1] != year:
                years.append(year)
    if len(years) < 2:
        return False
    return years[0] < years[-1]


def extract_tables(html) -> list[ParsedTable]:
    soup = build_soup(html)
    tables: list[ParsedTable] = []
    statement_scale: Optional[int] = None
    for position, element in enumerate(soup.find_all("table")):
        rows: list[ParsedRow] = []
        for row_element in element.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row_element.find_all(["td", "th"])
            ]
            parsed = parse_row(len(rows), cells)
            if parsed.raw_text:
                rows.append(parsed)
        if not rows:
            continue
        context_strings = nearest_context(element)
        context = " ".join(context_strings)
        leading_text = " ".join(row.raw_text for row in rows[:4])
        detected_scale = detect_unit_scale(leading_text) or detect_unit_scale(context)
        title = find_title(rows, context_strings)
        kind = classify_table(rows, f"{title} {context}")
        is_statement = kind in STATEMENT_KINDS
        if detected_scale is None and is_statement and statement_scale is not None:
            unit_scale = statement_scale
        else:
            unit_scale = detected_scale or 1
        if detected_scale is not None and is_statement:
            statement_scale = detected_scale
        tables.append(
            ParsedTable(
                table_id=f"t{position:04d}",
                kind=kind,
                title=title,
                unit_scale=unit_scale,
                rows=tuple(rows),
                columns_ascending=detect_ascending_columns(rows),
            )
        )
    return tables


def select_statement_tables(tables: list[ParsedTable]) -> dict[TableKind, ParsedTable]:
    chosen: dict[TableKind, ParsedTable] = {}
    for kind in STATEMENT_KINDS:
        candidates = [table for table in tables if table.kind == kind]
        if candidates:
            chosen[kind] = max(candidates, key=lambda table: len(table.rows))
    return chosen


def table_refs(tables: list[ParsedTable]) -> list[TableRef]:
    return [
        TableRef(
            table_id=table.table_id,
            kind=table.kind,
            title=table.title,
            unit_scale=table.unit_scale,
            row_count=len(table.rows),
        )
        for table in tables
    ]


def load_tables(bundle: FilingBundle) -> list[ParsedTable]:
    return extract_tables(Path(bundle.html_path).read_bytes())
