# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['playwright.async_api', 'scripts.live_interval_acceptance_probe', 'scripts.live_fast_click_probe', 'bet_desktop.ui.lightweight_probe_adapter', 'bet_desktop.backend.cluster_process_worker', 'bet_desktop.browser.live_runtime_state', 'bet_desktop.browser.login_flow', 'bet_desktop.browser.game_launch_url']
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['bet_desktop\\ui\\run_lightweight_dashboard.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LightweightHedgeConsole',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LightweightHedgeConsole',
)
