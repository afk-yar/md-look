# Project Notes

## Build Artifacts

- When work changes `app.py`, bundled assets, or anything affecting `MDLook.exe`, finish by rebuilding the folder distribution with:
  `python -m PyInstaller --noconfirm MDLook.spec`
- The primary runtime artifact for this project is `dist/MDLook/MDLook.exe`. Do not treat the root `MDLook.exe` one-file build as the main deliverable unless the user explicitly asks for it.
- After the build, verify `dist/MDLook/MDLook.exe` timestamp so it is clear the distribution was refreshed.

## Verifying UI Changes

**Headless Chromium screenshots of this template lie. Never judge appearance from them.**
Verified on 2026-08-15 while building the lightbox: a `position:fixed` overlay with
`background:rgba(...,.94)` rendered with no visible backdrop at all, and
`locator.screenshot()` returned a stale frame showing carousel item `3 / 4` while
`lbState.idx` was `0`. The template carries several `backdrop-filter` layers
(`.bar`, modals, lightbox controls) — that is the likely trigger.

What each tool is good for:

- **Playwright (headless Chromium)** — behaviour and measurements only: DOM state,
  `getComputedStyle`, click/keyboard handling, event wiring. These are reliable.
  `tests/test_lightbox.py` and `tests/test_links_images.py` follow this pattern;
  `file://` URLs work from pytest (they are blocked in the Playwright MCP server,
  so serve the file over `python -m http.server` when driving the MCP).
- **WebView2** — the only source of truth for how something looks. Load the built
  HTML in a `pywebview` window, drive it through `window.evaluate_js(...)` from the
  `webview.start(func=...)` thread, and capture the window itself.

Capturing the WebView2 window:

- Use `user32.PrintWindow(hwnd, mem_dc, 2)` (`PW_RENDERFULLCONTENT`) plus `GetDIBits`.
  `ImageGrab.grab(bbox)` captures whatever is on screen at those coordinates and will
  silently return an unrelated window that happens to be on top.
- `SetForegroundWindow` from a background process does not raise the window on Windows 10,
  so do not rely on it before grabbing.
- Match windows by PID (`GetWindowThreadProcessId`), not by `'MDLook' in title`.

Driving the built `dist/MDLook/MDLook.exe`:

- An installed copy at `C:\Program Files\MDLook\MDLook.exe` may already be running. It is
  harmless — the IPC port is derived from the executable path, so the dist copy starts its
  own process instead of handing off. Never `taskkill /IM MDLook.exe`: kill your own PID
  (`taskkill /F /PID <pid> /T`) or the user loses their open documents.
- Real mouse (`SetCursorPos` + `mouse_event`) and real keys (`keybd_event`) work against the
  window and prove the shipped artifact, not just the source template.
