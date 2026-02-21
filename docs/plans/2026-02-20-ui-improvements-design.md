# UI Improvements Design

**Date:** 2026-02-20
**Approach:** Targeted Fixes (Approach A)

## Overview

Five focused improvements to the Contract Dashboard UI: fix admin login bug, relocate quick prompts, add visual elevation to navigation, fix version display, and update env example.

## 1. Fix Admin Login Bug

**Problem:** Admin login page shows "Admin access is not configured. Set ADMIN_PASSWORD in .env to enable." despite `ADMIN_PASSWORD=Local302!` being set in `.env`.

**Root cause:** `admin_enabled()` in `app/services/auth.py:28` checks `bool(settings.ADMIN_PASSWORD)`. The settings object isn't picking up the value from `.env`, likely due to `.env` loading path changes in recent commits (bundled .exe mode).

**Fix:** Trace settings loading in `app/settings.py`, fix the `.env` resolution so `ADMIN_PASSWORD` is read correctly in both dev and .exe modes.

**Files:** `app/settings.py`, `app/services/auth.py`

## 2. Move Quick Prompts to Q&A Page

**Problem:** Quick prompts in the sidebar are disconnected from where users ask questions.

**Change:**
- Remove Quick Prompts section from `templates/components/sidebar.html` (lines 91-108)
- Add prompt chips below the "Ask a Question" card on `templates/qa.html`
- Display as horizontal-wrap flex chips with subtle border styling
- Clicking fills the textarea directly (no sessionStorage redirect needed)
- Move/simplify `usePrompt()` JS to qa.html

**Files:** `templates/components/sidebar.html`, `templates/qa.html`

## 3. Navbar & Sidebar Shadow/Elevation

**Problem:** Navbar and sidebar blend into the main content area. Sidebar uses the same `surface-900` background as main content.

**Change:**
- Navbar: Add `shadow-lg shadow-black/25` for downward drop shadow
- Sidebar: Add `shadow-xl shadow-black/30` for rightward shadow + `border-r border-surface-700` fallback
- Keep existing navbar bottom border

**Files:** `templates/components/navbar.html`, `templates/components/sidebar.html` (or `templates/layout.html` for sidebar container)

## 4. Dynamic Version Display

**Problem:** Sidebar and footer hardcode "Contract Dashboard v2.0" but actual version is 1.2.4.

**Change:**
- Add `APP_VERSION` to Jinja2 template global context (via `app/templates.py`)
- Replace hardcoded "v2.0" in `templates/components/sidebar.html:113` with `v{{ APP_VERSION }}`
- Replace hardcoded "v2.0" in `templates/layout.html:181` with `v{{ APP_VERSION }}`

**Files:** `app/templates.py`, `templates/components/sidebar.html`, `templates/layout.html`

## 5. Update .env.example

**Problem:** `.env.example` doesn't mention `ADMIN_PASSWORD` or `GITHUB_TOKEN`, leaving new users unaware these settings exist.

**Change:** Add commented entries for `ADMIN_PASSWORD` and `GITHUB_TOKEN`.

**Files:** `.env.example`
