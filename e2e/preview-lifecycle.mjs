// Standalone formal-component browser evidence. No real backend/JWT assertions.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { chromium } from "@playwright/test";
const require = createRequire(
  new URL("../frontend/package.json", import.meta.url),
);
const { createServer: createVite } = require("vite");
const root = fileURLToPath(new URL("../frontend/", import.meta.url));
process.chdir(root);
const out = process.env.OUT || "/home/cheese/preview-browser-evidence";
await mkdir(out, { recursive: true });
let version = "v1";
let deny = false;
const posts = [];
const content = createServer(async (req, res) => {
  if (req.method === "POST") {
    let body = "";
    for await (const chunk of req) body += chunk;
    const params = new URLSearchParams(body);
    assert.equal(params.get("grant"), "fixture-scoped-grant");
    assert.equal(params.get("path"), "/_cheese/room/site/index.html");
    assert.equal(params.has("token"), false);
    posts.push({ body, version });
    res
      .writeHead(303, {
        Location: `/_cheese/room/site/index.html?v=${version}`,
      })
      .end();
    return;
  }
  res
    .writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
    .end(
      `<!doctype html><html lang="zh"><meta charset="utf-8"><style>body{font:18px sans-serif;margin:24px;background:#fff;color:#222}section{height:1800px}</style><h1>内容域页面 ${version}</h1><label>未保存输入<input id="draft"></label><section>向下滚动以核实位置保留</section></html>`,
    );
});
await new Promise((resolve) => content.listen(0, "127.0.0.1", resolve));
const contentOrigin = `http://127.0.0.1:${content.address().port}`;
const fixturePlugin = {
  name: "preview-lifecycle-fixture",
  configureServer(server) {
    server.middlewares.use("/preview-lifecycle-fixture", (_req, res) => {
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      res.end(
        `<!doctype html><html lang="zh"><meta charset="utf-8"><style>body{margin:0}.fixture-layout{display:flex;height:900px}.fixture-main{flex:1;padding:28px}.fixture-right{width:var(--pane,360px);height:100%;border-left:1px solid #888;display:flex}.panel-preview{width:100%;height:100%}</style><div id="app"></div><script type="module" src="/@fs/${fileURLToPath(new URL("./preview-lifecycle-fixture.ts", import.meta.url))}"></script></html>`,
      );
    });
  },
};
const vite = await createVite({
  root,
  resolve: {
    alias: {
      vue: fileURLToPath(
        new URL(
          "../frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js",
          import.meta.url,
        ),
      ),
    },
    dedupe: ["vue"],
  },
  plugins: [fixturePlugin],
  server: {
    host: "127.0.0.1",
    port: 0,
    fs: { allow: [fileURLToPath(new URL("../", import.meta.url))] },
  },
  logLevel: "error",
});
await vite.listen();
const base = vite.resolvedUrls.local[0];
let browser;
const findings = [];
try {
  browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    reducedMotion: "reduce",
  });
  await context.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/preview-session") && deny) {
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({
            code: 403,
            message: "fixture authorization denied",
          }),
        });
        return;
      }
      const data = path.endsWith("/preview-session")
        ? {
            url: `${contentOrigin}/_cheese/session`,
            grant: "fixture-scoped-grant",
          }
        : path.endsWith("/preview/file")
          ? {
              path: "site/index.html",
              content: "<p>metadata only</p>",
              version,
              bytes: 20,
              binary: false,
              too_large: false,
            }
          : null;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ code: 200, message: "ok", data }),
      });
    },
  );
  const page = await context.newPage();
  page.on("pageerror", (error) => console.error("pageerror", error));
  page.on("console", (message) => {
    if (message.type() === "error") console.error("console", message.text());
  });
  page.setDefaultTimeout(60_000);
  page.on("requestfailed", (req) =>
    console.error("requestfailed", req.url(), req.failure()),
  );
  console.log("fixture", new URL("preview-lifecycle-fixture", base).href);
  await page.goto(new URL("preview-lifecycle-fixture", base).href);
  console.log("fixture HTML loaded");
  await page.waitForFunction(() => !!window.previewFixture);
  console.log("fixture mounted", await page.locator("body").innerText());
  const frame = await (
    await page.locator("iframe").elementHandle()
  ).contentFrame();
  await frame.locator("#draft").fill("浏览器未保存内容");
  await frame.evaluate(() => scrollTo(0, 640));
  const scroll = await frame.evaluate(() => scrollY);
  const name = await page.locator("iframe").getAttribute("name");
  await page.locator("#focus-anchor").focus();
  await page.evaluate(() => window.previewFixture.refresh());
  await page.waitForTimeout(500);
  assert.equal(posts.length, 1);
  assert.equal(await page.locator("iframe").getAttribute("name"), name);
  assert.equal(await frame.locator("#draft").inputValue(), "浏览器未保存内容");
  assert.equal(await frame.evaluate(() => scrollY), scroll);
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "focus-anchor",
  );
  findings.push(
    "known-version silent refresh: same frame/input/scroll/focus; one real POST",
  );
  for (const theme of ["light", "dark"]) {
    await page.evaluate((theme) => {
      window.previewFixture.theme(theme);
      document.documentElement.style.setProperty("--pane", "320px");
    }, theme);
    await page.screenshot({ path: `${out}/narrow-${theme}.png` });
    const bounds = await page.locator(".fixture-right").boundingBox();
    assert.equal(Math.round(bounds.width), 320);
    assert.ok(bounds.x > 900);
  }
  version = "v2";
  deny = true;
  await page.evaluate(() => window.previewFixture.refresh());
  await page.getByRole("alert").waitFor();
  assert.equal(await page.locator("iframe").getAttribute("name"), name);
  assert.match(
    await page.getByRole("alert").innerText(),
    /当前仍显示上次打开的页面/,
  );
  assert.match(
    await page.locator(".preview-bar").innerText(),
    /读取时版本：v1/,
  );
  await page.screenshot({ path: `${out}/retained-failure.png` });
  const retry = page.getByRole("button", { name: "重试新内容" });
  await retry.focus();
  assert.equal(
    await retry.evaluate((el) => el === document.activeElement),
    true,
  );
  await page.keyboard.press("Tab");
  assert.notEqual(
    await page.evaluate(() => document.activeElement?.hasAttribute("inert")),
    true,
  );
  assert.equal(
    await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
    true,
  );
  findings.push(
    "retry is keyboard-focusable; Tab leaves retry without an inert target; reduced-motion media query matches",
  );
  deny = false;
  await retry.focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    (name) =>
      document.querySelector("iframe")?.name !== name &&
      document.querySelectorAll("iframe").length === 1,
    name,
  );
  assert.equal(posts.length, 2);
  const replacement = await (
    await page.locator("iframe").elementHandle()
  ).contentFrame();
  await replacement.locator("#draft").waitFor();
  assert.equal(await replacement.locator("#draft").inputValue(), "");
  const priorName = await page.locator("iframe").getAttribute("name");
  await page.getByTitle("刷新", { exact: true }).click();
  await page.waitForFunction(
    (name) =>
      document.querySelectorAll("iframe").length === 1 &&
      document.querySelector("iframe")?.name !== name &&
      !document.querySelector('[role="status"]'),
    priorName,
  );
  assert.equal(posts.length, 3);
  findings.push(
    "denied replacement retains v1; explicit retry navigates v2; manual refresh posts again",
  );
  await writeFile(
    `${out}/result.json`,
    JSON.stringify(
      {
        browser: browser.version(),
        findings,
        posts,
        boundary:
          "formal owning component; substituted API; real separate-origin local HTTP form/navigation; no real JWT/cookie/production deployment; reduced-motion emulation, not real suspension",
      },
      null,
      2,
    ),
  );
} finally {
  await browser?.close();
  await vite.close();
  await new Promise((resolve) => content.close(resolve));
}
