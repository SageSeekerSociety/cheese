import { expect, test, type Page } from '@playwright/test'

// 任务对话里两处几何：提问收成的那一行要和消息正文在同一条竖线上，续话的已读圈不许压在字上。
//
// 两条都只有真浏览器量得出来：happy-dom 不排版，元素没有位置，圆和字重不重叠无从判断。
// 这里挂真的 ChatTimeline 和产品样式，行数据由预览站的夹具给（和组件预览站同一份）。

const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture" style="width:100%"></div>
<script type="module">
import { createApp, h } from '/node_modules/.vite/deps/vue.js';
import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';
import pinia from '/src/stores/index.ts';
import vuetify from '/src/plugins/vuetify.ts';
import i18n from '/src/i18n/index.ts';
import ChatTimeline from '/src/components/chat/ChatTimeline.vue';
import { chatTimelineProps, CHAT_TOPIC } from '/src/views/demo/catalogFixtures/chat.ts';
import '/src/styles/common.scss';
import '/src/styles/fonts.css';
import '/src/style.css';

const block = (id, author, content, at, meta) => ({
  id, conversation_id: CHAT_TOPIC.id, kind: 'message', author_type: 'participant', author,
  content, reply_to: null, refs: [], created_at: at, meta: meta ?? {},
});
const mine = 'me';
const agent = 'agent';
// 问到别人的两道题收成一行；它上面、下面各有我的一条消息，最后两条是我的续话。
// 这两道题已经由一位名字不短的人答过，答案本身也长：收起来的那一行要同时装下
// 「谁答的」和「答了什么」，窄屏上这正是最容易被撑出容器的那种形状。
const answerer = 'someone-else';
const answererName = '任缘（中国人民大学）';
const longAnswer =
  '按最近三十个自然日算，不含当天的数据，跨月的报告以截止日为终点往前推三十天';
const b1 = block('r1', mine, '我先把这版方案写出来，你看一下。', '2026-11-18T10:00:00Z');
const q1 = block('q1', agent, 'PDF 里的「最近」按多长时间算？', '2026-11-18T10:02:00Z', {
  notice_id: 'n1', asked: [answerer], options: [{ text: '7 天' }, { text: '30 天' }],
  answer_log: [{ kind: 'option', option: longAnswer, note: null, by: answerer, at: '2026-11-18T10:02:10Z', reply_id: 'x1' }],
});
const q2 = block('q2', agent, '报告里要不要写清数据的截止日？', '2026-11-18T10:02:30Z', {
  notice_id: 'n1', asked: [answerer], options: [{ text: '写' }, { text: '不写' }],
  answer_log: [{ kind: 'option', option: longAnswer, note: null, by: answerer, at: '2026-11-18T10:02:40Z', reply_id: 'x2' }],
});
const b2 = block('r2', mine, '我把这两处一起改掉。', '2026-11-18T10:03:00Z');
const b3 = block('r3', mine,
  '以及刚刚显示了一个问题，提示交给芝士，但最后却定在交给芝士O，你需要修复一下这种bug，这一句要够长，长到第一行排满、一直排到右边那颗圈的下面。',
  '2026-11-18T10:03:30Z');
const rows = [b1, q1, q2, b2, b3].map((b) => ({ block: b, notice: null, run: [b] }));
const receipt = { read: [], unread: [{ handle: agent, name: '芝士Opus', isAgent: true, avatar: null }] };

const names = { me: '蔡松洋', agent: '芝士Opus', [answerer]: answererName };

const props = chatTimelineProps({
  rows,
  askGroups: new Map([['q1', [q1, q2]]]),
  dayLabels: new Map([['r1', '今天']]),
  viewer: mine,
  myName: '蔡松洋',
  displayName: (b) => names[b.author] ?? b.author,
  isAgentBlock: (b) => b.author === agent,
  isMine: (b) => b.author === mine,
  receiptOf: (b) => (b.author === mine ? receipt : null),
  refs: { mentionNames: names, topicTitles: {} },
});

createApp({ render: () => h(VApp, { style: 'height:100%' }, () => h(ChatTimeline, props)) })
  .use(vuetify).use(i18n).use(pinia).mount('#fixture');
</script></body></html>`

type Box = { l: number; t: number; r: number; b: number }

async function openFixture(page: Page, width: number) {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(() => localStorage.clear())
  await page.route('**/__chat_geometry__', (route) => route.fulfill({ contentType: 'text/html', body: fixture }))
  await page.goto('/__chat_geometry__', { waitUntil: 'commit' })
  await page.waitForSelector('.tl-ask-group', { timeout: 60_000 })
  await page.evaluate(() => document.fonts.ready)
  // MarkdownView 首屏不等读法：读法（tiptap 那一套）是异步加载的，到之前正文先按
  // 原文画（`.md-plain-first`），行盒和真渲染出来的不是同一份。不等它，量到的可能是
  // 过渡那一版——冷启动时尤其如此。等这两个标志换成真的读法再量。
  await page.waitForFunction(
    () => document.querySelector('.im-row--cont .im-text .md-plain-first') === null,
    undefined,
    { timeout: 60_000 }
  )
  await page.evaluate(() => document.fonts.ready)
}

/** 正文每一条排下来的文字行（只取行盒，不要那个整块大小的外框）。 */
function textLines(page: Page): Promise<Box[]> {
  return page.evaluate(() => {
    const rows = [...document.querySelectorAll('.im-row--cont')].filter((r) => r.querySelector('.im-receipt--cont'))
    const text = rows[0]?.querySelector('.im-text')
    if (!text) throw new Error('没有找到续话的正文')
    const range = document.createRange()
    range.selectNodeContents(text)
    const all = [...range.getClientRects()].map((r) => ({
      l: r.left,
      t: r.top,
      r: r.right,
      b: r.bottom,
      h: r.height,
    }))
    const lineHeight = all.length ? Math.min(...all.map((x) => x.h)) : 0
    return all.filter((x) => x.h <= lineHeight * 1.5)
  })
}

test('提问收成的那一行和消息正文在同一条竖线上', async ({ page }) => {
  for (const width of [900, 360]) {
    await openFixture(page, width)
    const { group, text } = await page.evaluate(() => {
      const g = document.querySelector('.tl-ask-group')!.getBoundingClientRect()
      const t = document.querySelector('.im-row:not(.im-row--cont) .im-text')!.getBoundingClientRect()
      return {
        group: { l: g.left, r: g.right },
        text: { l: t.left, r: t.right },
      }
    })
    expect(Math.abs(group.l - text.l), `${width}px 下提问行的左缘要贴正文那条竖线`).toBeLessThanOrEqual(0.5)
    expect(Math.abs(group.r - text.r), `${width}px 下提问行的右缘要和正文的右缘对齐`).toBeLessThanOrEqual(0.5)
  }
})

test('提问收成的那一行不会被长名字长答案撑出容器', async ({ page }) => {
  for (const width of [900, 360]) {
    await openFixture(page, width)
    // 收起来的那一行里，谁问谁和「已经怎么样了」以前是 `flex: none` 且没有宽度上限：
    // 答案里出现一个长名字或一句长回答，整行就被撑到容器外面，右边直接看不见。
    const { rowRight, widestRight, scrollWidth, clientWidth } = await page.evaluate(() => {
      const fold = document.querySelector('.ask-fold') as HTMLElement
      const rowRight = fold.getBoundingClientRect().right
      const widestRight = Math.max(...[...fold.querySelectorAll('*')].map((el) => el.getBoundingClientRect().right))
      return {
        rowRight,
        widestRight,
        scrollWidth: fold.scrollWidth,
        clientWidth: fold.clientWidth,
      }
    })
    expect(
      widestRight,
      `${width}px 下提问行里的东西伸到了行外（${Math.round(widestRight)} > ${Math.round(rowRight)}）`
    ).toBeLessThanOrEqual(rowRight + 0.5)
    expect(scrollWidth, `${width}px 下提问行内容比它自己宽，右边被切掉了`).toBeLessThanOrEqual(clientWidth + 1)
  }
})

test('续话的已读圈点得开', async ({ page }) => {
  for (const width of [900, 360]) {
    await openFixture(page, width)
    // 圈是浮在正文里的，正文外面那层 `.fold` 有定位，会盖在浮动上面：视觉上圈在那儿，
    // 点下去命中的却是字，菜单打不开。点圈的正中心，问浏览器落到了谁身上。
    const hit = await page.evaluate(() => {
      const row = [...document.querySelectorAll('.im-row--cont')].find((r) => r.querySelector('.im-receipt--cont'))
      const ring = row!.querySelector('.im-receipt--cont') as HTMLElement
      const button = ring.querySelector('button')!
      const b = button.getBoundingClientRect()
      const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)
      // 圈里画的是一枚 svg 和一个按钮：命中它们中的任何一个都算点到了圈。
      return {
        inRing: !!top && ring.contains(top),
        got: top ? `${top.tagName}.${String(top.className)}` : 'null',
      }
    })
    expect(hit.inRing, `${width}px 下点在已读圈中心，命中的是 ${hit.got}，落在圈外面`).toBe(true)
  }
})

test('续话的已读圈不压在字上', async ({ page }) => {
  for (const width of [900, 360]) {
    await openFixture(page, width)
    const ring = await page.evaluate(() => {
      const row = [...document.querySelectorAll('.im-row--cont')].find((r) => r.querySelector('.im-receipt--cont'))
      const r = row!.querySelector('.im-receipt--cont')!.getBoundingClientRect()
      return { l: r.left, t: r.top, r: r.right, b: r.bottom }
    })
    const lines = await textLines(page)
    expect(lines.length, `${width}px 下续话要排成不止一行`).toBeGreaterThan(1)
    for (const line of lines) {
      const overlapsX = Math.min(ring.r, line.r) - Math.max(ring.l, line.l)
      const overlapsY = Math.min(ring.b, line.b) - Math.max(ring.t, line.t)
      expect(
        overlapsX <= 0.5 || overlapsY <= 0.5,
        `${width}px 下已读圈和文字叠在一起：圈 ${JSON.stringify(ring)}，字行 ${JSON.stringify(line)}`
      ).toBe(true)
    }
  }
})
