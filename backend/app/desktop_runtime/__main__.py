from __future__ import annotations

import argparse
import json
import sys
import threading
from collections.abc import Iterable
from pathlib import Path

from app.desktop_runtime.bundle import resolve_bundled_postgres_binary_dir
from app.desktop_runtime.paths import ApplicationPaths, default_data_directory
from app.desktop_runtime.supervisor import DesktopRuntime, DesktopRuntimeError
from app.desktop_runtime.webview import WEBVIEW_MODES
from app.api.capability import is_valid_api_capability


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the application-managed PostgreSQL and local API services."
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        help="application resource directory containing the PostgreSQL and frontend bundles",
    )
    parser.add_argument(
        "--postgres-bin",
        type=Path,
        help=(
            "optional PostgreSQL binary directory override; by default the "
            "versioned application bundle is used"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Persistent per-user data directory; defaults to LOCALAPPDATA/AI Career Copilot.",
    )
    parser.add_argument(
        "--webview-mode",
        choices=tuple(WEBVIEW_MODES),
        default="standalone",
        help="approved local browser origin mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if getattr(sys, "frozen", False):
        backend_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        default_install_dir = Path(sys.executable).resolve().parent.parent.parent
    else:
        backend_root = Path(__file__).resolve().parents[2]
        default_install_dir = backend_root.parent
    install_dir = (arguments.install_dir or default_install_dir).resolve()
    try:
        api_capability = _read_api_capability(sys.stdin)
        postgres_bin = arguments.postgres_bin or resolve_bundled_postgres_binary_dir(
            install_dir
        )
        data_dir = arguments.data_dir or default_data_directory()
        runtime = DesktopRuntime(
            ApplicationPaths(install_dir=install_dir, data_dir=data_dir),
            postgres_bin,
            backend_root=backend_root,
            webview_mode=arguments.webview_mode,
            api_capability=api_capability,
        )
        api_url = runtime.start()
        print(json.dumps({"event": "ready", "api_url": api_url}), flush=True)
        shutdown_event = threading.Event()
        threading.Thread(
            target=_watch_shutdown_commands,
            args=(sys.stdin, shutdown_event),
            name="desktop-runtime-control",
            daemon=True,
        ).start()
        runtime.run_until_stopped(shutdown_event=shutdown_event)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "error",
                    "category": type(exc).__name__,
                    "message": "desktop services failed to start safely; persistent data was preserved",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1


def _read_api_capability(lines: Iterable[str]) -> str:
    """Read the one-time native-to-backend startup secret from stdin."""
    try:
        line = next(iter(lines))
        if len(line) > 512 or not line.endswith("\n"):
            raise ValueError
        message = json.loads(line)
    except (StopIteration, json.JSONDecodeError, ValueError, TypeError):
        raise RuntimeError("desktop startup handshake was invalid") from None
    if (
        not isinstance(message, dict)
        or message.get("command") != "initialize"
        or not is_valid_api_capability(message.get("api_capability"))
    ):
        raise RuntimeError("desktop startup handshake was invalid") from None
    return message["api_capability"]


def _watch_shutdown_commands(lines: Iterable[str], shutdown_event: threading.Event) -> None:
    for line in lines:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("command") == "shutdown":
            shutdown_event.set()
            return
    # If the owning desktop process exits, its pipe closes. Treat EOF as a
    # graceful shutdown request so the managed database is not left running.
    shutdown_event.set()


if __name__ == "__main__":
    raise SystemExit(main())
