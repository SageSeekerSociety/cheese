// 界面导览：新用户第一次进项目的「综合」时，一步一个高亮框，带上一步 / 下一步，把
// 频道、输入框、「交给芝士」、总览、资料库挨个指一遍。导览之外还有两条「第一次」才
// 出现的提示：频道下面第一次挂上任务时指那一行，第一次有交付等人审阅时指那张卡。
//
// 看过没有记在这个浏览器里：这是一个人的选择，不是项目的数据。帮助菜单「界面导览」
// 把它重新打开（`replayTour`）。亲手采纳过交付的人不再自动出现（ChatPanel 读
// 上手三步的 `learned`）。
import { computed, ref } from 'vue'

export type TourStop = { anchor: string; key: string }

/** 导览的五站，按这个顺序走。`key` 是文案 `welcome.tour.<key>`。 */
export const TOUR_STOPS: TourStop[] = [
  { anchor: 'sidebar-general', key: 'channels' },
  { anchor: 'composer-input', key: 'composer' },
  { anchor: 'composer-summon', key: 'summon' },
  { anchor: 'page-workspace-overview', key: 'overview' },
  { anchor: 'page-project-library', key: 'library' },
]

/** 第一次才出现的提示：控件出现在页面上、又没看过，就指它一次。 */
export const FIRST_TIME_TIPS: TourStop[] = [
  { anchor: 'accept-card', key: 'accept' },
  { anchor: 'task-row', key: 'task' },
]

const SEEN_PREFIX = 'cheese.tour.seen:'

function readSeen(key: string): boolean {
  try {
    return localStorage.getItem(SEEN_PREFIX + key) === '1'
  } catch {
    return false
  }
}
function writeSeen(key: string, value: boolean) {
  try {
    if (value) localStorage.setItem(SEEN_PREFIX + key, '1')
    else localStorage.removeItem(SEEN_PREFIX + key)
  } catch {
    // 存不下：这一次先收起来，下次进来还会出现。
  }
}

const seen = ref<Record<string, boolean>>({})
const stop = ref(0)
/** 从帮助菜单重新打开的：不看这个人会不会了，照样走一遍。 */
const replaying = ref(false)

function isSeen(key: string): boolean {
  if (!(key in seen.value)) seen.value = { ...seen.value, [key]: readSeen(key) }
  return seen.value[key]
}
function markSeen(key: string, value = true) {
  writeSeen(key, value)
  seen.value = { ...seen.value, [key]: value }
}

/** 帮助菜单「界面导览」：从第一站重新走一遍。 */
export function replayTour() {
  stop.value = 0
  replaying.value = true
  markSeen('tour', false)
}

/** 给测试用。 */
export function __resetProjectTourForTests() {
  seen.value = {}
  stop.value = 0
  replaying.value = false
}

export function useProjectTour() {
  const tourDone = computed(() => isSeen('tour'))
  return {
    stop,
    tourDone,
    replaying,
    /** 下一站；`total` 是这一页上找得到目标的站数，走完就记下看过了。 */
    next(total: number) {
      if (stop.value + 1 < total) stop.value += 1
      else {
        markSeen('tour')
        replaying.value = false
      }
    },
    back() {
      if (stop.value > 0) stop.value -= 1
    },
    skip() {
      markSeen('tour')
      replaying.value = false
    },
    tipSeen: (key: string) => isSeen(`tip.${key}`),
    dismissTip: (key: string) => markSeen(`tip.${key}`),
  }
}
