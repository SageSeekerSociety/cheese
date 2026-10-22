// 改一个「别处在用」的模型之前先停一下：编辑、停用、删除、改档位、动它的线路，都先弹一个
// 框说清楚它在哪里起作用，人确认了才往下走。没什么可说的模型直接放行，不弹框。
//
// 说什么由服务端算出来，不靠人手写的标签：平台模型角色（默认模型、语音输入……）指着这个
// 模型时，列出是哪几个（服务端的 `roles`）。
import type { Ref } from 'vue'
import type { ModelsListing } from '@/lib/adminModels'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { roleLabelKey } from '@/lib/platformRoles'

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
    if (row.roles?.length) {
      const roles = row.roles.map((role) => t(roleLabelKey(role))).join(t('models.roles.separator'))
      out.push(t('models.caution.roles', { name, roles }))
    }
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
