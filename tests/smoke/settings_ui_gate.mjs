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
const outputDir = process.argv[3] || path.join(root, ".tmp", "r07-artifacts");
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
    { name: "tablet", width: 768, height: 640 },
  ]) {
    const page = await browser.newPage({ viewport });
    page.on("pageerror", (error) => consoleErrors.push(`${viewport.name}: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().includes("favicon")) {
        consoleErrors.push(`${viewport.name}: ${message.text()}`);
      }
    });

    await page.goto(`${baseUrl}/settings`, { waitUntil: "networkidle" });
    await page.locator(".settings-shell").waitFor();

    const navLabels = await page.locator(".settings-nav button").allInnerTexts();
    for (const expected of ["模型与服务", "声音与语言", "字幕与外观", "热词与总结", "通用与诊断"]) {
      if (!navLabels.some((label) => label.includes(expected))) {
        throw new Error(`${viewport.name}: missing settings tab ${expected}`);
      }
      await page.getByRole("button", { name: new RegExp(expected) }).click();
      if ((await page.locator(".settings-panel").innerText()).trim().length === 0) {
        throw new Error(`${viewport.name}: empty settings tab ${expected}`);
      }
    }

    await page.getByRole("button", { name: /声音与语言/ }).click();
    await page.getByRole("combobox", { name: "源语言" }).selectOption("fr");
    await page.getByRole("combobox", { name: "目标语言" }).selectOption("zh");

    await page.getByRole("button", { name: /字幕与外观/ }).click();
    await page.getByRole("slider", { name: "原文字号" }).fill("28");
    await page.getByRole("slider", { name: "译文字号" }).fill("20");
    await page.getByRole("button", { name: "原文为主" }).click();
    await page.getByRole("button", { name: "译文在上" }).click();
    const previewSourceSize = await page
      .locator(".preview-stage__subtitle p")
      .evaluate((node) => getComputedStyle(node).fontSize);
    const previewTargetSize = await page
      .locator(".preview-stage__subtitle strong")
      .evaluate((node) => getComputedStyle(node).fontSize);
    const previewFirstTag = await page
      .locator(".preview-stage__subtitle > div")
      .evaluate((node) => node.firstElementChild?.tagName);
    if (previewSourceSize !== "28px" || previewTargetSize !== "20px") {
      throw new Error(
        `${viewport.name}: independent preview sizes drifted: ${previewSourceSize}/${previewTargetSize}`,
      );
    }
    if (previewFirstTag !== "STRONG") {
      throw new Error(`${viewport.name}: target-top layout was not rendered`);
    }

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    if (overflow) throw new Error(`${viewport.name}: horizontal overflow`);

    const storageLeak = await page.evaluate(() => {
      const values = [
        ...Object.keys(localStorage),
        ...Object.values(localStorage),
        ...Object.keys(sessionStorage),
        ...Object.values(sessionStorage),
      ];
      return values.some((value) => String(value).includes("sk-"));
    });
    if (storageLeak) throw new Error(`${viewport.name}: key material in browser storage`);

    const audioNodes = await page.locator("audio").count();
    if (audioNodes !== 0) throw new Error(`${viewport.name}: audio element exists`);

    const bodyText = await page.locator("body").innerText();
    if (/(Token|费用|花费|usage)/i.test(bodyText)) {
      throw new Error(`${viewport.name}: metering UI text exists`);
    }

    await page.screenshot({
      path: path.join(outputDir, `${viewport.name}-settings.png`),
      fullPage: true,
    });

    await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
    await page.locator(".monitor-focus").waitFor();
    const dashboardOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    if (dashboardOverflow) throw new Error(`${viewport.name}: dashboard horizontal overflow`);
    await page.screenshot({
      path: path.join(outputDir, `${viewport.name}-dashboard.png`),
      fullPage: true,
    });

    for (const route of [
      { path: "/en", selector: ".monitor-focus" },
      { path: "/zh", selector: ".monitor-focus" },
      { path: "/split", selector: ".monitor-focus" },
    ]) {
      await page.goto(`${baseUrl}${route.path}`, { waitUntil: "networkidle" });
      await page.locator(route.selector).waitFor();
      const routeOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      );
      if (routeOverflow) {
        throw new Error(`${viewport.name}: ${route.path} horizontal overflow`);
      }
      await page.screenshot({
        path: path.join(outputDir, `${viewport.name}-${route.path.slice(1)}.png`),
        fullPage: true,
      });
    }

    results.push({
      viewport: viewport.name,
      navCount: navLabels.length,
      previewSourceSize,
      previewTargetSize,
      overflow,
      dashboardOverflow,
      audioNodes,
    });
    await page.close();
  }
} finally {
  await browser.close();
}

if (consoleErrors.length) throw new Error(consoleErrors.join("\n"));
console.log(JSON.stringify({ results, outputDir }, null, 2));
