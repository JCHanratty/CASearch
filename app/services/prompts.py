"""Prompts service - dynamic suggested prompts for Q&A based on indexed documents."""

import logging
from app.db import get_db
from app.settings import settings

logger = logging.getLogger(__name__)

# Common topics that apply to most collective agreements
COMMON_TOPICS = [
    "overtime rate",
    "sick leave policy",
    "vacation days",
    "grievance procedure",
    "wages and salary schedule",
    "benefits and health coverage",
    "seniority provisions",
    "bereavement leave",
    "pension plan",
    "hours of work",
]

# Templates that use document short_names
DOC_TEMPLATES = [
    "What is the overtime rate in {name}?",
    "Show me the sick leave policy for {name}.",
    "How many vacation days does {name} provide after 5 years?",
    "What is the grievance procedure in {name}?",
    "Summarize the wage schedule for {name}.",
    "What benefits does {name} offer?",
]

# General templates (no specific doc)
GENERAL_TEMPLATES = [
    "Compare overtime rates across all agreements.",
    "Which agreement has the most vacation days?",
    "What are the scheduling rules?",
    "Summarize the pension provisions.",
]


def get_suggested_prompts(limit: int = 6) -> list[str]:
    """Generate dynamic suggested prompts based on indexed documents.

    If documents with short_names exist, builds personalized prompts.
    Falls back to static prompts from settings if no documents are indexed.

    Args:
        limit: Maximum number of prompts to return

    Returns:
        List of suggested prompt strings
    """
    try:
        doc_names = _get_indexed_doc_names()
    except Exception as e:
        logger.warning("Failed to load doc names for prompts: %s", e)
        doc_names = []

    if not doc_names:
        return settings.SUGGESTED_PROMPTS[:limit]

    prompts = []

    # Add document-specific prompts (rotate through names and templates)
    for i, name in enumerate(doc_names[:3]):
        template = DOC_TEMPLATES[i % len(DOC_TEMPLATES)]
        prompts.append(template.format(name=name))

    # Add general prompts
    for template in GENERAL_TEMPLATES:
        if len(prompts) >= limit:
            break
        prompts.append(template)

    return prompts[:limit]


def _get_indexed_doc_names() -> list[str]:
    """Get short names of indexed documents, preferring metadata short_name."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT short_name, filename FROM files WHERE status = 'indexed' ORDER BY filename"
            ).fetchall()
    except Exception:
        return []

    names = []
    for row in rows:
        name = row["short_name"] if row["short_name"] else row["filename"].replace(".pdf", "")
        names.append(name)
    return names
