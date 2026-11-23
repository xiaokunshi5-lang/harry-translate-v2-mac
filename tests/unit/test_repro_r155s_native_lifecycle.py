from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = ROOT / "src-tauri"
FRONTEND = ROOT / "frontend"

DISPLAY_SOURCE_PATHS = (
    SRC_TAURI / "src" / "lib.rs",
    SRC_TAURI / "src" / "windows" / "controls.rs",
    SRC_TAURI / "src" / "windows" / "macos_space.rs",
    SRC_TAURI / "src" / "windows" / "native.rs",
    SRC_TAURI / "src" / "windows" / "summary.rs",
    SRC_TAURI / "src" / "windows" / "tray.rs",
)

SUMMARY_LAYOUT_GATE_SCRIPT = r"""
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright-core");

const candidates = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  path.join(
    process.env.HOME || "",
    "Library/Caches/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-mac-arm64/chrome-headless-shell",
  ),
  path.join(
    process.env.HOME || "",
    "Library/Caches/ms-playwright/chromium-1254/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  ),
];
const executablePath = candidates.find((candidate) => fs.existsSync(candidate));
if (!executablePath) {
  throw new Error("No compatible Chrome executable found");
}

const themeCss = fs
  .readFileSync("src/styles/theme.css", "utf8")
  .replace(/^\s*@tailwind.*$/gm, "");
const summaryCss = fs.readFileSync("src/styles/summary.css", "utf8");
const badgeValues = ["2", "9+"];

function toJson(rect) {
  return {
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
    width: rect.width,
    height: rect.height,
  };
}

(async () => {
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--no-sandbox"],
  });
  const results = [];
  try {
    for (const badgeText of badgeValues) {
      const page = await browser.newPage({
        viewport: { width: 48, height: 48 },
        deviceScaleFactor: 1,
      });
      try {
        await page.setContent(
          `<!doctype html>
          <html lang="en" data-window="summary">
            <head>
              <meta charset="utf-8">
              <style>${themeCss}\n${summaryCss}</style>
            </head>
            <body>
              <div id="root">
                <div class="summary-native summary-native--icon" data-summary-widget="icon">
                  <button type="button" class="summary-icon" aria-label="Open summary">
                    <svg width="21" height="21" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"></path>
                    </svg>
                    <span class="summary-icon__badge" aria-label="summary badge">${badgeText}</span>
                  </button>
                </div>
              </div>
            </body>
          </html>`,
        );
        const metrics = await page.evaluate((text) => {
          const icon = document.querySelector(".summary-icon");
          const badge = document.querySelector(".summary-icon__badge");
          if (!icon || !badge) {
            throw new Error("Summary icon fixture did not render");
          }
          return {
            badgeText: text,
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
            icon: icon.getBoundingClientRect().toJSON(),
            badge: badge.getBoundingClientRect().toJSON(),
          };
        }, badgeText);

        const epsilon = 0.01;
        const inViewport = (rect) =>
          rect.left >= -epsilon &&
          rect.top >= -epsilon &&
          rect.right <= metrics.innerWidth + epsilon &&
          rect.bottom <= metrics.innerHeight + epsilon;
        const icon = toJson(metrics.icon);
        const badge = toJson(metrics.badge);
        const noHorizontalOverflow =
          metrics.scrollWidth === metrics.clientWidth &&
          metrics.clientWidth === metrics.innerWidth;
        if (!inViewport(icon) || !inViewport(badge) || !noHorizontalOverflow) {
          throw new Error(
            `48px summary viewport contract failed: ${JSON.stringify({
              badgeText,
              innerWidth: metrics.innerWidth,
              innerHeight: metrics.innerHeight,
              scrollWidth: metrics.scrollWidth,
              clientWidth: metrics.clientWidth,
              icon,
              badge,
            })}`,
          );
        }
        results.push({ badgeText, icon, badge, noHorizontalOverflow });
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ results }));
})().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def production_prefix(path: Path) -> str:
    return read_text(path).split("#[cfg(test)]", 1)[0]


def cargo_dependency_version(manifest: dict[str, Any], name: str) -> str | None:
    tables: list[dict[str, Any]] = []
    dependencies = manifest.get("dependencies")
    if isinstance(dependencies, dict):
        tables.append(dependencies)
    targets = manifest.get("target")
    if isinstance(targets, dict):
        for target in targets.values():
            if not isinstance(target, dict):
                continue
            target_dependencies = target.get("dependencies")
            if isinstance(target_dependencies, dict):
                tables.append(target_dependencies)
    for table in tables:
        spec = table.get(name)
        if isinstance(spec, str):
            return spec.removeprefix("=")
        if isinstance(spec, dict):
            version = spec.get("version")
            if isinstance(version, str):
                return version.removeprefix("=")
    return None


def dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


class NativeLifecycleReproductionTests(unittest.TestCase):
    def assert_source_contains(
        self,
        source: str,
        expected: str,
        message: str,
    ) -> None:
        self.assertTrue(expected in source, message)

    def assert_source_matches(
        self,
        source: str,
        pattern: str,
        message: str,
    ) -> None:
        self.assertIsNotNone(re.search(pattern, source), message)

    def assert_source_excludes(
        self,
        source: str,
        pattern: str,
        message: str,
    ) -> None:
        self.assertIsNone(re.search(pattern, source), message)

    def test_overlay_requires_real_nspanel_and_summary_uses_native_window(self) -> None:
        manifest = tomllib.loads(read_text(SRC_TAURI / "Cargo.toml"))
        with self.subTest(dependency="tauri-nspanel"):
            self.assertEqual(
                cargo_dependency_version(manifest, "tauri-nspanel"),
                "2.1.1",
                "macOS windows must use the fixed tauri-nspanel 2.1.1 dependency",
            )

        native = production_prefix(SRC_TAURI / "src" / "windows" / "native.rs")
        with self.subTest(contract="real NSPanel conversion"):
            self.assert_source_contains(
                native,
                "tauri_nspanel",
                "native.rs must import/use tauri_nspanel",
            )
            self.assert_source_contains(
                native,
                "to_panel",
                "native.rs must convert WebviewWindow into a tauri-nspanel panel",
            )
            self.assert_source_contains(
                native,
                "NSPanel",
                "native.rs must operate on the real NSPanel type",
            )
        with self.subTest(contract="native layer and Space flags"):
            self.assert_source_contains(
                native,
                "setLevel",
                "native.rs must reapply the screen-saver level",
            )
            self.assert_source_contains(
                native,
                "setCollectionBehavior",
                "native.rs must reapply full-screen Space collection behavior",
            )
            self.assert_source_contains(
                native,
                "orderFrontRegardless",
                "native display must use orderFrontRegardless semantics",
            )

        combined_display_source = "\n".join(
            production_prefix(path) for path in DISPLAY_SOURCE_PATHS
        )
        with self.subTest(contract="native hide semantics"):
            self.assert_source_contains(
                combined_display_source,
                "orderOut",
                "native hide must use orderOut semantics",
            )
        with self.subTest(contract="summary is a closable ordinary NSWindow"):
            summary = production_prefix(SRC_TAURI / "src" / "windows" / "summary.rs")
            self.assert_source_contains(
                summary,
                "configure_macos_summary_window",
                "summary must use the ordinary native window helper",
            )
            self.assert_source_contains(
                summary,
                "close_macos_summary_window",
                "summary must use the ordinary window close helper",
            )
            self.assert_source_excludes(
                summary,
                r"configure_macos_overlay_window|NativePanelKind::Summary",
                "summary must not dynamically swizzle a transient WebKit window",
            )
            self.assert_source_excludes(
                native,
                r"\.to_window\(\)|close_macos_panel",
                "the rejected dynamic NSPanel close path must be removed",
            )
        with self.subTest(contract="hidden windows stay hidden across Space changes"):
            self.assert_source_matches(
                production_prefix(SRC_TAURI / "src" / "windows" / "macos_space.rs"),
                r"is_visible|isVisible",
                "Space changes must not show a user-hidden window",
            )

        for path in (
            SRC_TAURI / "src" / "lib.rs",
            SRC_TAURI / "src" / "windows" / "controls.rs",
            SRC_TAURI / "src" / "windows" / "summary.rs",
            SRC_TAURI / "src" / "windows" / "tray.rs",
        ):
            relative = path.relative_to(ROOT)
            with self.subTest(contract="shared native panel path", path=str(relative)):
                source = production_prefix(path)
                self.assert_source_matches(
                    source,
                    r"configure_macos_overlay_window|\.to_panel"
                    r"|show_[a-z0-9_]*(?:window|panel)",
                    f"{relative} must route through the native panel helper",
                )
            with self.subTest(contract="native display helper", path=str(relative)):
                source = production_prefix(path)
                self.assert_source_matches(
                    source,
                    r"(?:(?:show|present)_[a-z0-9_]*(?:window|panel)"
                    r"|order_front_[a-z0-9_]*)\s*\(",
                    f"{relative} must use an orderFront-aware display helper",
                )

        for path in DISPLAY_SOURCE_PATHS:
            relative = path.relative_to(ROOT)
            source = production_prefix(path)
            with self.subTest(contract="no bare window.show", path=str(relative)):
                self.assert_source_excludes(
                    source,
                    r"\b(?:window|overlay_window|summary_window|webview_window)"
                    r"\.show\(\s*\)",
                    f"{relative} must not call bare window.show()",
                )
            with self.subTest(contract="no bare window.hide", path=str(relative)):
                self.assert_source_excludes(
                    source,
                    r"\b(?:window|overlay_window|summary_window|webview_window)"
                    r"\.hide\(\s*\)",
                    f"{relative} must not call bare window.hide()",
                )

    def test_native_summary_48px_badge_stays_inside_viewport(self) -> None:
        self.assertTrue(
            (FRONTEND / "node_modules" / "playwright-core").is_dir(),
            "run npm ci in frontend before pytest",
        )
        chrome_candidates = (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home()
            / "Library/Caches/ms-playwright/chromium_headless_shell-1254/"
            "chrome-headless-shell-mac-arm64/chrome-headless-shell",
            Path.home()
            / "Library/Caches/ms-playwright/chromium-1254/"
            "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
            "Google Chrome for Testing",
        )
        self.assertTrue(
            any(candidate.is_file() for candidate in chrome_candidates),
            "a compatible Chrome/Chromium executable is required",
        )

        environment = os.environ.copy()
        result = subprocess.run(
            ["node", "-e", SUMMARY_LAYOUT_GATE_SCRIPT],
            cwd=FRONTEND,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr or result.stdout,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["results"]), 2)
        for measurement in payload["results"]:
            with self.subTest(badge=measurement["badgeText"]):
                self.assertTrue(measurement["noHorizontalOverflow"])

    def test_process_lifecycle_removes_detached_orphans_and_adds_guarded_shutdown(
        self,
    ) -> None:
        main_source = read_text(ROOT / "app" / "main.py")
        tree = ast.parse(main_source)
        popen_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and dotted_name(node.func).endswith("Popen")
        ]
        with self.subTest(contract="overlay Popen exists"):
            self.assertTrue(popen_calls)
        for call in popen_calls:
            with self.subTest(contract="overlay does not detach session", line=call.lineno):
                self.assertNotIn(
                    "start_new_session",
                    {keyword.arg for keyword in call.keywords},
                )
        for key in ("HARRY_OWNER_PID", "HARRY_INSTANCE_TOKEN"):
            with self.subTest(contract="Python passes lifecycle identity", key=key):
                self.assert_source_contains(
                    main_source,
                    key,
                    f"Python overlay launch must pass {key}",
                )

        manifest = tomllib.loads(read_text(SRC_TAURI / "Cargo.toml"))
        with self.subTest(dependency="tauri-plugin-single-instance"):
            self.assertEqual(
                cargo_dependency_version(manifest, "tauri-plugin-single-instance"),
                "2.4.3",
            )

        lib_source = production_prefix(SRC_TAURI / "src" / "lib.rs")
        with self.subTest(contract="single-instance plugin initialized"):
            self.assert_source_matches(
                lib_source,
                r"tauri_plugin_single_instance::init\s*\(",
                "Tauri must initialize tauri-plugin-single-instance",
            )

        sidecar = production_prefix(SRC_TAURI / "src" / "sidecar.rs")
        rust_sources = "\n".join(
            production_prefix(path)
            for path in sorted((SRC_TAURI / "src").rglob("*.rs"))
        )
        for key in ("HARRY_OWNER_PID", "HARRY_INSTANCE_TOKEN"):
            with self.subTest(contract="Rust consumes lifecycle identity", key=key):
                self.assert_source_contains(
                    rust_sources,
                    key,
                    f"Tauri must consume {key}",
                )
        with self.subTest(contract="attached backend health monitor"):
            self.assert_source_matches(
                sidecar,
                r"fn\s+(?:monitor|watch|guard)_[a-z0-9_]*"
                r"(?:attached|backend)[a-z0-9_]*\s*\(",
                "sidecar.rs must run an attached-backend health monitor",
            )
            self.assert_source_contains(
                sidecar,
                "/api/health?fast=1",
                "attached-backend monitoring must use the fast health endpoint",
            )
            self.assert_source_matches(
                sidecar,
                r"(?:consecutive[^\n]{0,80}(?:>=|==)\s*3"
                r"|(?:>=|==)\s*3[^\n]{0,80}consecutive"
                r"|(?:const|static)\s+\w*(?:FAIL|HEALTH)\w*\s*:"
                r"\s*[^=]+=\s*3\b)",
                "attached-backend monitoring must require 3 consecutive failures",
            )

        controls = production_prefix(
            SRC_TAURI / "src" / "windows" / "controls.rs"
        )
        with self.subTest(contract="loopback control shutdown"):
            self.assert_source_contains(
                controls,
                'TcpListener::bind("127.0.0.1:0")',
                "overlay control must remain loopback-only",
            )
            self.assert_source_matches(
                controls,
                r"[\"']/shutdown[\"']",
                "overlay control must expose POST /shutdown",
            )
            self.assert_source_matches(
                controls,
                r"\.exit\(\s*0\s*\)",
                "control shutdown must terminate the Tauri host",
            )
        with self.subTest(contract="single display state synchronization"):
            self.assert_source_matches(
                controls,
                r"fn show_primary_overlay_window[\s\S]{0,1200}server\.set_visible\(true\)",
                "all display paths must synchronize the control-service visible state",
            )
        with self.subTest(contract="graceful backend shutdown wait"):
            self.assert_source_contains(
                main_source,
                "shutdown_requested",
                "Python must wait for requested overlay shutdown before SIGTERM",
            )
            self.assert_source_contains(
                main_source,
                "time.monotonic() + 1.5",
                "Python overlay shutdown wait must be bounded",
            )
        with self.subTest(contract="Space callback cancellation"):
            macos_space = production_prefix(
                SRC_TAURI / "src" / "windows" / "macos_space.rs"
            )
            self.assert_source_contains(
                macos_space,
                "is_cancelled",
                "deferred Space callbacks must stop after host exit begins",
            )

    def test_transient_summary_uses_ordinary_nswindow_close(self) -> None:
        native = production_prefix(SRC_TAURI / "src" / "windows" / "native.rs")
        match = re.search(
            r"pub fn close_macos_summary_window[\s\S]*?"
            r"(?=\n    pub fn native_panel_diagnostics)",
            native,
        )
        self.assertIsNotNone(
            match,
            "close_macos_summary_window must remain present",
        )
        assert match is not None
        close_body = match.group(0)
        self.assertIn(
            "window.close()",
            close_body,
            "ordinary summary windows must close through Tauri",
        )
        self.assertNotIn(
            "to_window",
            close_body,
            "summary close must not downgrade a dynamically swizzled panel",
        )
        self.assertNotIn(
            ".destroy()",
            close_body,
            "force destroy bypasses Tauri's normal window lifecycle",
        )


if __name__ == "__main__":
    unittest.main()
