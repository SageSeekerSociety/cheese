import { expect, test, type Page } from "@playwright/test";

// The guide's bubble must not sit on top of text a person is reading. Only a real
// browser can judge this: happy-dom has no layout, so every box it reports is 0×0
// and a unit test cannot see one rectangle covering another. The layout below is
// synthetic — a ringed button and columns of body copy — not the product's own
// guide. It exists to put text and a bubble on one screen and measure them.

// Wide enough to wrap, so the page has several *lines* of text rather than one
// box per paragraph. The component treats a line as the obstacle, which is what
// lets it thread between them.
const COPY =
  "芝士把这件事做成了任务，进度和改动都在这一页上。这一段是正文，人在这里要读它，所以浮窗不能压住它。";

const COL_W = 280;

/** Where the bubble may sit, mirroring the component's own window and grid. */
const EDGE = 12;
const GRID = 16;

interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface Layout {
  /** Space between copy columns; 0 makes them contiguous. */
  gap: number;
  /** Left edge of the first column. */
  left: number;
  columns: number;
  rows: number;
}

function copyColumn(layout: Layout, index: number): string {
  const left = layout.left + index * (COL_W + layout.gap);
  let paragraphs = "";
  for (let r = 0; r < layout.rows; r += 1) {
    paragraphs += '<p class="copy-line">' + COPY + "</p>";
  }
  return (
    '<div class="copy-col" style="position:absolute;top:24px;left:' +
    left +
    "px;width:" +
    COL_W +
    'px">' +
    paragraphs +
    "</div>"
  );
}

/** The page under test: `columns` × `rows` of copy, a ringed button, the bubble. */
function fixture(layout: Layout): string {
  let columns = "";
  for (let c = 0; c < layout.columns; c += 1) columns += copyColumn(layout, c);
  return (
    '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">' +
    "<title>guide bubble</title></head>\n<body>\n" +
    '<div id="stage" style="position:relative;width:100vw;height:100vh;overflow:hidden">' +
    '<button id="target" style="position:absolute;left:24px;top:120px;width:96px;height:28px;' +
    "border:1px solid var(--line-2);border-radius:6px;background:var(--raised);color:var(--text)\">" +
    "打开任务</button>" +
    columns +
    '<div id="bubble-host"></div>' +
    "</div>\n" +
    '<script type="module">\n' +
    "import { createApp, h } from '/node_modules/.vite/deps/vue.js';\n" +
    "import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';\n" +
    "import GuideBubble from '/src/components/common/GuideBubble.vue';\n" +
    "import vuetify from '/src/plugins/vuetify.ts';\n" +
    "import i18n, { setLocale } from '/src/i18n/index.ts';\n" +
    "import '/src/style.css';\n" +
    "import '/src/styles/fonts.css';\n" +
    "setLocale('zh-CN');\n" +
    "createApp({ render: () => h(VApp, {}, { default: () => h(GuideBubble, {\n" +
    "  target: document.getElementById('target'),\n" +
    "  title: '任务',\n" +
    "  text: '芝士把这件事做成了任务。点开任务，看进度和改动。',\n" +
    "  step: 1, total: 7, ring: true,\n" +
    "}) }) }).use(vuetify).use(i18n).mount('#bubble-host');\n" +
    "</script></body></html>"
  );
}

/**
 * What the page looks like now: one box per *line* of visible text (a Range, so
 * a wrapped paragraph is several boxes — the same thing the component treats as
 * an obstacle), and the bubble's own box.
 */
async function observe(page: Page) {
  return page.evaluate(() => {
    const lines: Rect[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || !parent.classList.contains("copy-line") || !node.textContent?.trim()) {
        continue;
      }
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width >= 1 && r.height >= 1) {
          lines.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
        }
      }
    }
    const el = document.querySelector(".gb__bubble") as HTMLElement | null;
    const b = el?.getBoundingClientRect();
    return {
      lines,
      bubble: b
        ? {
            left: b.left,
            top: b.top,
            right: b.right,
            bottom: b.bottom,
            width: el!.offsetWidth,
            height: el!.offsetHeight,
          }
        : null,
      viewport: { width: window.innerWidth, height: window.innerHeight },
    };
  }) as Promise<{
    lines: Rect[];
    bubble: (Rect & { width: number; height: number }) | null;
    viewport: { width: number; height: number };
  }>;
}

/** How much of `lines` a rectangle covers, in px². */
function coveredArea(lines: Rect[], box: Rect): number {
  let sum = 0;
  for (const r of lines) {
    const x = Math.min(r.right, box.right) - Math.max(r.left, box.left);
    const y = Math.min(r.bottom, box.bottom) - Math.max(r.top, box.top);
    if (x > 0 && y > 0) sum += x * y;
  }
  return sum;
}

async function open(page: Page, body: string) {
  await page.route("**/__guide-bubble-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body }),
  );
  await page.goto("/__guide-bubble-fixture__", { waitUntil: "commit" });
  await expect(page.locator(".gb__bubble")).toBeVisible({ timeout: 30_000 });
  // The bubble draws hidden for one frame to learn its real height, then places
  // itself against the measured one. Let that settle before reading the result.
  await page.waitForFunction(() => !document.querySelector(".gb__bubble--parked"), null, {
    timeout: 15_000,
  });
  await page.waitForTimeout(300);
}

test("the guide bubble covers no line of text when there is room for it", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await open(page, fixture({ gap: 20, left: 140, columns: 3, rows: 8 }));
  const seen = await observe(page);
  const covered = seen.bubble ? coveredArea(seen.lines, seen.bubble) : 0;
  await test.info().attach("bubble-versus-text.json", {
    body: JSON.stringify({ ...seen, covered: Math.round(covered) }, null, 2),
    contentType: "application/json",
  });
  await test.info().attach("bubble-versus-text.png", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  expect(seen.bubble, "the bubble must be drawn").not.toBeNull();
  // There is free space, so the whole promise applies: no readable line loses
  // any of its area to the bubble.
  expect(Math.round(covered), "the bubble must not cover any text line").toBe(0);
});

test("with nowhere free it covers the least text, not the spot nearest the target", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  // Contiguous columns across the whole width, so the bubble cannot slip into a
  // gap; the copy stops short of the bottom by less than the bubble needs, so no
  // placement is entirely free and which one it picks is the whole point.
  await open(page, fixture({ gap: 0, left: 0, columns: 6, rows: 9 }));
  const seen = await observe(page);
  expect(seen.bubble, "the bubble must be drawn").not.toBeNull();
  const bubble = seen.bubble!;
  const covered = coveredArea(seen.lines, bubble);

  // The least any placement in the window can cover, over the same grid the
  // bubble chooses from. Written by hand here so the assertion has an
  // independent number to compare against.
  let least = Infinity;
  const maxLeft = Math.max(EDGE, seen.viewport.width - bubble.width - EDGE);
  const maxTop = Math.max(EDGE, seen.viewport.height - bubble.height - EDGE);
  for (let top = EDGE; top <= maxTop; top += GRID) {
    for (let left = EDGE; left <= maxLeft; left += GRID) {
      const box = {
        left,
        top,
        right: left + bubble.width,
        bottom: top + bubble.height,
      };
      least = Math.min(least, coveredArea(seen.lines, box));
    }
  }

  await test.info().attach("bubble-least-cover.json", {
    body: JSON.stringify(
      { ...seen, covered: Math.round(covered), least: Math.round(least) },
      null,
      2,
    ),
    contentType: "application/json",
  });
  await test.info().attach("bubble-least-cover.png", {
    body: await page.screenshot(),
    contentType: "image/png",
  });

  // The case is only worth its name if no placement is free; otherwise the first
  // test already covers it.
  expect(least, "this layout must leave the bubble no uncovered spot").toBeGreaterThan(0);
  expect(
    Math.round(covered),
    "the bubble must cover exactly as little as the least-covering spot",
  ).toBeLessThanOrEqual(Math.round(least));
});


test("shrinking the window re-places a bubble that would be clipped", async ({ page }) => {
  // A fixed target in the bottom-right corner and no text: the first placement is
  // clear, so nothing but the window bounds can make it re-place. Shrink the window
  // and the old spot is out of bounds while still covering nothing — the bubble has
  // to move rather than stay where it is and be cut off at the edge.
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route("**/__guide-bubble-window__", (route) =>
    route.fulfill({
      contentType: "text/html",
      body:
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">' +
        "<title>guide bubble window</title></head>\n<body>\n" +
        '<div style="position:relative;width:100vw;height:100vh;overflow:hidden">' +
        '<button id="target" style="position:fixed;left:1120px;top:700px;width:96px;height:28px;' +
        'border:1px solid var(--line-2);border-radius:6px;background:var(--raised);color:var(--text)">' +
        "打开任务</button>" +
        '<div id="bubble-host"></div></div>\n' +
        '<script type="module">\n' +
        "import { createApp, h } from '/node_modules/.vite/deps/vue.js';\n" +
        "import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';\n" +
        "import GuideBubble from '/src/components/common/GuideBubble.vue';\n" +
        "import vuetify from '/src/plugins/vuetify.ts';\n" +
        "import i18n, { setLocale } from '/src/i18n/index.ts';\n" +
        "import '/src/style.css';\n" +
        "import '/src/styles/fonts.css';\n" +
        "setLocale('zh-CN');\n" +
        "createApp({ render: () => h(VApp, {}, { default: () => h(GuideBubble, {\n" +
        "  target: document.getElementById('target'),\n" +
        "  title: '任务',\n" +
        "  text: '芝士把这件事做成了任务。点开任务，看进度和改动。',\n" +
        "  step: 1, total: 7, ring: true,\n" +
        "}) }) }).use(vuetify).use(i18n).mount('#bubble-host');\n" +
        "</script></body></html>",
    }),
  );
  await page.goto("/__guide-bubble-window__", { waitUntil: "commit" });
  await expect(page.locator(".gb__bubble")).toBeVisible({ timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector(".gb__bubble--parked"), null, {
    timeout: 15_000,
  });

  await page.setViewportSize({ width: 900, height: 560 });
  await page.waitForTimeout(600);
  const box = await page.evaluate(() => {
    const b = document.querySelector(".gb__bubble")!.getBoundingClientRect();
    return { right: b.right, bottom: b.bottom, vw: window.innerWidth, vh: window.innerHeight };
  });
  await test.info().attach("bubble-after-resize.json", {
    body: JSON.stringify(box, null, 2),
    contentType: "application/json",
  });
  await test.info().attach("bubble-after-resize.png", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  // The whole bubble stays inside the window, a 12px margin included.
  expect(box.right, "the bubble must not be clipped on the right").toBeLessThanOrEqual(box.vw - 12);
  expect(box.bottom, "the bubble must not be clipped at the bottom").toBeLessThanOrEqual(
    box.vh - 12,
  );
});

test("a bubble the person dragged is pulled back in when the window shrinks", async ({
  page,
}) => {
  // Dragging is main's (#4719): a dragged position wins over the computed one, and
  // a resize has to clamp it back — otherwise the bubble a person parked in the
  // corner stays outside the window after it shrinks.
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route("**/__guide-bubble-drag__", (route) =>
    route.fulfill({
      contentType: "text/html",
      body:
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">' +
        "<title>guide bubble drag</title></head>\n<body>\n" +
        '<div style="position:relative;width:100vw;height:100vh;overflow:hidden">' +
        '<button id="target" style="position:fixed;left:40px;top:40px;width:96px;height:28px;' +
        'border:1px solid var(--line-2);border-radius:6px;background:var(--raised);color:var(--text)">' +
        "打开任务</button>" +
        '<div id="bubble-host"></div></div>\n' +
        '<script type="module">\n' +
        "import { createApp, h } from '/node_modules/.vite/deps/vue.js';\n" +
        "import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';\n" +
        "import GuideBubble from '/src/components/common/GuideBubble.vue';\n" +
        "import vuetify from '/src/plugins/vuetify.ts';\n" +
        "import i18n, { setLocale } from '/src/i18n/index.ts';\n" +
        "import '/src/style.css';\n" +
        "import '/src/styles/fonts.css';\n" +
        "setLocale('zh-CN');\n" +
        "createApp({ render: () => h(VApp, {}, { default: () => h(GuideBubble, {\n" +
        "  target: document.getElementById('target'),\n" +
        "  title: '任务',\n" +
        "  text: '芝士把这件事做成了任务。点开任务，看进度和改动。',\n" +
        "  step: 1, total: 7, ring: true,\n" +
        "}) }) }).use(vuetify).use(i18n).mount('#bubble-host');\n" +
        "</script></body></html>",
    }),
  );
  await page.goto("/__guide-bubble-drag__", { waitUntil: "commit" });
  await expect(page.locator(".gb__bubble")).toBeVisible({ timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector(".gb__bubble--parked"), null, {
    timeout: 15_000,
  });

  // Drag it hard into the bottom-right corner.
  const head = await page.locator(".gb__head").boundingBox();
  expect(head, "the bubble must have a title row to drag").not.toBeNull();
  await page.mouse.move(head!.x + 40, head!.y + head!.height / 2);
  await page.mouse.down();
  await page.mouse.move(1200, 760);
  await page.mouse.up();
  await page.waitForTimeout(300);
  const dragged = await page.evaluate(() => {
    const r = document.querySelector(".gb__bubble")!.getBoundingClientRect();
    return { left: Math.round(r.left), top: Math.round(r.top) };
  });

  await page.setViewportSize({ width: 700, height: 500 });
  await page.waitForTimeout(800);
  const after = await page.evaluate(() => {
    const r = document.querySelector(".gb__bubble")!.getBoundingClientRect();
    return {
      left: Math.round(r.left),
      top: Math.round(r.top),
      right: Math.round(r.right),
      bottom: Math.round(r.bottom),
      vw: window.innerWidth,
      vh: window.innerHeight,
    };
  });
  await test.info().attach("bubble-dragged-then-shrunk.json", {
    body: JSON.stringify({ dragged, after }, null, 2),
    contentType: "application/json",
  });

  // The drag took effect (it did not stay at the auto-placed spot)...
  expect(dragged.left, "the drag must move the bubble").toBeGreaterThan(600);
  // ...and after the shrink the whole bubble is back inside the window.
  expect(after.left, "the bubble must not be clipped on the left").toBeGreaterThanOrEqual(0);
  expect(after.top, "the bubble must not be clipped at the top").toBeGreaterThanOrEqual(0);
  expect(after.right, "the bubble must not be clipped on the right").toBeLessThanOrEqual(after.vw);
  expect(after.bottom, "the bubble must not be clipped at the bottom").toBeLessThanOrEqual(after.vh);
});
