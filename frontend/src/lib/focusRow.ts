import { nextTick } from 'vue'

import { scrollBehavior } from '@/utils/motion'

/**
 * 从别处点进来要看的那一条：滚到眼前，亮一下。
 *
 * 房间里的「去确认」把人带到「定时与触发」「工作方法」页上的某一条；页面上可能
 * 已经有好几条，不指出来，人还得自己找一遍。
 */
export async function focusRow(selector: string): Promise<boolean> {
  await nextTick()
  const el = document.querySelector<HTMLElement>(selector)
  if (!el) return false
  el.scrollIntoView?.({ block: 'center', behavior: scrollBehavior() })
  el.classList.add('row--focus')
  window.setTimeout(() => el.classList.remove('row--focus'), 2400)
  return true
}
