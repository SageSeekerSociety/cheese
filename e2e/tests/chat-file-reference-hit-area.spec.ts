import { test, expect } from "@playwright/test";

// Browser geometry belongs here: happy-dom's element.click() bypasses the hit
// target and cannot see a hover action sitting over a file reference. Mount the
// real ChatPanel and product styles; only history/API/socket are substituted.
// This fixture never sends a message to a backend or an agent.
const FILE = "library/design-fit-4096x2304(3).png";
const fixture = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<div id="fixture"></div><output id="opened"></output>
<script type="module">
import { createApp, h } from '/node_modules/.vite/deps/vue.js';
import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';
import ChatPanel from '/src/components/ChatPanel.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n, { setLocale } from '/src/i18n/index.ts';
import '/src/style.css';
import '/src/styles/fonts.css';

setLocale('zh-CN');
localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }));
window.WebSocket = class {
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
const message = (id, content, created_at) => ({
  id, project_id: 'p1', topic_id: 't1', kind: 'message',
  author_type: 'participant', author: 'me', content,
  reply_to: null, refs: [], created_at, task_id: null,
});
const history = [
  message('before', '前一条消息。\\n'.repeat(8), '2026-10-02T09:17:00Z'),
  message('file', 'Design 正式验收文件：<&${FILE}>', '2026-10-02T09:18:00Z'),
];
window.fetch = async (url) => {
  const u = String(url);
  let data;
  if (u.includes('/members')) data = { data: [
    { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
  ], total: 1 };
  else if (u.includes('/progress')) data = { items: [], updated_at: null };
  else if (u.includes('/tasks') || u.includes('/library')) data = { data: [], total: 0 };
  else if (u.includes('/agent-control')) data = { id: null, connected: false };
  else data = { data: history, total: 2, has_more: false };
  return new Response(JSON.stringify({ code: 200, data }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
};
const topic = {
  id: 't1', project_id: 'p1', parent_id: null, title: '文件引用', kind: 'topic',
  status: 'active', created_by: 'me', created_at: '2026-10-02T09:00:00Z',
};
createApp({ render: () => h(VApp, {}, { default: () => h('main', {
  style: 'width: min(460px, calc(100vw - 64px)); height: 560px; margin: 32px; display: flex; flex-direction: column',
}, [h(ChatPanel, {
  topic, hideHeader: true, showComposer: true,
  onOpenFile: (path, taskId) => {
    const opened = document.querySelector('#opened');
    opened.dataset.path = path;
    opened.dataset.task = taskId ?? '';
  },
})]) }) }).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`;

test("hover actions leave a continuation file reference clickable at its center", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: fixture,
    }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  const row = page.locator('[data-mid="file"]');
  const chip = row
    .locator("[data-file]")
    .filter({ hasText: "design-fit-4096x2304(3).png" });
  await expect(chip).toBeVisible({ timeout: 30_000 });
  await expect(row).toHaveClass(/im-row--cont/);
  const bounds = await chip.boundingBox();
  if (!bounds) throw new Error("file reference has no visible box");
  const center = {
    x: bounds.x + bounds.width / 2,
    y: bounds.y + bounds.height / 2,
  };

  // A normal pointer approach first shows the real message actions. Then use
  // the browser's hit-test result, not DOM dispatch or a forced locator click.
  await page.mouse.move(center.x, center.y);
  const bar = page.locator(".hover-bar--shown");
  await expect(bar).toBeVisible();
  await expect
    .poll(() => bar.evaluate((el) => getComputedStyle(el).opacity))
    .toBe("1");
  const hit = await page.evaluate(({ x, y }) => {
    const target = document.elementFromPoint(x, y);
    return {
      file: target?.closest<HTMLElement>("[data-file]")?.dataset.file ?? null,
      target: target?.outerHTML ?? null,
      chip: document
        .querySelector('[data-mid="file"] [data-file]')
        ?.getBoundingClientRect()
        .toJSON(),
      bar: document
        .querySelector(".hover-bar--shown")
        ?.getBoundingClientRect()
        .toJSON(),
    };
  }, center);
  await test.info().attach("file-reference-hit-target", {
    body: JSON.stringify(hit, null, 2),
    contentType: "application/json",
  });
  await test.info().attach("file-reference-hit-area", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  expect(
    hit.file,
    "hover actions must not consume the file reference center",
  ).toBe(FILE);
  await page.mouse.click(center.x, center.y);
  await expect(page.locator("#opened")).toHaveAttribute("data-path", FILE);
  await expect(page.locator("#opened")).toHaveAttribute("data-task", "");
  await expect(page.locator(".rx-picker")).toHaveCount(0);
});

test("first-row actions stay in the scroll viewport and remain clickable on pointer entry", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: fixture,
    }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  const row = page.locator('[data-mid="before"]');
  await expect(row).toBeVisible({ timeout: 30_000 });
  const scroll = page.getByTestId("chat-scroll");
  await scroll.evaluate((el) => (el.scrollTop = 0));
  await row.locator(".im-text").hover();
  const bar = page.locator(".hover-bar--shown");
  await expect(bar).toBeVisible();
  await expect
    .poll(() => bar.evaluate((el) => getComputedStyle(el).opacity))
    .toBe("1");
  const placement = await bar.evaluate((el) => ({
    bar: el.getBoundingClientRect().toJSON(),
    viewport: el.closest(".messages")!.getBoundingClientRect().toJSON(),
  }));
  await test.info().attach("first-row-hover-placement", {
    body: JSON.stringify(placement, null, 2),
    contentType: "application/json",
  });
  expect(placement.bar.top).toBeGreaterThanOrEqual(placement.viewport.top);
  expect(placement.bar.bottom).toBeLessThanOrEqual(placement.viewport.bottom);

  // Approach through the toolbar's actual hit area, so moving onto its buttons
  // must preserve the same row instead of hiding or retargeting the actions.
  const reply = bar.getByRole("button", { name: "回复", exact: true });
  const bounds = await reply.boundingBox();
  if (!bounds) throw new Error("reply action has no visible box");
  const center = {
    x: bounds.x + bounds.width / 2,
    y: bounds.y + bounds.height / 2,
  };
  await page.mouse.move(center.x, center.y, { steps: 10 });
  await expect(bar).toBeVisible();
  await test.info().attach("first-row-hover-actions", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  await page.mouse.click(center.x, center.y);
  await expect(page.locator(".reply-chip")).toContainText("前一条消息。");
});

// Restore #2399's above-row placement without a reserved action area to make
// this owner fail: the later row's actions consume the preceding file center.
for (const scene of [
  { name: "460px light", width: 1280, theme: "light" },
  { name: "390px dark", width: 390, theme: "dark" },
] as const) {
  test(`adjacent continuation files survive the ten-point pointer return (${scene.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: scene.width, height: 820 });
    const adjacent = fixture
      .replace(
        "'前一条消息。\\n'.repeat(8)",
        "'前一条消息。\\n'.repeat(7) + 'Design 正式验收附件文件：<&" +
          FILE +
          ">'",
      )
      .replace(
        '<html lang="zh-CN">',
        `<html lang="zh-CN" data-theme="${scene.theme}">`,
      )
      .replace(
        "<head>",
        `<head><script>localStorage.setItem('cheesex.theme', '${scene.theme}');</script>`,
      );
    await page.route("**/__chat-file-hit-fixture__", (route) =>
      route.fulfill({ contentType: "text/html", body: adjacent }),
    );
    await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
    const previous = page.locator('[data-mid="before"] [data-file]');
    const current = page.locator('[data-mid="file"] [data-file]');
    await expect(previous).toBeVisible({ timeout: 30_000 });
    await expect(current).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const boxes = await page.evaluate(() => {
      const box = (selector: string) =>
        document.querySelector(selector)!.getBoundingClientRect().toJSON();
      const fragment = (selector: string) =>
        document.querySelector(selector)!.getClientRects()[0].toJSON();
      return {
        previous: box('[data-mid="before"] [data-file]'),
        current: box('[data-mid="file"] [data-file]'),
        previousFragment: fragment('[data-mid="before"] [data-file]'),
        currentFragment: fragment('[data-mid="file"] [data-file]'),
      };
    });
    // Narrow inline file refs can wrap into several rectangles. Click a visible
    // fragment, since the center of the union may be ordinary inter-line space.
    // At 460px each ref has one rectangle: this is the original exact path.
    const start = {
      x: boxes.currentFragment.x + boxes.currentFragment.width / 2,
      y: boxes.currentFragment.y + boxes.currentFragment.height / 2,
    };
    const center = {
      x: boxes.previousFragment.x + boxes.previousFragment.width / 2,
      y: boxes.previousFragment.y + boxes.previousFragment.height / 2,
    };
    await page.mouse.move(start.x, start.y);
    const bar = page.locator(".hover-bar--shown");
    await expect(bar).toBeVisible();
    await expect
      .poll(() => bar.evaluate((el) => getComputedStyle(el).opacity))
      .toBe("1");
    const path = [];
    for (let step = 1; step <= 10; step++) {
      const point = {
        x: start.x + ((center.x - start.x) * step) / 10,
        y: start.y + ((center.y - start.y) * step) / 10,
      };
      await page.mouse.move(point.x, point.y);
      path.push(
        await page.evaluate(({ x, y }) => {
          const target = document.elementFromPoint(x, y);
          return {
            x,
            y,
            row:
              target?.closest<HTMLElement>("[data-mid]")?.dataset.mid ?? null,
            toolbar: !!target?.closest(".hover-bar"),
            target: target?.outerHTML ?? null,
          };
        }, point),
      );
    }
    const hit = await page.evaluate(({ x, y }) => {
      const target = document.elementFromPoint(x, y);
      const rect = (selector: string) =>
        document.querySelector(selector)!.getBoundingClientRect().toJSON();
      return {
        file: target?.closest<HTMLElement>("[data-file]")?.dataset.file ?? null,
        row: target?.closest<HTMLElement>("[data-mid]")?.dataset.mid ?? null,
        previous: rect('[data-mid="before"] [data-file]'),
        current: rect('[data-mid="file"] [data-file]'),
        bar: rect(".hover-bar--shown"),
        content: rect('[data-mid="before"] .im-text'),
        previousRow: rect('[data-mid="before"]'),
        fonts: {
          ready: document.fonts.status,
          mdi: document.fonts.check('15px "Material Design Icons"'),
        },
        theme: document.documentElement.dataset.theme,
        vuetifyTheme: document.querySelector(".v-application")?.className,
        overflow: document.documentElement.scrollWidth > innerWidth,
      };
    }, center);
    await test.info().attach("adjacent-file-ten-point-path", {
      body: JSON.stringify({ beforeHover: boxes, path, hit }, null, 2),
      contentType: "application/json",
    });
    await test
      .info()
      .attach("adjacent-file-hit-area", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
    expect(
      hit.file,
      "the previous file must own its center after approaching from the later row",
    ).toBe(FILE);
    expect(hit.row).toBe("before");
    expect(hit.previous).toEqual(boxes.previous);
    expect(hit.current).toEqual(boxes.current);
    expect(hit.bar.top).toBeGreaterThanOrEqual(hit.previousRow.top);
    expect(hit.bar.bottom).toBeLessThanOrEqual(hit.content.top);
    expect(hit.overflow).toBe(false);
    expect(hit.theme).toBe(scene.theme);
    expect(hit.vuetifyTheme).toContain(`v-theme--${scene.theme}`);
    expect(hit.fonts.mdi).toBe(true);
    await page.mouse.click(center.x, center.y);
    await expect(page.locator("#opened")).toHaveAttribute("data-path", FILE);
    await expect(page.locator("#opened")).toHaveAttribute("data-task", "");
    await expect(page.locator(".rx-picker")).toHaveCount(0);
    await expect(page.locator(".reply-chip")).toHaveCount(0);
    await test.info().attach("adjacent-file-physical-click", {
      body: JSON.stringify(
        {
          opened: await page
            .locator("#opened")
            .evaluate((el) => ({ ...(el as HTMLElement).dataset })),
          pickerCount: await page.locator(".rx-picker").count(),
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
  });
}
