from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def route_paths() -> set[str]:
    tree = ast.parse((ROOT / "legacy" / "v1" / "server.py").read_text(encoding="utf-8"))
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in {"get", "post", "websocket"}
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                routes.add(decorator.args[0].value)
    return routes


class BaselineSanityTests(unittest.TestCase):
    def test_v1_web_routes_remain_available(self) -> None:
        expected = {"/", "/en", "/zh", "/split"}
        self.assertTrue(expected.issubset(route_paths()), route_paths())

    def test_v1_channel_websocket_remains_available_until_legacy_cutover(self) -> None:
        self.assertIn("/ws/{channel}", route_paths())

    def test_v1_entry_files_are_present(self) -> None:
        for relative in [
            "legacy/v1/server.py",
            "legacy/v1/index.html",
            "legacy/v1/switch_key.py",
            "start.sh",
        ]:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_phase_zero_benchmark_uses_50ms_pcm_slices(self) -> None:
        text = (ROOT / "scripts/bench_latency.py").read_text(encoding="utf-8")
        self.assertIn("SAMPLE_RATE = 16_000", text)
        self.assertIn("CHUNK_SIZE = 800", text)
        self.assertIn("asyncio.Queue", text)
        self.assertIn("json.dumps", text)


if __name__ == "__main__":
    unittest.main()
