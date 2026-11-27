#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, quote, unquote, urlparse

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

    def _stream_body_to_file(self, dst_path: Path) -> int:
        """Stream HTTP request body in 1 MiB chunks directly to disk (no size cap)."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            dst_path.write_bytes(b"")
            return 0
        remaining = length
        written = 0
        with open(dst_path, "wb") as out_f:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                out_f.write(chunk)
                remaining -= len(chunk)
                written += len(chunk)
        return written

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 2 * 1024 * 1024:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

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

            # GET /api/export/default-path/<surface>/<conv_id>
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "export" and parts[2] == "default-path":
                surface, conv_id = parts[3], parts[4]
                self._send_json(HTTPStatus.OK, core.default_export_info(surface, conv_id))
                return

            # GET /api/export/<surface>/<conv_id>?include_brain=1
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "export":
                surface, conv_id = parts[2], parts[3]
                inc_raw = (query.get("include_brain", ["1"])[0] or "1").lower()
                include_brain = inc_raw not in ("0", "false", "no")
                with tempfile.TemporaryDirectory(prefix="agy_dl_") as tmp:
                    tmp_zip = Path(tmp) / "bundle.zip"
                    filename, size_bytes = core._write_session_zip(
                        surface, conv_id, tmp_zip, include_brain=include_brain
                    )
                    ascii_fallback = f"session_{conv_id[:8]}.agysess.zip"
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(size_bytes))
                    self.send_header(
                        "Content-Disposition",
                        f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}",
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    with open(tmp_zip, "rb") as f:
                        shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
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
            if path == "/api/import/inspect":
                fname = unquote(self.headers.get("X-Filename", "") or (query.get("filename", [""])[0]))
                with tempfile.TemporaryDirectory(prefix="agy_up_ins_") as tmp:
                    up_path = Path(tmp) / "upload.bin"
                    self._stream_body_to_file(up_path)
                    res = core.inspect_import_file(up_path, filename=fname)
                self._send_json(HTTPStatus.OK, res)
                return

            if path == "/api/import":
                target_surface = (query.get("target_surface", ["desktop"])[0] or "desktop").strip()
                new_id_raw = (query.get("new_id", ["1"])[0] or "1").lower()
                new_id_mode = new_id_raw not in ("0", "false", "no")
                overwrite_raw = (query.get("overwrite", ["0"])[0] or "0").lower()
                overwrite = overwrite_raw in ("1", "true", "yes")
                project_id = query.get("project_id", ["outside-of-project"])[0] or "outside-of-project"
                title = query.get("title", [""])[0] or None
                with tempfile.TemporaryDirectory(prefix="agy_up_imp_") as tmp:
                    up_path = Path(tmp) / "upload.bin"
                    self._stream_body_to_file(up_path)
                    res = core.import_session_file(
                        file_path=up_path,
                        target_surface=target_surface,
                        new_id_mode=new_id_mode,
                        title=title,
                        project_id=project_id,
                        overwrite=overwrite,
                    )
                self._send_json(HTTPStatus.OK, res)
                return

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

            if path == "/api/export/pick-path":
                chosen = core.pick_save_path_native(
                    default_filename=body.get("default_filename", "session.agysess.zip"),
                    default_dir=body.get("default_dir"),
                )
                self._send_json(HTTPStatus.OK, {"path": chosen, "cancelled": chosen is None})
                return

            if path == "/api/export/save":
                res = core.export_session_to_path(
                    surface=body["surface"],
                    conv_id=body["conv_id"],
                    dest_path=body["dest_path"],
                    include_brain=bool(body.get("include_brain", True)),
                )
                self._send_json(HTTPStatus.OK, res)
                return

            if path == "/api/reveal":
                core.reveal_in_file_manager(body["path"])
                self._send_json(HTTPStatus.OK, {"ok": True})
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
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
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
