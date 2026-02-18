# Admin Panel + Document Sync + Auto-Update

## Overview

Admin login gate on the app. When authenticated, admin can upload/delete PDFs, scan, index, and publish the database to GitHub Releases. Users' .exe instances detect new data, download it to a staging area, show a modal, and apply on restart.

## Auth

- Single password in `.env` (`ADMIN_PASSWORD`)
- `GITHUB_TOKEN` in `.env` for publishing
- Signed session cookie via `itsdangerous`
- No password set = admin features disabled

## Admin Panel

- Upload PDFs → `data/agreements/`
- Delete documents (PDF + index data)
- Scan & index (existing functionality, gated)
- Publish button → packages `app.db` into `index-vX.zip`, pushes to GitHub Releases

## Update Flow (Users)

1. App starts, loads current data
2. Background check finds newer index on GitHub
3. Downloads zip to `data/pending_update/`
4. Modal: "New documents available. Restart to apply."
5. User clicks restart → app closes
6. Next launch: detect `pending_update/`, swap `app.db`, clean up, boot

## Files

- Create: `app/services/auth.py`, `app/routes/admin.py`, `templates/admin_login.html`, `templates/admin_panel.html`, `templates/components/update_modal.html`
- Modify: `app/main.py`, `app/services/updater.py`, `app/settings.py`, sidebar, layout, requirements.txt
