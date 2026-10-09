import { test, expect, type Page } from "@playwright/test";

// The start guide's bubble floats over the page next to the control it points
// at. It must never sit on top of something a person needs to use: the
// composer it is pointing into, or the answers under a question 芝士 just
// asked. Where things are on screen is layout, which happy-dom does not have,
// so this mounts the real ChatPanel and product styles in a browser; only
// history/API/socket are substituted. Nothing is sent to a backend or agent.
const fixture = (opts: { spoken: boolean }) => `<!doctype html>
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
localStorage.clear();
localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }));
window.WebSocket = class {
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
const block = (id, author, content, minute, meta) => ({
  id, project_id: 'p1', conversation_id: 'root-1', kind: 'message',
  author_type: 'participant', author, content, meta: meta ?? null,
  reply_to: null, refs: [], created_at: new Date(Date.UTC(2026, 9, 9, 8, minute)).toISOString(), task_id: null,
});
// A brand-new project: the person said one thing and 芝士 answered with a
// question and its options, the newest thing in the room.
const history = ${opts.spoken} ? [
  block('hello', 'me', '帮我做一个报名表', 0),
  block('ask', 'cheese', '报名表要收哪些信息？', 1, {
    options: [{ text: '姓名和学号' }, { text: '姓名、学号和手机' }, { text: '我自己写' }],
  }),
] : [];
window.fetch = async (url) => {
  const u = String(url);
  let data;
  if (u.includes('/members')) data = { data: [
    { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
    { id: '2', member_handle: 'cheese', name: '芝士', role: 'member', agent: true },
  ], total: 2 };
  else if (u.includes('/forge')) data = { connected: false };
  else if (u.includes('/progress')) data = { items: [], updated_at: null };
  else if (u.includes('/tasks') || u.includes('/library')) data = { data: [], total: 0 };
  else if (u.includes('/agent-control')) data = { id: null, connected: false };
  else data = { data: history, total: history.length, has_more: false };
  return new Response(JSON.stringify({ code: 200, data }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
};
const topic = {
  id: 'root-1', project_id: 'p1', parent_id: null, title: '报名表', kind: 'root',
  status: 'active', created_by: 'me', created_at: '2026-10-09T08:00:00Z',
};
const members = [
  { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
  { id: '2', member_handle: 'cheese', name: '芝士', role: 'member', agent: true },
];
createApp({ render: () => h(VApp, {}, { default: () => h('main', {
  style: 'width: min(760px, 100vw); height: 100vh; display: flex; flex-direction: column',
}, [h(ChatPanel, { topic, members, hideHeader: true, showComposer: true })]) }) })
  .use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`;

type Rect = { left: number; top: number; right: number; bottom: number };
const overlaps = (a: Rect, b: Rect) =>
  a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

async function open(page: Page, viewport: { width: number; height: number }, spoken: boolean) {
  await page.setViewportSize(viewport);
  await page.route("**/__start-guide-placement__", (route) =>
    route.fulfill({ contentType: "text/html", body: fixture({ spoken }) }),
  );
  await page.goto("/__start-guide-placement__", { waitUntil: "commit" });
}

/** Every control a person can use in the chat column: buttons, links, inputs. */
async function controls(page: Page) {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>(
      'main button, main a[href], main textarea, main input, main [contenteditable="true"], main [role="button"]',
    )]
      .map((el) => ({ el: el.outerHTML.slice(0, 120), ...el.getBoundingClientRect().toJSON() }))
      .filter((r) => r.width > 0 && r.height > 0),
  );
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  for (const scene of [
    { step: "talk", spoken: false, text: "跟芝士说第一句话" },
    { step: "materials", spoken: true, text: "资料库里放也行" },
  ] as const) {
    test(`the ${scene.step} bubble covers no control (${viewport.width}px)`, async ({ page }) => {
      await open(page, viewport, scene.spoken);
      const bubble = page.locator(".sg__bubble");
      await expect(bubble).toContainText(scene.text, { timeout: 30_000 });
      if (scene.spoken) await expect(page.getByRole("button", { name: "姓名和学号" })).toBeVisible();

      const box = (await bubble.evaluate((el) => el.getBoundingClientRect().toJSON())) as Rect;
      const covered = (await controls(page)).filter((c) => overlaps(box, c));
      await test.info().attach(`${scene.step}-${viewport.width}`, {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      expect(covered, "the guide bubble sits on top of these controls").toEqual([]);
      // Inside the viewport, so its own 跳过引导 can be pressed.
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.top).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(viewport.width);
      expect(box.bottom).toBeLessThanOrEqual(viewport.height);
    });
  }
}
