// 第一次碰到某样东西时的那一句说明（验收卡在等你、规则草稿要确认才跑……）。
//
// 「看过了」记在这台浏览器上，不落库、不分项目：它教的是这个人怎么用平台，
// 换一个项目再碰到同一样东西，他已经会了。键名和开始清单的「收起」同一个前缀
// （`cheese.gettingStarted.dismissed.<项目>`）：`cheese.hint.dismissed.<提示名>`。
//
// 同一个说明可能在一页上出现几次（两张草稿规则），所以「看过了」是模块级的一份，
// 点掉一处，几处一起收。
import { computed, ref } from 'vue'

export type FirstTimeHintId = 'accept-card' | 'routine-draft' | 'skill-proposal' | 'own-device'

const PREFIX = 'cheese.hint.dismissed.'

function read(id: FirstTimeHintId): boolean {
  try {
    return localStorage.getItem(PREFIX + id) === '1'
  } catch {
    // 隐私模式下读不了：当作没看过，最坏是多看见一次。
    return false
  }
}

const seen = ref<Partial<Record<FirstTimeHintId, boolean>>>({})

export function useFirstTimeHint(id: FirstTimeHintId) {
  if (seen.value[id] === undefined) seen.value = { ...seen.value, [id]: read(id) }
  const visible = computed(() => !seen.value[id])
  function dismiss() {
    seen.value = { ...seen.value, [id]: true }
    try {
      localStorage.setItem(PREFIX + id, '1')
    } catch {
      /* 写不进去也只在这一页收起。 */
    }
  }
  return { visible, dismiss }
}

/** 测试用：清掉模块里记着的那份，让下一次 `useFirstTimeHint` 重新读 localStorage。 */
export function resetFirstTimeHints(): void {
  seen.value = {}
}
