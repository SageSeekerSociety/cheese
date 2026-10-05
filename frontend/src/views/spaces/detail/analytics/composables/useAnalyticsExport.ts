import type { Ref } from 'vue'
import type { AnalyticsExportSection, SpaceAnalyticsQueryState } from '../utils'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { buildAnalyticsExportUrl } from '../utils'

import accountService from '@/services/account'

/**
 * 「导出这一格」的取数：拼地址、带 token、把回包的 CSV 落成一个下载文件。这段从前
 * 长在 `AnalyticsExportButton` 里，取数把一颗按钮拖成了 C 级；现在按钮只发一件事，
 * 打开下载这段归页面（容器）。
 */
export function useAnalyticsExport(spaceId: Ref<number>, filters: Ref<SpaceAnalyticsQueryState>) {
  const { t } = useI18n()
  const exporting = ref(false)

  const exportCsv = async (section: AnalyticsExportSection) => {
    exporting.value = true
    try {
      const resp = await fetch(
        buildAnalyticsExportUrl(import.meta.env.VITE_NEW_API_BASE_URL, spaceId.value, section, filters.value),
        {
          headers: accountService.accessToken ? { Authorization: `Bearer ${accountService.accessToken}` } : {},
        }
      )

      if (!resp.ok) {
        throw new Error(`Export failed: ${resp.status}`)
      }

      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `space-${spaceId.value}-${section}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error)
      toast.error(t('spaces.analytics.export.failed'))
    } finally {
      exporting.value = false
    }
  }

  return { exporting, exportCsv }
}
