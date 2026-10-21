// 改一个「别处在用」的模型之前先停一下：编辑、停用、删除、改档位、动它的线路，都先弹一个
// 框说清楚它在哪里起作用，人确认了才往下走。没什么可说的模型直接放行，不弹框。
//
// 说什么由模型自己的状态决定，不靠人手写的标签：停用且未上架的模型只剩它的单价，而平台
// 仍按这个名字给订阅用量记账（服务端的 `price_only`）。
import type { Ref } from 'vue'
import type { ModelsListing } from '@/lib/adminModels'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

interface Pending {
  name: string
  run: () => unknown
}

export function useAdminModelsCaution(models: Ref<ModelsListing | null>) {
  const { t } = useI18n()
  const pending = ref<Pending | null>(null)

  /** 这个模型在别处起的作用，一条一句；空表就是没什么可说。 */
  function cautions(name: string): string[] {
    const row = models.value?.models.find((m) => m.name === name)
    if (!row) return []
    const out: string[] = []
    if (row.price_only) out.push(t('models.caution.priceOnly', { name }))
    return out
  }

  /** 包一层：对有话要说的模型先弹框，确认后才跑 `action`；别的模型直接跑。 */
  function heed<A extends unknown[]>(nameOf: (first: A[0]) => string, action: (...args: A) => unknown) {
    return (...args: A) => {
      const name = nameOf(args[0])
      if (!cautions(name).length) return action(...args)
      pending.value = { name, run: () => action(...args) }
    }
  }

  const cautionBody = computed(() => (pending.value ? cautions(pending.value.name).join(' ') : ''))

  function confirmCaution() {
    const target = pending.value
    pending.value = null
    target?.run()
  }

  function closeCaution() {
    pending.value = null
  }

  return { cautionTarget: pending, cautionBody, heed, confirmCaution, closeCaution }
}
