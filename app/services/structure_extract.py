"""Structure-aware PDF extraction with heading detection and semantic chunking.

This module extracts PDF content while preserving document structure:
- Detects headings (Articles, Sections, numbered items)
- Creates semantic chunks based on document hierarchy
- Stores metadata for each chunk (heading, parent, page range)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from collections import Counter

from pypdf import PdfReader


@dataclass
class Heading:
    """Represents a detected heading in the document."""
    level: int  # 1 = Article, 2 = Section, 3 = Subsection
    text: str
    page_number: int
    line_number: int
    heading_type: str  # 'article', 'section', 'numbered', 'caps'


@dataclass
class DocumentChunk:
    """A semantic chunk of document content with metadata."""
    chunk_id: int
    text: str
    heading: Optional[str] = None
    parent_heading: Optional[str] = None
    section_number: Optional[str] = None
    page_start: int = 1
    page_end: int = 1
    headings_in_chunk: list[str] = field(default_factory=list)
    chunk_type: str = 'text'  # 'text' or 'table'


@dataclass
class StructuredPage:
    """Page content with structural annotations."""
    page_number: int
    text: str
    raw_text: str
    headings: list[Heading] = field(default_factory=list)


# Heading detection patterns - enhanced for collective agreements
HEADING_PATTERNS = [
    # ARTICLE patterns (Level 1) - various formats
    (r'^ARTICLE\s+([IVXLCDM]+|\d+)[:\s]*[-–—]?\s*(.*)$', 1, 'article'),
    (r'^Article\s+([IVXLCDM]+|\d+)[:\s]*[-–—]?\s*(.*)$', 1, 'article'),
    (r'^ART\.?\s*([IVXLCDM]+|\d+)[:\s]*[-–—]?\s*(.*)$', 1, 'article'),

    # SECTION patterns (Level 2) - including decimal notation
    (r'^SECTION\s+(\d+(?:\.\d+)?)[:\s]*[-–—]?\s*(.*)$', 2, 'section'),
    (r'^Section\s+(\d+(?:\.\d+)?)[:\s]*[-–—]?\s*(.*)$', 2, 'section'),
    (r'^Sec\.?\s*(\d+(?:\.\d+)?)[:\s]*[-–—]?\s*(.*)$', 2, 'section'),

    # Decimal numbered sections common in contracts: 7.01, 12.03, 15.1.2 (Level 2-3)
    (r'^(\d+\.\d{2})\s+(.+)$', 2, 'numbered'),  # 7.01 Overtime
    (r'^(\d+\.\d+(?:\.\d+)?)\s+(.+)$', 2, 'numbered'),

    # Roman numeral sections (Level 2)
    (r'^([IVXLCDM]+)\.\s+(.+)$', 2, 'roman'),

    # Letter sections with content: (a) ..., A. ..., a) ... (Level 3)
    (r'^\(([a-zA-Z])\)\s+(.{10,})$', 3, 'lettered'),
    (r'^([a-zA-Z])\.\s+(.{10,})$', 3, 'lettered'),
    (r'^([a-zA-Z])\)\s+(.{10,})$', 3, 'lettered'),

    # Roman numeral subsections: (i), (ii), (iii) (Level 3)
    (r'^\(([ivxlcdm]+)\)\s+(.+)$', 3, 'roman_sub'),

    # SCHEDULE/APPENDIX patterns (Level 1)
    (r'^(SCHEDULE|APPENDIX|EXHIBIT)\s+([A-Z]|\d+)[:\s]*[-–—]?\s*(.*)$', 1, 'appendix'),
    (r'^(Schedule|Appendix|Exhibit)\s+([A-Z]|\d+)[:\s]*[-–—]?\s*(.*)$', 1, 'appendix'),

    # LETTER OF UNDERSTANDING (Level 1)
    (r'^LETTER\s+OF\s+(UNDERSTANDING|AGREEMENT)[:\s]*(.*)$', 1, 'letter'),

    # ALL CAPS headings (potential Level 1-2)
    (r'^([A-Z][A-Z\s]{4,50})$', 2, 'caps'),
]

# Phrases that indicate a heading even without formatting
HEADING_KEYWORDS = [
    'PREAMBLE', 'DEFINITIONS', 'RECOGNITION', 'MANAGEMENT RIGHTS',
    'UNION SECURITY', 'GRIEVANCE', 'ARBITRATION', 'DISCIPLINE',
    'SENIORITY', 'LAYOFF', 'RECALL', 'HOURS OF WORK', 'OVERTIME',
    'HOLIDAYS', 'VACATION', 'SICK LEAVE', 'LEAVE OF ABSENCE',
    'BENEFITS', 'INSURANCE', 'PENSION', 'WAGES', 'SALARIES',
    'CLASSIFICATIONS', 'PROBATION', 'TRAINING', 'SAFETY', 'HEALTH',
    'DURATION', 'TERMINATION', 'GENERAL PROVISIONS', 'APPENDIX',
    'SCHEDULE', 'LETTER OF UNDERSTANDING', 'MEMORANDUM'
]


def detect_heading(line: str, line_number: int, page_number: int) -> Optional[Heading]:
    """
    Detect if a line is a heading and determine its level.

    Args:
        line: The text line to analyze
        line_number: Line number within the page
        page_number: Page number in the document

    Returns:
        Heading object if detected, None otherwise
    """
    line = line.strip()
    if not line or len(line) < 3:
        return None

    # Skip lines that are too long to be headings
    if len(line) > 100:
        return None

    # Check against patterns
    for pattern, level, heading_type in HEADING_PATTERNS:
        match = re.match(pattern, line, re.IGNORECASE if heading_type != 'caps' else 0)
        if match:
            return Heading(
                level=level,
                text=line,
                page_number=page_number,
                line_number=line_number,
                heading_type=heading_type
            )

    # Check for keyword-based headings (ALL CAPS keywords)
    upper_line = line.upper()
    for keyword in HEADING_KEYWORDS:
        if upper_line == keyword or upper_line.startswith(keyword + ' '):
            return Heading(
                level=1 if keyword in ['PREAMBLE', 'DEFINITIONS'] else 2,
                text=line,
                page_number=page_number,
                line_number=line_number,
                heading_type='keyword'
            )

    return None


def extract_section_number(heading_text: str) -> Optional[str]:
    """Extract the section/article number from a heading."""
    # Try various patterns
    patterns = [
        r'ARTICLE\s+([IVXLCDM]+|\d+)',
        r'Article\s+([IVXLCDM]+|\d+)',
        r'SECTION\s+(\d+(?:\.\d+)?)',
        r'Section\s+(\d+(?:\.\d+)?)',
        r'^(\d+\.\d+(?:\.\d+)?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, heading_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def dehyphenate(text: str) -> str:
    """Fix line-break hyphenation."""
    pattern = r'(\w+)-\n(\w+)'

    def join_hyphenated(match):
        first_part = match.group(1)
        second_part = match.group(2)
        if second_part and second_part[0].islower():
            return first_part + second_part
        return first_part + '-' + second_part

    return re.sub(pattern, join_hyphenated, text)


def normalize_text(text: str) -> str:
    """Normalize text whitespace."""
    text = dehyphenate(text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    lines = text.split('\n')
    normalized_lines = []

    for line in lines:
        normalized_line = ' '.join(line.split())
        if normalized_line:
            normalized_lines.append(normalized_line)

    return '\n'.join(normalized_lines)


def detect_repeated_lines(pages: list[str], threshold: float = 0.6) -> set[str]:
    """Detect lines that appear on many pages (headers/footers)."""
    if len(pages) < 3:
        return set()

    line_counts = Counter()

    for page_text in pages:
        page_lines = set()
        for line in page_text.split('\n'):
            normalized = line.strip()
            if normalized and len(normalized) > 2:
                page_lines.add(normalized)

        for line in page_lines:
            line_counts[line] += 1

    min_occurrences = int(len(pages) * threshold)
    repeated = set()

    for line, count in line_counts.items():
        if count >= min_occurrences:
            # Don't remove article/section headings
            if not re.match(r'^(Article|ARTICLE|Section|SECTION)\s+', line):
                repeated.add(line)

    return repeated


def extract_structured_pages(filepath: Path) -> list[StructuredPage]:
    """
    Extract pages with structural annotations (headings detected).

    Args:
        filepath: Path to PDF file

    Returns:
        List of StructuredPage objects with heading annotations
    """
    try:
        reader = PdfReader(filepath)
    except Exception as e:
        raise Exception(f"Cannot read PDF: {e}")

    # First pass: extract raw text
    raw_pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = f"[Page {i} extraction failed]"
        raw_pages.append(text)

    # Detect repeated lines
    normalized_pages = [normalize_text(p) for p in raw_pages]
    repeated_lines = detect_repeated_lines(normalized_pages)

    # Second pass: structure extraction
    structured_pages = []

    for i, raw_text in enumerate(raw_pages, start=1):
        normalized = normalize_text(raw_text)

        # Remove repeated headers/footers
        if repeated_lines:
            lines = normalized.split('\n')
            filtered_lines = [l for l in lines if l.strip() not in repeated_lines]
            cleaned = '\n'.join(filtered_lines)
        else:
            cleaned = normalized

        # Detect headings in this page
        headings = []
        for line_num, line in enumerate(cleaned.split('\n'), start=1):
            heading = detect_heading(line, line_num, i)
            if heading:
                headings.append(heading)

        structured_pages.append(StructuredPage(
            page_number=i,
            text=cleaned,
            raw_text=normalized,
            headings=headings
        ))

    return structured_pages


def create_semantic_chunks(
    pages: list[StructuredPage],
    max_chunk_size: int = 2000,
    min_chunk_size: int = 200,
    overlap_size: int = 200,
    tables: list = None,
) -> list[DocumentChunk]:
    """
    Create semantic chunks based on document structure with overlap.

    Chunks are created at heading boundaries (Articles/Sections),
    with fallback to page boundaries for unstructured content.
    Tables get their own dedicated chunks (exempt from size splitting).
    Overlap ensures context continuity across chunk boundaries.

    Args:
        pages: List of StructuredPage objects
        max_chunk_size: Maximum characters per chunk
        min_chunk_size: Minimum characters before starting new chunk at heading
        overlap_size: Characters of overlap between consecutive chunks
        tables: Optional list of TableData objects from pdf_extract

    Returns:
        List of DocumentChunk objects
    """
    chunks = []
    chunk_id = 0
    previous_chunk_text = ""  # Track for overlap

    # Build a map of tables by page number for quick lookup
    tables_by_page = {}
    if tables:
        for table in tables:
            page_num = table.page_number
            if page_num not in tables_by_page:
                tables_by_page[page_num] = []
            tables_by_page[page_num].append(table)

    # Collect all headings with their positions
    all_headings = []
    for page in pages:
        for heading in page.headings:
            all_headings.append({
                'heading': heading,
                'page': page.page_number
            })

    # If no headings found, fall back to page-based chunks with overlap
    if not all_headings:
        for page in pages:
            chunk_id += 1
            # Add overlap from previous chunk
            overlap = _get_overlap_text(previous_chunk_text, overlap_size) if previous_chunk_text else ""
            text_with_overlap = (overlap + "\n\n" + page.text).strip() if overlap else page.text
            chunks.append(DocumentChunk(
                chunk_id=chunk_id,
                text=text_with_overlap,
                page_start=page.page_number,
                page_end=page.page_number
            ))
            previous_chunk_text = page.text
        return chunks

    # Build chunks based on headings
    current_chunk_text = []
    current_heading = None
    current_parent = None
    current_section = None
    current_page_start = 1
    current_headings = []

    for page in pages:
        lines = page.text.split('\n')
        line_idx = 0

        for line in lines:
            line_idx += 1

            # Check if this line is a heading
            heading_match = None
            for h in page.headings:
                if h.line_number == line_idx:
                    heading_match = h
                    break

            if heading_match and heading_match.level <= 2:
                # Level 1-2 heading: potentially start new chunk
                current_text = '\n'.join(current_chunk_text).strip()

                if current_text and len(current_text) >= min_chunk_size:
                    # Save current chunk with overlap from previous
                    chunk_id += 1
                    overlap = _get_overlap_text(previous_chunk_text, overlap_size) if previous_chunk_text else ""
                    text_with_overlap = (overlap + "\n\n" + current_text).strip() if overlap else current_text
                    chunks.append(DocumentChunk(
                        chunk_id=chunk_id,
                        text=text_with_overlap,
                        heading=current_heading,
                        parent_heading=current_parent,
                        section_number=current_section,
                        page_start=current_page_start,
                        page_end=page.page_number,
                        headings_in_chunk=current_headings.copy()
                    ))
                    previous_chunk_text = current_text  # Store for next overlap
                    current_chunk_text = []
                    current_headings = []
                    current_page_start = page.page_number

                # Update heading context
                if heading_match.level == 1:
                    current_parent = None
                    current_heading = heading_match.text
                else:
                    if current_heading:
                        current_parent = current_heading
                    current_heading = heading_match.text

                current_section = extract_section_number(heading_match.text)
                current_headings.append(heading_match.text)

            current_chunk_text.append(line)

            # Check chunk size
            current_size = len('\n'.join(current_chunk_text))
            if current_size >= max_chunk_size:
                # Force chunk boundary with overlap
                chunk_id += 1
                current_text = '\n'.join(current_chunk_text).strip()
                overlap = _get_overlap_text(previous_chunk_text, overlap_size) if previous_chunk_text else ""
                text_with_overlap = (overlap + "\n\n" + current_text).strip() if overlap else current_text
                chunks.append(DocumentChunk(
                    chunk_id=chunk_id,
                    text=text_with_overlap,
                    heading=current_heading,
                    parent_heading=current_parent,
                    section_number=current_section,
                    page_start=current_page_start,
                    page_end=page.page_number,
                    headings_in_chunk=current_headings.copy()
                ))
                previous_chunk_text = current_text  # Store for next overlap
                current_chunk_text = []
                current_headings = []
                current_page_start = page.page_number

    # Don't forget the last chunk (with overlap)
    if current_chunk_text:
        chunk_id += 1
        final_text = '\n'.join(current_chunk_text).strip()
        if final_text:
            overlap = _get_overlap_text(previous_chunk_text, overlap_size) if previous_chunk_text else ""
            text_with_overlap = (overlap + "\n\n" + final_text).strip() if overlap else final_text
            chunks.append(DocumentChunk(
                chunk_id=chunk_id,
                text=text_with_overlap,
                heading=current_heading,
                parent_heading=current_parent,
                section_number=current_section,
                page_start=current_page_start,
                page_end=pages[-1].page_number if pages else 1,
                headings_in_chunk=current_headings
            ))

    # Create dedicated table chunks (tables stay whole, exempt from size splitting)
    if tables_by_page:
        for page_num, page_tables in sorted(tables_by_page.items()):
            for table in page_tables:
                chunk_id += 1
                # Use the table's context heading or find nearest heading from text chunks
                table_heading = table.context_heading
                if not table_heading:
                    # Find the nearest heading from existing chunks for this page
                    for c in reversed(chunks):
                        if c.page_start <= page_num <= c.page_end and c.heading:
                            table_heading = c.heading
                            break

                chunks.append(DocumentChunk(
                    chunk_id=chunk_id,
                    text=table.markdown_text,
                    heading=table_heading,
                    page_start=page_num,
                    page_end=page_num,
                    headings_in_chunk=[table_heading] if table_heading else [],
                    chunk_type='table',
                ))

    return chunks


def extract_with_structure(filepath: Path, tables: list = None) -> tuple[list[StructuredPage], list[DocumentChunk]]:
    """
    Extract PDF with full structure analysis.

    Args:
        filepath: Path to PDF file
        tables: Optional pre-extracted TableData list from pdfplumber

    Returns:
        Tuple of (structured_pages, semantic_chunks)
    """
    pages = extract_structured_pages(filepath)
    chunks = create_semantic_chunks(pages, tables=tables)
    return pages, chunks


def _get_overlap_text(text: str, overlap_size: int) -> str:
    """
    Get the last N characters of text for overlap, breaking at word boundary.

    Args:
        text: Source text to get overlap from
        overlap_size: Target overlap size in characters

    Returns:
        Overlap text (may be slightly shorter to break at word boundary)
    """
    if len(text) <= overlap_size:
        return text

    # Get last overlap_size characters
    overlap = text[-overlap_size:]

    # Find first word boundary (space) to avoid splitting words
    space_idx = overlap.find(' ')
    if space_idx > 0 and space_idx < overlap_size // 2:
        overlap = overlap[space_idx + 1:]

    return overlap.strip()


def get_document_outline(pages: list[StructuredPage]) -> list[dict]:
    """
    Generate a document outline (table of contents) from extracted headings.

    Args:
        pages: List of StructuredPage objects

    Returns:
        List of outline entries with level, text, and page number
    """
    outline = []

    for page in pages:
        for heading in page.headings:
            outline.append({
                'level': heading.level,
                'text': heading.text,
                'page': heading.page_number,
                'type': heading.heading_type
            })

    return outline
