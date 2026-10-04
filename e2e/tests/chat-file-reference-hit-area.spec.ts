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
  { name: "180px desktop pane dark", width: 1280, column: 180, theme: "dark" },
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
    if ("column" in scene) {
      await page.locator("main").evaluate((el, width) => {
        el.style.width = `${width}px`;
      }, scene.column);
    }
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
    await test.info().attach("adjacent-file-hit-area", {
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

// TopicView permits a 25% desktop split and its columns have min-width: 0.
// A narrow pane is therefore reachable while the viewport is still desktop.
const narrowFixture = fixture
  .replace("author: 'me'", "author: 'external'")
  .replace("'前一条消息。\\n'.repeat(8)", "'前一条消息。'")
  .replace(
    "total: 2, has_more: false",
    "total: history.length, has_more: false",
  )
  .replace(
    "window.fetch = async (url) => {",
    `history.push({ ...message('self', '自己的消息。', '2026-10-02T09:19:00Z'), author: 'me' });
Object.defineProperty(navigator, 'clipboard', { value: {
  writeText: async text => { document.querySelector('#opened').dataset.copied = text; },
} });
window.fetch = async (url, init) => {`,
  )
  .replace(
    "if (u.includes('/members'))",
    `if (u.includes('/reactions')) {
    document.querySelector('#opened').dataset.reaction = u;
    data = { reactions: [] };
  } else if (u.includes('/members'))`,
  )
  .replace(
    "{ id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },",
    `{ id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
    { id: '2', member_handle: 'external', name: '王陈设计验收外部协作成员', role: 'member', agent: false },`,
  )
  .replace(
    "topic, hideHeader: true, showComposer: true,",
    `topic, hideHeader: true, showComposer: true,
  members: [
    { user_handle: 'me', name: '我', source: 'owner' },
    { user_handle: 'external', name: '王陈设计验收外部协作成员', source: 'external' },
  ],
  onMentionClick: handle => { document.querySelector('#opened').dataset.member = handle; },
  onUpgradeMessage: id => { document.querySelector('#opened').dataset.upgraded = id; },`,
  );

test("180px desktop pane preserves author clicks and keeps actions above the message", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: narrowFixture }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  const row = page.locator('[data-mid="before"]');
  await expect(row.locator(".external-tag")).toBeVisible({ timeout: 30_000 });
  await page.locator("main").evaluate((el) => {
    el.style.width = "180px";
  });
  await page.getByTestId("chat-scroll").evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.evaluate(() => document.fonts.ready);
  const before = await row.locator(".im-text").boundingBox();
  await row.locator(".im-text").hover();
  const geometry = await page.evaluate(() => {
    const row = document.querySelector('[data-mid="before"]')!;
    const author = row.querySelector(".im-name")!;
    const header = row.querySelector(".im-meta")!;
    const bar = document.querySelector(".hover-bar--shown")!;
    const scroll = document.querySelector(".messages")!;
    const rect = (el: Element) => el.getBoundingClientRect().toJSON();
    const a = rect(author);
    const point = { x: a.x + 6, y: a.y + 9 };
    return {
      author: a,
      header: rect(header),
      bar: rect(bar),
      body: rect(row.querySelector(".im-text")!),
      point,
      authorOwned:
        document.elementFromPoint(point.x, point.y)?.closest(".im-name") ===
        author,
      clientWidth: scroll.clientWidth,
      scrollWidth: scroll.scrollWidth,
    };
  });
  await test.info().attach("180px-desktop-header", {
    body: JSON.stringify(geometry, null, 2),
    contentType: "application/json",
  });
  expect(geometry.clientWidth).toBe(180);
  expect(geometry.scrollWidth).toBe(geometry.clientWidth);
  expect(geometry.body.width).toBe(110);
  expect(geometry.author.width).toBeGreaterThan(0);
  expect(geometry.authorOwned).toBe(true);
  expect(geometry.bar.right).toBeLessThanOrEqual(geometry.header.right);
  expect(geometry.bar.bottom).toBeLessThanOrEqual(geometry.header.top);
  expect(await row.locator(".im-text").boundingBox()).toEqual(before);
  await page.mouse.click(geometry.point.x, geometry.point.y);
  await expect(page.locator("#opened")).toHaveAttribute(
    "data-member",
    "external",
  );
  await expect(page.locator(".rx-picker")).toHaveCount(0);
  await expect(page.locator(".hover-bar__more")).toBeVisible();
  await test.info().attach("180px-desktop-header", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  await page.locator("main").evaluate((el) => {
    el.style.width = "460px";
  });
  await expect(page.locator(".hover-bar__more")).toBeHidden();
  await expect(
    page
      .locator(".hover-bar")
      .getByRole("button", { name: "回复", exact: true }),
  ).toBeVisible();
});

test("compact desktop menu keeps all actions on its captured message after hovering another row", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: narrowFixture }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  await expect(page.locator('[data-mid="self"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.locator("main").evaluate((el) => {
    el.style.width = "180px";
  });
  const row = page.locator('[data-mid="before"]');
  const menu = page.locator(".v-overlay--active .v-list");
  const open = async (id: string) => {
    // Let ResizeObserver finish the layout turn after the reply-row transition
    // boundary below, before choosing a new physical pointer target.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    // The bar floats above its row; against the top of the scroll area it is
    // cut off, as in Discord. Give the row room above it first.
    await page.locator(`[data-mid="${id}"]`).evaluate((el) => {
      const scroll = el.closest<HTMLElement>(".messages")!;
      const room =
        el.getBoundingClientRect().top - scroll.getBoundingClientRect().top;
      scroll.scrollTop -= 40 - room;
    });
    await page.locator(`[data-mid="${id}"] .im-text`).hover();
    const more = page.locator(".hover-bar__more");
    await expect(more).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate((targetId) => {
          const bar = document
            .querySelector(".hover-bar")!
            .getBoundingClientRect();
          const message = document
            .querySelector(`[data-mid="${targetId}"] .im-main`)!
            .getBoundingClientRect();
          return bar.bottom - message.top;
        }, id),
      )
      .toBe(0);
    const box = await more.boundingBox();
    if (!box) throw new Error("compact action has no visible box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, {
      steps: 10,
    });
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(menu).toBeVisible();
  };
  await open("before");
  await expect(menu.locator(".hover-menu__emojis button")).toHaveCount(8);
  for (const label of ["回复", "复制", "转为话题"]) {
    await expect(menu.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(menu.getByText("编辑", { exact: true })).toHaveCount(0);
  await test.info().attach("180px-desktop-actions", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  // Move the physical pointer to another row while the menu is open. The menu
  // must keep its original target even though the single timeline bar moves.
  const next = await page.locator('[data-mid="file"] .im-text').boundingBox();
  if (!next) throw new Error("next row has no visible box");
  await page.mouse.move(next.x + 4, next.y + next.height / 2);
  const hoverTarget = await page.evaluate(
    ({ x, y }) => ({
      row: document.elementFromPoint(x, y)?.closest<HTMLElement>("[data-mid]")
        ?.dataset.mid,
      barTop: document
        .querySelector(".hover-bar--shown")!
        .getBoundingClientRect().top,
      rowTop: document
        .querySelector('[data-mid="file"]')!
        .getBoundingClientRect().top,
    }),
    { x: next.x + 4, y: next.y + next.height / 2 },
  );
  expect(hoverTarget.row).toBe("file");
  expect(hoverTarget.barTop).toBe(hoverTarget.rowTop - 24);
  await test.info().attach("compact-menu-hover-retarget", {
    body: JSON.stringify(hoverTarget, null, 2),
    contentType: "application/json",
  });
  await menu.getByText("回复", { exact: true }).click();
  await expect(page.locator(".reply-chip")).toContainText("前一条消息。");
  await expect(page.locator(".reply-chip")).not.toContainText("自己的消息。");
  // The label appears before its row finishes growing. Finish that real layout
  // transition before hovering again: bottom-follow otherwise moves the next
  // message under the physical pointer after Playwright chooses its point.
  await expect(page.locator(".chip-row-enter-active")).toHaveCount(0);

  await open("before");
  await menu.getByText("复制", { exact: true }).click();
  await expect(page.locator("#opened")).toHaveAttribute(
    "data-copied",
    "前一条消息。",
  );
  await open("before");
  await menu.getByText("转为话题", { exact: true }).click();
  await expect(page.locator("#opened")).toHaveAttribute(
    "data-upgraded",
    "before",
  );
  await open("before");
  await menu.getByRole("button", { name: "👍", exact: true }).click();
  await expect(page.locator("#opened")).toHaveAttribute(
    "data-reaction",
    /before/,
  );
  await expect(page.locator(".rx-picker")).toHaveCount(0);
  await open("self");
  await menu.getByText("编辑", { exact: true }).click();
  await expect(page.locator('[data-mid="self"] .im-text')).toHaveCount(0);
  await expect(page.locator('[data-mid="self"] textarea')).toHaveValue(
    "自己的消息。",
  );
  await expect(row.locator(".im-text")).toContainText("前一条消息。");
});

test("compact menu returns visible focus and preserves it when the pane grows", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: narrowFixture }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  await expect(page.locator('[data-mid="before"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.locator("main").evaluate((el) => {
    el.style.width = "180px";
  });
  await page.locator('[data-mid="before"] .im-text').hover();
  const more = page.locator(".hover-bar__more");
  await more.click();
  const menu = page.locator(".v-overlay--active .v-list");
  await expect(menu).toBeVisible();
  await page.mouse.move(800, 600);
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(more).toBeFocused();
  await expect
    .poll(() =>
      page.locator(".hover-bar").evaluate((el) => getComputedStyle(el).opacity),
    )
    .toBe("1");
  await expect(page.locator(".hover-bar")).toHaveAttribute(
    "aria-hidden",
    "false",
  );
  await page.locator("main").evaluate((el) => {
    el.style.width = "460px";
  });
  await expect(more).toBeVisible();
  await expect(more).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(menu.getByText("回复", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(more).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(more).not.toBeFocused();
  await expect(more).toBeHidden();
});

// Removing the CSS resize recovery must fail this owner: a focused wide Reply
// used to disappear into BODY when TopicView's desktop pane became narrow.
test("focused message actions survive both directions of desktop pane resize", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: narrowFixture }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  await expect(page.locator('[data-mid="self"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>("main")!;
    const handle = document.createElement("div");
    handle.id = "pane-drag";
    handle.style.cssText =
      "position:absolute;top:32px;height:100px;width:10px;cursor:col-resize;z-index:10000";
    const position = () => {
      handle.style.left = `${main.getBoundingClientRect().right}px`;
    };
    position();
    document.body.append(handle);
    let dragging = false;
    handle.addEventListener("mousedown", (event) => {
      event.preventDefault();
      dragging = true;
    });
    window.addEventListener("mousemove", (event) => {
      if (!dragging) return;
      main.style.width = `${Math.max(180, Math.min(460, event.clientX - main.getBoundingClientRect().left - 5))}px`;
      position();
    });
    window.addEventListener("mouseup", () => {
      dragging = false;
    });
  });
  const dragPane = async (width: number) => {
    const handle = await page.locator("#pane-drag").boundingBox();
    if (!handle) throw new Error("pane drag handle is missing");
    await page.mouse.move(handle.x + 5, handle.y + 20);
    await page.mouse.down();
    await page.mouse.move(32 + width + 5, handle.y + 20, { steps: 12 });
    await page.mouse.up();
    await expect
      .poll(() =>
        page.locator("main").evaluate((el) => el.getBoundingClientRect().width),
      )
      .toBe(width);
  };
  await page.locator('[data-mid="self"] .im-text').hover();
  await page.locator(".hover-bar__wide .rx-toggle").focus();
  await page.keyboard.press("Tab");
  await expect(
    page.locator('.hover-bar__wide button[title="回复"]'),
  ).toBeFocused();
  await page.mouse.move(800, 700);
  await dragPane(180);
  const more = page.locator(".hover-bar__more");
  const state = () =>
    page.locator(".hover-bar").evaluate((el) => ({
      activeTag: document.activeElement?.tagName,
      activeTitle: document.activeElement?.getAttribute("title"),
      moreFocused:
        el.querySelector(".hover-bar__more") === document.activeElement,
      ariaHidden: el.getAttribute("aria-hidden"),
      opacity: getComputedStyle(el).opacity,
      wideDisplay: getComputedStyle(el.querySelector(".hover-bar__wide")!)
        .display,
    }));
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await test.info().attach("wide-to-narrow-focus", {
    body: JSON.stringify(await state(), null, 2),
    contentType: "application/json",
  });
  const anchor = () =>
    page.evaluate(() => ({
      bar: document
        .querySelector(".hover-bar")!
        .getBoundingClientRect()
        .toJSON(),
      message: document
        .querySelector('[data-mid="self"] .im-main')!
        .getBoundingClientRect()
        .toJSON(),
    }));
  await test.info().attach("resized-target-header", {
    body: JSON.stringify(await anchor(), null, 2),
    contentType: "application/json",
  });
  await expect
    .poll(async () => {
      const geometry = await anchor();
      return geometry.bar.bottom - geometry.message.top;
    })
    .toBe(0);
  await expect(more).toBeFocused();
  await expect(more).toBeVisible();
  await expect(page.locator(".hover-bar")).toHaveAttribute(
    "aria-hidden",
    "false",
  );
  await expect
    .poll(() =>
      page.locator(".hover-bar").evaluate((el) => getComputedStyle(el).opacity),
    )
    .toBe("1");
  await page.keyboard.press("Enter");
  const menu = page.locator(".v-overlay--active .v-list");
  await expect(menu.getByText("编辑", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(more).toBeFocused();
  await dragPane(460);
  await expect
    .poll(async () => {
      const geometry = await anchor();
      return geometry.bar.bottom - geometry.message.top;
    })
    .toBe(0);
  await expect(more).toBeVisible();
  await expect(more).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(menu.getByText("回复", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Tab");
  await expect(more).not.toBeFocused();
  await expect(more).toBeHidden();
});

test("pane resize recovery leaves external focus and unmounted messages alone", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  const unmountable = narrowFixture
    .replace("createApp({ render:", "window.fixtureApp = createApp({ render:")
    .replace(".mount('#fixture');", ";window.fixtureApp.mount('#fixture');");
  await page.route("**/__chat-file-hit-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: unmountable }),
  );
  await page.goto("/__chat-file-hit-fixture__", { waitUntil: "commit" });
  await expect(page.locator('[data-mid="self"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.evaluate(() => {
    const external = document.createElement("button");
    external.id = "external-focus";
    external.textContent = "外部控件";
    document.body.append(external);
  });
  const external = page.locator("#external-focus");
  await page.locator('[data-mid="self"] .im-text').hover();
  await page.locator(".hover-bar__wide .rx-toggle").focus();
  await page.keyboard.press("Tab");
  await page.evaluate(() => {
    const external = document.querySelector<HTMLElement>("#external-focus")!;
    document.querySelector(".hover-bar__wide")!.addEventListener(
      "focusout",
      (event) => {
        const left = event as FocusEvent;
        external.dataset.hiddenBlur = String(
          left.relatedTarget === null &&
            !(left.target as HTMLElement).getClientRects().length,
        );
        // Run after the bar's own bubbling handler has queued recovery, before
        // its animation frame. Removing the active-element guard must steal focus.
        queueMicrotask(() => external.focus());
      },
      { once: true },
    );
    document.querySelector<HTMLElement>("main")!.style.width = "180px";
  });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(external).toHaveAttribute("data-hidden-blur", "true");
  await expect(external).toBeFocused();
  await page.locator("main").evaluate((el) => {
    el.style.width = "460px";
  });
  await page.locator('[data-mid="self"] .im-text').hover();
  await page.locator(".hover-bar__wide .rx-toggle").focus();
  await page.keyboard.press("Tab");
  await page.evaluate(() => {
    const external = document.querySelector<HTMLElement>("#external-focus")!;
    document.querySelector(".hover-bar__wide")!.addEventListener(
      "focusout",
      (event) => {
        const left = event as FocusEvent;
        external.dataset.unmountHiddenBlur = String(
          left.relatedTarget === null &&
            !(left.target as HTMLElement).getClientRects().length,
        );
        queueMicrotask(() => {
          (
            window as typeof window & { fixtureApp: { unmount(): void } }
          ).fixtureApp.unmount();
          external.focus();
        });
      },
      { once: true },
    );
    document.querySelector<HTMLElement>("main")!.style.width = "180px";
  });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(external).toHaveAttribute("data-unmount-hidden-blur", "true");
  await expect(page.locator(".hover-bar")).toHaveCount(0);
  await expect(external).toBeFocused();
});

// Keyboard parity for the very same bar. A message row is a real tab stop, Tab
// reaches it, the bar comes up for it like a hover, Tab steps from the row into
// the bar's buttons, and Escape puts the bar away again without the row losing
// its place.
test("message actions are reachable from the row by keyboard", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.route("**/__chat-file-keyboard-fixture__", (route) =>
    route.fulfill({ contentType: "text/html", body: fixture }),
  );
  await page.goto("/__chat-file-keyboard-fixture__", { waitUntil: "commit" });
  const row = page.locator('[data-mid="file"]');
  await expect(row).toBeVisible({ timeout: 30_000 });
  // The row itself is the tab stop that carries the actions.
  await expect(row).toHaveAttribute("tabindex", "0");
  const before = await row.boundingBox();

  // Plain Tab, from the top of the page, really lands on the row.
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur?.());
  let reached = false;
  for (let press = 0; press < 20 && !reached; press += 1) {
    await page.keyboard.press("Tab");
    reached = await row.evaluate((el) => el === document.activeElement);
  }
  expect(reached).toBe(true);

  const bar = page.locator(".hover-bar");
  await expect
    .poll(() => bar.evaluate((el) => getComputedStyle(el).opacity))
    .toBe("1");
  await expect(bar).toHaveAttribute("aria-hidden", "false");
  // Floating the bar over the row must not push the timeline around.
  expect((await row.boundingBox())?.y).toBe(before?.y);

  // Tab hands focus from the row to its actions; Shift+Tab hands it back.
  const react = page.locator(".hover-bar .rx-toggle");
  await page.keyboard.press("Tab");
  await expect(react).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(row).toBeFocused();

  // Escape hides the bar; the row stays focused and in place.
  await page.keyboard.press("Tab");
  await expect(react).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(bar).toHaveAttribute("aria-hidden", "true");
  await expect(row).toBeFocused();
  expect((await row.boundingBox())?.y).toBe(before?.y);
});
