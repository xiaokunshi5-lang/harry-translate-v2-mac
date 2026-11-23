from __future__ import annotations

import gc
import hashlib
import json
import shutil
import statistics
import subprocess
import tempfile
import time
import tomllib
import tracemalloc
from collections import deque
from functools import partial
from pathlib import Path
from typing import Any

from app.engines.base import EngineState
from app.engines.qwen_realtime import (
    AUTO_COMMIT_LOW_ENERGY_RMS,
    HEALTH_CHECK_INTERVAL_SEC,
    HEALTH_PROBE_TIMEOUT_SEC,
    SESSION_FINISH_DRAIN_SEC,
    QwenRealtimeAdapter,
    classify_engine_error,
)
from app.glossary import GlossaryStore
from app.session_manager import MAX_OVERLAY_WINDOWS, sanitize_target_for_lang
from app.transcript_cleaner import (
    SingleSessionCleaner,
    _join_text,
    _strip_false_period,
    assign_para,
    clean_speech,
    mend_fragment,
)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
MAX_CARDS = 1000
MAX_GLOSSARY_WIRE_BYTES = 6144
MAX_COLD_START_MS = 5.0
MAX_TAIL_HEAD_RATIO = 1.2


class _CaptureSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []
        self.closed = False

    def settimeout(self, timeout: float | None) -> None:
        self.timeouts.append(timeout)

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def close(self) -> None:
        self.closed = True


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _clock_value(clock: list[float]) -> float:
    return clock[0]


class _FullHistoryReferenceCleaner:
    """Test-local copy of the former full-history R13 paragraph algorithm."""

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._sentences: list[str] = []
        self._last_pre_mend_source = ""
        self._last_output_target = ""
        self._last_final_at: float | None = None
        self._last_sent_id: int | None = None
        self._next_sent_id = 0

    def finalize_utterance(
        self,
        source: str,
        target: str,
        *,
        sent_id: int | None = None,
        source_lang: str = "en",
        target_lang: str = "zh",
        gap_ms: float | None = None,
        protected_terms: tuple[str, ...] = (),
    ) -> tuple[str, str, int | None, str]:
        now = self._clock()
        cleaned_source = clean_speech(source, source_lang)
        cleaned_target = clean_speech(target, target_lang)
        if gap_ms is None and self._last_final_at is not None:
            gap_ms = max(0.0, (now - self._last_final_at) * 1_000)

        merged_with_prev_id: int | None = None
        if self._last_pre_mend_source and cleaned_source:
            should_merge, previous, following = mend_fragment(
                self._last_pre_mend_source,
                cleaned_source,
                gap_ms,
                protected_terms=protected_terms,
            )
            if should_merge and self._last_sent_id is not None:
                merged_with_prev_id = self._last_sent_id
                cleaned_source = _join_text(previous, following)
                cleaned_target = _join_text(
                    self._last_output_target,
                    cleaned_target,
                )
                if self._sentences:
                    self._sentences.pop()

        output_source = _strip_false_period(cleaned_source)
        self._sentences.append(output_source)
        paragraphs = assign_para(self._sentences)
        paragraph_id = f"p{max(1, len(paragraphs))}"
        if sent_id is None:
            self._next_sent_id += 1
            sent_id = self._next_sent_id
        else:
            self._next_sent_id = max(self._next_sent_id, int(sent_id))
        self._last_pre_mend_source = cleaned_source
        self._last_output_target = cleaned_target
        self._last_final_at = now
        self._last_sent_id = int(sent_id)
        return output_source, cleaned_target, merged_with_prev_id, paragraph_id

    def reset(self) -> None:
        self._sentences.clear()
        self._last_pre_mend_source = ""
        self._last_output_target = ""
        self._last_final_at = None
        self._last_sent_id = None
        self._next_sent_id = 0


def test_bounded_cleaner_matches_full_history_reference_semantics() -> None:
    long_sentence = ("x" * 521) + "."
    cases = [
        ("soft_rows", [f"Sentence {index}." for index in range(1, 20)]),
        (
            "hard_boundary_merge",
            [
                *(f"Sentence {index}." for index in range(1, 12)),
                "We talked about the.",
                "Project scope.",
            ],
        ),
        (
            "sealed_boundary_then_merge",
            [
                *(f"Sentence {index}." for index in range(1, 13)),
                "We talked about the.",
                "Project scope.",
            ],
        ),
        (
            "character_boundary",
            [long_sentence, "Next sentence.", "Another sentence."],
        ),
        (
            "cjk_and_protected_term",
            [
                "这是第一句话。",
                "这是第二句话。",
                "Jensen Huang is here.",
                "We discussed Project scope.",
            ],
        ),
    ]

    for name, sources in cases:
        with tempfile.TemporaryDirectory(prefix=f"harry-r17-{name}-"):
            now = [10.0]
            bounded = SingleSessionCleaner(clock=partial(_clock_value, now))
            reference = _FullHistoryReferenceCleaner(
                clock=partial(_clock_value, now)
            )
            try:
                for index, source in enumerate(sources, start=1):
                    target = f"译文 {index}。"
                    kwargs = {
                        "sent_id": index,
                        "source_lang": "en" if source.isascii() else "zh",
                        "target_lang": "zh",
                        "gap_ms": 100.0,
                        "protected_terms": ("Project",),
                    }
                    bounded_result = bounded.finalize_utterance(
                        source,
                        target,
                        **kwargs,
                    )
                    reference_result = reference.finalize_utterance(
                        source,
                        target,
                        **kwargs,
                    )
                    assert bounded_result == reference_result, (name, index)
                    now[0] += 0.1
            finally:
                bounded.reset()
                reference.reset()


def test_thousand_card_session_converges_with_bounded_cleaner_state() -> None:
    store = GlossaryStore(ROOT)
    cleaner = SingleSessionCleaner()
    cards: deque[dict[str, Any]] = deque(maxlen=MAX_CARDS)
    first_batch_ms: list[float] = []
    tail_batch_ms: deque[float] = deque(maxlen=100)
    paragraph_ids: list[str] = []
    memory_at_1000 = 0

    tracemalloc.start()
    try:
        for index in range(1, 2001):
            source = (
                "Well um we discuss Jensen Huang and the model architecture "
                f"again in sentence {index}."
            )
            target = f"我们再次讨论黄仁勋和模型架构，这是第 {index} 句。"
            started = time.process_time_ns()
            fixed_source, fixed_target = store.apply_fixes(
                source,
                target,
                source_lang="en",
                target_lang="zh",
            )
            output_source, output_target, merged_id, paragraph_id = (
                cleaner.finalize_utterance(
                    fixed_source,
                    fixed_target,
                    sent_id=index,
                    source_lang="en",
                    target_lang="zh",
                    gap_ms=100.0,
                    protected_terms=("Jensen Huang",),
                )
            )
            source_hash = _source_hash(output_source)
            if merged_id is not None and cards and cards[-1]["sent_id"] == merged_id:
                cards[-1] = {
                    **cards[-1],
                    "source": output_source,
                    "target": output_target,
                    "source_hash": source_hash,
                    "paragraph_id": paragraph_id,
                }
            else:
                cards.append(
                    {
                        "sent_id": index,
                        "source": output_source,
                        "target": output_target,
                        "source_hash": source_hash,
                        "paragraph_id": paragraph_id,
                        "patch_version": 0,
                    }
                )
            elapsed_ms = (time.process_time_ns() - started) / 1_000_000
            if index <= 100:
                first_batch_ms.append(elapsed_ms)
            if index > 1000:
                tail_batch_ms.append(elapsed_ms)
            paragraph_ids.append(paragraph_id)
            if index == 1000:
                gc.collect()
                memory_at_1000, _ = tracemalloc.get_traced_memory()

        gc.collect()
        memory_at_2000, peak = tracemalloc.get_traced_memory()
    finally:
        store.stop_background_load()
        tracemalloc.stop()

    assert len(cards) == MAX_CARDS
    assert cards[0]["sent_id"] == 1001
    assert cards[-1]["sent_id"] == 2000
    assert all(card["source_hash"] == _source_hash(card["source"]) for card in cards)
    assert paragraph_ids[0] == "p1"
    assert paragraph_ids == sorted(
        paragraph_ids,
        key=lambda value: int(value.removeprefix("p")),
    )

    head_ms = statistics.fmean(first_batch_ms)
    tail_ms = statistics.fmean(tail_batch_ms)
    ratio = tail_ms / max(head_ms, 1e-9)
    memory_growth = memory_at_2000 - memory_at_1000

    print(
        "R17-SOAK "
        f"head_ms={head_ms:.6f} tail_ms={tail_ms:.6f} ratio={ratio:.3f} "
        f"memory_growth_kib={memory_growth / 1024:.1f} peak_kib={peak / 1024:.1f}"
    )
    assert ratio <= MAX_TAIL_HEAD_RATIO
    assert memory_growth <= 128 * 1024
    assert memory_at_2000 <= memory_at_1000 * 1.25


def test_single_window_ws_soak_degrade_and_finish_boundaries() -> None:
    assert MAX_OVERLAY_WINDOWS == 1
    assert HEALTH_CHECK_INTERVAL_SEC == 10.0
    assert HEALTH_PROBE_TIMEOUT_SEC == 4.0
    assert SESSION_FINISH_DRAIN_SEC == 0.25
    assert AUTO_COMMIT_LOW_ENERGY_RMS == 380.0

    engine = QwenRealtimeAdapter(
        api_key="sk-test-r17-local-only",
        target_lang="en",
        corpus_phrases={"黄仁勋": "Jensen Huang"},
        enable_health_check=False,
        recovery_continuity=0.01,
        session_finish_drain=0.0,
    )
    socket = _CaptureSocket()
    engine._running = True
    engine._socket = socket
    engine._reconnect_attempt = 3

    assert engine._send_session_update()
    update = socket.sent[-1]
    assert update["type"] == "session.update"
    assert "turn_detection" not in update["session"]
    assert update["session"]["translation"]["corpus"]["phrases"]

    engine.handle_message({"type": "session.updated"})
    assert engine.current_state == EngineState.READY
    assert engine.reconnect_attempt == 3
    for _ in range(1000):
        engine.handle_message({"type": "pong"})
    time.sleep(0.02)
    engine.handle_message({"type": "pong"})
    assert engine.reconnect_attempt == 0

    engine.handle_message(
        {
            "type": "error",
            "error": {
                "code": "<400>",
                "message": "InvalidParameter: corpus length",
            },
        }
    )
    assert engine.corpus_suspended
    degraded_socket = _CaptureSocket()
    engine._running = True
    engine._socket = degraded_socket
    engine._finish_sent = False
    assert engine._send_session_update()
    minimal_update = degraded_socket.sent[-1]
    assert len(json.dumps(minimal_update).encode("utf-8")) == 105
    assert "corpus" not in minimal_update["session"]["translation"]
    assert "turn_detection" not in minimal_update["session"]

    for _ in range(1000):
        assert classify_engine_error("1007 rate limit exceeded") == EngineState.QUOTA_ERROR
        assert classify_engine_error("429 too many requests") == EngineState.QUOTA_ERROR

    french = "Commençons dès maintenant avec les accents français."
    for _ in range(1000):
        assert sanitize_target_for_lang(french, "我们现在开始", "fr") == french

    close_durations_ms: list[float] = []
    for _ in range(25):
        close_socket = _CaptureSocket()
        close_engine = QwenRealtimeAdapter(
            api_key="sk-test-r17-local-only",
            target_lang="zh",
            enable_health_check=False,
            session_finish_drain=0.0,
        )
        close_engine._running = True
        close_engine._socket = close_socket
        close_engine._session_update_count = 1
        started = time.perf_counter()
        close_engine.close()
        close_durations_ms.append((time.perf_counter() - started) * 1000)
        assert close_socket.sent[-1]["type"] == "session.finish"
        assert close_engine.current_state == EngineState.CLOSED

    print(
        "R17-WS "
        f"pong_cycles=1001 rate_limit_cycles=2000 "
        f"close_p99_ms={statistics.quantiles(close_durations_ms, n=100, method='inclusive')[98]:.3f}"
    )
    assert max(close_durations_ms) <= 250.0


def test_curated_glossary_cold_start_and_wire_budget() -> None:
    init_samples_ms: list[float] = []
    for _ in range(7):
        started = time.perf_counter()
        store = GlossaryStore(ROOT)
        init_samples_ms.append((time.perf_counter() - started) * 1000)
        store.stop_background_load()

    packs = store.get_packs()["packs"]
    assert [pack["id"] for pack in packs] == [
        "00_base_emergency",
        "01_curated_everyday",
    ]
    assert sum(pack["terms_count"] for pack in packs) <= 250
    default_phrases = store.get_budgeted_corpus_phrases(
        source_lang="zh",
        target_lang="en",
        max_bytes=4096,
    )
    oversized_phrases = store.get_budgeted_corpus_phrases(
        source_lang="zh",
        target_lang="en",
        max_bytes=99_999,
        hard_cap=99_999,
    )
    default_bytes = len(
        json.dumps(default_phrases, ensure_ascii=False).encode("utf-8")
    )
    hard_bytes = len(
        json.dumps(oversized_phrases, ensure_ascii=False).encode("utf-8")
    )
    assert default_bytes <= 4096
    assert hard_bytes <= MAX_GLOSSARY_WIRE_BYTES
    assert all(len(source) <= 48 for source in oversized_phrases)
    assert all(not source.startswith(("党建", "外交", "方言")) for source in oversized_phrases)

    median_ms = statistics.median(init_samples_ms)
    print(
        "R17-GLOSSARY "
        f"median_ms={median_ms:.3f} max_ms={max(init_samples_ms):.3f} "
        f"default_bytes={default_bytes} hard_bytes={hard_bytes}"
    )
    assert median_ms < MAX_COLD_START_MS


def test_frontend_thousand_card_pipeline_and_dom_soak() -> None:
    pipeline_source = (
        FRONTEND / "src/lib/subtitles/subtitlePipeline.ts"
    ).read_text(encoding="utf-8")
    timeline_css = (
        FRONTEND / "src/windows/overlay/timeline/timeline.css"
    ).read_text(encoding="utf-8")
    assert "MAX_TIMELINE_ENTRIES = 1000" in pipeline_source
    assert ".slice(-MAX_TIMELINE_ENTRIES)" in pipeline_source
    assert "overflow-x: hidden" in timeline_css

    npm = shutil.which("npm")
    assert npm is not None
    result = subprocess.run(
        [
            npm,
            "--prefix",
            "frontend",
            "test",
            "--",
            "--run",
            "src/lib/subtitles/subtitlePipeline.test.ts",
            "src/windows/overlay/timeline/Timeline.test.tsx",
            "src/windows/overlay/timeline/TimelineSoak.test.tsx",
            "-t",
            "keeps finalized entries hard bounded|does not remeasure 1000 historical cards|keeps 1000 finalized cards plus one draft",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    output = f"{result.stdout}\n{result.stderr}"
    assert "3 passed" in output


def test_clean_directory_lock_and_package_contract() -> None:
    required = [
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "requirements.lock",
        ROOT / "rust-toolchain.toml",
        ROOT / "src-tauri/Cargo.lock",
        ROOT / "frontend/.nvmrc",
        ROOT / "frontend/package-lock.json",
    ]
    for path in required:
        assert path.is_file(), path

    with tempfile.TemporaryDirectory(prefix="harry-r17-clean-") as temporary:
        clean = Path(temporary)
        (clean / "frontend").mkdir()
        (clean / "src-tauri").mkdir()
        shutil.copy2(ROOT / "pyproject.toml", clean / "pyproject.toml")
        shutil.copy2(ROOT / "uv.lock", clean / "uv.lock")
        shutil.copy2(ROOT / "requirements.lock", clean / "requirements.lock")
        shutil.copy2(ROOT / "rust-toolchain.toml", clean / "rust-toolchain.toml")
        shutil.copy2(
            ROOT / "src-tauri/Cargo.lock",
            clean / "src-tauri/Cargo.lock",
        )
        shutil.copy2(ROOT / "frontend/.nvmrc", clean / "frontend/.nvmrc")
        shutil.copy2(
            ROOT / "frontend/package-lock.json",
            clean / "frontend/package-lock.json",
        )
        assert not (clean / ".venv").exists()
        assert not (clean / "frontend/node_modules").exists()
        json.loads((clean / "frontend/package-lock.json").read_text(encoding="utf-8"))
        tomllib.loads((clean / "uv.lock").read_text(encoding="utf-8"))
        tomllib.loads((clean / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))

        uv = shutil.which("uv")
        assert uv is not None
        lock_check = subprocess.run(
            [uv, "lock", "--check", "--project", str(clean)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert lock_check.returncode == 0, f"{lock_check.stdout}\n{lock_check.stderr}"

    package_check = subprocess.run(
        [
            str(ROOT / ".venv-v2/bin/python"),
            "scripts/verify_package_smoke.py",
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert package_check.returncode == 0
    assert "PACKAGE-CHECK PASS" in package_check.stdout


def test_release_redlines_and_open_source_notices() -> None:
    active_roots = [
        ROOT / "app",
        ROOT / "frontend/src",
        ROOT / "src-tauri/src",
    ]
    active_files = [
        path
        for root in active_roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx", ".rs", ".css"}
        and ".test." not in path.name
    ]
    active_text = "\n".join(path.read_text(encoding="utf-8") for path in active_files)

    for forbidden in (
        "summary_tokens",
        "get_usage",
        "fetchStats",
        "/api/stats",
        "DilMesh",
        "sokuji",
        "AudioContext",
        "<audio",
        "untranslatedWords",
        "useSubtitleTail",
    ):
        assert forbidden not in active_text

    token_names = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "usage_stats",
    )
    for path in active_files:
        text = path.read_text(encoding="utf-8")
        hits = [name for name in token_names if name in text]
        if hits:
            assert path.name in {"session_manager.py", "transcript_exporter.py"}

    session_source = (ROOT / "app/session_manager.py").read_text(encoding="utf-8")
    config_source = (ROOT / "app/config_store.py").read_text(encoding="utf-8")
    rust_overlay = (ROOT / "src-tauri/src/windows/overlay.rs").read_text(
        encoding="utf-8"
    )
    assert "MAX_OVERLAY_WINDOWS = 1" in session_source
    assert "MAX_OVERLAY_WINDOWS = 1" in config_source
    assert "MAX_OVERLAY_WINDOWS: usize = 1" in rust_overlay

    qwen_source = (ROOT / "app/engines/qwen_realtime.py").read_text(encoding="utf-8")
    qwen38_branch = qwen_source.split(
        'if not self.model.casefold().startswith("qwen3.8"):',
        maxsplit=1,
    )[1].split("with self._state_lock:", maxsplit=1)[0]
    assert "turn_detection" in qwen38_branch
    assert 'startswith("qwen3.8")' in qwen_source

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for required in (
        "yuxino/Mimi",
        "Samoyed123-yed/echo-translate",
        "HiMeditator/auto-caption",
        "sounddevice",
        "SakiRinn/LiveCaptions-Translator",
        "MIT License",
        "Apache License",
        "Version 2.0",
        "No AGPL-3.0 source is copied",
    ):
        assert required in notices
