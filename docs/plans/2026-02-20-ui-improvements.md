# UI Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix admin login bug, move quick prompts to Q&A page, add shadow/elevation to navbar & sidebar, make version display dynamic, and update .env.example.

**Architecture:** All changes are in the existing FastAPI + Jinja2 + Tailwind app. No new dependencies or files required — only modifications to existing templates, settings, and config files.

**Tech Stack:** Python/FastAPI, Jinja2 templates, Tailwind CSS (CDN), pydantic-settings

---

### Task 1: Fix Admin Login — Diagnose .env Loading

**Files:**
- Modify: `app/settings.py:54-56`

**Context:** The `Settings` class uses pydantic-settings v2 (`from pydantic_settings import BaseSettings`) but declares env file loading via a v1-style inner `Config` class. While pydantic-settings v2 has backwards compatibility for `Config`, this can be unreliable. The `.env` file contains `ADMIN_PASSWORD=Local302!` but `settings.ADMIN_PASSWORD` returns `""` (the default), causing `admin_enabled()` to return `False`.

**Step 1: Migrate Settings to pydantic-settings v2 syntax**

In `app/settings.py`, replace the inner `Config` class with the proper v2 `model_config`:

```python
# Replace lines 54-56:
#     class Config:
#         env_file = ".env"
#         env_file_encoding = "utf-8"
#
# With:
from pydantic_settings import BaseSettings, SettingsConfigDict

# Add to Settings class body (replace the Config class):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )
```

Note: The `SettingsConfigDict` import must be added to the existing import on line 4. The final import line should be:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
```

**Step 2: Verify the fix**

Run the app and navigate to `/admin/login`:
```bash
cd "C:/Users/jorda/Desktop/AI Collective Group"
.venv/Scripts/python -c "from app.settings import settings; print('ADMIN_PASSWORD:', repr(settings.ADMIN_PASSWORD))"
```

Expected: `ADMIN_PASSWORD: 'Local302!'` (not empty string)

If this still shows `''`, the fallback fix is to make `env_file` use an absolute path:
```python
import os
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

model_config = SettingsConfigDict(
    env_file=env_path,
    env_file_encoding="utf-8",
)
```

**Step 3: Commit**

```bash
git add app/settings.py
git commit -m "fix: migrate Settings to pydantic-settings v2 model_config for reliable .env loading"
```

---

### Task 2: Move Quick Prompts from Sidebar to Q&A Page

**Files:**
- Modify: `templates/components/sidebar.html:91-128` (remove quick prompts section + usePrompt JS)
- Modify: `templates/qa.html:42-43` (add quick prompts below the form card)

**Step 1: Remove Quick Prompts from sidebar**

In `templates/components/sidebar.html`, remove lines 91-108 (the "Suggested Prompts" section including the divider above it):

```html
<!-- DELETE this entire block (lines 91-108): -->
        <!-- Suggested Prompts -->
        <div class="border-t border-surface-700 my-3"></div>
        <div class="px-1">
            <p class="px-2 text-[10px] font-medium text-surface-500 uppercase tracking-widest mb-2">Quick Prompts</p>
            ...all the way to...
            </a>
        </div>
```

Also remove the `usePrompt` script block at lines 116-128:
```html
<!-- DELETE this script block: -->
    <script>
    function usePrompt(text) {
        ...
    }
    </script>
```

**Step 2: Add Quick Prompts to Q&A page**

In `templates/qa.html`, add a new section between the "Ask a Question" card (ends at line 42 `</div>`) and the "How it works" card (starts at line 44):

```html
            <!-- Quick Prompts -->
            <div class="bg-surface-800 border border-surface-700 rounded-lg p-5">
                <h3 class="text-sm font-sans font-medium text-surface-200 flex items-center mb-3">
                    <i data-lucide="lightbulb" class="w-4 h-4 mr-2 text-copper-600"></i>
                    Try a Suggested Prompt
                </h3>
                <div class="flex flex-wrap gap-2">
                    {% for prompt in SUGGESTED_PROMPTS() %}
                    <button type="button"
                            onclick="document.getElementById('question').value = this.getAttribute('data-prompt'); document.getElementById('question').focus();"
                            data-prompt="{{ prompt | e }}"
                            class="px-3 py-1.5 text-xs text-surface-300 bg-surface-900 border border-surface-700 rounded-full hover:border-copper-600 hover:text-copper-400 transition-colors truncate max-w-xs"
                            title="{{ prompt }}">
                        {{ prompt[:50] }}{% if prompt|length > 50 %}...{% endif %}
                    </button>
                    {% endfor %}
                </div>
            </div>
```

**Step 3: Verify visually**

Run the app and check:
1. `/qa` page shows prompt chips below the form card
2. Clicking a chip fills the textarea
3. Sidebar no longer shows Quick Prompts section

**Step 4: Commit**

```bash
git add templates/components/sidebar.html templates/qa.html
git commit -m "feat: move quick prompts from sidebar to Q&A page as clickable chips"
```

---

### Task 3: Add Shadow/Elevation to Navbar and Sidebar

**Files:**
- Modify: `templates/components/navbar.html:1`
- Modify: `templates/layout.html:160`

**Step 1: Add shadow to navbar**

In `templates/components/navbar.html`, line 1, change:
```html
<nav class="bg-surface-800 border-b border-surface-700">
```
to:
```html
<nav class="bg-surface-800 border-b border-surface-700 shadow-lg shadow-black/25 relative z-20">
```

The `relative z-20` ensures the shadow renders above the sidebar and main content.

**Step 2: Add shadow to sidebar container**

In `templates/layout.html`, line 160, the sidebar container `<div>` needs the shadow. Change:
```html
<div id="sidebar-container" class="fixed md:static z-40 -translate-x-full md:translate-x-0 transition-transform duration-200 ease-out">
```
to:
```html
<div id="sidebar-container" class="fixed md:static z-40 -translate-x-full md:translate-x-0 transition-transform duration-200 ease-out shadow-xl shadow-black/30">
```

**Step 3: Add border to sidebar `<aside>`**

In `templates/components/sidebar.html`, line 1, change:
```html
<aside class="w-60 sidebar-surface min-h-[calc(100vh-3.5rem)] flex flex-col">
```
to:
```html
<aside class="w-60 sidebar-surface min-h-[calc(100vh-3.5rem)] flex flex-col border-r border-surface-700">
```

**Step 4: Verify visually**

Run the app and confirm:
1. Navbar casts a visible downward shadow
2. Sidebar has a rightward shadow and a subtle right border
3. Both elements feel visually distinct from the main content

**Step 5: Commit**

```bash
git add templates/components/navbar.html templates/layout.html templates/components/sidebar.html
git commit -m "style: add shadow elevation to navbar and sidebar for visual separation"
```

---

### Task 4: Dynamic Version Display

**Files:**
- Modify: `app/templates.py:17` (add APP_VERSION global)
- Modify: `templates/components/sidebar.html:113` (replace hardcoded version)
- Modify: `templates/layout.html:181` (replace hardcoded version)

**Step 1: Register APP_VERSION in Jinja2 globals**

In `app/templates.py`, after line 18 (`templates.env.globals["LEGAL_DISCLAIMER"] = ...`), add:
```python
templates.env.globals["APP_VERSION"] = settings.APP_VERSION
```

Note: `settings.APP_VERSION` is already defined in `app/settings.py:29` as `__version__` (currently "1.2.4").

**Step 2: Update sidebar version display**

In `templates/components/sidebar.html`, line 113, change:
```html
        <p class="text-[10px] text-surface-600">Contract Dashboard v2.0</p>
```
to:
```html
        <p class="text-[10px] text-surface-600">Contract Dashboard v{{ APP_VERSION }}</p>
```

**Step 3: Update footer version display**

In `templates/layout.html`, line 181, change:
```html
                    <span>Contract Dashboard v2.0</span>
```
to:
```html
                    <span>Contract Dashboard v{{ APP_VERSION }}</span>
```

**Step 4: Verify**

Run the app and confirm both the sidebar footer and layout footer display "Contract Dashboard v1.2.4".

**Step 5: Commit**

```bash
git add app/templates.py templates/components/sidebar.html templates/layout.html
git commit -m "fix: display real app version (1.2.4) instead of hardcoded v2.0"
```

---

### Task 5: Update .env.example

**Files:**
- Modify: `.env.example`

**Step 1: Add admin and GitHub entries**

Append to `.env.example` after the existing content:

```
# Admin panel (optional — enables document upload and publishing)
# ADMIN_PASSWORD=
# GITHUB_TOKEN=

# Bug reporting (optional — auto-create GitHub issues)
# BUGREPORT_CREATE_ISSUE=false
# BUGREPORT_GITHUB_REPO=
# BUGREPORT_GITHUB_TOKEN=

# Branding (optional)
# ORGANIZATION_NAME=
```

**Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add admin and branding settings to .env.example"
```

---

### Task 6: Run Existing Tests

**Step 1: Run the test suite**

```bash
cd "C:/Users/jorda/Desktop/AI Collective Group"
.venv/Scripts/python -m pytest tests/ -v --tb=short
```

Expected: All existing tests pass. Pay attention to `test_routes.py` tests which load the app and templates.

**Step 2: Manual smoke test**

1. Start the app: `.venv/Scripts/python run.py` (or `uvicorn app.main:app`)
2. Navigate to `/admin/login` — should show login form (not "not configured" error)
3. Navigate to `/qa` — should show prompt chips below the form
4. Check sidebar — no Quick Prompts section, version shows v1.2.4
5. Check footer — version shows v1.2.4
6. Verify navbar and sidebar have visible shadow/elevation
