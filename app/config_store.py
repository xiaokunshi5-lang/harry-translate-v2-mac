"""Layered local configuration and secret storage for the Mac sidecar."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger(__name__)
MAX_OVERLAY_WINDOWS = 1
PRIMARY_WINDOW_ID = "main"
ENV_KEY_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
SECRET_FIELD_NAMES = {
    "DASHSCOPE_API_KEY",
    "WORKSPACE_ID",
    "DASHSCOPE_WORKSPACE_ID",
}
PLACEHOLDER_KEYS = {
    "在此填入",
    "在此填入你的dashscope_api_key",
    "sk-your-dashscope-api-key-here",
    "your_api_key",
    "your-api-key",
    "sk-xxx",
    "changeme",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "active_provider": "qwen",
    "active_model": "qwen3.8-livetranslate-flash-realtime",
    "source_lang": "auto",
    "target_lang": "zh",
    "provider": "qwen",
    "model": "qwen3.8-livetranslate-flash-realtime",
    "audio_backend": "auto",
    "audio_input": "both",
    "input_device": None,
    "audio_device": None,
    "workspace_id": "",
    "subtitle_display_mode": "bilingual",
    "line_height_multiplier": 1.32,
    "show_subtitle_dividers": True,
    "visible_subtitle_count": 3,
    "mic_subtitle_color": "#FFD54F",
    "system_subtitle_color": "#4FD1C5",
    "subtitle_color": "#FFFFFF",
    "font_size": 16,
    "source_font_size": 15,
    "target_font_size": 18,
    "subtitle_emphasis": "translation_primary",
    "bilingual_layout_order": "source_top",
    "glossary_enabled": True,
    "glossary_wire_budget_bytes": 4096,
    "overlay_window_mode": "single",
    "overlay_immersive": False,
    "overlay_click_through": True,
    "hud_geometry": {
        "x": 200,
        "y": 120,
        "width": 580,
        "height": 240,
        "mode": "expanded",
        "locked": False,
    },
    "background_opacity": 80,
    "subtitle_alignment": "left",
    "is_overlay_locked": False,
    "correction_enabled": True,
    "correction_mode": "llm",
    "correction_model": "qwen-turbo",
    "correction_gap_ms": 1500,
    "summary_enabled": False,
    "summary_mode": "bailian",
    "summary_model": "qwen-turbo",
    "summary_min_words": 120,
    "summary_min_interval_sec": 30,
    "ui_appearance": {
        "density": "comfortable",
        "accent": "teal",
    },
    "windows": [],
}


def _strip_env_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        os.chmod(path, mode)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold()
            if any(token in normalized for token in ("api_key", "secret", "token", "password")):
                return True
            if _contains_secret_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _strip_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).casefold()
            if any(token in normalized for token in ("api_key", "secret", "token", "password")):
                continue
            cleaned[key] = _strip_secret_fields(child)
        return cleaned
    if isinstance(value, list):
        return [_strip_secret_fields(item) for item in value]
    return value


class KeyValidator:
    """Validate and mask API keys without retaining or logging plaintext."""

    @staticmethod
    def is_valid_api_key(api_key: str) -> tuple[bool, str]:
        candidate = (api_key or "").strip()
        normalized = candidate.casefold()
        if not candidate:
            return False, "API Key 不能为空"
        if any(normalized == item or normalized.startswith(item) for item in PLACEHOLDER_KEYS):
            return False, "检测到示例占位符，请填写真实 API Key"
        if len(candidate) < 8:
            return False, "API Key 长度不足"
        if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
            return False, "API Key 不得包含控制字符或换行"
        if not candidate.isascii() or any(character.isspace() for character in candidate):
            return False, "API Key 必须是连续的 ASCII 字符"
        return True, ""

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        candidate = (api_key or "").strip()
        valid, _reason = KeyValidator.is_valid_api_key(candidate)
        if not valid:
            return ""
        return f"{candidate[:3]}****{candidate[-4:]}"


class ConfigStore:
    """Persist secrets in `.env` and non-secret settings in an atomic JSON file."""

    def __init__(self, project_root: Path | str = PROJECT_ROOT) -> None:
        self.project_root = Path(project_root).resolve()
        self.env_path = self.project_root / ".env"
        self.env_example_path = self.project_root / ".env.example"
        self.settings_path = self.project_root / "config" / "user_settings.json"
        self._lock = threading.RLock()
        self.ensure_env_file()
        self.load_settings(create_if_missing=True)

    def ensure_env_file(self) -> bool:
        with self._lock:
            if self.env_path.exists():
                os.chmod(self.env_path, 0o600)
                return False
            if self.env_example_path.is_file():
                content = self.env_example_path.read_text(encoding="utf-8")
            else:
                content = (
                    "DASHSCOPE_API_KEY=sk-your-dashscope-api-key-here\n"
                    "WORKSPACE_ID=\n"
                    "QWEN_REALTIME_WS_URL=\n"
                )
            _atomic_write_text(self.env_path, content, mode=0o600)
            return True

    def read_env(self) -> dict[str, str]:
        with self._lock:
            if not self.env_path.is_file():
                return {}
            values: dict[str, str] = {}
            for line in self.env_path.read_text(encoding="utf-8").splitlines():
                match = ENV_KEY_PATTERN.match(line)
                if match:
                    values[match.group(1)] = _strip_env_value(match.group(2))
            return values

    def get_env_value(self, key: str, default: str = "") -> str:
        file_value = self.read_env().get(key, "")
        if key == "DASHSCOPE_API_KEY" and file_value:
            valid, _reason = KeyValidator.is_valid_api_key(file_value)
            if valid:
                return file_value
        environment_value = os.environ.get(key, "")
        if environment_value:
            return environment_value
        return file_value or default

    def update_env_value(self, key: str, value: str) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("非法环境变量名")
        if "\n" in value or "\r" in value:
            raise ValueError("环境变量值不得包含换行")

        with self._lock:
            self.ensure_env_file()
            lines = self.env_path.read_text(encoding="utf-8").splitlines()
            replacement = f"{key}={value}"
            output: list[str] = []
            replaced = False
            for line in lines:
                match = ENV_KEY_PATTERN.match(line)
                if not match or match.group(1) != key:
                    output.append(line)
                    continue
                if not replaced:
                    output.append(replacement)
                    replaced = True
            if not replaced:
                output.append(replacement)
            _atomic_write_text(self.env_path, "\n".join(output).rstrip() + "\n", mode=0o600)

    def get_api_key(self) -> str:
        return self.get_env_value("DASHSCOPE_API_KEY").strip()

    @property
    def key_mask(self) -> str:
        return KeyValidator.mask_api_key(self.get_api_key())

    @property
    def needs_setup(self) -> bool:
        valid, _reason = KeyValidator.is_valid_api_key(self.get_api_key())
        return not valid

    def set_api_key(self, api_key: str) -> str:
        if not (api_key or "").strip():
            return self.key_mask
        valid, reason = KeyValidator.is_valid_api_key(api_key)
        if not valid:
            raise ValueError(reason)
        candidate = api_key.strip()
        self.update_env_value("DASHSCOPE_API_KEY", candidate)
        return KeyValidator.mask_api_key(candidate)

    def load_settings(self, *, create_if_missing: bool = False) -> dict[str, Any]:
        with self._lock:
            if not self.settings_path.is_file():
                settings = copy.deepcopy(DEFAULT_SETTINGS)
                if create_if_missing:
                    self.save_settings(settings)
                return settings
            try:
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                LOGGER.warning("settings file is unreadable; using defaults: %s", self.settings_path)
                try:
                    raw = self.settings_path.read_bytes()
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    backup_path = self.settings_path.with_name(
                        f"user_settings.corrupt-{timestamp}.json"
                    )
                    _atomic_write_text(backup_path, raw.decode("utf-8", errors="replace"), 0o600)
                except OSError:
                    LOGGER.exception("failed to back up unreadable settings")
                settings = copy.deepcopy(DEFAULT_SETTINGS)
                self.save_settings(settings)
                return settings
            if not isinstance(loaded, dict):
                LOGGER.warning("settings file is not an object; using defaults: %s", self.settings_path)
                settings = copy.deepcopy(DEFAULT_SETTINGS)
                self.save_settings(settings)
                return settings
            loaded = self._sync_lock_aliases(loaded)
            merged = _deep_merge(DEFAULT_SETTINGS, loaded)
            legacy_font = loaded.get("font_size")
            if isinstance(legacy_font, (int, float)):
                if "source_font_size" not in loaded:
                    merged["source_font_size"] = int(legacy_font)
                if "target_font_size" not in loaded:
                    merged["target_font_size"] = int(legacy_font)
            normalized = self._normalize_values(self._normalize_aliases(merged))
            return _strip_secret_fields(normalized)

    @staticmethod
    def _normalize_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return min(max(parsed, minimum), maximum)

    @classmethod
    def _normalize_hud_geometry(cls, raw: Any) -> dict[str, Any]:
        defaults = DEFAULT_SETTINGS["hud_geometry"]
        source = raw if isinstance(raw, dict) else {}
        mode = str(source.get("mode", defaults["mode"])).strip().lower()
        if mode not in {"expanded", "collapsed"}:
            mode = defaults["mode"]
        return {
            "x": cls._normalize_int(
                source.get("x"),
                default=int(defaults["x"]),
                minimum=-10000,
                maximum=10000,
            ),
            "y": cls._normalize_int(
                source.get("y"),
                default=int(defaults["y"]),
                minimum=-10000,
                maximum=10000,
            ),
            "width": cls._normalize_int(
                source.get("width"),
                default=int(defaults["width"]),
                minimum=360,
                maximum=1200,
            ),
            "height": cls._normalize_int(
                source.get("height"),
                default=int(defaults["height"]),
                minimum=100,
                maximum=600,
            ),
            "mode": mode,
            "locked": source.get("locked") is True,
        }

    @staticmethod
    def _sync_lock_aliases(settings: dict[str, Any]) -> dict[str, Any]:
        """Keep the top-level lock flag and hud_geometry.locked in lockstep."""

        normalized = copy.deepcopy(settings)
        hud = normalized.get("hud_geometry")
        has_top_level = "is_overlay_locked" in normalized
        has_nested = isinstance(hud, dict) and "locked" in hud
        if has_top_level:
            locked = normalized.get("is_overlay_locked") is True
        elif has_nested:
            locked = hud.get("locked") is True
        else:
            return normalized
        normalized["is_overlay_locked"] = locked
        if isinstance(normalized.get("hud_geometry"), dict):
            normalized["hud_geometry"]["locked"] = locked
        return normalized

    @classmethod
    def _normalize_values(cls, settings: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(settings)
        normalized["source_font_size"] = cls._normalize_int(
            normalized.get("source_font_size"),
            default=int(DEFAULT_SETTINGS["source_font_size"]),
            minimum=12,
            maximum=48,
        )
        normalized["target_font_size"] = cls._normalize_int(
            normalized.get("target_font_size"),
            default=int(DEFAULT_SETTINGS["target_font_size"]),
            minimum=12,
            maximum=48,
        )
        normalized["font_size"] = normalized["target_font_size"]
        normalized["glossary_wire_budget_bytes"] = cls._normalize_int(
            normalized.get("glossary_wire_budget_bytes"),
            default=int(DEFAULT_SETTINGS["glossary_wire_budget_bytes"]),
            minimum=0,
            maximum=6144,
        )
        normalized["glossary_enabled"] = (
            normalized.get("glossary_enabled") is True
        )
        normalized["correction_enabled"] = (
            normalized.get("correction_enabled") is True
        )
        correction_mode = str(
            normalized.get("correction_mode", DEFAULT_SETTINGS["correction_mode"])
        ).strip().casefold()
        normalized["correction_mode"] = (
            correction_mode
            if correction_mode in {"llm", "rules"}
            else DEFAULT_SETTINGS["correction_mode"]
        )
        correction_model = str(
            normalized.get(
                "correction_model",
                DEFAULT_SETTINGS["correction_model"],
            )
        ).strip()
        normalized["correction_model"] = (
            correction_model
            if correction_model in {"qwen-turbo", "qwen-flash"}
            else DEFAULT_SETTINGS["correction_model"]
        )
        normalized["correction_gap_ms"] = cls._normalize_int(
            normalized.get("correction_gap_ms"),
            default=int(DEFAULT_SETTINGS["correction_gap_ms"]),
            minimum=0,
            maximum=10000,
        )
        normalized["summary_enabled"] = normalized.get("summary_enabled") is True
        summary_mode = str(
            normalized.get("summary_mode", DEFAULT_SETTINGS["summary_mode"])
        ).strip().casefold()
        normalized["summary_mode"] = (
            summary_mode
            if summary_mode in {"bailian", "rules"}
            else DEFAULT_SETTINGS["summary_mode"]
        )
        summary_model = str(
            normalized.get("summary_model", DEFAULT_SETTINGS["summary_model"])
        ).strip()
        normalized["summary_model"] = (
            summary_model
            if summary_model in {"qwen-turbo", "qwen-flash"}
            else DEFAULT_SETTINGS["summary_model"]
        )
        normalized["summary_min_words"] = cls._normalize_int(
            normalized.get("summary_min_words"),
            default=int(DEFAULT_SETTINGS["summary_min_words"]),
            minimum=20,
            maximum=2000,
        )
        normalized["summary_min_interval_sec"] = cls._normalize_int(
            normalized.get("summary_min_interval_sec"),
            default=int(DEFAULT_SETTINGS["summary_min_interval_sec"]),
            minimum=1,
            maximum=600,
        )
        normalized["overlay_window_mode"] = "single"
        raw_display_mode = str(
            normalized.get("subtitle_display_mode", DEFAULT_SETTINGS["subtitle_display_mode"])
        ).strip().casefold()
        display_mode_aliases = {
            "bilingual": "bilingual",
            "translation": "translation",
            "only_translation": "translation",
            "original": "original",
            "only_source": "original",
        }
        normalized["subtitle_display_mode"] = display_mode_aliases.get(
            raw_display_mode,
            str(DEFAULT_SETTINGS["subtitle_display_mode"]),
        )
        legacy_overlay_windows = normalized.pop("overlay_windows", None)
        normalized.pop("multi_window", None)
        normalized.pop("max_overlay_windows", None)
        raw_windows = normalized.get("windows")
        if (not isinstance(raw_windows, list) or not raw_windows) and isinstance(
            legacy_overlay_windows, list
        ):
            raw_windows = legacy_overlay_windows
        if isinstance(raw_windows, list) and raw_windows:
            dict_windows = [item for item in raw_windows if isinstance(item, dict)]
            primary = next(
                (
                    item
                    for item in dict_windows
                    if item.get("window_id") == PRIMARY_WINDOW_ID
                    or item.get("id") == PRIMARY_WINDOW_ID
                ),
                dict_windows[0] if dict_windows else None,
            )
            if primary is not None:
                migrated = copy.deepcopy(primary)
                migrated["window_id"] = PRIMARY_WINDOW_ID
                normalized["windows"] = [migrated][:MAX_OVERLAY_WINDOWS]
            else:
                normalized["windows"] = []
        else:
            normalized["windows"] = []
        normalized["overlay_immersive"] = normalized.get("overlay_immersive") is True
        normalized["overlay_click_through"] = (
            normalized.get("overlay_click_through") is True
        )
        normalized["background_opacity"] = cls._normalize_int(
            normalized.get("background_opacity"),
            default=int(DEFAULT_SETTINGS["background_opacity"]),
            minimum=1,
            maximum=100,
        )
        normalized["hud_geometry"] = cls._normalize_hud_geometry(
            normalized.get("hud_geometry")
        )
        normalized["is_overlay_locked"] = (
            normalized.get("is_overlay_locked") is True
        )
        normalized["hud_geometry"]["locked"] = normalized["is_overlay_locked"]
        if normalized.get("subtitle_emphasis") not in {
            "translation_primary",
            "source_primary",
            "balanced",
        }:
            normalized["subtitle_emphasis"] = DEFAULT_SETTINGS["subtitle_emphasis"]
        if normalized.get("bilingual_layout_order") not in {
            "source_top",
            "target_top",
        }:
            normalized["bilingual_layout_order"] = DEFAULT_SETTINGS[
                "bilingual_layout_order"
            ]
        return normalized

    @staticmethod
    def _normalize_aliases(settings: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(settings)
        provider = str(normalized.get("provider", "")).strip()
        active_provider = str(normalized.get("active_provider", "")).strip()
        if provider in {"", "dashscope"}:
            provider = active_provider or "qwen"
        if active_provider in {"", "dashscope"}:
            active_provider = provider
        normalized["provider"] = provider
        normalized["active_provider"] = active_provider

        model = str(normalized.get("model", "")).strip()
        active_model = str(normalized.get("active_model", "")).strip()
        if not model:
            model = active_model or str(DEFAULT_SETTINGS["model"])
        if not active_model:
            active_model = model
        normalized["model"] = model
        normalized["active_model"] = active_model

        input_device = normalized.get("input_device")
        audio_device = normalized.get("audio_device")
        if not input_device and audio_device:
            normalized["input_device"] = audio_device
        if not audio_device and input_device:
            normalized["audio_device"] = input_device
        return normalized

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("设置必须是 JSON 对象")
        if _contains_secret_key(settings):
            raise ValueError("密钥字段只能写入 .env，禁止写入 user_settings.json")
        settings = self._sync_lock_aliases(settings)
        merged = _deep_merge(DEFAULT_SETTINGS, settings)
        legacy_font = settings.get("font_size")
        if isinstance(legacy_font, (int, float)):
            if "source_font_size" not in settings:
                merged["source_font_size"] = int(legacy_font)
            if "target_font_size" not in settings:
                merged["target_font_size"] = int(legacy_font)
        normalized = self._normalize_values(self._normalize_aliases(merged))
        payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
        with self._lock:
            _atomic_write_text(self.settings_path, payload, mode=0o600)
        return normalized

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("设置更新必须是 JSON 对象")
        if _contains_secret_key(patch):
            raise ValueError("密钥字段只能写入 .env，禁止写入 user_settings.json")
        patch = self._sync_lock_aliases(patch)
        legacy_patch = copy.deepcopy(patch)
        legacy_font = legacy_patch.get("font_size")
        if isinstance(legacy_font, (int, float)):
            if "source_font_size" not in legacy_patch:
                legacy_patch["source_font_size"] = int(legacy_font)
            if "target_font_size" not in legacy_patch:
                legacy_patch["target_font_size"] = int(legacy_font)
        with self._lock:
            merged = _deep_merge(self.load_settings(), legacy_patch)
            return self.save_settings(merged)

    def public_config(self) -> dict[str, Any]:
        settings = self.load_settings()
        return {
            "settings": settings,
            "user_settings": settings,
            "key_mask": self.key_mask,
            "api_key_masked": self.key_mask,
            "needs_setup": self.needs_setup,
        }
