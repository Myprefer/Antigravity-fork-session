#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse

import core

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _sidecar_token() -> str:
    return (
        os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN")
        or os.environ.get("ANTIGRAVITY_SIDECAR_AUTH_TOKEN")
        or os.environ.get("ANTIGRAVITY_SIDECAR_WEB_TOKEN")
        or ""
    ).strip()


class SessionManagerHandler(BaseHTTPRequestHandler):
    server_version = "AntigravityForkSession/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        ctype, _ = mimetypes.guess_type(str(path))
        if not ctype:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype = f"{ctype}; charset=utf-8"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _check_origin_and_token(self, query: Dict[str, Any]) -> bool:
        expected_token = _sidecar_token()
        if expected_token:
            provided = (
                self.headers.get("X-Sidecar-Token", "").strip()
                or (query.get("token", [""])[0] if isinstance(query.get("token"), list) else "")
            )
            if provided and provided != expected_token:
                return False

        origin = (self.headers.get("Origin") or "").strip()
        if not origin or origin == "null":
            return True
        parsed = urlparse(origin)
        if parsed.scheme == "plugin":
            return True
        return parsed.hostname in ("127.0.0.1", "localhost")

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 2 * 1024 * 1024:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path in ("/api/health", "/healthz"):
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "pid": os.getpid(),
                        "sidecar_mode": bool(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT")),
                        "ls_connected": bool(
                            os.environ.get("ANTIGRAVITY_LS_ADDRESS")
                            and os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
                        ),
                    },
                )
                return

            if path == "/api/overview":
                sessions = core.list_sessions()
                projects = core.load_projects()
                backups = core.list_backups()
                desktop_cnt = sum(
                    1 for s in sessions if s["surface"] == "desktop" and not s["is_subsession"]
                )
                cli_cnt = sum(
                    1 for s in sessions if s["surface"] == "cli" and not s["is_subsession"]
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "sessions": sessions,
                        "projects": projects,
                        "backups": backups,
                        "stats": {
                            "desktop_count": desktop_cnt,
                            "cli_count": cli_cnt,
                            "total_count": len(sessions),
                            "backup_count": len(backups),
                        },
                    },
                )
                return

            parts = [unquote(p) for p in path.strip("/").split("/") if p]
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "sessions" and parts[4] == "turns":
                surface, conv_id = parts[2], parts[3]
                data = core.get_session_turns(surface, conv_id)
                self._send_json(HTTPStatus.OK, data)
                return

            if path in ("/", "", "/index.html"):
                self._send_file(STATIC_DIR / "index.html")
                return
            rel = path.lstrip("/")
            candidate = (STATIC_DIR / rel).resolve()
            if str(candidate).startswith(str(STATIC_DIR.resolve())) and candidate.is_file():
                self._send_file(candidate)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown path: {path}"})
        except FileNotFoundError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._check_origin_and_token(query):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Origin or token rejected"})
            return

        path = parsed.path

        try:
            body = self._read_json_body()

            if path == "/api/fork":
                keep_steps = body.get("keep_steps")
                keep_steps = int(keep_steps) if keep_steps not in (None, "") else None
                res = core.fork_session(
                    source_surface=body["source_surface"],
                    source_id=body["source_id"],
                    target_surface=body.get("target_surface", "desktop"),
                    title=body.get("title"),
                    project_id=body.get("project_id"),
                    keep_steps=keep_steps,
                )
                self._send_json(HTTPStatus.OK, res)
                return

            if path == "/api/sync":
                res = core.sync_session_exact(
                    source_surface=body["source_surface"],
                    source_id=body["source_id"],
                    target_surface=body["target_surface"],
                    overwrite=bool(body.get("overwrite", False)),
                    title=body.get("title"),
                    project_id=body.get("project_id"),
                )
                self._send_json(HTTPStatus.OK, res)
                return

            if path == "/api/rename":
                res = core.rename_session(
                    surface=body["surface"],
                    conv_id=body["conv_id"],
                    title=body.get("title", ""),
                )
                self._send_json(HTTPStatus.OK, res)
                return

            if path == "/api/backup":
                res = core.create_backup(
                    surface=body["surface"],
                    conv_id=body["conv_id"],
                    reason=body.get("reason", "manual"),
                )
                self._send_json(HTTPStatus.OK, res)
                return

            if path == "/api/backups/restore":
                res = core.restore_backup(backup_name=body["backup_name"])
                self._send_json(HTTPStatus.OK, res)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown endpoint: {path}"})
        except FileExistsError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc), "code": "ALREADY_EXISTS"})
        except FileNotFoundError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def resolve_port(cli_port: int | None) -> int:
    if cli_port is not None:
        return cli_port
    for env_key in ("ANTIGRAVITY_SIDECAR_WEB_PORT", "AGY_MANAGER_PORT"):
        raw = os.environ.get(env_key, "").strip()
        if raw.isdigit():
            return int(raw)
    return 18766


def main() -> None:
    parser = argparse.ArgumentParser(description="Antigravity-fork-session Sidecar Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    port = resolve_port(args.port)
    httpd = ThreadingHTTPServer((args.host, port), SessionManagerHandler)
    print(f"Antigravity-fork-session Sidecar listening on http://{args.host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
