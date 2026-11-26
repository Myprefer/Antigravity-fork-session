from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GEMINI_DIR = Path(os.environ.get("AGY_GEMINI_DIR", "") or (Path.home() / ".gemini"))
SURFACES: Dict[str, Path] = {
    "desktop": Path(
        os.environ.get("AGY_DESKTOP_DIR")
        or os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
        or (GEMINI_DIR / "antigravity")
    ),
    "cli": Path(os.environ.get("AGY_CLI_DIR") or (GEMINI_DIR / "antigravity-cli")),
}
PROJECTS_DIR = GEMINI_DIR / "config" / "projects"

APP_DIR = Path(__file__).resolve().parent
_EXEC_DATA_DIR = os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR", "").strip()
DATA_ROOT = Path(_EXEC_DATA_DIR) if _EXEC_DATA_DIR else APP_DIR
BACKUP_DIR = DATA_ROOT / "backups"
STAGING_DIR = DATA_ROOT / ".staging"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
BACKUP_NAME_RE = re.compile(r"^[0-9A-Za-z._-]+$")


# ---------------------------------------------------------------------------
# Protobuf wire-format helpers
# ---------------------------------------------------------------------------


def encode_varint(val: int) -> bytes:
    if val < 0:
        val &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf: bytes, idx: int) -> Tuple[int, int]:
    val = 0
    shift = 0
    n = len(buf)
    while idx < n:
        b = buf[idx]
        idx += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, idx
        shift += 7
        if shift > 70:
            raise ValueError("Malformed varint")
    raise ValueError("Truncated varint")


def parse_fields(buf: bytes) -> List[Tuple[int, int, Any]]:
    """Parse raw protobuf bytes into [(field_number, wire_type, value), ...]."""
    if not buf:
        return []
    i = 0
    n = len(buf)
    fields: List[Tuple[int, int, Any]] = []
    while i < n:
        tag, i = decode_varint(buf, i)
        fn = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, i = decode_varint(buf, i)
        elif wt == 1:
            if i + 8 > n:
                raise ValueError("Truncated 64-bit field")
            v = buf[i : i + 8]
            i += 8
        elif wt == 2:
            ln, i = decode_varint(buf, i)
            if i + ln > n:
                raise ValueError("Truncated length-delimited field")
            v = buf[i : i + ln]
            i += ln
        elif wt == 5:
            if i + 4 > n:
                raise ValueError("Truncated 32-bit field")
            v = buf[i : i + 4]
            i += 4
        else:
            raise ValueError(f"Unsupported wire type {wt}")
        fields.append((fn, wt, v))
    return fields


def encode_fields(fields: List[Tuple[int, int, Any]]) -> bytes:
    out = bytearray()
    for fn, wt, v in fields:
        out += encode_varint((fn << 3) | wt)
        if wt == 0:
            out += encode_varint(int(v))
        elif wt == 1:
            out += v
        elif wt == 2:
            if isinstance(v, str):
                v = v.encode("utf-8")
            out += encode_varint(len(v)) + v
        elif wt == 5:
            out += v
        else:
            raise ValueError(f"Unsupported wire type {wt}")
    return bytes(out)


def encode_timestamp(epoch_ns: int) -> bytes:
    return encode_fields([(1, 0, epoch_ns // 10**9), (2, 0, epoch_ns % 10**9)])


def decode_timestamp(buf: bytes) -> Optional[float]:
    try:
        secs = 0
        nanos = 0
        for fn, wt, v in parse_fields(buf):
            if fn == 1 and wt == 0:
                secs = int(v)
            elif fn == 2 and wt == 0:
                nanos = int(v)
        if secs > 1_000_000_000:
            return secs + nanos / 1e9
    except Exception:
        pass
    return None


def safe_utf8(data: Any) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        return data
    return ""


# ---------------------------------------------------------------------------
# Annotations (.pbtxt) helpers
# ---------------------------------------------------------------------------


def escape_pbtxt_string(s: str) -> str:
    out: List[str] = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


def unescape_pbtxt_string(raw: str) -> str:
    # Handle standard protobuf text escapes including octal UTF-8 byte escapes (\345\257\271)
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "n":
                out.extend(b"\n")
                i += 2
            elif nxt == "r":
                out.extend(b"\r")
                i += 2
            elif nxt == "t":
                out.extend(b"\t")
                i += 2
            elif nxt == '"':
                out.extend(b'"')
                i += 2
            elif nxt == "\\":
                out.extend(b"\\")
                i += 2
            elif nxt in "01234567" and i + 3 < n and all(c in "01234567" for c in raw[i + 1 : i + 4]):
                out.append(int(raw[i + 1 : i + 4], 8))
                i += 4
            elif nxt == "x" and i + 3 < n:
                try:
                    out.append(int(raw[i + 2 : i + 4], 16))
                    i += 4
                except ValueError:
                    out.extend(nxt.encode("utf-8"))
                    i += 2
            else:
                out.extend(nxt.encode("utf-8"))
                i += 2
        else:
            out.extend(ch.encode("utf-8"))
            i += 1
    return out.decode("utf-8", errors="replace")


def parse_annotation_pbtxt(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "title": "",
        "archived": False,
        "pinned": False,
        "starred": False,
        "side_question_ids": [],
    }
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    m_title = re.search(r'(?:^|\s)title\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if m_title:
        result["title"] = unescape_pbtxt_string(m_title.group(1))
    if re.search(r"(?:^|\s)archived\s*:\s*true\b", text):
        result["archived"] = True
    if re.search(r"(?:^|\s)pinned\s*:\s*true\b", text):
        result["pinned"] = True
    if re.search(r"(?:^|\s)starred\s*:\s*true\b", text):
        result["starred"] = True
    for m in re.finditer(r'side_question_cascade_ids\s*:\s*"([^"]+)"', text):
        result["side_question_ids"].append(m.group(1))
    return result


def write_annotation_title(surface_dir: Path, conv_id: str, title: str, touch_view_time: bool = True) -> None:
    ann_dir = surface_dir / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    ann_path = ann_dir / f"{conv_id}.pbtxt"

    escaped = escape_pbtxt_string(title.strip())
    now_ns = time.time_ns()
    ts_str = f"last_user_view_time:{{seconds:{now_ns // 10**9}  nanos:{now_ns % 10**9}}}"

    if ann_path.exists():
        text = ann_path.read_text(encoding="utf-8", errors="replace")
        if re.search(r'(?:^|\s)title\s*:\s*"(?:\\.|[^"\\])*"', text):
            text = re.sub(
                r'((?:^|\s))title\s*:\s*"(?:\\.|[^"\\])*"',
                lambda m: f'{m.group(1)}title:"{escaped}"',
                text,
                count=1,
            )
        else:
            text = f'title:"{escaped}"  {text.strip()}\n'
        if touch_view_time:
            if re.search(r"last_user_view_time\s*:\s*\{[^}]*\}", text):
                text = re.sub(r"last_user_view_time\s*:\s*\{[^}]*\}", ts_str, text, count=1)
            else:
                text = f"{text.strip()}  {ts_str}\n"
        ann_path.write_text(text, encoding="utf-8")
    else:
        parts = []
        if escaped:
            parts.append(f'title:"{escaped}"')
        if touch_view_time:
            parts.append(ts_str)
        ann_path.write_text("  ".join(parts) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Validation & SQLite safe access
# ---------------------------------------------------------------------------


def validate_surface(surface: str) -> Path:
    if surface not in SURFACES:
        raise ValueError(f"Unsupported surface: {surface!r}")
    return SURFACES[surface]


def validate_conv_id(conv_id: str) -> str:
    if not isinstance(conv_id, str) or not UUID_RE.match(conv_id):
        raise ValueError(f"Invalid conversation ID: {conv_id!r}")
    return conv_id.lower()


def open_db_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite DB read-only without corrupting or creating stray WAL/SHM files."""
    wal_path = Path(str(db_path) + "-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        con.execute("PRAGMA query_only = ON")
        return con
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=5.0)
    except sqlite3.OperationalError:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        con.execute("PRAGMA query_only = ON")
        return con


def snapshot_db(src_db: Path, dst_db: Path) -> None:
    """Safely snapshot a SQLite DB (even if currently open in WAL mode) to dst_db."""
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    if dst_db.exists():
        dst_db.unlink()
    wal_path = Path(str(src_db) + "-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        src_con = sqlite3.connect(str(src_db), timeout=10.0)
    else:
        try:
            src_con = sqlite3.connect(f"file:{src_db}?mode=ro&immutable=1", uri=True, timeout=10.0)
        except sqlite3.OperationalError:
            src_con = sqlite3.connect(str(src_db), timeout=10.0)
    dst_con = sqlite3.connect(str(dst_db))
    try:
        src_con.backup(dst_con)
    finally:
        src_con.close()
        dst_con.close()


def is_db_active(surface_dir: Path, conv_id: str) -> bool:
    wal = surface_dir / "conversations" / f"{conv_id}.db-wal"
    try:
        return wal.exists() and wal.stat().st_size > 0
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Projects loader
# ---------------------------------------------------------------------------


def load_projects() -> List[Dict[str, Any]]:
    projects: List[Dict[str, Any]] = [
        {
            "id": "outside-of-project",
            "name": "无项目（通用会话）",
            "folder_uris": [],
            "is_sidecar": False,
            "builtin": True,
        }
    ]
    if not PROJECTS_DIR.exists():
        return projects

    for pfile in sorted(PROJECTS_DIR.glob("*.json")):
        try:
            data = json.loads(pfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = data.get("id") or pfile.stem
        name = data.get("name") or pid
        is_sidecar = bool(data.get("sidecar"))
        folder_uris: List[str] = []
        resources = (data.get("projectResources") or {}).get("resources") or []
        for res in resources:
            if not isinstance(res, dict):
                continue
            if "folderUri" in res and res["folderUri"]:
                folder_uris.append(res["folderUri"])
            elif isinstance(res.get("gitFolder"), dict) and res["gitFolder"].get("folderUri"):
                folder_uris.append(res["gitFolder"]["folderUri"])
        projects.append(
            {
                "id": pid,
                "name": name,
                "folder_uris": folder_uris,
                "is_sidecar": is_sidecar,
                "builtin": False,
            }
        )
    return projects


def projects_by_id() -> Dict[str, Dict[str, Any]]:
    return {p["id"]: p for p in load_projects()}


# ---------------------------------------------------------------------------
# Session metadata & turn extraction
# ---------------------------------------------------------------------------


def _parse_datetime_str(ts: str) -> Optional[float]:
    if not ts or ts.startswith("0001-01-01"):
        return None
    s = ts.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _extract_user_text_from_payload(payload: bytes) -> str:
    if not payload:
        return ""
    try:
        for fn, wt, v in parse_fields(payload):
            if fn == 19 and wt == 2:
                text_f2 = ""
                text_f3 = ""
                for sfn, swt, sv in parse_fields(v):
                    if sfn == 2 and swt == 2 and not text_f2:
                        text_f2 = safe_utf8(sv)
                    elif sfn == 3 and swt == 2 and not text_f3:
                        for ssfn, sswt, ssv in parse_fields(sv):
                            if ssfn == 1 and sswt == 2:
                                text_f3 = safe_utf8(ssv)
                                break
                return (text_f2 or text_f3).strip()
    except Exception:
        pass
    return ""


def _extract_assistant_text_from_payload(payload: bytes) -> str:
    if not payload:
        return ""
    try:
        for fn, wt, v in parse_fields(payload):
            if fn == 20 and wt == 2:
                f8 = ""
                f1 = ""
                for sfn, swt, sv in parse_fields(v):
                    if sfn == 8 and swt == 2 and not f8:
                        f8 = safe_utf8(sv)
                    elif sfn == 1 and swt == 2 and not f1:
                        f1 = safe_utf8(sv)
                return (f8 or f1).strip()
    except Exception:
        pass
    return ""


def _extract_step_timestamp(meta: bytes) -> Optional[float]:
    if not meta:
        return None
    try:
        for fn, wt, v in parse_fields(meta):
            if fn == 1 and wt == 2:
                return decode_timestamp(v)
    except Exception:
        pass
    return None


def _read_db_quick_info(db_path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "step_count": 0,
        "user_turns": 0,
        "project_id": "",
        "workspace_uris": [],
        "preview": "",
        "created_ts": None,
        "last_step_ts": None,
        "source_code": None,
        "trajectory_id": "",
    }
    con = open_db_readonly(db_path)
    try:
        row = con.execute("SELECT trajectory_id, source FROM trajectory_meta LIMIT 1").fetchone()
        if row:
            info["trajectory_id"] = row[0] or ""
            info["source_code"] = row[1]

        cnt_row = con.execute("SELECT count(*) FROM steps").fetchone()
        info["step_count"] = int(cnt_row[0]) if cnt_row else 0

        ut_row = con.execute("SELECT count(*) FROM steps WHERE step_type = 14").fetchone()
        info["user_turns"] = int(ut_row[0]) if ut_row else 0

        first_u = con.execute(
            "SELECT step_payload FROM steps WHERE step_type = 14 ORDER BY idx ASC LIMIT 1"
        ).fetchone()
        if first_u and first_u[0]:
            info["preview"] = _extract_user_text_from_payload(first_u[0])[:240]

        last_s = con.execute("SELECT metadata FROM steps ORDER BY idx DESC LIMIT 1").fetchone()
        if last_s and last_s[0]:
            info["last_step_ts"] = _extract_step_timestamp(last_s[0])

        blob_row = con.execute(
            "SELECT data FROM trajectory_metadata_blob WHERE id='main' LIMIT 1"
        ).fetchone()
        if blob_row and blob_row[0]:
            ws_uris: List[str] = []
            for fn, wt, v in parse_fields(blob_row[0]):
                if fn == 18 and wt == 2:
                    info["project_id"] = safe_utf8(v)
                elif fn == 2 and wt == 2:
                    info["created_ts"] = decode_timestamp(v)
                elif fn == 7 and wt == 2:
                    u = safe_utf8(v)
                    if u and u not in ws_uris:
                        ws_uris.append(u)
                elif fn == 1 and wt == 2:
                    for sfn, swt, sv in parse_fields(v):
                        if sfn == 1 and swt == 2:
                            u = safe_utf8(sv)
                            if u and u not in ws_uris:
                                ws_uris.append(u)
            info["workspace_uris"] = ws_uris
    finally:
        con.close()
    return info


def _load_summaries_cache(surface_dir: Path, expected_app_dir: str) -> Dict[str, Dict[str, Any]]:
    db_path = surface_dir / "conversation_summaries.db"
    out: Dict[str, Dict[str, Any]] = {}
    if not db_path.exists():
        return out
    try:
        con = open_db_readonly(db_path)
    except Exception:
        return out
    try:
        q = (
            "SELECT conversation_id, title, preview, step_count, last_modified_time, "
            "workspace_uris, status, project_id, parent_conversation_id, app_data_dir "
            "FROM conversation_summaries"
        )
        for row in con.execute(q):
            cid, title, preview, sc, lmt, ws_raw, status, pid, parent_id, app_dir = row
            if app_dir and app_dir != expected_app_dir:
                continue
            ws_list: List[str] = []
            if ws_raw:
                try:
                    parsed = json.loads(ws_raw)
                    if isinstance(parsed, list):
                        ws_list = [str(x) for x in parsed]
                except Exception:
                    pass
            out[cid] = {
                "title": title or "",
                "preview": preview or "",
                "step_count": int(sc or 0),
                "last_modified_ts": _parse_datetime_str(lmt or ""),
                "workspace_uris": ws_list,
                "status": status or "",
                "project_id": pid or "",
                "parent_conversation_id": parent_id or "",
            }
    except Exception:
        pass
    finally:
        con.close()
    return out


def list_sessions() -> List[Dict[str, Any]]:
    proj_map = projects_by_id()
    per_surface: Dict[str, Dict[str, Dict[str, Any]]] = {"desktop": {}, "cli": {}}

    # Track side-question cascade IDs so UI can optionally filter sub-sessions
    side_question_ids: Dict[str, set] = {"desktop": set(), "cli": set()}

    for surface, sdir in SURFACES.items():
        ann_dir = sdir / "annotations"
        ann_map: Dict[str, Dict[str, Any]] = {}
        if ann_dir.exists():
            for p in ann_dir.glob("*.pbtxt"):
                ann = parse_annotation_pbtxt(p)
                ann_map[p.stem] = ann
                for sqid in ann.get("side_question_ids", []):
                    side_question_ids[surface].add(sqid)

        expected_app_dir = "antigravity" if surface == "desktop" else "antigravity-cli"
        summaries = _load_summaries_cache(sdir, expected_app_dir)

        conv_dir = sdir / "conversations"
        if not conv_dir.exists():
            continue

        for db_file in conv_dir.glob("*.db"):
            cid = db_file.stem
            if not UUID_RE.match(cid):
                continue
            try:
                stat = db_file.stat()
            except OSError:
                continue

            summ = summaries.get(cid)
            ann = ann_map.get(cid, {})
            active = is_db_active(sdir, cid)

            # Read quick DB info if missing from summary, active WAL, or missing preview/project
            need_db_read = (
                summ is None
                or active
                or not summ.get("project_id")
                or (summ.get("step_count", 0) == 0 and stat.st_size > 400_000)
            )
            db_info: Optional[Dict[str, Any]] = None
            if need_db_read:
                try:
                    db_info = _read_db_quick_info(db_file)
                except Exception:
                    db_info = None

            step_count = (
                db_info["step_count"]
                if db_info is not None
                else (summ["step_count"] if summ else 0)
            )
            project_id = (
                (db_info["project_id"] if db_info and db_info["project_id"] else "")
                or (summ["project_id"] if summ else "")
                or "outside-of-project"
            )
            workspace_uris = (
                (db_info["workspace_uris"] if db_info and db_info["workspace_uris"] else [])
                or (summ["workspace_uris"] if summ else [])
            )
            title = ann.get("title") or (summ["title"] if summ else "") or ""
            preview = (
                (summ["preview"] if summ and summ["preview"] else "")
                or (db_info["preview"] if db_info else "")
                or ""
            )
            # Clean newlines in preview
            preview_clean = re.sub(r"\s+", " ", preview).strip()

            mtime = stat.st_mtime
            if db_info and db_info.get("last_step_ts"):
                mtime = max(mtime, float(db_info["last_step_ts"]))
            elif summ and summ.get("last_modified_ts"):
                mtime = max(mtime, float(summ["last_modified_ts"]))

            parent_id = summ["parent_conversation_id"] if summ else ""
            is_subsession = bool(parent_id) or (cid in side_question_ids[surface])

            proj = proj_map.get(project_id)
            project_name = proj["name"] if proj else project_id

            brain_dir = sdir / "brain" / cid
            has_brain = brain_dir.exists()

            per_surface[surface][cid] = {
                "id": cid,
                "surface": surface,
                "title": title,
                "display_title": title or (preview_clean[:60] if preview_clean else f"未命名会话 ({cid[:8]})"),
                "preview": preview_clean[:240],
                "step_count": step_count,
                "size_bytes": stat.st_size,
                "mtime": mtime,
                "mtime_iso": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "project_id": project_id,
                "project_name": project_name,
                "workspace_uris": workspace_uris,
                "status": (summ["status"] if summ else "") or ("ACTIVE" if active else "IDLE"),
                "active": active,
                "archived": bool(ann.get("archived")),
                "pinned": bool(ann.get("pinned")),
                "starred": bool(ann.get("starred")),
                "is_subsession": is_subsession,
                "has_brain": has_brain,
                "conversation_url": f"conversation://{cid}" if surface == "desktop" else None,
                "cli_command": f"agy --conversation {cid}" if surface == "cli" else None,
            }

    # Cross-surface linking (same ID present in both desktop and cli)
    all_sessions: List[Dict[str, Any]] = []
    for surface in ("desktop", "cli"):
        other = "cli" if surface == "desktop" else "desktop"
        for cid, item in per_surface[surface].items():
            other_item = per_surface[other].get(cid)
            item["exists_in_other"] = other_item is not None
            item["other_step_count"] = other_item["step_count"] if other_item else None
            item["other_mtime"] = other_item["mtime"] if other_item else None
            all_sessions.append(item)

    all_sessions.sort(key=lambda x: (x["pinned"], x["mtime"]), reverse=True)
    return all_sessions


def get_session_turns(surface: str, conv_id: str) -> Dict[str, Any]:
    sdir = validate_surface(surface)
    cid = validate_conv_id(conv_id)
    db_path = sdir / "conversations" / f"{cid}.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Session {cid} not found in {surface}")

    con = open_db_readonly(db_path)
    try:
        rows = con.execute(
            "SELECT idx, step_type, status, metadata, step_payload FROM steps ORDER BY idx ASC"
        ).fetchall()
    finally:
        con.close()

    total_steps = len(rows)
    user_indices = [i for i, r in enumerate(rows) if r[1] == 14]

    turns: List[Dict[str, Any]] = []
    for t_pos, list_idx in enumerate(user_indices):
        step_idx, _, status, meta, payload = rows[list_idx]
        next_list_idx = user_indices[t_pos + 1] if t_pos + 1 < len(user_indices) else total_steps
        end_step_idx = rows[next_list_idx - 1][0] if next_list_idx > list_idx else step_idx
        keep_steps_after = end_step_idx + 1

        user_text = _extract_user_text_from_payload(payload or b"")
        ts = _extract_step_timestamp(meta or b"")

        # Find last non-empty assistant reply in this turn
        assistant_reply = ""
        tool_count = 0
        planner_count = 0
        for r in rows[list_idx + 1 : next_list_idx]:
            st = r[1]
            if st == 15:
                planner_count += 1
                txt = _extract_assistant_text_from_payload(r[4] or b"")
                if txt:
                    assistant_reply = txt
            elif st not in (14, 15):
                tool_count += 1

        turns.append(
            {
                "turn_number": t_pos + 1,
                "start_step_idx": int(step_idx),
                "end_step_idx": int(end_step_idx),
                "keep_steps_after": int(keep_steps_after),
                "keep_steps_before": int(step_idx),
                "step_count": int(end_step_idx - step_idx + 1),
                "tool_steps": tool_count,
                "planner_steps": planner_count,
                "timestamp": ts,
                "timestamp_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "user_text": user_text,
                "user_preview": re.sub(r"\s+", " ", user_text).strip()[:200],
                "assistant_text": assistant_reply[:16000],
                "assistant_preview": re.sub(r"\s+", " ", assistant_reply).strip()[:260],
            }
        )

    return {
        "id": cid,
        "surface": surface,
        "total_steps": total_steps,
        "turn_count": len(turns),
        "turns": turns,
    }


# ---------------------------------------------------------------------------
# Protobuf DB mutation helpers for fork / sync
# ---------------------------------------------------------------------------


def _build_workspace_field1(uri: str) -> bytes:
    return encode_fields([(1, 2, uri.encode("utf-8")), (3, 2, b"")])


def _rewrite_metadata_blob(
    blob: bytes,
    new_cid: str,
    new_sess_id: str,
    target_surface: str,
    target_project_id: Optional[str],
    proj_map: Dict[str, Dict[str, Any]],
    update_timestamp: bool = True,
) -> bytes:
    fields = parse_fields(blob)
    now_ns = time.time_ns()
    ts_bytes = encode_timestamp(now_ns)

    change_project = target_project_id is not None
    target_uris: List[str] = []
    if change_project and target_project_id != "outside-of-project":
        proj = proj_map.get(target_project_id or "")
        if proj:
            target_uris = list(proj.get("folder_uris") or [])

    out: List[Tuple[int, int, Any]] = []
    saw_f2 = saw_f3 = saw_f6 = saw_f18 = saw_f25 = False

    for fn, wt, v in fields:
        if fn == 2 and update_timestamp:
            out.append((2, 2, ts_bytes))
            saw_f2 = True
        elif fn == 3 and new_sess_id:
            out.append((3, 2, new_sess_id.encode("utf-8")))
            saw_f3 = True
        elif fn == 6:
            out.append((6, 2, new_cid.encode("utf-8")))
            saw_f6 = True
        elif fn == 1 and change_project:
            # Drop old workspace messages; we'll re-insert below if target project has folder_uris
            continue
        elif fn == 7 and change_project:
            continue
        elif fn == 18 and change_project:
            out.append((18, 2, (target_project_id or "outside-of-project").encode("utf-8")))
            saw_f18 = True
        elif fn == 25:
            if target_surface == "cli":
                out.append((25, 0, 1))
                saw_f25 = True
            # Omit field 25 for desktop
        else:
            out.append((fn, wt, v))

    if update_timestamp and not saw_f2:
        out.append((2, 2, ts_bytes))
    if new_sess_id and not saw_f3:
        out.append((3, 2, new_sess_id.encode("utf-8")))
    if not saw_f6:
        out.append((6, 2, new_cid.encode("utf-8")))
    if change_project:
        if not saw_f18:
            out.append((18, 2, (target_project_id or "outside-of-project").encode("utf-8")))
        for uri in target_uris:
            out.append((1, 2, _build_workspace_field1(uri)))
        if target_uris:
            out.append((7, 2, target_uris[0].encode("utf-8")))
    if target_surface == "cli" and not saw_f25:
        out.append((25, 0, 1))

    # Keep canonical field-number ordering
    out.sort(key=lambda item: item[0])
    return encode_fields(out)


def _truncate_db_to_steps(con: sqlite3.Connection, keep_steps: int) -> None:
    """Truncate a conversation DB so it keeps steps with idx < keep_steps."""
    if keep_steps <= 0:
        raise ValueError("keep_steps must be positive")
    last_kept_idx = keep_steps - 1

    # 1. Truncate steps
    con.execute("DELETE FROM steps WHERE idx >= ?", (keep_steps,))

    # 2. Truncate executor_metadata using field 3 (end_step_index)
    em_delete: List[int] = []
    for idx, data in con.execute("SELECT idx, data FROM executor_metadata ORDER BY idx"):
        end_idx = None
        try:
            for fn, wt, v in parse_fields(data or b""):
                if fn == 3 and wt == 0:
                    end_idx = int(v)
                    break
        except Exception:
            pass
        if end_idx is not None and end_idx >= keep_steps:
            em_delete.append(idx)
    if em_delete:
        con.executemany("DELETE FROM executor_metadata WHERE idx = ?", [(i,) for i in em_delete])

    # 3. Truncate gen_metadata using last_step_index (f1 -> f20 map) with fallback to step_type=15 count
    gm_delete: List[int] = []
    saw_any_lsi = False
    for idx, data in con.execute("SELECT idx, data FROM gen_metadata ORDER BY idx"):
        lsi = None
        try:
            for fn, wt, v in parse_fields(data or b""):
                if fn == 1 and wt == 2:
                    for sfn, swt, sv in parse_fields(v):
                        if sfn == 20 and swt == 2:
                            kv = {}
                            for kfn, kwt, kv_val in parse_fields(sv):
                                if kwt == 2:
                                    kv[kfn] = safe_utf8(kv_val)
                            if kv.get(1) == "last_step_index" and kv.get(2):
                                lsi = int(kv[2])
                                saw_any_lsi = True
                                break
        except Exception:
            pass
        if lsi is not None and lsi >= keep_steps:
            gm_delete.append(idx)

    if saw_any_lsi:
        if gm_delete:
            con.executemany("DELETE FROM gen_metadata WHERE idx = ?", [(i,) for i in gm_delete])
    else:
        st15_cnt = con.execute("SELECT count(*) FROM steps WHERE step_type = 15").fetchone()[0]
        con.execute("DELETE FROM gen_metadata WHERE idx >= ?", (st15_cnt,))

    # 4. Truncate/clamp parent_references (field 2 = inclusive end step index)
    prefs = con.execute("SELECT idx, data FROM parent_references ORDER BY idx").fetchall()
    kept_prefs: List[Tuple[int, bytes]] = []
    reached_limit = False
    for idx, data in prefs:
        if reached_limit:
            break
        try:
            pfields = parse_fields(data or b"")
            f2_val = None
            for fn, wt, v in pfields:
                if fn == 2 and wt == 0:
                    f2_val = int(v)
                    break
            if f2_val is not None and f2_val >= last_kept_idx:
                if f2_val > last_kept_idx:
                    new_pf = [
                        (2, 0, last_kept_idx) if (fn == 2 and wt == 0) else (fn, wt, v)
                        for fn, wt, v in pfields
                    ]
                    data = encode_fields(new_pf)
                kept_prefs.append((idx, data))
                reached_limit = True
            else:
                kept_prefs.append((idx, data))
        except Exception:
            kept_prefs.append((idx, data))

    con.execute("DELETE FROM parent_references")
    if kept_prefs:
        con.executemany(
            "INSERT INTO parent_references(idx, data) VALUES (?, ?)",
            [(i, d) for i, (_, d) in enumerate(kept_prefs)],
        )


def _append_parent_ref_if_needed(
    con: sqlite3.Connection,
    orig_traj_id: str,
    orig_cid: str,
    orig_ttype: int,
    last_step_idx: int,
) -> None:
    prefs = con.execute("SELECT idx, data FROM parent_references ORDER BY idx").fetchall()
    last_covered = -1
    if prefs:
        try:
            for fn, wt, v in parse_fields(prefs[-1][1] or b""):
                if fn == 2 and wt == 0:
                    last_covered = int(v)
                    break
        except Exception:
            pass
    if last_covered >= last_step_idx:
        return
    next_idx = (prefs[-1][0] + 1) if prefs else 0
    pref_blob = encode_fields(
        [
            (1, 2, orig_traj_id.encode("utf-8")),
            (2, 0, max(0, last_step_idx)),
            (3, 0, orig_ttype or 4),
            (4, 0, 15),
            (5, 0, 2),
            (6, 2, orig_cid.encode("utf-8")),
        ]
    )
    con.execute("INSERT INTO parent_references(idx, data) VALUES (?, ?)", (next_idx, pref_blob))


def _copy_brain_dir(src_brain: Path, dst_brain: Path) -> None:
    if not src_brain.exists():
        return
    if dst_brain.exists():
        shutil.rmtree(dst_brain)
    shutil.copytree(src_brain, dst_brain, dirs_exist_ok=True)


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


def create_backup(surface: str, conv_id: str, reason: str = "manual") -> Dict[str, Any]:
    sdir = validate_surface(surface)
    cid = validate_conv_id(conv_id)
    src_db = sdir / "conversations" / f"{cid}.db"
    if not src_db.exists():
        raise FileNotFoundError(f"Cannot backup missing session {cid} in {surface}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = re.sub(r"[^0-9A-Za-z_-]+", "_", reason)[:24]
    bname = f"{ts}_{surface}_{cid[:8]}_{safe_reason}"
    bdir = BACKUP_DIR / bname
    bdir.mkdir(parents=True, exist_ok=True)

    snapshot_db(src_db, bdir / f"{cid}.db")
    ann_src = sdir / "annotations" / f"{cid}.pbtxt"
    if ann_src.exists():
        shutil.copy2(ann_src, bdir / f"{cid}.pbtxt")
    brain_src = sdir / "brain" / cid
    if brain_src.exists():
        shutil.copytree(brain_src, bdir / "brain", dirs_exist_ok=True)

    meta = {
        "backup_name": bname,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "surface": surface,
        "conv_id": cid,
        "reason": reason,
        "title": parse_annotation_pbtxt(ann_src).get("title", "") if ann_src.exists() else "",
        "size_bytes": (bdir / f"{cid}.db").stat().st_size,
    }
    (bdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def list_backups() -> List[Dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    items: List[Dict[str, Any]] = []
    for bdir in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not bdir.is_dir():
            continue
        mfile = bdir / "meta.json"
        if mfile.exists():
            try:
                items.append(json.loads(mfile.read_text(encoding="utf-8")))
            except Exception:
                continue
    return items


def restore_backup(backup_name: str) -> Dict[str, Any]:
    if not BACKUP_NAME_RE.match(backup_name):
        raise ValueError("Invalid backup name")
    bdir = BACKUP_DIR / backup_name
    mfile = bdir / "meta.json"
    if not mfile.exists():
        raise FileNotFoundError(f"Backup not found: {backup_name}")
    meta = json.loads(mfile.read_text(encoding="utf-8"))
    surface = meta["surface"]
    cid = validate_conv_id(meta["conv_id"])
    sdir = validate_surface(surface)

    b_db = bdir / f"{cid}.db"
    if not b_db.exists():
        raise FileNotFoundError("Backup DB missing")

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staged_db = STAGING_DIR / f"restore_{cid}.db"
    shutil.copy2(b_db, staged_db)
    os.chmod(staged_db, 0o600)

    dst_db = sdir / "conversations" / f"{cid}.db"
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    # Remove any stale WAL/SHM if overwriting an idle DB
    for suffix in ("-wal", "-shm"):
        w = Path(str(dst_db) + suffix)
        if w.exists():
            try:
                w.unlink()
            except OSError:
                pass
    os.replace(staged_db, dst_db)

    b_ann = bdir / f"{cid}.pbtxt"
    if b_ann.exists():
        ann_dst = sdir / "annotations" / f"{cid}.pbtxt"
        ann_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b_ann, ann_dst)

    b_brain = bdir / "brain"
    if b_brain.exists():
        _copy_brain_dir(b_brain, sdir / "brain" / cid)

    return {"restored": True, "surface": surface, "conv_id": cid, "backup_name": backup_name}


# ---------------------------------------------------------------------------
# Desktop Language Server live notification (documented ConversationService)
# ---------------------------------------------------------------------------


def notify_desktop_ls(conv_id: str, title: Optional[str] = None) -> bool:
    """Notify the running Antigravity Desktop ConversationService so the sidebar updates immediately."""
    addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS", "").strip()
    csrf = os.environ.get("ANTIGRAVITY_CSRF_TOKEN", "").strip()
    if not addr or not csrf:
        return False

    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "x-antigravity-csrf-token": csrf,
    }

    def _call(method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"http://{addr}/product.v1.ConversationService/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    got = _call("GetConversation", {"conversationId": conv_id})
    if got is None:
        return False
    effective_title = title if title is not None else (got.get("annotations") or {}).get("title", "")
    _call(
        "UpdateConversation",
        {"conversationId": conv_id, "annotations": {"title": effective_title}},
    )
    return True


# ---------------------------------------------------------------------------
# Main operations: Fork, Sync, Rename
# ---------------------------------------------------------------------------


def fork_session(
    source_surface: str,
    source_id: str,
    target_surface: str = "desktop",
    title: Optional[str] = None,
    project_id: Optional[str] = None,
    keep_steps: Optional[int] = None,
) -> Dict[str, Any]:
    src_dir = validate_surface(source_surface)
    dst_dir = validate_surface(target_surface)
    src_cid = validate_conv_id(source_id)

    src_db = src_dir / "conversations" / f"{src_cid}.db"
    if not src_db.exists():
        raise FileNotFoundError(f"Source session {src_cid} not found in {source_surface}")

    proj_map = projects_by_id()
    if project_id is not None and project_id != "" and project_id not in proj_map:
        raise ValueError(f"Unknown project_id: {project_id}")
    effective_project_override = project_id if (project_id is not None and project_id != "") else None

    new_id = str(uuid.uuid4())
    new_traj_id = str(uuid.uuid4())
    new_sess_id = str(uuid.uuid4())

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staged_db = STAGING_DIR / f"{new_id}.db"
    snapshot_db(src_db, staged_db)

    try:
        con = sqlite3.connect(str(staged_db))
        try:
            meta_row = con.execute(
                "SELECT trajectory_id, cascade_id, trajectory_type, source FROM trajectory_meta LIMIT 1"
            ).fetchone()
            if not meta_row:
                raise RuntimeError("Source DB missing trajectory_meta row")
            orig_traj_id, orig_cid, orig_ttype, _orig_src = meta_row

            orig_step_count = int(con.execute("SELECT count(*) FROM steps").fetchone()[0])
            truncated = False
            if keep_steps is not None and 0 < keep_steps < orig_step_count:
                _truncate_db_to_steps(con, keep_steps)
                truncated = True

            final_step_count = int(con.execute("SELECT count(*) FROM steps").fetchone()[0])

            # Record lineage in parent_references
            if final_step_count > 0:
                _append_parent_ref_if_needed(
                    con, orig_traj_id, orig_cid, int(orig_ttype or 4), final_step_count - 1
                )

            target_source_code = 1 if target_surface == "desktop" else 17
            con.execute(
                "UPDATE trajectory_meta SET trajectory_id = ?, cascade_id = ?, source = ?",
                (new_traj_id, new_id, target_source_code),
            )

            blob_row = con.execute(
                "SELECT data FROM trajectory_metadata_blob WHERE id='main' LIMIT 1"
            ).fetchone()
            final_project_id = effective_project_override
            if blob_row and blob_row[0]:
                # Extract original project_id for return value if not overriding
                if final_project_id is None:
                    for fn, wt, v in parse_fields(blob_row[0]):
                        if fn == 18 and wt == 2:
                            final_project_id = safe_utf8(v)
                            break
                new_blob = _rewrite_metadata_blob(
                    blob_row[0],
                    new_cid=new_id,
                    new_sess_id=new_sess_id,
                    target_surface=target_surface,
                    target_project_id=effective_project_override,
                    proj_map=proj_map,
                    update_timestamp=True,
                )
                con.execute(
                    "UPDATE trajectory_metadata_blob SET data = ? WHERE id='main'",
                    (new_blob,),
                )

            con.commit()
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
        finally:
            con.close()

        # Copy brain directory first so it's ready before DB appears
        _copy_brain_dir(src_dir / "brain" / src_cid, dst_dir / "brain" / new_id)

        # Write annotation title if provided (or default "<orig> (Fork)")
        orig_ann = parse_annotation_pbtxt(src_dir / "annotations" / f"{src_cid}.pbtxt")
        final_title = (title or "").strip()
        if not final_title and orig_ann.get("title"):
            suffix = f" (Fork @{final_step_count})" if truncated else " (Fork)"
            final_title = f"{orig_ann['title']}{suffix}"
        if final_title or target_surface == "desktop":
            write_annotation_title(dst_dir, new_id, final_title, touch_view_time=True)

        # Atomic move into target conversations directory
        os.chmod(staged_db, 0o600)
        dst_conv_dir = dst_dir / "conversations"
        dst_conv_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staged_db, dst_conv_dir / f"{new_id}.db")

        # Clean up any staging WAL/SHM
        for suffix in ("-wal", "-shm"):
            p = Path(str(staged_db) + suffix)
            if p.exists():
                p.unlink()
    except Exception:
        if staged_db.exists():
            staged_db.unlink()
        raise

    desktop_notified = False
    if target_surface == "desktop":
        desktop_notified = notify_desktop_ls(new_id, final_title or None)

    return {
        "new_id": new_id,
        "source_id": src_cid,
        "source_surface": source_surface,
        "target_surface": target_surface,
        "title": final_title,
        "project_id": final_project_id or "outside-of-project",
        "original_step_count": orig_step_count,
        "step_count": final_step_count,
        "truncated": truncated,
        "desktop_notified": desktop_notified,
        "conversation_url": f"conversation://{new_id}" if target_surface == "desktop" else None,
        "cli_command": f"agy --conversation {new_id}" if target_surface == "cli" else None,
    }


def sync_session_exact(
    source_surface: str,
    source_id: str,
    target_surface: str,
    overwrite: bool = False,
    title: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy a session across surfaces keeping the EXACT same conversation ID."""
    if source_surface == target_surface:
        raise ValueError("sync_session_exact requires different source and target surfaces; use fork instead")
    src_dir = validate_surface(source_surface)
    dst_dir = validate_surface(target_surface)
    cid = validate_conv_id(source_id)

    src_db = src_dir / "conversations" / f"{cid}.db"
    if not src_db.exists():
        raise FileNotFoundError(f"Source session {cid} not found in {source_surface}")

    dst_db = dst_dir / "conversations" / f"{cid}.db"
    backup_meta = None
    if dst_db.exists():
        if not overwrite:
            raise FileExistsError(
                f"Session {cid} already exists in {target_surface}. Pass overwrite=true or use Fork."
            )
        backup_meta = create_backup(target_surface, cid, reason=f"before_sync_from_{source_surface}")

    proj_map = projects_by_id()
    effective_project_override = project_id if (project_id is not None and project_id != "") else None
    if effective_project_override and effective_project_override not in proj_map:
        raise ValueError(f"Unknown project_id: {effective_project_override}")

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staged_db = STAGING_DIR / f"sync_{cid}.db"
    snapshot_db(src_db, staged_db)

    try:
        if effective_project_override is not None:
            con = sqlite3.connect(str(staged_db))
            try:
                blob_row = con.execute(
                    "SELECT data FROM trajectory_metadata_blob WHERE id='main' LIMIT 1"
                ).fetchone()
                if blob_row and blob_row[0]:
                    new_blob = _rewrite_metadata_blob(
                        blob_row[0],
                        new_cid=cid,
                        new_sess_id="",
                        target_surface=target_surface,
                        target_project_id=effective_project_override,
                        proj_map=proj_map,
                        update_timestamp=False,
                    )
                    con.execute(
                        "UPDATE trajectory_metadata_blob SET data = ? WHERE id='main'",
                        (new_blob,),
                    )
                    con.commit()
            finally:
                con.close()

        _copy_brain_dir(src_dir / "brain" / cid, dst_dir / "brain" / cid)

        if title and title.strip():
            write_annotation_title(dst_dir, cid, title.strip(), touch_view_time=True)
        elif not (dst_dir / "annotations" / f"{cid}.pbtxt").exists():
            src_ann = src_dir / "annotations" / f"{cid}.pbtxt"
            if src_ann.exists():
                (dst_dir / "annotations").mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_ann, dst_dir / "annotations" / f"{cid}.pbtxt")

        os.chmod(staged_db, 0o600)
        dst_db.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            w = Path(str(dst_db) + suffix)
            if w.exists():
                try:
                    w.unlink()
                except OSError:
                    pass
        os.replace(staged_db, dst_db)
    except Exception:
        if staged_db.exists():
            staged_db.unlink()
        raise

    desktop_notified = False
    if target_surface == "desktop":
        desktop_notified = notify_desktop_ls(cid, title.strip() if title else None)

    return {
        "id": cid,
        "source_surface": source_surface,
        "target_surface": target_surface,
        "overwrote": backup_meta is not None,
        "backup": backup_meta,
        "desktop_notified": desktop_notified,
        "conversation_url": f"conversation://{cid}" if target_surface == "desktop" else None,
        "cli_command": f"agy --conversation {cid}" if target_surface == "cli" else None,
    }


def rename_session(surface: str, conv_id: str, title: str) -> Dict[str, Any]:
    sdir = validate_surface(surface)
    cid = validate_conv_id(conv_id)
    db_path = sdir / "conversations" / f"{cid}.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Session {cid} not found in {surface}")
    write_annotation_title(sdir, cid, title.strip(), touch_view_time=False)
    desktop_notified = False
    if surface == "desktop":
        desktop_notified = notify_desktop_ls(cid, title.strip())
    return {"id": cid, "surface": surface, "title": title.strip(), "desktop_notified": desktop_notified}
