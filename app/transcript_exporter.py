"""Single-session transcript persistence and MD/SRT/JSON export."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
FORBIDDEN_METER_KEYS = {
    "usage",
    "usage_stats",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "token_count",
    "cost",
    "billing",
    "price",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in FORBIDDEN_METER_KEYS or normalized.endswith("_tokens"):
                continue
            cleaned[str(key)] = _clean_value(child)
        return cleaned
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_timestamp(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _validate_session_id(session_id: Any) -> str:
    value = str(session_id or "").strip()
    if not SESSION_ID_RE.fullmatch(value):
        raise ValueError("invalid_session_id")
    return value


def _normalize_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("window_id", "main") != "main":
        return None
    sent_id = _coerce_int(raw.get("sent_id", raw.get("sentence_id")), -1)
    if sent_id < 0:
        return None
    entry: dict[str, Any] = {
        "window_id": "main",
        "sent_id": sent_id,
        "source": str(raw.get("source", "")),
        "target": str(raw.get("target", "")),
        "channel": str(raw.get("channel", "main")),
        "target_lang": str(raw.get("target_lang", "")),
        "source_hash": str(raw.get("source_hash", "")),
    }
    for optional in ("patched_source", "patched_target"):
        if optional in raw:
            entry[optional] = str(raw[optional])
    for optional in ("start_ms", "end_ms"):
        if optional in raw:
            entry[optional] = max(0, _coerce_int(raw[optional]))
    if "para_id" in raw:
        entry["para_id"] = str(raw["para_id"])
    if "merged_with_prev_id" in raw:
        entry["merged_with_prev_id"] = _coerce_int(raw["merged_with_prev_id"])
    return entry


def _normalize_summary_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text", "")).strip()
    if not text:
        return None
    return {
        "level": max(1, min(5, _coerce_int(raw.get("level"), 1))),
        "text": text,
        "category": str(raw.get("category", "")),
    }


def _canonical_payload(
    raw: dict[str, Any],
    *,
    session_id: str,
    client_saved_at: int,
) -> dict[str, Any]:
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        entries_raw = raw.get("transcript", [])
    entries = [
        normalized
        for item in entries_raw
        if (normalized := _normalize_entry(item)) is not None
    ]
    summary_raw = raw.get("summary_items", raw.get("notes", []))
    summary_items = [
        normalized
        for item in (summary_raw if isinstance(summary_raw, list) else [])
        if (normalized := _normalize_summary_item(item)) is not None
    ]
    payload: dict[str, Any] = {
        "id": session_id,
        "session_id": session_id,
        "clientSavedAt": client_saved_at,
        "client_saved_at": client_saved_at,
        "created_at": str(raw.get("created_at") or _iso_now()),
        "updated_at": _iso_now(),
        "audio_source": str(raw.get("audio_source", "microphone")),
        "source_lang": str(raw.get("source_lang", "auto")),
        "target_lang": str(raw.get("target_lang", "zh")),
        "entries": entries,
        "summary_items": summary_items,
    }
    for key in ("started_at", "title", "subject"):
        if key in raw:
            payload[key] = _clean_value(raw[key])
    return payload


def _format_srt_time(milliseconds: int) -> str:
    value = max(0, int(milliseconds))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _entry_source(entry: dict[str, Any]) -> str:
    return str(entry.get("patched_source") or entry.get("source") or "")


def _entry_target(entry: dict[str, Any]) -> str:
    return str(entry.get("patched_target") or entry.get("target") or "")


@dataclass(frozen=True, slots=True)
class ExportResult:
    content: str
    media_type: str
    filename: str


class SessionPersistenceManager:
    """Atomic single-session JSON storage with monotonic client timestamps."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.exports_dir = self.project_root / "exports"
        self.locks_dir = self.exports_dir / ".locks"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._thread_locks: dict[str, threading.RLock] = {}

    def _thread_lock(self, session_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._thread_locks.setdefault(session_id, threading.RLock())

    def _session_path(self, session_id: str) -> Path:
        return self.exports_dir / f"session_{session_id}.json"

    def _read_unlocked(self, session_id: str) -> dict[str, Any] | None:
        path = self._session_path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        temp_name = ""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)

    def save(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_payload, dict):
            raise ValueError("session_payload_required")
        payload = raw_payload.get("session", raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("session_payload_required")
        session_id = _validate_session_id(payload.get("id", payload.get("session_id")))
        client_saved_at = _coerce_timestamp(
            payload.get("clientSavedAt", payload.get("client_saved_at"))
        )
        if client_saved_at <= 0:
            client_saved_at = _now_ms()
        canonical = _canonical_payload(
            payload,
            session_id=session_id,
            client_saved_at=client_saved_at,
        )
        path = self._session_path(session_id)
        lock_path = self.locks_dir / f"{session_id}.lock"
        with self._thread_lock(session_id):
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    previous = self._read_unlocked(session_id)
                    previous_timestamp = _coerce_timestamp(
                        (previous or {}).get(
                            "clientSavedAt",
                            (previous or {}).get("client_saved_at"),
                        )
                    )
                    if previous_timestamp > client_saved_at:
                        return {
                            "ok": True,
                            "ignored": True,
                            "reason": "stale_timestamp",
                            "session_id": session_id,
                            "clientSavedAt": previous_timestamp,
                        }
                    self._atomic_write(path, canonical)
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return {
            "ok": True,
            "ignored": False,
            "session_id": session_id,
            "clientSavedAt": client_saved_at,
            "entries": len(canonical["entries"]),
        }

    def load(self, session_id: str) -> dict[str, Any]:
        safe_id = _validate_session_id(session_id)
        data = self._read_unlocked(safe_id)
        if data is None:
            raise FileNotFoundError("session_not_found")
        return data

    def latest(self) -> dict[str, Any] | None:
        candidates: list[tuple[float, dict[str, Any]]] = []
        for path in self.exports_dir.glob("session_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    modified = 0.0
                candidates.append((modified, data))
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item[0],
                _coerce_timestamp(item[1].get("clientSavedAt")),
            ),
            reverse=True,
        )
        return candidates[0][1]

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        lines = [
            "# HARRY Translate Session",
            "",
            f"- Session: `{payload['id']}`",
            f"- Created: {payload.get('created_at', '')}",
            f"- Source: {payload.get('source_lang', 'auto')}",
            f"- Target: {payload.get('target_lang', 'zh')}",
            "",
        ]
        summary = payload.get("summary_items", [])
        if summary:
            lines.extend(["## Key Points", ""])
            for item in sorted(summary, key=lambda value: -_coerce_int(value.get("level"), 1)):
                level = _coerce_int(item.get("level"), 1)
                lines.append(f"- [{level}] {item.get('text', '')}")
            lines.append("")
        lines.extend(["## Transcript", ""])
        for index, entry in enumerate(payload.get("entries", []), start=1):
            lines.extend(
                [
                    f"### {index}",
                    "",
                    f"**Source:** {_entry_source(entry)}",
                    "",
                    f"**Target:** {_entry_target(entry)}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_srt(payload: dict[str, Any]) -> str:
        blocks: list[str] = []
        previous_end = 0
        for index, entry in enumerate(payload.get("entries", []), start=1):
            start = _coerce_int(entry.get("start_ms"), previous_end)
            end = _coerce_int(entry.get("end_ms"), start + 800)
            if end <= start:
                end = start + 800
            start = max(start, previous_end)
            if end <= start:
                end = start + 400
            previous_end = end
            source = _entry_source(entry).strip()
            target = _entry_target(entry).strip()
            text = "\n".join(part for part in (source, target) if part)
            blocks.append(
                f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}\n"
            )
        return "\n".join(blocks).rstrip() + ("\n" if blocks else "")

    def export(self, session_id: str, format_name: str) -> ExportResult:
        payload = self.load(session_id)
        normalized = str(format_name or "").strip().casefold()
        if normalized == "md":
            return ExportResult(
                self._render_markdown(payload),
                "text/markdown; charset=utf-8",
                f"session_{payload['id']}.md",
            )
        if normalized == "srt":
            return ExportResult(
                self._render_srt(payload),
                "application/x-subrip; charset=utf-8",
                f"session_{payload['id']}.srt",
            )
        if normalized == "json":
            return ExportResult(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                "application/json; charset=utf-8",
                f"session_{payload['id']}.json",
            )
        raise ValueError("unsupported_export_format")
