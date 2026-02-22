"""PDF extraction service - extracts text from PDF files with normalization and table detection."""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Common 2-3 letter English words that should NOT be joined to adjacent words
# by the spurious-split fixer.  Used by normalize_text() pattern 4.
_COMMON_SHORT_WORDS: frozenset[str] = frozenset({
    # 2-letter
    'ad', 'ah', 'am', 'an', 'as', 'at', 'ax', 'be', 'by', 'do', 'go',
    'ha', 'he', 'hi', 'ho', 'if', 'in', 'is', 'it', 'ma', 'me', 'mr',
    'ms', 'my', 'no', 'of', 'oh', 'ok', 'on', 'or', 'ow', 'ox', 'so',
    'to', 'up', 'us', 'we',
    # 3-letter
    'abs', 'ace', 'act', 'add', 'age', 'ago', 'aid', 'aim', 'air', 'all',
    'and', 'any', 'apt', 'arc', 'are', 'ark', 'arm', 'art', 'ask', 'ate',
    'bad', 'bag', 'ban', 'bar', 'bat', 'bay', 'bed', 'bet', 'bid', 'big',
    'bit', 'bow', 'box', 'boy', 'bud', 'bug', 'bun', 'bus', 'but', 'buy',
    'cab', 'can', 'cap', 'car', 'cat', 'cop', 'cow', 'cry', 'cup', 'cut',
    'dad', 'dam', 'day', 'did', 'die', 'dig', 'dim', 'dip', 'dog', 'dot',
    'dry', 'dub', 'due', 'dug', 'dye', 'ear', 'eat', 'egg', 'ego', 'end',
    'era', 'eve', 'eye', 'fan', 'far', 'fat', 'fax', 'fed', 'fee', 'few',
    'fig', 'fin', 'fit', 'fix', 'fly', 'fog', 'for', 'fox', 'fry', 'fun',
    'fur', 'gap', 'gas', 'get', 'god', 'got', 'gum', 'gun', 'gut', 'guy',
    'gym', 'had', 'ham', 'has', 'hat', 'hay', 'hen', 'her', 'hid', 'him',
    'hip', 'his', 'hit', 'hog', 'hop', 'hot', 'how', 'hub', 'hug', 'hut',
    'ice', 'icy', 'ill', 'ink', 'inn', 'ion', 'its', 'ivy', 'jam', 'jar',
    'jaw', 'jay', 'jet', 'job', 'jog', 'joy', 'jug', 'key', 'kid', 'kin',
    'kit', 'lab', 'lad', 'lag', 'lap', 'law', 'lay', 'led', 'leg', 'let',
    'lid', 'lie', 'lip', 'lit', 'log', 'lot', 'low', 'mad', 'man', 'map',
    'mat', 'max', 'may', 'men', 'met', 'mid', 'mix', 'mob', 'mod', 'mom',
    'mop', 'mud', 'mug', 'nap', 'net', 'new', 'nil', 'nod', 'nor', 'not',
    'now', 'nun', 'nut', 'oak', 'oar', 'oat', 'odd', 'off', 'oft', 'oil',
    'old', 'one', 'opt', 'ore', 'our', 'out', 'owe', 'owl', 'own', 'pad',
    'pal', 'pan', 'par', 'pat', 'paw', 'pay', 'pea', 'peg', 'pen', 'per',
    'pet', 'pie', 'pig', 'pin', 'pit', 'ply', 'pod', 'pop', 'pot', 'pro',
    'pry', 'pub', 'pun', 'pup', 'put', 'rag', 'ram', 'ran', 'rap', 'rat',
    'raw', 'ray', 'red', 'ref', 'rib', 'rid', 'rig', 'rim', 'rip', 'rob',
    'rod', 'rot', 'row', 'rub', 'rug', 'rum', 'run', 'rut', 'rye', 'sad',
    'sag', 'sap', 'sat', 'saw', 'say', 'sea', 'set', 'sew', 'she', 'shy',
    'sin', 'sip', 'sir', 'sis', 'sit', 'six', 'ski', 'sky', 'sly', 'sob',
    'sod', 'son', 'sow', 'soy', 'spa', 'spy', 'sub', 'sue', 'sum', 'sun',
    'tab', 'tag', 'tan', 'tap', 'tar', 'tax', 'tea', 'ten', 'the', 'thy',
    'tie', 'tin', 'tip', 'toe', 'ton', 'too', 'top', 'tot', 'tow', 'toy',
    'try', 'tub', 'tug', 'two', 'urn', 'use', 'van', 'vat', 'vet', 'via',
    'vow', 'wad', 'wag', 'war', 'was', 'wax', 'way', 'web', 'wed', 'wet',
    'who', 'why', 'wig', 'win', 'wit', 'woe', 'wok', 'won', 'woo', 'wow',
    'yam', 'yap', 'yaw', 'yea', 'yes', 'yet', 'yew', 'you', 'zap', 'zen',
    'zip', 'zoo',
})


@dataclass
class TableData:
    """Extracted table from a PDF page."""
    page_number: int  # 1-indexed
    table_index: int  # 0-indexed within the page
    headers: list[str]
    rows: list[list[str]]
    markdown_text: str  # Markdown-formatted table
    context_heading: Optional[str] = None  # Heading above the table
    is_wage_table: bool = False


@dataclass
class PageText:
    """Extracted text from a single PDF page."""

    page_number: int  # 1-indexed
    text: str  # Cleaned text for indexing
    raw_text: str  # Original text before cleaning
    tables: list[TableData] = field(default_factory=list)


class ExtractionError(Exception):
    """Raised when PDF extraction fails."""

    pass


# Wage table detection keywords
WAGE_TABLE_KEYWORDS = [
    '$', '%', 'hour', 'hourly', 'annual', 'biweekly', 'bi-weekly',
    'step', 'classification', 'grade', 'level', 'rate', 'salary',
    'wage', 'pay', 'scale', 'schedule', 'premium', 'differential',
]


def detect_wage_table(headers: list[str], rows: list[list[str]]) -> bool:
    """
    Heuristic to detect if a table contains wage/rate data.

    Looks for dollar signs, percentage signs, and wage-related keywords
    in headers and first few rows.
    """
    # Check headers
    header_text = ' '.join(h.lower() for h in headers if h)
    if any(kw in header_text for kw in ['$', 'rate', 'salary', 'wage', 'pay', 'step', 'hour', 'annual']):
        return True

    # Check first 5 rows for dollar amounts or percentages
    dollar_count = 0
    for row in rows[:5]:
        row_text = ' '.join(str(cell) for cell in row if cell)
        if '$' in row_text or re.search(r'\d+\.\d{2}', row_text):
            dollar_count += 1
        if '%' in row_text:
            dollar_count += 1

    # If more than half the checked rows have dollar amounts, it's a wage table
    return dollar_count >= min(2, len(rows[:5]))


def format_table_as_markdown(headers: list[str], rows: list[list[str]]) -> str:
    """
    Format table data as a Markdown table string preserving column alignment.

    Args:
        headers: Column header strings
        rows: List of row data (list of cell strings)

    Returns:
        Markdown-formatted table string
    """
    if not headers and not rows:
        return ""

    # Determine number of columns
    num_cols = max(
        len(headers) if headers else 0,
        max((len(row) for row in rows), default=0) if rows else 0
    )

    if num_cols == 0:
        return ""

    # Pad headers/rows to uniform column count
    padded_headers = list(headers) + [''] * (num_cols - len(headers)) if headers else [''] * num_cols
    padded_rows = [list(row) + [''] * (num_cols - len(row)) for row in rows]

    # Clean cell values
    def clean_cell(val):
        if val is None:
            return ''
        return str(val).strip().replace('|', '/').replace('\n', ' ')

    clean_headers = [clean_cell(h) for h in padded_headers]
    clean_rows = [[clean_cell(c) for c in row] for row in padded_rows]

    # Calculate column widths
    col_widths = [max(len(h), 3) for h in clean_headers]
    for row in clean_rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))

    # Build markdown
    lines = []

    # Header row
    header_line = '| ' + ' | '.join(h.ljust(col_widths[i]) for i, h in enumerate(clean_headers)) + ' |'
    lines.append(header_line)

    # Separator
    sep_line = '| ' + ' | '.join('-' * col_widths[i] for i in range(num_cols)) + ' |'
    lines.append(sep_line)

    # Data rows
    for row in clean_rows:
        row_line = '| ' + ' | '.join(
            row[i].ljust(col_widths[i]) if i < len(row) else ' ' * col_widths[i]
            for i in range(num_cols)
        ) + ' |'
        lines.append(row_line)

    return '\n'.join(lines)


def extract_tables_from_page(pdf_path: Path, page_number: int, page_text: str = "") -> list[TableData]:
    """
    Extract tables from a specific PDF page using pdfplumber.

    Args:
        pdf_path: Path to the PDF file
        page_number: 1-indexed page number
        page_text: The extracted text of the page (for context heading detection)

    Returns:
        List of TableData objects found on the page
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed, skipping table extraction")
        return []

    tables = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                return []

            page = pdf.pages[page_number - 1]  # pdfplumber is 0-indexed
            extracted_tables = page.extract_tables()

            if not extracted_tables:
                return []

            # Try to find a context heading from the page text
            context_heading = _find_context_heading(page_text)

            for idx, table in enumerate(extracted_tables):
                if not table or len(table) < 2:
                    continue  # Skip empty or single-row tables

                # First row is typically headers
                raw_headers = [str(cell).strip() if cell else '' for cell in table[0]]
                raw_rows = []
                for row in table[1:]:
                    raw_rows.append([str(cell).strip() if cell else '' for cell in row])

                # Skip tables that are all empty
                all_cells = raw_headers + [c for r in raw_rows for c in r]
                if not any(c for c in all_cells):
                    continue

                is_wage = detect_wage_table(raw_headers, raw_rows)
                markdown = format_table_as_markdown(raw_headers, raw_rows)

                if markdown:
                    tables.append(TableData(
                        page_number=page_number,
                        table_index=idx,
                        headers=raw_headers,
                        rows=raw_rows,
                        markdown_text=markdown,
                        context_heading=context_heading,
                        is_wage_table=is_wage,
                    ))

    except Exception as e:
        logger.warning(f"Table extraction failed for page {page_number}: {e}")

    return tables


def _find_context_heading(page_text: str) -> Optional[str]:
    """
    Find the most likely heading from page text that provides context for tables.
    Looks for article/section headings or schedule/appendix markers.
    """
    if not page_text:
        return None

    lines = page_text.strip().split('\n')

    for line in lines[:10]:  # Check first 10 lines
        line = line.strip()
        if not line:
            continue
        # Article/Section heading
        if re.match(r'^(ARTICLE|Article|SECTION|Section|SCHEDULE|Schedule|APPENDIX|Appendix)', line):
            return line
        # All caps heading
        if len(line) > 5 and len(line) < 80 and line == line.upper() and line[0].isalpha():
            return line

    return None


def extract_all_tables(filepath: Path, pages: list[PageText]) -> list[TableData]:
    """
    Extract tables from all pages of a PDF.

    Args:
        filepath: Path to PDF file
        pages: Already-extracted PageText objects (for context heading detection)

    Returns:
        List of all TableData objects across the document
    """
    all_tables = []

    for page in pages:
        page_tables = extract_tables_from_page(filepath, page.page_number, page.text)
        page.tables = page_tables
        all_tables.extend(page_tables)

    if all_tables:
        logger.info(f"Extracted {len(all_tables)} tables from {filepath.name} "
                     f"({sum(1 for t in all_tables if t.is_wage_table)} wage tables)")

    return all_tables


def dehyphenate(text: str) -> str:
    """
    Fix line-break hyphenation where words are split across lines.

    Examples:
        "bene-\nfits" => "benefits"
        "over-\ntime" => "overtime"
    """
    # Pattern: word fragment + hyphen + newline + word fragment (lowercase continuation)
    pattern = r'(\w+)-\n(\w+)'

    def join_hyphenated(match):
        first_part = match.group(1)
        second_part = match.group(2)
        # Only join if second part starts with lowercase (indicates continuation)
        if second_part and second_part[0].islower():
            return first_part + second_part
        # Keep the hyphen for compound words that happen to be at line breaks
        return first_part + '-' + second_part

    return re.sub(pattern, join_hyphenated, text)


def normalize_text(text: str) -> str:
    """
    Normalize text for consistent indexing.

    - Dehyphenates line-break splits
    - Rejoins spurious single-letter splits from PDF extraction
    - Normalizes whitespace
    - Removes excessive blank lines
    """
    # First, dehyphenate
    text = dehyphenate(text)

    # Rejoin spurious single-letter splits caused by PDF text extraction.
    # These patterns fix broken words like "member s", "pe rform", "o f" that
    # commonly occur when PDF renderers insert extra spaces mid-word.

    # Pattern 1: two adjacent single lowercase letters - "o f" → "of"
    # Must run first so pairs like "o f" are joined before other patterns
    # consume them incorrectly.
    text = re.sub(r'(?:(?<=\s)|(?<=^))([b-hj-z]) ([b-hj-z])(?=\s|[.,;:!?\)]|$)', r'\1\2', text)

    # Pattern 2: trailing single letter on a word - "member s" → "members"
    # Matches: 2+ word chars, space, single lowercase letter (not 'a'/'i'),
    # followed by a space, punctuation, or end-of-string.
    text = re.sub(r'(\w{2,}) ([b-hj-z])(?=\s|[.,;:!?\)]|$)', r'\1\2', text)

    # Pattern 3: leading single letter before continuation - "e mployee" → "employee"
    # Matches: single lowercase letter (not 'a'/'i') preceded by whitespace or
    # start-of-string, followed by a space and 2+ lowercase chars.
    text = re.sub(r'(?:(?<=\s)|(?<=^))([b-hj-z]) ([a-z]{2,})', r'\1\2', text)

    # Pattern 4: short fragment (2-3 chars) before a longer continuation.
    # "pe rform" → "perform". Uses a word list to avoid joining real short
    # words like "do", "is", "no", "to" with the following word.
    def _rejoin_short_fragment(m):
        if m.group(1).lower() in _COMMON_SHORT_WORDS:
            return m.group(0)  # preserve real words
        return m.group(1) + m.group(2)

    text = re.sub(
        r'(?:(?<=\s)|(?<=^))([a-z]{2,3}) ([a-z]{3,})',
        _rejoin_short_fragment,
        text,
    )

    # Normalize various whitespace characters
    text = text.replace('\r\n', '\n')
    text = text.replace('\r', '\n')

    # Normalize lines
    lines = text.split('\n')
    normalized_lines = []

    for line in lines:
        # Normalize spaces within each line
        normalized_line = ' '.join(line.split())
        if normalized_line:
            normalized_lines.append(normalized_line)

    return '\n'.join(normalized_lines)


def detect_repeated_lines(pages: list[str], threshold: float = 0.6) -> set[str]:
    """
    Detect lines that appear on many pages (likely headers/footers).

    Args:
        pages: List of page texts
        threshold: Fraction of pages a line must appear on to be considered repeated

    Returns:
        Set of lines that appear on >= threshold fraction of pages
    """
    if len(pages) < 3:
        return set()  # Need at least 3 pages to detect patterns

    # Count line occurrences across all pages
    line_counts = Counter()

    for page_text in pages:
        # Get unique lines per page (don't count duplicates within same page)
        page_lines = set()
        for line in page_text.split('\n'):
            normalized = line.strip()
            if normalized and len(normalized) > 2:  # Ignore very short lines
                page_lines.add(normalized)

        for line in page_lines:
            line_counts[line] += 1

    # Find lines appearing on >= threshold of pages
    min_occurrences = int(len(pages) * threshold)
    repeated = set()

    for line, count in line_counts.items():
        if count >= min_occurrences:
            # Additional checks to avoid removing important content
            # Don't remove lines that look like article headers
            if not re.match(r'^Article\s+\d+', line, re.IGNORECASE):
                repeated.add(line)

    return repeated


def remove_repeated_lines(text: str, repeated_lines: set[str]) -> str:
    """
    Remove repeated header/footer lines from text.

    Args:
        text: Page text
        repeated_lines: Set of lines to remove

    Returns:
        Text with repeated lines removed
    """
    if not repeated_lines:
        return text

    lines = text.split('\n')
    filtered_lines = []

    for line in lines:
        normalized = line.strip()
        if normalized not in repeated_lines:
            filtered_lines.append(line)

    return '\n'.join(filtered_lines)


def extract_pdf_pages(filepath: Path, strip_headers_footers: bool = True) -> list[PageText]:
    """
    Extract text from each page of a PDF with normalization.
    Deterministic: same PDF always produces same output.

    Args:
        filepath: Path to PDF file
        strip_headers_footers: Whether to detect and remove repeated headers/footers

    Returns:
        List of PageText objects, one per page

    Raises:
        ExtractionError: If PDF cannot be read
    """
    try:
        reader = PdfReader(filepath)
    except Exception as e:
        raise ExtractionError(f"Cannot read PDF: {e}")

    # First pass: extract raw text from all pages
    raw_pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = f"[Page {i} extraction failed]"
        raw_pages.append(text)

    # Detect repeated lines (headers/footers) across all pages
    repeated_lines = set()
    if strip_headers_footers and len(raw_pages) >= 3:
        # Normalize first for consistent detection
        normalized_for_detection = [normalize_text(p) for p in raw_pages]
        repeated_lines = detect_repeated_lines(normalized_for_detection)

    # Second pass: normalize and clean each page
    pages = []
    for i, raw_text in enumerate(raw_pages, start=1):
        # Normalize the text
        normalized = normalize_text(raw_text)

        # Remove repeated headers/footers for indexing
        if repeated_lines:
            cleaned = remove_repeated_lines(normalized, repeated_lines)
        else:
            cleaned = normalized

        pages.append(PageText(
            page_number=i,
            text=cleaned,  # Cleaned version for FTS indexing
            raw_text=normalized,  # Normalized but complete text for display
        ))

    return pages


def get_pdf_page_count(filepath: Path) -> int:
    """Get the number of pages in a PDF without full extraction."""
    try:
        reader = PdfReader(filepath)
        return len(reader.pages)
    except Exception:
        return 0
