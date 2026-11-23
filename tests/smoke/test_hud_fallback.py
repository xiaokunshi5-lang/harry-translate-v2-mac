from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from typing import Any

from app.config_store import ConfigStore

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_ready(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        readable, _, _ = select.select(
            [process.stdout.fileno()],
            [],
            [],
            max(0.0, deadline - time.monotonic()),
        )
        if not readable:
            break
        line = process.stdout.readline().strip()
        if not line:
            continue
        payload = json.loads(line)
        if payload.get("event") == "sidecar_ready":
            return payload
    raise AssertionError("sidecar failed to become ready")


class HudFallbackSmokeTests(unittest.TestCase):
    def test_hud_fallback_page_serves_and_persists_geometry_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
                encoding="utf-8",
            )
            runtime_file = root / ".agents" / "runtime_port"
            env = os.environ.copy()
            env["DASHSCOPE_API_KEY"] = PLACEHOLDER

            def launch() -> subprocess.Popen[str]:
                return subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "app.main",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "0",
                        "--runtime-port-file",
                        str(runtime_file),
                        "--project-root",
                        str(root),
                        "--no-audio-capture",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            proc1 = launch()
            try:
                ready1 = _read_ready(proc1)
                base_url = f"http://127.0.0.1:{ready1['port']}"
                for route in ("/hud", "/hud?fallback=browser"):
                    with LOCAL_OPENER.open(f"{base_url}{route}", timeout=3) as resp:
                        self.assertEqual(resp.status, 200)
                        html = resp.read().decode("utf-8", errors="ignore")
                        self.assertIn("<div id=\"root\">", html)

                patch_body = json.dumps(
                    {
                        "settings": {
                            "hud_geometry": {
                                "x": 240,
                                "y": 150,
                                "width": 720,
                                "height": 280,
                                "mode": "expanded",
                                "locked": True,
                            }
                        }
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"{base_url}/api/config",
                    data=patch_body,
                    method="PUT",
                    headers={"Content-Type": "application/json"},
                )
                with LOCAL_OPENER.open(req, timeout=3) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                proc1.terminate()
                proc1.wait(timeout=5)

            # Restart backend and verify geometry survives in user_settings.json.
            proc2 = launch()
            try:
                ready2 = _read_ready(proc2)
                with LOCAL_OPENER.open(
                    f"http://127.0.0.1:{ready2['port']}/api/config",
                    timeout=3,
                ) as resp:
                    payload = json.loads(resp.read())
                geo = payload["user_settings"]["hud_geometry"]
                self.assertEqual(geo["x"], 240)
                self.assertEqual(geo["y"], 150)
                self.assertEqual(geo["width"], 720)
                self.assertEqual(geo["height"], 280)
                self.assertEqual(geo["mode"], "expanded")
                self.assertTrue(geo["locked"])
                self.assertEqual(payload["user_settings"]["overlay_window_mode"], "single")

                # Verify browser rendering in headless Chromium without overflow or audio elements.
                chrome_path = Path(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                )
                if chrome_path.is_file():
                    script = """
import { createRequire } from 'node:module';
const require = createRequire(process.argv[1]);
const { chromium } = require('playwright-core');
const browser = await chromium.launch({ executablePath: process.argv[2], headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(process.argv[3], { waitUntil: 'networkidle' });
  await page.locator('.overlay-window').waitFor();
  const initialBox = await page.locator('.overlay-window').boundingBox();
  await page.getByRole('button', { name: '折叠为迷你条' }).click();
  await page.locator('.overlay-window--collapsed').waitFor();
  const collapsedBox = await page.locator('.overlay-window').boundingBox();
  await page.getByRole('button', { name: '展开字幕悬浮窗' }).click();
  await page.locator('.overlay-capsule').waitFor();
  const audioCount = await page.locator('audio').count();
  console.log(JSON.stringify({ initialBox, collapsedBox, audioCount }));
} finally {
  await browser.close();
}
"""
                    res = subprocess.run(
                        [
                            "node",
                            "--input-type=module",
                            "-e",
                            script,
                            str(ROOT / "frontend" / "package.json"),
                            str(chrome_path),
                            f"http://127.0.0.1:{ready2['port']}/hud?fallback=browser",
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(res.returncode, 0, res.stderr or res.stdout)
                    data = json.loads(res.stdout.strip())
                    self.assertEqual(data["audioCount"], 0)
                    self.assertEqual(round(data["collapsedBox"]["width"]), 280)
                    self.assertEqual(round(data["collapsedBox"]["height"]), 54)
                    self.assertEqual(round(data["initialBox"]["width"]), 720)
            finally:
                proc2.terminate()
                proc2.wait(timeout=5)

            reloaded = ConfigStore(root).load_settings()
            self.assertEqual(reloaded["hud_geometry"]["width"], 720)


if __name__ == "__main__":
    unittest.main()
