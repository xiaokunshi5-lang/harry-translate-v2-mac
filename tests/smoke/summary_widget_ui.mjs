import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const require = createRequire(path.join(root, "frontend", "package.json"));
const { chromium } = require("playwright-core");

const baseUrl = process.argv[2];
const outputDir = process.argv[3] || path.join(root, ".tmp", "r15-artifacts");
const chromeCandidates = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  path.join(
    os.homedir(),
    "Library/Caches/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-mac-arm64/chrome-headless-shell",
  ),
  path.join(
    os.homedir(),
    "Library/Caches/ms-playwright/chromium-1254/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  ),
];
const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));

if (!baseUrl) throw new Error("base url is required");
if (!executablePath) throw new Error("No compatible Chrome executable found");

fs.mkdirSync(outputDir, { recursive: true });

const summaryPayload = {
  enabled: true,
  mode: "bailian",
  model: "qwen-turbo",
  engine_used: "bailian",
  pending_words: 0,
  pinned: [
    { level: 5, text: "项目必须在十二月上线。", category: "decision" },
    { level: 4, text: "目标是增长20%。", category: "metric" },
  ],
  archived: [
    { level: 2, text: "背景情况属于辅助信息。", category: "detail" },
  ],
};

async function setSummaryEnabled(page, enabled) {
  const result = await page.evaluate(async (nextEnabled) => {
    const response = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        settings: {
          summary_enabled: nextEnabled,
          summary_mode: "bailian",
          summary_model: "qwen-turbo",
        },
      }),
    });
    return response.status;
  }, enabled);
  if (result !== 200) throw new Error(`failed to set summary enabled=${enabled}`);
}

const browser = await chromium.launch({
  executablePath,
  headless: true,
  args: ["--no-sandbox"],
});

const results = [];
const consoleErrors = [];

try {
  for (const viewport of [
    { name: "desktop", width: 1440, height: 900 },
    { name: "narrow", width: 390, height: 844 },
  ]) {
    const page = await browser.newPage({ viewport });
    const summaryRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/summary")) summaryRequests.push(request.url());
    });
    page.on("pageerror", (error) => consoleErrors.push(`${viewport.name}: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().includes("favicon")) {
        consoleErrors.push(`${viewport.name}: ${message.text()}`);
      }
    });
    await page.route("**/api/summary", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(summaryPayload),
      }),
    );

    await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
    await page.locator(".monitor-focus").waitFor();
    if ((await page.locator("[data-summary-widget]").count()) !== 0) {
      throw new Error(`${viewport.name}: disabled summary rendered DOM`);
    }
    if (summaryRequests.length !== 0) {
      throw new Error(`${viewport.name}: disabled summary made API requests`);
    }

    await setSummaryEnabled(page, true);
    await page.reload({ waitUntil: "networkidle" });
    const icon = page.getByRole("button", { name: "打开重点总结" });
    await icon.waitFor();
    const box = await icon.boundingBox();
    if (!box || box.width > 48 || box.height > 48) {
      throw new Error(`${viewport.name}: summary icon exceeds 48x48`);
    }

    await icon.click();
    await page.locator('[data-summary-widget="expanded"]').waitFor();
    const pinned = page.locator(".summary-point--pinned");
    if ((await pinned.count()) !== 2) {
      throw new Error(`${viewport.name}: pinned point count drifted`);
    }
    if (!(await pinned.nth(0).innerText()).includes("项目必须在十二月上线")) {
      throw new Error(`${viewport.name}: pinned order drifted`);
    }
    if ((await page.locator(".summary-archive").count()) !== 1) {
      throw new Error(`${viewport.name}: archived details missing`);
    }

    const layout = await page.evaluate(() => {
      const panel = document.querySelector(".summary-panel");
      return {
        documentOverflow:
          document.documentElement.scrollWidth > document.documentElement.clientWidth,
        panelOverflow: panel ? panel.scrollWidth > panel.clientWidth : false,
        audioNodes: document.querySelectorAll("audio").length,
      };
    });
    if (layout.documentOverflow || layout.panelOverflow) {
      throw new Error(`${viewport.name}: summary caused horizontal overflow`);
    }
    if (layout.audioNodes !== 0) {
      throw new Error(`${viewport.name}: summary introduced audio nodes`);
    }
    const bodyText = await page.locator("body").innerText();
    if (/(Token|usage|费用|花费)/i.test(bodyText)) {
      throw new Error(`${viewport.name}: summary exposed metering UI`);
    }

    await page.screenshot({
      path: path.join(outputDir, `${viewport.name}-summary.png`),
      fullPage: true,
    });

    await page.getByRole("button", { name: "收起回小图标" }).click();
    await page.getByRole("button", { name: "打开重点总结" }).waitFor();
    await page.getByRole("button", { name: "打开重点总结" }).click();
    await page.getByRole("button", { name: "彻底关闭总结" }).click();
    await page.locator("[data-summary-widget]").waitFor({ state: "detached" });

    results.push({
      viewport: viewport.name,
      icon: { width: box.width, height: box.height },
      ...layout,
      summaryRequests: summaryRequests.length,
    });
    await page.close();
  }
} finally {
  await browser.close();
}

if (consoleErrors.length) throw new Error(consoleErrors.join("\n"));
console.log(JSON.stringify({ results, outputDir }, null, 2));
