"""Synonym expansion service for search and QA.

Provides configurable synonym maps to expand queries and improve retrieval.
Supports both built-in mappings and admin-uploaded CSV/JSON files.
"""

import csv
import json
import re
from pathlib import Path
from typing import Optional

from app.db import get_db
from app.settings import settings


# Built-in synonym mappings for common labor contract terms
# Format: canonical_term -> [synonyms]
BUILTIN_SYNONYMS = {
    # Leave types
    "sick leave": ["sick time", "sick days", "illness leave", "medical leave", "sick pay"],
    "vacation": ["annual leave", "vacation leave", "paid time off", "pto", "holiday leave"],
    "bereavement": ["bereavement leave", "compassionate leave", "funeral leave"],
    "maternity": ["maternity leave", "parental leave", "pregnancy leave"],
    "paternity": ["paternity leave", "parental leave"],
    "lieu time": ["banked time", "time in lieu", "lieu days", "compensatory time", "comp time"],
    "statutory holiday": ["general holiday", "stat holiday", "named holiday", "public holiday"],
    "education leave": ["professional development", "training leave", "study leave", "ed leave"],
    "leave of absence": ["loa", "personal leave", "unpaid leave"],
    "jury duty": ["court leave", "jury leave", "witness leave"],
    # Compensation
    "wages": ["pay", "salary", "compensation", "earnings", "remuneration"],
    "overtime": ["ot", "overtime pay", "overtime rate", "time and a half", "overtime compensation"],
    "step increase": ["increment", "step progression", "wage step", "grid step", "pay step"],
    "acting pay": ["acting allowance", "temporary assignment pay", "higher duties pay"],
    "standby": ["on-call", "standby pay", "on call", "standby allowance"],
    "callback": ["call-back", "call-in", "call back pay", "call-back pay"],
    "shift differential": ["shift premium", "evening premium", "night premium", "weekend premium"],
    "cola": ["cost of living", "cost of living adjustment", "cost-of-living"],
    # Benefits
    "benefits": ["benefit", "employee benefits", "fringe benefits"],
    "dental": ["dental plan", "dental coverage", "dental benefits"],
    "health": ["health plan", "health coverage", "medical", "health benefits"],
    "pension": ["retirement", "retirement plan", "pension plan"],
    "ltd": ["long term disability", "long-term disability", "ltdi"],
    "std": ["short term disability", "short-term disability", "stdi", "weekly indemnity"],
    "eap": ["employee assistance", "employee assistance program", "employee assistance plan"],
    "life insurance": ["group life", "group life insurance", "ad&d"],
    "vision": ["vision care", "eye care", "optical", "vision benefits"],
    # Employment
    "seniority": ["tenure", "years of service", "service time"],
    "probation": ["probationary period", "trial period", "probationary"],
    "termination": ["dismissal", "firing", "discharge", "separation"],
    "layoff": ["lay off", "layoffs", "reduction in force", "rif"],
    "recall": ["callback", "call back", "return to work"],
    "discipline": ["disciplinary action", "progressive discipline", "corrective action"],
    "job posting": ["posting", "vacancy", "job competition", "internal posting"],
    "job classification": ["classification", "job class", "position classification"],
    # Union / Bargaining
    "grievance": ["grievances", "complaint", "dispute", "appeal"],
    "union": ["local", "bargaining unit", "association"],
    "collective agreement": ["collective bargaining agreement", "cba", "contract", "labor agreement"],
    "dues": ["union dues", "membership dues"],
    "arbitration": ["arbitrations", "arbitrator", "arbitral"],
    "union steward": ["steward", "shop steward", "union representative", "union rep"],
    # Scheduling / Hours
    "shift": ["shifts", "work shift", "tour of duty"],
    "hours of work": ["work hours", "working hours", "scheduled hours", "regular hours"],
    "flexible hours": ["flex time", "flextime", "flexible schedule", "variable hours"],
    # Safety
    "safety": ["occupational health", "ohs", "workplace safety", "health and safety"],
    "ppe": ["personal protective equipment", "protective equipment", "safety equipment"],
    "whmis": ["workplace hazardous materials", "hazardous materials information"],
    # Other
    "clothing allowance": ["uniform allowance", "boot allowance", "safety footwear"],
    "mileage": ["vehicle allowance", "travel allowance", "km rate", "kilometre rate"],
    "meal allowance": ["meal reimbursement", "per diem", "subsistence"],
}

# Reverse mapping: synonym -> canonical_term
_REVERSE_MAP: dict[str, str] = {}


def _build_reverse_map():
    """Build reverse mapping from synonyms to canonical terms."""
    global _REVERSE_MAP
    if _REVERSE_MAP:
        return

    for canonical, synonyms in BUILTIN_SYNONYMS.items():
        _REVERSE_MAP[canonical.lower()] = canonical.lower()
        for syn in synonyms:
            _REVERSE_MAP[syn.lower()] = canonical.lower()


def get_synonyms(term: str) -> list[str]:
    """
    Get all synonyms for a term (including the term itself).

    Args:
        term: The term to expand

    Returns:
        List of synonyms including the original term
    """
    _build_reverse_map()
    term_lower = term.lower()

    # Check if term is a canonical term
    if term_lower in BUILTIN_SYNONYMS:
        return [term_lower] + BUILTIN_SYNONYMS[term_lower]

    # Check if term is a synonym of something
    if term_lower in _REVERSE_MAP:
        canonical = _REVERSE_MAP[term_lower]
        if canonical in BUILTIN_SYNONYMS:
            return [canonical] + BUILTIN_SYNONYMS[canonical]

    # No synonyms found
    return [term_lower]


def expand_query(query: str, include_original: bool = True) -> list[str]:
    """
    Expand a query with synonyms for known terms.

    Args:
        query: Original query string
        include_original: Whether to include the original query

    Returns:
        List of expanded query variants
    """
    _build_reverse_map()
    query_lower = query.lower()
    expanded = []

    if include_original:
        expanded.append(query)

    # Check for multi-word synonym matches first (longest match wins)
    sorted_terms = sorted(_REVERSE_MAP.keys(), key=len, reverse=True)

    for term in sorted_terms:
        if term in query_lower and len(term) > 3:  # Skip very short matches
            synonyms = get_synonyms(term)
            for syn in synonyms:
                if syn != term:
                    variant = re.sub(re.escape(term), syn, query_lower, flags=re.IGNORECASE)
                    if variant not in expanded and variant != query_lower:
                        expanded.append(variant)

    return expanded if expanded else [query]


def detect_document_reference(query: str) -> tuple[Optional[int], str]:
    """
    Detect if query contains a document/file name reference.

    Checks query against indexed filenames to identify document-scoped searches.

    Args:
        query: User's query string

    Returns:
        Tuple of (file_id if found or None, remaining query without doc reference)
    """
    query_lower = query.lower()

    # Common query patterns that indicate document scoping
    scope_patterns = [
        r'\bfor\s+(?:the\s+)?(.+?)(?:\s+contract|\s+agreement|\s+local)?$',
        r'\bin\s+(?:the\s+)?(.+?)(?:\s+contract|\s+agreement|\s+local)?$',
        r'\bfrom\s+(?:the\s+)?(.+?)(?:\s+contract|\s+agreement|\s+local)?$',
        r'^(.+?)(?:\'s|s\')\s+',  # "Spruce Grove's sick leave"
    ]

    # Get all indexed filenames (with metadata short_name if available)
    with get_db() as conn:
        try:
            rows = conn.execute(
                "SELECT id, filename, short_name, employer_name, union_local, region FROM files WHERE status = 'indexed'"
            ).fetchall()
        except Exception:
            # Fallback for older schema without metadata columns
            rows = conn.execute(
                "SELECT id, filename FROM files WHERE status = 'indexed'"
            ).fetchall()

    if not rows:
        return None, query

    # Build a mapping of searchable names to file IDs
    file_matches = {}
    for row in rows:
        filename = row["filename"]
        file_id = row["id"]

        # Prefer short_name from metadata if available
        short_name = row["short_name"] if "short_name" in row.keys() else None
        if short_name:
            file_matches[short_name.lower()] = file_id

        # Also use employer_name and region for matching
        employer = row["employer_name"] if "employer_name" in row.keys() else None
        if employer:
            file_matches[employer.lower()] = file_id

        region = row["region"] if "region" in row.keys() else None
        if region:
            file_matches[region.lower()] = file_id

        union_local = row["union_local"] if "union_local" in row.keys() else None
        if union_local:
            file_matches[union_local.lower()] = file_id

        # Fallback: extract meaningful name from filename
        name = Path(filename).stem.lower()
        name = re.sub(r'^(collective[_\s]?agreement[_\s]?[-_]?|ca[_\s]?[-_]?)', '', name)
        name = re.sub(r'[-_]', ' ', name).strip()

        # Store multiple variations
        file_matches[name] = file_id
        file_matches[filename.lower()] = file_id

        # Also store individual words if multi-word name
        words = name.split()
        if len(words) >= 2:
            file_matches[' '.join(words)] = file_id
            if len(words) >= 2:
                file_matches[' '.join(words[:2])] = file_id
            file_matches[words[0]] = file_id

    # Try to find a document reference in the query
    best_match = None
    best_match_len = 0

    # First check for exact substring matches against file names
    for name, file_id in file_matches.items():
        if len(name) > 2 and name in query_lower:
            if len(name) > best_match_len:
                best_match = (file_id, name)
                best_match_len = len(name)

    if best_match:
        file_id, matched_name = best_match
        # Remove the document reference from query
        remaining = re.sub(
            rf'\b(for|in|from)\s+(the\s+)?{re.escape(matched_name)}(\s+contract|\s+agreement|\s+local)?\b',
            '',
            query,
            flags=re.IGNORECASE
        ).strip()

        # Also try removing possessive forms
        remaining = re.sub(
            rf'\b{re.escape(matched_name)}(\'s|s\')\s*',
            '',
            remaining,
            flags=re.IGNORECASE
        ).strip()

        # Clean up extra whitespace
        remaining = ' '.join(remaining.split())

        # If remaining query is too short, use original minus just the name
        if len(remaining.split()) < 2:
            remaining = re.sub(rf'\b{re.escape(matched_name)}\b', '', query, flags=re.IGNORECASE)
            remaining = ' '.join(remaining.split())

        return file_id, remaining if remaining else query

    return None, query


def load_custom_synonyms(filepath: Path) -> dict[str, list[str]]:
    """
    Load custom synonyms from a CSV or JSON file.

    CSV format: canonical_term,synonym1,synonym2,...
    JSON format: {"canonical_term": ["synonym1", "synonym2", ...]}

    Args:
        filepath: Path to the synonym file

    Returns:
        Dictionary of canonical_term -> [synonyms]
    """
    if not filepath.exists():
        return {}

    suffix = filepath.suffix.lower()

    if suffix == '.json':
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    elif suffix == '.csv':
        synonyms = {}
        with open(filepath, 'r', encoding='utf-8', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    canonical = row[0].strip().lower()
                    syns = [s.strip().lower() for s in row[1:] if s.strip()]
                    synonyms[canonical] = syns
        return synonyms

    return {}


def merge_synonyms(base: dict[str, list[str]], custom: dict[str, list[str]]) -> dict[str, list[str]]:
    """
    Merge custom synonyms into base synonyms.

    Custom synonyms can extend existing entries or add new ones.

    Args:
        base: Base synonym dictionary
        custom: Custom synonyms to merge

    Returns:
        Merged synonym dictionary
    """
    merged = {k: list(v) for k, v in base.items()}

    for canonical, syns in custom.items():
        if canonical in merged:
            # Extend existing, avoiding duplicates
            for syn in syns:
                if syn not in merged[canonical]:
                    merged[canonical].append(syn)
        else:
            merged[canonical] = syns

    return merged


# --- Custom Synonyms Storage Functions ---

# Cache for merged synonyms (built-in + custom)
_MERGED_SYNONYMS: dict[str, list[str]] = {}
_CUSTOM_SYNONYMS: dict[str, list[str]] = {}


def get_custom_synonyms_from_db() -> dict[str, list[str]]:
    """
    Load custom synonyms from the database.

    Returns:
        Dictionary of canonical_term -> [synonyms]
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT canonical_term, synonyms FROM custom_synonyms"
        ).fetchall()

    result = {}
    for row in rows:
        try:
            result[row["canonical_term"].lower()] = json.loads(row["synonyms"])
        except json.JSONDecodeError:
            continue

    return result


def save_custom_synonyms_to_db(synonyms: dict[str, list[str]], replace: bool = False) -> int:
    """
    Save custom synonyms to the database.

    Args:
        synonyms: Dictionary of canonical_term -> [synonyms]
        replace: If True, replace all existing synonyms. If False, merge with existing.

    Returns:
        Number of synonyms saved/updated
    """
    global _CUSTOM_SYNONYMS, _MERGED_SYNONYMS, _REVERSE_MAP

    with get_db() as conn:
        if replace:
            conn.execute("DELETE FROM custom_synonyms")

        count = 0
        for canonical, syns in synonyms.items():
            canonical_lower = canonical.lower()
            syns_lower = [s.lower() for s in syns if s.strip()]

            if not syns_lower:
                continue

            # Upsert: INSERT OR REPLACE
            conn.execute(
                """INSERT INTO custom_synonyms (canonical_term, synonyms, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(canonical_term) DO UPDATE SET
                   synonyms = excluded.synonyms,
                   updated_at = datetime('now')""",
                (canonical_lower, json.dumps(syns_lower))
            )
            count += 1

    # Reload cache after saving
    reload_synonyms()

    return count


def delete_custom_synonym(canonical_term: str) -> bool:
    """
    Delete a custom synonym from the database.

    Args:
        canonical_term: The canonical term to delete

    Returns:
        True if deleted, False if not found
    """
    global _CUSTOM_SYNONYMS, _MERGED_SYNONYMS, _REVERSE_MAP

    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM custom_synonyms WHERE canonical_term = ?",
            (canonical_term.lower(),)
        )
        deleted = result.rowcount > 0

    if deleted:
        reload_synonyms()

    return deleted


def reload_synonyms() -> dict[str, list[str]]:
    """
    Reload synonyms from database and rebuild the reverse map.

    Returns:
        The merged synonyms dictionary
    """
    global _CUSTOM_SYNONYMS, _MERGED_SYNONYMS, _REVERSE_MAP

    # Load custom synonyms from DB
    _CUSTOM_SYNONYMS = get_custom_synonyms_from_db()

    # Merge with built-in
    _MERGED_SYNONYMS = merge_synonyms(BUILTIN_SYNONYMS, _CUSTOM_SYNONYMS)

    # Rebuild the reverse map with merged synonyms
    _REVERSE_MAP = {}
    for canonical, synonyms_list in _MERGED_SYNONYMS.items():
        _REVERSE_MAP[canonical.lower()] = canonical.lower()
        for syn in synonyms_list:
            _REVERSE_MAP[syn.lower()] = canonical.lower()

    return _MERGED_SYNONYMS


def get_all_synonyms() -> dict[str, list[str]]:
    """
    Get all synonyms (built-in + custom merged).

    Returns:
        Merged synonym dictionary
    """
    global _MERGED_SYNONYMS

    if not _MERGED_SYNONYMS:
        reload_synonyms()

    return _MERGED_SYNONYMS


def get_builtin_synonyms() -> dict[str, list[str]]:
    """
    Get only the built-in synonyms.

    Returns:
        Built-in synonym dictionary
    """
    return BUILTIN_SYNONYMS.copy()


def get_custom_synonyms_only() -> dict[str, list[str]]:
    """
    Get only the custom synonyms (not merged with built-in).

    Returns:
        Custom synonym dictionary
    """
    global _CUSTOM_SYNONYMS

    if _CUSTOM_SYNONYMS is None:
        _CUSTOM_SYNONYMS = get_custom_synonyms_from_db()

    return _CUSTOM_SYNONYMS.copy()


def parse_uploaded_synonyms(content: bytes, filename: str) -> dict[str, list[str]]:
    """
    Parse uploaded synonym file (CSV or JSON).

    CSV format: canonical_term,synonym1,synonym2,...
    JSON format: {"canonical_term": ["synonym1", "synonym2", ...]}

    Args:
        content: File content bytes
        filename: Original filename (used to detect format)

    Returns:
        Dictionary of canonical_term -> [synonyms]

    Raises:
        ValueError: If file format is invalid or parsing fails
    """
    text = content.decode('utf-8')
    suffix = Path(filename).suffix.lower()

    if suffix == '.json':
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("JSON must be an object with canonical terms as keys")

            result = {}
            for canonical, syns in data.items():
                if not isinstance(syns, list):
                    raise ValueError(f"Synonyms for '{canonical}' must be a list")
                result[canonical.lower().strip()] = [s.lower().strip() for s in syns if s.strip()]

            return result
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")

    elif suffix == '.csv':
        result = {}
        lines = text.strip().split('\n')

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Parse CSV line
            try:
                reader = csv.reader([line])
                row = next(reader)
            except csv.Error as e:
                raise ValueError(f"CSV error on line {line_num}: {e}")

            if len(row) < 2:
                continue  # Skip lines without at least canonical + 1 synonym

            canonical = row[0].strip().lower()
            syns = [s.strip().lower() for s in row[1:] if s.strip()]

            if canonical and syns:
                result[canonical] = syns

        return result

    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .csv or .json")
