// 输入框下面那一行（MemberActivity）要的数据：谁在忙、叫什么、在哪个阶段、此刻
// 哪一步、多久没出新帧。名字由房间壳给（它手里有名册和队友表）；阶段从队友在动
// 的头像来；当前一步和最后一帧时刻从 useLiveSteps 来。从 useChatPanel 拆出来，
// 是为了让那个房间壳不再长。

import type { ComputedRef } from 'vue'
import type { MemberActivity, MemberActivityLine } from '../../../lib/memberActivity'
import type { SiteStatus } from '../../../lib/siteStatus'
import type { useLiveSteps } from './useLiveSteps'

import { computed } from 'vue'

import { activityLines } from '../../../lib/memberActivity'
import { siteStatusLabel } from '../../../lib/siteStatusLabel'

export function useActivityLines(
  others: ComputedRef<MemberActivity[]>,
  liveSteps: ReturnType<typeof useLiveSteps>,
  nameOf: (handle: string) => string,
  statusOf: (handle: string) => SiteStatus | null | undefined
): ComputedRef<MemberActivityLine[]> {
  return computed(() =>
    activityLines(
      others.value,
      nameOf,
      (handle) => {
        const status = statusOf(handle)
        return status ? siteStatusLabel(status) : null
      },
      (handle) => liveSteps.states.value[handle]?.step ?? null,
      (handle) => liveSteps.states.value[handle]?.at ?? null
    )
  )
}
