"""Approved browser origins for local desktop WebViews."""

from __future__ import annotations

WEBVIEW_MODES: dict[str, tuple[str, tuple[str, ...]]] = {
    "standalone": ("127.0.0.1", ()),
    "tauri-dev": ("localhost", ("http://localhost:5173",)),
    "tauri-local": ("localhost", ("http://tauri.localhost",)),
}

ALLOWED_DESKTOP_CORS_ORIGINS = frozenset(
    origin for _host, origins in WEBVIEW_MODES.values() for origin in origins
)


__all__ = ["ALLOWED_DESKTOP_CORS_ORIGINS", "WEBVIEW_MODES"]
