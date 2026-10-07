// 模型详情抽屉（契约 §3.2）自己那一趟取数：请求和它的加载 / 出错态都落在这里，
// `components/admin/AdminModelDetailDrawer.vue` 只画收到的状态——组件不吃 API 层
// （.claude/rules/architecture.md）。
//
// 抽屉仍然是**自己去拉**的那个东西（收一个 `name` 而不是一个塞满字段的 props）：
// 详情比列表项多出 `series` 和 `platform_usage` 两块，让页面一起查好再传进来，页面
// 就得同时管两份加载态，而这一层本来就需要自己的「正在加载」。这里只是把那趟请求
// 从组件里搬出来，形状没变。什么时候拉（打开时、窗口变了）由调用方决定，所以这里
// 不自己 watch。
import { ref } from 'vue'

import { getGatewayModel } from '@/api'
import { t } from '@/i18n'

/** §3.2 的 `series` 一项。 */
export interface SeriesPoint {
  date: string
  spend_usd: number
  requests: number
  tokens: number
}

/** §3.2 的 `platform_usage`。 */
export interface PlatformUsage {
  calls: number
  tokens: number
  cost_usd: number
  unpriced_tokens: number
  note?: string | null
}

/** §3.2 的 `model` 一项（这一层用得到的字段）。 */
export interface GatewayModelDetail {
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

export interface GatewayModelDetailPayload {
  model: GatewayModelDetail
  series: SeriesPoint[]
  platform_usage: PlatformUsage
}

export function useGatewayModelDetail() {
  const detail = ref<GatewayModelDetailPayload | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  /** 拉一次详情。`name` 为空（抽屉关着的常态）时不发请求。 */
  async function load(name: string | null, days: number): Promise<void> {
    if (!name) return
    loading.value = true
    error.value = null
    try {
      detail.value = (await getGatewayModel(name, days)) as unknown as GatewayModelDetailPayload
    } catch (e) {
      // 服务端把原因写在 `message` 里（`ApiError` 带上来的），照它显示，不另造一句。
      error.value = e instanceof Error && e.message ? e.message : t('models.detail.loadFailed')
      detail.value = null
    } finally {
      loading.value = false
    }
  }

  return { detail, loading, error, load }
}
