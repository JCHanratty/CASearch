"""Shared Jinja2Templates instance with globals configured."""

import re
from pathlib import Path
from markupsafe import Markup
from fastapi.templating import Jinja2Templates

from app.settings import settings
from app.services.prompts import get_suggested_prompts

# Shared templates instance
templates_path = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

# Expose settings and dynamic prompts to templates
templates.env.globals["SUGGESTED_PROMPTS"] = get_suggested_prompts
templates.env.globals["ORGANIZATION_NAME"] = settings.ORGANIZATION_NAME
templates.env.globals["LEGAL_DISCLAIMER"] = settings.LEGAL_DISCLAIMER


def _get_update_info():
    """Lazy accessor for update_info stored on app.state during startup."""
    try:
        from app.main import app
        return getattr(app.state, "update_info", None)
    except Exception:
        return None


templates.env.globals["update_info"] = _get_update_info


def regex_replace(value: str, pattern: str, replacement: str) -> str:
    """Custom Jinja2 filter for regex replacement."""
    if value is None:
        return ""
    return re.sub(pattern, replacement, str(value))


def render_ai_markdown(text: str) -> str:
    """Convert AI analysis markdown to compact HTML."""
    if not text:
        return ""

    lines = text.split('\n')
    html_parts = []
    in_table = False
    table_rows = []

    def process_inline(s: str) -> str:
        # Bold **text**
        s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
        # Citations [Doc, Page X] - make smaller
        s = re.sub(r'\[([^\]]+), Page (\d+)\]', r'<span class="text-slate-400 text-xs">[<span>\1</span>, p.\2]</span>', s)
        return s

    def flush_table():
        nonlocal table_rows, in_table
        if not table_rows:
            return ""
        html = '<div class="overflow-x-auto my-2"><table class="w-full text-xs border border-slate-300">'
        for i, row in enumerate(table_rows):
            cells = [c.strip() for c in row.split('|')[1:-1]]
            if i == 0:
                html += '<thead class="bg-slate-100"><tr>'
                for cell in cells:
                    html += f'<th class="px-2 py-1 text-left font-semibold text-slate-700 border-b border-slate-300">{process_inline(cell)}</th>'
                html += '</tr></thead><tbody>'
            elif '---' in row:
                continue  # skip separator
            else:
                html += '<tr class="border-b border-slate-200">'
                for cell in cells:
                    html += f'<td class="px-2 py-1 text-slate-600">{process_inline(cell)}</td>'
                html += '</tr>'
        html += '</tbody></table></div>'
        table_rows = []
        in_table = False
        return html

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            if in_table:
                html_parts.append(flush_table())
            continue

        # Table row
        if trimmed.startswith('|') and trimmed.endswith('|'):
            in_table = True
            table_rows.append(trimmed)
            continue

        # Flush any pending table
        if in_table:
            html_parts.append(flush_table())

        # Headings
        if trimmed.startswith('## '):
            html_parts.append(f'<h4 class="font-semibold text-slate-800 mt-3 mb-1 pb-1 border-b border-slate-200">{trimmed[3:]}</h4>')
        elif trimmed.startswith('# '):
            html_parts.append(f'<h3 class="font-bold text-slate-900 mt-3 mb-1">{trimmed[2:]}</h3>')
        # Bullets
        elif trimmed.startswith('- ') or trimmed.startswith('* '):
            html_parts.append(f'<div class="flex items-start my-0.5 ml-1"><span class="text-red-500 mr-1.5">•</span><span>{process_inline(trimmed[2:])}</span></div>')
        # Regular paragraph
        else:
            html_parts.append(f'<p class="my-1">{process_inline(trimmed)}</p>')

    # Flush any remaining table
    if in_table:
        html_parts.append(flush_table())

    return Markup(''.join(html_parts))


def highlight_text(text: str, term: str) -> str:
    """Highlight occurrences of a term in text (case-insensitive)."""
    if not text or not term:
        return Markup(text) if text else ""

    # Escape HTML in the text first
    import html
    text = html.escape(str(text))

    # Case-insensitive highlight
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    highlighted = pattern.sub(
        lambda m: f'<mark class="bg-yellow-200 px-0.5 rounded">{m.group()}</mark>',
        text
    )
    return Markup(highlighted)


# Register custom filters
templates.env.filters["regex_replace"] = regex_replace
templates.env.filters["render_ai_markdown"] = render_ai_markdown
templates.env.filters["highlight_text"] = highlight_text
