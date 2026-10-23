// 新手引导的后半程：新用户进项目的「综合」之后，一个浮窗一步一步带着把第一件事做完。
//
// 第一步就是人来要做的那件事：把输入框里的第一件事交给芝士。之后每一步要人真的做了
// 才往下走：点开芝士回复的那条支线、打开它建的任务、采纳第一份交付（服务端记下了
// 合并）。界面上的地方不先讲，用到时才讲：采纳以后，最后一步指向总览，采纳的结果
// 在「做出了什么」里成了项目的一个新版本，点「下一步」走完。做没做到看两样：服务端的事实（上手的 `asked` / `accepted`，
// ChatPanel 问来后写进来）和页面上出现了什么（`v-guide-anchor` 登记的控件：支线开着、
// 任务页开着）。
//
// 该指的东西还没出现时（芝士还在回、任务还没建），浮窗指着一个等得到它的地方（对话
// 区、任务页），说它会出现在哪、可以先离开。
//
// 走到哪、看完没有记在这个浏览器里。亲手采纳过交付的人不再自动出现；帮助菜单「界面
// 导览」从头再走一遍（`replayTour`）。
import { computed, ref } from 'vue'

import { storageGet, storageRemove, storageSet } from '@/lib/browserStorage'

export type GuideFacts = { asked: boolean; answered: boolean; accepted: boolean; learned: boolean }

export interface GuideContext {
  facts: GuideFacts
  /** 这个名字的控件此刻登记在页面上没有。 */
  has: (anchor: string) => boolean
}

export interface GuideStep {
  /** 文案 `welcome.guide.<key>`；在等的时候用 `textWaiting`。 */
  key: string
  /** 要圈的控件，取第一个在页面上的。 */
  anchors: string[]
  /** 该圈的还没出现时，浮窗指着这里等（不画圈）。 */
  waiting?: string[]
  /** 圈到的是这一个时用 `textFailed`：该出现的东西出了错（芝士没回成）。 */
  failed?: string
  /** 这几个锚点是整块区域（任务页）：不画圈，只把浮窗放在上面。 */
  areas?: string[]
  /** 有它就是动作步：做到了自己往下走，没有「下一步」。 */
  done?: (c: GuideContext) => boolean
}

export const GUIDE_STEPS: GuideStep[] = [
  { key: 'send', anchors: ['composer-send'], done: (c) => c.facts.asked },
  {
    key: 'reply',
    anchors: ['reply-thread', 'reply-failed'],
    failed: 'reply-failed',
    // 芝士还在回：指着那一行等，不说「回复了」。
    waiting: ['reply-coming', 'timeline'],
    // 没回成就停在这一步：后面几步讲的是计划和任务，都还没有。
    done: (c) => c.has('thread-pane') && !c.has('reply-failed') && !c.has('reply-coming'),
  },
  // 圈支线的输入框本身：要人在那里回一句，浮窗就不能压着它。
  { key: 'confirm', anchors: ['thread-composer'], waiting: ['reply-thread'] },
  {
    key: 'task',
    anchors: ['task-card', 'task-row'],
    waiting: ['timeline', 'thread-pane'],
    // 支线转成的任务在右半边打开，任务页由那里的「整页打开」进。
    done: (c) => c.has('task-page') || c.has('task-pane'),
  },
  // 任务页是一整块区域：正文可能还是空的，只圈标题栏那一条什么也说明不了，浮窗就落在
  // 页面上。还只在支线右半边时圈的是「整页打开」那颗按钮，那是颗要点的控件，照常画圈。
  {
    key: 'taskPage',
    anchors: ['task-page', 'task-open-page'],
    areas: ['task-page'],
    waiting: ['task-card', 'task-row'],
  },
  {
    key: 'accept',
    anchors: ['accept-card'],
    waiting: ['task-page', 'timeline'],
    done: (c) => c.facts.accepted,
  },
  { key: 'overview', anchors: ['page-workspace-overview'] },
]

/** 一步此刻落在哪：指哪个目标、说哪句、画不画圈。 */
export interface GuideStop {
  key: string
  /** 要指的目标；该圈的还没出现时是 `waiting` 里等到的那个。 */
  target: HTMLElement
  /** 文案的后半段（`welcome.guide.<key>.<text>`）：在等、出了错，还是照常讲。 */
  text: 'text' | 'textWaiting' | 'textFailed'
  /** 整块区域（任务页）：不画圈，只放浮窗。 */
  area: boolean
  /** 动作步：做到了自己往下走，没有「下一步」。 */
  action: boolean
}

/**
 * 这一步此刻指哪里：锚点按顺序取第一个在页面上的，都没有就指 `waiting` 里的地方等。
 * 一个能指的都没有时返回 null——人已经离开了这一站讲的地方。
 *
 * 是不是整块区域按圈到的那个锚点算：同一站里可以既有一颗要点的按钮（照常画圈），
 * 又有兜底指向的整块页面（不画圈）。
 */
export function resolveGuideStep(
  step: GuideStep,
  find: (name: string) => HTMLElement | null,
  { replaying = false }: { replaying?: boolean } = {}
): GuideStop | null {
  const hit = step.anchors.find((name) => find(name))
  const target = hit ? find(hit) : null
  const waiting = target ? null : (step.waiting ?? []).map(find).find(Boolean)
  // 重播时每一站都要有落脚点：这一步该指的地方没开着（支线、任务页、手机上没侧栏），
  // 就落到频道的时间线上。不然点到这一站整块浮窗会消失，后面也没处点。
  const replayAt = !target && !waiting && replaying ? find(REPLAY_FALLBACK_ANCHOR) : null
  const at = target ?? waiting ?? replayAt ?? null
  if (!at) return null
  return {
    key: step.key,
    target: at,
    text: !target ? 'textWaiting' : hit === step.failed ? 'textFailed' : 'text',
    area: !hit || !!step.areas?.includes(hit),
    // 重播时**每一站**都有「下一步」：它不从人的旧记录里跳步（`settle` 在重播时不动），
    // 没有这个的话，已经发过消息的人会停在第一站，永远出不去。
    action: !!step.done && !replaying,
  }
}

/** 重播时目标没开着，浮窗落到的地方：频道的时间线，哪一站都在。 */
const REPLAY_FALLBACK_ANCHOR = 'timeline'

/** 介绍步的目标在侧栏上；在频道页上找不到（手机上没有侧栏）就跳过。 */
export const INTRO_PAGE_ANCHOR = 'composer-input'

const KEY_STOP = 'cheese.tour.stop'
const KEY_DONE = 'cheese.tour.done'

function read(key: string): string | null {
  return storageGet(key)
}
function write(key: string, value: string | null) {
  // 存不下：这一次照样走，下次进来从头。
  if (value === null) storageRemove(key)
  else storageSet(key, value)
}

const stop = ref(Number(read(KEY_STOP) ?? 0) || 0)
const done = ref(read(KEY_DONE) === '1')
/** 从帮助菜单重新打开的：不看这个人会不会了，照样走一遍。 */
const replaying = ref(false)
/** 服务端的事实，ChatPanel 问来后写进来；没问到之前是 null，那时谁也不带。 */
const facts = ref<GuideFacts | null>(null)

function setStop(value: number) {
  stop.value = value
  write(KEY_STOP, String(value))
}
function finish() {
  done.value = true
  replaying.value = false
  write(KEY_DONE, '1')
  write(KEY_STOP, null)
}

/** 上手的事实问回来时写进来。 */
export function noteFacts(value: GuideFacts) {
  facts.value = value
}

/** 帮助菜单「界面导览」：从第一步重新走一遍。
 *
 *  重播是「看看界面长什么样」，不是把第一件事再做一遍：所以它**不走 `settle`**
 *  （见那里），一页一页点「下一步」往前，不受这个人以前发过什么、采纳过什么影响。 */
export function replayTour() {
  done.value = false
  replaying.value = true
  write(KEY_DONE, null)
  setStop(0)
}

/** 给测试用：像重新打开页面那样，从这个浏览器存下的状态重新读一遍。 */
export function __resetProjectTourForTests() {
  stop.value = Number(read(KEY_STOP) ?? 0) || 0
  done.value = read(KEY_DONE) === '1'
  replaying.value = false
  facts.value = null
}

export function useProjectTour() {
  /** 引导此刻该不该出现：没走完，而且这个人还不会（或者是他自己要重看）。 */
  const active = computed(() => !done.value && (replaying.value || (facts.value !== null && !facts.value.learned)))
  return {
    stop,
    done,
    replaying,
    facts,
    active,
    total: GUIDE_STEPS.length,
    current: computed(() => GUIDE_STEPS[stop.value] ?? null),
    next() {
      if (stop.value + 1 < GUIDE_STEPS.length) setStop(stop.value + 1)
      else finish()
    },
    back() {
      if (stop.value > 0) setStop(stop.value - 1)
    },
    skip: finish,
    /**
     * 动作步做到了就往下走；一连几步都已经做到（人先走到了前面）就一并跳过。
     * 介绍步在频道页上找不到目标时也跳过。
     *
     * **重播不动它**：重播要的是从第一站看起，而这个人的事实里「发过消息」「采纳过」
     * 早就为真，照这里走会一进来就跳到中间某一步。重播改成每站都有「下一步」，
     * 由人自己一页一页走（见 `AppShell.vue` 的 `guideStep.action`）。
     */
    settle(c: GuideContext) {
      if (replaying.value) return
      for (;;) {
        const step = GUIDE_STEPS[stop.value]
        if (!step) return finish()
        const missingIntro = !step.done && !step.waiting && c.has(INTRO_PAGE_ANCHOR) && !step.anchors.some(c.has)
        if (!(step.done?.(c) || missingIntro)) return
        if (stop.value + 1 >= GUIDE_STEPS.length) return finish()
        setStop(stop.value + 1)
      }
    },
  }
}
