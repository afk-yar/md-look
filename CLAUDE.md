# Project Notes

## Build Artifacts

- When work changes `app.py`, bundled assets, or anything affecting `MDLook.exe`, finish by rebuilding the folder distribution with:
  `python -m PyInstaller --noconfirm MDLook.spec`
- The primary runtime artifact for this project is `dist/MDLook/MDLook.exe`. Do not treat the root `MDLook.exe` one-file build as the main deliverable unless the user explicitly asks for it.
- After the build, verify `dist/MDLook/MDLook.exe` timestamp so it is clear the distribution was refreshed.
