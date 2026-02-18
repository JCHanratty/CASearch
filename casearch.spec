# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for CASearch — single .exe mode."""

import os

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'app.routes.dashboard',
        'app.routes.documents',
        'app.routes.search',
        'app.routes.qa',
        'app.routes.compare',
        'app.routes.matrix',
        'app.routes.diagnostics',
        'app.routes.admin_synonyms',
        'app.routes.tutorial',
        'app.routes.admin',
        'itsdangerous',
        'uvicorn.logging',
        'uvicorn.lifespan.on',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        # pywebview + Windows EdgeChromium backend
        'webview',
        'clr_loader',
        'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CASearch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
)
