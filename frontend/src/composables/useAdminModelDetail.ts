// 模型详情抽屉**取数**的那一半：收一个模型名（和当前窗口），拉回它的上游、价格、趋势、
// 平台用量。
//
// 为什么单分一份：这份数据有两个入口。抽屉自己是一个（`AdminModelDetailDrawer.vue` 收一个
// `name` 就地拉，预览站和它的单测都这么挂），模型管理页是另一个 —— 页面拆成容器 + 视图
// 之后，抽屉那一半在页面的视图里，取数就得跟着页面的窗口走，落在 `useAdminModels` 上。
// 两处各写一遍 watch 和竞态处理迟早就分家，这里只写一遍。
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getGatewayModel } from '@/api'

/** §3.2 的 `series` 一项。 */
export interface ModelSeriesPoint {
  date: string
  spend_usd: number
  requests: number
  tokens: number
}

/** §3.2 的 `platform_usage`。 */
export interface ModelPlatformUsage {
  calls: number
  tokens: number
  cost_usd: number
  unpriced_tokens: number
  note?: string | null
}

/** §3.2 的 `model` 一项（详情这一层用得到的字段）。 */
export interface ModelDetail {
  name: string
  label: string
  origin: string
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reasons?: string[]
  unpriced_reason?: string | null
  upstream: { model: string; host?: string | null; provider?: string }
  prices: Record<string, number | null | undefined>
  capabilities: Record<string, boolean | undefined>
  config_yaml?: string | null
  usage?: { spend_usd: number; requests: number; failed_requests: number; total_tokens: number }
}

export interface ModelDetailPayload {
  model: ModelDetail
  series: ModelSeriesPoint[]
  platform_usage: ModelPlatformUsage
}

/** 什么时候该拉：抽屉开着、看的是哪一个模型、窗口是多少天。三者都由调用方给。 */
export interface ModelDetailSource {
  open: () => boolean
  name: () => string | null
  days: () => number
}

export function useAdminModelDetail(source: ModelDetailSource) {
  const { t } = useI18n()

  const detail = ref<ModelDetailPayload | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    const name = source.name()
    if (!name) return
    loading.value = true
    error.value = null
    try {
      detail.value = (await getGatewayModel(name, source.days())) as unknown as ModelDetailPayload
    } catch (e) {
      // 服务端把原因写在 `message` 里（`ApiError` 带上来的），照它显示，不另造一句。
      error.value = e instanceof Error && e.message ? e.message : t('models.detail.loadFailed')
      detail.value = null
    } finally {
      loading.value = false
    }
  }

  // 打开时拉一次，窗口变了（用户在页面上切了天数）也重拉 —— 抽屉开着时窗口不该是旧的。
  watch(
    () => [source.open(), source.name(), source.days()] as const,
    ([open]) => {
      if (!open) return
      void load()
    },
    { immediate: true }
  )

  return { detail, loading, error, load }
}
