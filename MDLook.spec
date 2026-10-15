# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['E:/_Проекты/pet/md-look/app.py'],
    pathex=[],
    binaries=[],
    datas=[('E:/_Проекты/pet/md-look/MDLook-template-offline.html', '.'), ('E:/_Проекты/pet/md-look/MDLook.ico', '.'), ('E:/_Проекты/pet/md-look/MDLook-doc.ico', '.'), ('E:/_Проекты/pet/md-look/example.md', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # numpy: only PIL's optional array helpers; cryptography: only pywebview's ssl=True cert generation.
    # PIL is needed just to open MDLook.ico for the pystray tray icon, so its heavy codecs go too.
    excludes=[
        'numpy', 'cryptography',
        'PIL._avif', 'PIL.AvifImagePlugin', 'PIL._webp', 'PIL.WebPImagePlugin',
        'PIL._imagingcms', 'PIL.ImageCms', 'PIL._imagingtk', 'PIL.ImageTk',
        'PIL._imagingmath', 'PIL.ImageMath', 'PIL.ImageQt',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MDLook',
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
    icon=['E:\\_Проекты\\pet\\md-look\\MDLook.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MDLook',
)
