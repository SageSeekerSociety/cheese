// 改题页保存之前问一句：这次对阶段的修改会让几个人被判未通过、几个人从未通过回到
// 进行中（后端 `POST /tasks/{id}/stages/effect`，只算不写）。一个都没有就直接存；有就
// 举着 `effect` 等出题人答（`answer`），答「保存」才往下存。
import type { TaskStage } from '@/types'

import { ref } from 'vue'

export interface StageChangeEffect {
  /** 现在不是、存了之后会被判未通过的领取数。 */
  failing: number
  /** 现在是未通过、存了之后不再是的领取数。 */
  reopening: number
  /** 这两种各是谁（个人的昵称、团队的队名）。 */
  failingNames: string[]
  reopeningNames: string[]
  /** 让前一种人判为未通过的那几个阶段的名字。 */
  failingStages: string[]
}

export function useStageChangeCheck(ask: (stages: TaskStage[]) => Promise<StageChangeEffect>) {
  /** 等出题人答的那一次：有人会跨过「未通过」那条线时才有值。 */
  const effect = ref<StageChangeEffect | null>(null)
  let decide: ((go: boolean) => void) | null = null

  /** 这些阶段可不可以存：没人受影响就是可以；有就等出题人答。 */
  async function allows(stages: TaskStage[]): Promise<boolean> {
    const found = await ask(stages)
    if (!found.failing && !found.reopening) return true
    effect.value = found
    return new Promise<boolean>((resolve) => (decide = resolve))
  }

  function answer(go: boolean) {
    effect.value = null
    decide?.(go)
    decide = null
  }

  return { effect, allows, answer }
}
