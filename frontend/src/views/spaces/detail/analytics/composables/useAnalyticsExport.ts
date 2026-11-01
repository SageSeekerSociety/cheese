import type { MaybeRefOrGetter } from 'vue'
import type { AnalyticsExportSection, SpaceAnalyticsQueryState } from '../utils'

import { ref, toValue } from 'vue'
import { useI18n } from 'vue-i18n'

import { buildAnalyticsExportUrl } from '../utils'

import { toast } from '@/lib/toast'
import accountService from '@/services/account'

/**
 * 导出某一格的分析数据：把这一格当前的筛选原样交给导出接口，存成一个 CSV。
 *
 * 取数留在页面这一层——画面那一半（`<X>View.vue`）只报「点了导出」，因为它要能只给
 * props 就挂起来；带登录凭据去拉文件这件事，只有页面做得了。
 */
export function useAnalyticsExport(
  section: AnalyticsExportSection,
  spaceId: MaybeRefOrGetter<number>,
  filters: MaybeRefOrGetter<SpaceAnalyticsQueryState>
) {
  const { t } = useI18n()
  const exporting = ref(false)

  const run = async () => {
    exporting.value = true
    try {
      const url = buildAnalyticsExportUrl(
        import.meta.env.VITE_NEW_API_BASE_URL,
        toValue(spaceId),
        section,
        toValue(filters)
      )
      const resp = await fetch(url, {
        headers: accountService.accessToken ? { Authorization: `Bearer ${accountService.accessToken}` } : {},
      })

      if (!resp.ok) {
        throw new Error(`Export failed: ${resp.status}`)
      }

      const blob = await resp.blob()
      const objectUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = objectUrl
      a.download = `space-${toValue(spaceId)}-${section}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(objectUrl)
    } catch (error) {
      console.error(error)
      toast.error(t('spaces.analytics.export.failed'))
    } finally {
      exporting.value = false
    }
  }

  return { exporting, run }
}
