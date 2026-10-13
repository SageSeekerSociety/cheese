import { test, expect } from "@playwright/test";

// 「回到最新」 has to land on the newest message in a real browser. Rows that
// were never on screen are laid out at an estimated height until they render
// (room-row.css, content-visibility), so where "the bottom" is changes while
// the jump is under way; happy-dom has no layout and cannot see that. Mount the
// real ChatPanel and product styles; only history/API/socket are substituted.
// This fixture never sends a message to a backend or an agent.
const fixture = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<div id="fixture"></div>
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
// A task conversation: the teammate answers in long, multi-paragraph replies,
// far taller than one line of text.
const paragraph = '这一段写的是这一步做了什么、为什么这么做、还剩下什么没做。'.repeat(6);
const history = Array.from({ length: 60 }, (_, i) => ({
  id: 'm' + i, project_id: 'p1', conversation_id: 'task-1', kind: 'message',
  author_type: i % 2 ? 'agent' : 'participant', author: i % 2 ? 'cheese-bot' : 'me',
  content: i === 59 ? '最新的一条' : (i % 2 ? Array(6).fill(paragraph).join('\\n\\n') : '第 ' + i + ' 个问题'),
  reply_to: null, refs: [], created_at: new Date(Date.UTC(2026, 9, 9, 8, i)).toISOString(), task_id: null,
}));
window.fetch = async (url) => {
  const u = String(url);
  let data;
  if (u.includes('/members')) data = { data: [
    { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
    { id: '2', member_handle: 'cheese-bot', name: '芝士', role: 'member', agent: true },
  ], total: 2 };
  else if (u.includes('/progress')) data = { items: [], updated_at: null };
  else if (u.includes('/tasks') || u.includes('/library')) data = { data: [], total: 0 };
  else if (u.includes('/agent-control')) data = { id: null, connected: false };
  else data = { data: history, total: history.length, has_more: false };
  return new Response(JSON.stringify({ code: 200, data }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
};
const topic = {
  id: 'room-1', project_id: 'p1', parent_id: null, title: '任务', kind: 'topic',
  status: 'active', created_by: 'me', created_at: '2026-10-09T08:00:00Z',
};
createApp({ render: () => h(VApp, {}, { default: () => h('main', {
  style: 'width: min(720px, calc(100vw - 32px)); height: calc(100vh - 32px); margin: 16px; display: flex; flex-direction: column',
}, [h(ChatPanel, { topic, conversationId: 'task-1', hideHeader: true, showComposer: true })]) }) })
  .use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`;

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`回到最新 lands on the newest message (${viewport.width}px)`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await page.route("**/__chat-back-to-latest__", (route) =>
      route.fulfill({ contentType: "text/html", body: fixture }),
    );
    await page.goto("/__chat-back-to-latest__", { waitUntil: "commit" });
    const scroll = page.getByTestId("chat-scroll");
    const newest = page.locator('[data-mid="m59"]');
    await expect(newest).toBeVisible({ timeout: 30_000 });

    // Read from the top, the way someone scrolls back through a long task: with
    // the wheel. A scrollTop write with no input while rows are still being
    // laid out is what useChatScroll takes for the browser, not the reader.
    await scroll.hover();
    await page.mouse.wheel(0, -100_000);
    await expect
      .poll(() => scroll.evaluate((el) => el.scrollTop), { timeout: 5_000 })
      .toBe(0);
    const pill = page.getByRole("button", { name: "回到最新" });
    await expect(pill).toBeVisible();
    await pill.click();

    await expect
      .poll(
        () =>
          scroll.evaluate(
            (el) => el.scrollHeight - el.scrollTop - el.clientHeight,
          ),
        { timeout: 5_000 },
      )
      .toBeLessThan(2);
    await expect(newest).toBeInViewport();
    await expect(pill).toBeHidden();
  });
}

// Enter on a message takes the keyboard into its actions, the toolbar first.
// The toolbar floats above the row's top edge, so on a row flush with the top
// of the list it starts outside the visible area; wherever the focus lands it
// has to be scrolled into view. Same fixture: it needs a list long enough to
// put a message at the very top.
for (const arrival of ["already at the top", "arrow key"] as const) {
  test(`Enter on a message at the top of the list shows the focused action (${arrival})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.route("**/__chat-back-to-latest__", (route) =>
      route.fulfill({ contentType: "text/html", body: fixture }),
    );
    await page.goto("/__chat-back-to-latest__", { waitUntil: "commit" });
    await expect(page.locator('[data-mid="m59"]')).toBeVisible({
      timeout: 30_000,
    });
    // m40 is a one-line question; m41 the long reply under it.
    const target = page.locator('[data-mid="m40"]');
    const pinned = arrival === "arrow key" ? "m41" : "m40";
    // Rows render (and change height) as they come on screen: settle until the
    // row stays flush with the top of the list.
    await expect
      .poll(() =>
        page.evaluate((id) => {
          const row = document.querySelector<HTMLElement>(
            `[data-mid="${id}"]`,
          )!;
          const scroll = row.closest<HTMLElement>(".messages")!;
          const gap =
            row.getBoundingClientRect().top -
            scroll.getBoundingClientRect().top;
          scroll.scrollTop += gap;
          return Math.round(gap);
        }, pinned),
      )
      .toBe(0);

    if (arrival === "arrow key") {
      await page.locator(`[data-mid="${pinned}"]`).focus();
      await page.keyboard.press("ArrowUp");
    } else {
      await target.focus();
    }
    await expect(target).toBeFocused();
    await page.keyboard.press("Enter");

    const placed = page.locator(".hover-bar :focus");
    await expect(placed).toHaveAccessibleName("添加表情");
    // The focus ring is drawn outside the button; it has to show whole too.
    const box = await page.evaluate(() => {
      const el = document.activeElement!;
      const style = getComputedStyle(el);
      const ring =
        parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset);
      const rect = el.getBoundingClientRect();
      const list = document
        .querySelector('[data-testid="chat-scroll"]')!
        .getBoundingClientRect();
      return {
        top: rect.top - ring,
        bottom: rect.bottom + ring,
        listTop: list.top,
        listBottom: list.bottom,
      };
    });
    expect(box.top).toBeGreaterThanOrEqual(box.listTop);
    expect(box.bottom).toBeLessThanOrEqual(box.listBottom);
  });
}
