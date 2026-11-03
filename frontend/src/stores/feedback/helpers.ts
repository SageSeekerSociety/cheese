/**
 * 反馈这一层的**常量与纯函数**：没有 state、没有请求、没有缓存，给什么返回什么。
 *
 * 它们从 `stores/feedback.ts` 里搬出来，是因为那个文件到了 1708 行。这里没有一个
 * 函数读 store 上的东西，所以谁都可以 import，测试也可以单独拿它们试。
 */

import type { StatsKind } from '@/api'
import type { FeedbackCard, FeedbackCounts, FeedbackCreateBody, FeedbackKind } from '@/cx_types'
import type { FeedbackDraft } from './types'

/** 搜索防抖，毫秒。250 是人停手和「它没反应」之间的那条线。 */
export const SEARCH_DEBOUNCE_MS = 250
/** 一页拉多少条。**列表有「加载更多」了，但这一页仍然给 50**：一次拿得宽一点，
 *  多数列表（这个平台上百来条反馈撑死）一页就到底，第一屏不必再等第二次往返；
 *  真长起来的那几栏才走翻页。 */
export const PAGE_SIZE = 50
/** 表单落盘的防抖，毫秒。比搜索那条长一点：写的是本机磁盘，不是网络，而中文输入法
 *  在候选阶段就会发 `update:model-value`，250 的话一次输入要写好几遍。 */
export const DRAFT_SAVE_DEBOUNCE_MS = 400

/** 有窗口语义的那几类（响应里带 `days`、切 7/30/90 要重拉的那几类）。
 *  `performance` 读进程内存（只有此刻）、`integrations` 是存量问题，两类都没有
 *  窗口 —— `setStatsDays` 跳过重拉它们，页头也不给它们画窗口切换器。 */
export const WINDOWED_KINDS: StatsKind[] = ['pipeline', 'product', 'feedback', 'usage', 'platform']

export const EMPTY_COUNTS: FeedbackCounts = { all: 0, hot: 0, resolved: 0, deployed: 0, unread: 0 }

/** 哪几类反馈要问「怎么重现」/「你以为会发生什么」。**按类型分栏的口径写在
 *  这一个地方**：表单问什么、请求体带什么、以及以后按类型筛列表，三处问的都是
 *  同一件「这类反馈该有哪些字段」；各写一遍的话，症状是「建议类提交上去的
 *  `repro` 永远是空的」这种谁也看不出来的偏差。 */
export const REPRO_KINDS: FeedbackKind[] = ['bug']
export const EXPECTATION_KINDS: FeedbackKind[] = ['bug', 'suggestion']

/** 一条反馈能带多少个标签。后端的 `max_length=20` 是同一件事（Pydantic 在 list 上
 *  数的是**条数**），两处写同一个数是刻意的：客户端先截，用户就在输入框边上看到上限，
 *  而不是写完正文才被服务端 422 挡回来。 */
export const MAX_TAGS = 20

/** 标签的规范化：去空白、丢空串、去重、截到 20 条。
 *
 *  后端的 `max_length=20` 只管条数，**不去重也不裁空白** —— 把这三件事放在客户端，
 *  是因为它们在人还在打字时就有答案；全都留给服务端，代价是「标签填了、提交被拒、
 *  正文跟着丢一次」这类最贵的那种失败。 */
export function cleanTags(tags: readonly string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of tags) {
    const tag = raw.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    out.push(tag)
    if (out.length >= MAX_TAGS) break
  }
  return out
}

export function emptyDraft(): FeedbackDraft {
  return {
    kind: 'bug',
    title: '',
    body: '',
    repro: '',
    expectation: '',
    tags: [],
    attachments: [],
    attachContext: false,
    visibility: 'public',
  }
}

/** 把表单折成请求体。**只有这一个地方做这件事**：两条提交路走的是同一个动作，
 *  各折一次的话，「可见范围」这种后来加的字段必然只会加进其中一份。 */
export function toCreateBody(draft: FeedbackDraft): FeedbackCreateBody {
  const body = draft.body.trim()
  const agent = draft.attachContext ? draft.fromAgent : undefined
  // 按类型收进来的那两栏。**没问过的那一类一律不带**：表单上没出现过的字段，值只
  // 可能来自「上一次换了类型之前」（选过 bug、填了重现、又改成建议），把它一起发
  // 上去就是替用户声明了一件他没说过的事。
  const repro = REPRO_KINDS.includes(draft.kind) ? draft.repro.trim() : ''
  const expectation = EXPECTATION_KINDS.includes(draft.kind) ? draft.expectation.trim() : ''
  return {
    kind: draft.kind,
    title: draft.title.trim(),
    // 摘要默认取正文第一行：它在列表里是给人扫的那一句，空着等于列表里有一条空白。
    summary: body.split('\n')[0]?.slice(0, 120) || draft.title.trim(),
    problem: body,
    visibility: draft.visibility,
    tags: cleanTags(draft.tags),
    // 人自己写的那一栏优先，agent 带的现场只在人没写时补上 —— 现场是跟着提案卡
    // 一起过来的，而这一栏人刚才就看着它（bug 那一类表单项里有），他改过就该算他的。
    repro: repro || agent?.repro || null,
    expectation: expectation || null,
    what_happened: agent?.whatHappened ?? null,
    evidence: agent?.evidence ?? null,
    session_id: agent?.sessionId ?? null,
    environment: agent?.environment ?? null,
    // 附件只发 id：字节早在选中那一刻就传上去了。**这份数组里只有传成功的那些**
    // （上传中、上传失败的都在表单自己的那一格，见 `useFeedbackAttachments`），
    // 所以这里不必再筛一遍——没有 id 的占位根本进不了这个数组。
    attachment_ids: draft.attachments.map((file) => file.id),
  }
}

/** 后端把能读的原因写在 `message` 里（`ApiError` 会把它带上），照它显示 ——
 *  「HTTP 412 for /feedback/...」会让人去查一个服务端早就解释过的东西。 */
export function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

/** 把新到的一页接在手上这份清单后面。
 *
 *  **必须按 id 去重**，因为 `page_start` 是**偏移量**、不是游标：两次请求之间有人提了
 *  一条新的，它就插到队首，于是第 2 页从原来的第 50 条**前一条**开始 —— 屏幕上第 50
 *  条出现两遍。这不是排序没写全的锅（服务端三个排序都有 `display_no` 兜底，任何两条
 *  都比得出先后），是偏移量本身的性质：它数的是**位置**，而位置会动。
 *
 *  重复的代价不只是「多一条」：`:key` 撞了之后 Vue 会复用同 key 的节点，于是「多一条」
 *  和「少一条」同时发生 —— 那张卡片上显示的可能根本是另一条反馈的内容。
 *
 *  去重按 id（服务端的 uuid），不按标题：同一个标题下真的可以有几条不同的反馈。
 *  两份列表（公开 / 我的）共用这一个函数，是因为这里写错的表现是「某一页多一条」，
 *  两边各写一遍时这种错只会出现在其中一边。 */
export function appendPage(existing: FeedbackCard[], page: FeedbackCard[]): FeedbackCard[] {
  if (!page.length) return existing
  const seen = new Set(existing.map((card) => card.id))
  const fresh = page.filter((card) => !seen.has(card.id))
  // 一条新的都没有时**原样返回那个数组**：换一个新数组会让整片列表重新渲染一遍，
  // 而这一次翻页什么都没带来。
  return fresh.length ? [...existing, ...fresh] : existing
}
