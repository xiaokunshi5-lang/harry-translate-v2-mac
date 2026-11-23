"""R15.6 regression contract for dynamic subtitle lane scrolling."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TIMELINE_DIR = ROOT / "frontend" / "src" / "windows" / "overlay" / "timeline"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestReproR156CompactLane(unittest.TestCase):
    def test_lane_budget_contract_is_exported(self) -> None:
        model = read(TIMELINE_DIR / "overlayModel.ts")
        self.assertIn("export interface LaneBudget", model)
        self.assertRegex(
            model,
            r"export function subtitleLaneBudget\(",
            "R15.6 requires an exported subtitleLaneBudget pure function",
        )
        self.assertIn("availableLaneHeight", model)
        self.assertIn("lineHeightMultiplier", model)
        self.assertIn("measured", model)

    def test_timeline_wires_compact_lanes_and_live_history_switch(self) -> None:
        timeline = read(TIMELINE_DIR / "Timeline.tsx")
        required_symbols = (
            "useLaneHeight",
            "useLaneRollupGlide",
            "CompactLane",
            "SubtitleLane",
            "measureSource",
            "measureTarget",
            "onWheel",
            "onKeyDown",
            "ArrowUp",
            "PageUp",
            "Home",
            "followLive",
        )
        missing = [symbol for symbol in required_symbols if symbol not in timeline]
        self.assertEqual(
            missing,
            [],
            f"R15.6 timeline integration is incomplete: {missing}",
        )
        self.assertNotIn("useSubtitleTail", timeline)
        self.assertNotRegex(
            timeline,
            r"fontWeight\s*:",
            "timeline must not override the shared emphasisStyle weights",
        )
        self.assertIn("show_subtitle_dividers", timeline)
        self.assertIn("browserFallback", timeline)

    def test_lane_viewport_css_preserves_horizontal_containment(self) -> None:
        css = read(TIMELINE_DIR / "timeline.css")
        self.assertIn(".overlay-lane-viewport", css)
        self.assertIn("overflow: hidden", css)
        self.assertIn("min-width: 0", css)
        self.assertIn("bottom: 0", css)
        self.assertNotIn("overflow-x: auto", css)

    def test_no_new_dependency_or_forbidden_runtime_marker(self) -> None:
        package = json.loads(read(ROOT / "frontend" / "package.json"))
        dependencies = {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
        }
        self.assertNotIn("use-subtitle-tail", dependencies)
        self.assertNotIn("framer-motion", dependencies)

        source_paths = [
            TIMELINE_DIR / "Timeline.tsx",
            TIMELINE_DIR / "overlayModel.ts",
            TIMELINE_DIR / "timeline.css",
        ]
        combined = "\n".join(read(path) for path in source_paths)
        self.assertIsNone(re.search(r"\buseSubtitleTail\b", combined))
        self.assertNotRegex(combined, r"\b(?:usage|token_count|cost_usd)\b")


if __name__ == "__main__":
    unittest.main()
