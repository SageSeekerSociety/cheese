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
  style: 'width: 460px; height: 560px; margin: 32px; display: flex; flex-direction: column',
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
