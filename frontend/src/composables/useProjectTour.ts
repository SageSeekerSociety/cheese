// 新手引导的后半程：新用户进项目的「综合」之后，一个浮窗一步一步带着把第一件事做完。
//
// 前几步是介绍（频道、输入框、总览、资料库），点「下一步」往前。后面每一步要人真的
// 做了才往下走：发出第一件事（服务端记下了一轮）、点开芝士回复的那条支线、打开它建的
// 任务、采纳第一份交付（服务端记下了合并）。做没做到看两样：服务端的事实（上手的
// `asked` / `accepted`，ChatPanel 问来后写进来）和页面上出现了什么（`v-guide-anchor`
// 登记的控件：支线开着、任务页开着）。
//
// 该指的东西还没出现时（芝士还在回、任务还没建），浮窗指着一个等得到它的地方（对话
// 区、任务页），说它会出现在哪、可以先离开。
//
// 走到哪、看完没有记在这个浏览器里。亲手采纳过交付的人不再自动出现；帮助菜单「界面
// 导览」从头再走一遍（`replayTour`）。
import { computed, ref } from 'vue'

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
  /** 圈到的是这几个时换一句文案（`控件名 → 文案键`）：芝士没回成、任务还没开始。 */
  texts?: Record<string, string>
  /** 这几个目标是一整块区域（任务页）：不画圈，只放浮窗。 */
  areas?: string[]
  /** 有它就是动作步：做到了自己往下走，没有「下一步」。 */
  done?: (c: GuideContext) => boolean
}

export const GUIDE_STEPS: GuideStep[] = [
  { key: 'channels', anchors: ['sidebar-general'] },
  { key: 'composer', anchors: ['composer-input'] },
  { key: 'overview', anchors: ['page-workspace-overview'] },
  { key: 'library', anchors: ['page-project-library'] },
  { key: 'send', anchors: ['composer-send'], done: (c) => c.facts.asked },
  {
    key: 'reply',
    anchors: ['reply-thread', 'reply-failed'],
    texts: { 'reply-failed': 'textFailed' },
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
    done: (c) => c.has('task-page'),
  },
  // 芝士建的任务一般已经在跑，页上没有「开始」：那时不讲「开始」，也不只圈标题栏
  // ——正文可能还是空的，浮窗就落在那里，说进度会出现在这一页。
  {
    key: 'taskPage',
    anchors: ['task-start', 'task-page'],
    texts: { 'task-start': 'textStart' },
    areas: ['task-page'],
    waiting: ['task-card', 'task-row'],
  },
  {
    key: 'accept',
    anchors: ['accept-card'],
    waiting: ['task-page', 'timeline'],
    done: (c) => c.facts.accepted,
  },
]

/** 介绍那几步的目标只在项目的频道页上有；在那一页上找不到（手机上没有侧栏）就跳过。 */
export const INTRO_PAGE_ANCHOR = 'composer-input'

const KEY_STOP = 'cheese.tour.stop'
const KEY_DONE = 'cheese.tour.done'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // 存不下：这一次照样走，下次进来从头。
  }
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

/** 帮助菜单「界面导览」：从第一步重新走一遍。 */
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
     */
    settle(c: GuideContext) {
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
