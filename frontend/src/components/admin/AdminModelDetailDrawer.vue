<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import { getGatewayModel, getSubscriptionQuota, revokeSubscription, type SubscriptionQuotaTier } from '@/api'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminModelPriceCell from '@/components/admin/AdminModelPriceCell.vue'
import AdminSubscriptionImportDialog from '@/components/admin/AdminSubscriptionImportDialog.vue'
import { fmtCost, fmtNum, fmtPercent } from '@/lib/usageFormat'

// 一个模型的详情抽屉（契约 §3.2）。**它自己去拉数据**（收一个 `name`），不接一个塞满
// 字段的 props 对象 —— 详情比列表项多出 `series` 和 `platform_usage` 两块，让页面把这些
// 一起查好再传进来，页面就得同时管两份加载态，而这一层本来就需要自己的「正在加载」。
//
// 四态分开画（契约 §4）：loading（骨架）/ error（服务端原话 + 重试）/ empty（这一族没有
// 数据）在这里都有落点；ok 才是内容。日报主线画 **token**，副线画**花费**（dashed，与主线
// 同色族的虚线空心圆 —— 图表系列不靠颜色区分，靠线型）；可访问形式是图下那张
// <details> 数据表，副系列自动多一列。
//
// **订阅块**：模型由一条导入的订阅喂养时（detail 响应带 `subscription`），它是这个抽屉
// 管这条订阅的地方 —— 状态灯、凭据过期时间、额度条、刷新读数、重新授权、移除订阅，
// 各是一个动作或一句话，不混在模型的字段里。

/** §3.2 的 `series` 一项。 */
interface SeriesPoint {
  date: string
  spend_usd: number
  requests: number
  tokens: number
}

/** §3.2 的 `platform_usage`。 */
interface PlatformUsage {
  calls: number
  tokens: number
  cost_usd: number
  unpriced_tokens: number
  note?: string | null
}

/** 详情里模型项上的订阅 overlay（与列表同一个形状）。 */
interface SubscriptionOverlay {
  id: string
  status: string
  account_email: string | null
  token_expires_at?: string | null
  last_refresh_error?: string | null
  quota: { tiers: SubscriptionQuotaTier[]; fetched_at: string | null } | null
}

/** §3.2 的 `model` 一项（这一层用得到的字段）。 */
interface Detail {
  name: string
  label: string
  origin: string
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reason?: string | null
  unpriced_reason?: string | null
  upstream: { model: string; host?: string | null; provider?: string }
  prices: Record<string, number | null | undefined>
  capabilities: Record<string, boolean | undefined>
  config_yaml?: string | null
  usage?: { spend_usd: number; requests: number; failed_requests: number; total_tokens: number }
  subscription?: SubscriptionOverlay | null
}

interface DetailPayload {
  model: Detail
  series: SeriesPoint[]
  platform_usage: PlatformUsage
}

const props = defineProps<{
  modelValue: boolean
  /** 要看的模型名。`null` 时抽屉不拉数据（关着的常态）。 */
  name: string | null
  /** 与页面统一的统计窗口，天。 */
  days: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  /** 订阅被改动（重新授权完成 / 移除成功）：页面该重拉列表了。 */
  (e: 'changed'): void
}>()

const { t, locale } = useI18n()

// 抽屉宽度：设计值是 480，但**不能超过视口** —— 390px 的手机上固定 480 会把左边
// 约 90px（标题正好在那儿）切到屏幕外。取两者的较小值，窄屏时它自然占满。
const { width: viewportWidth } = useDisplay()
const drawerWidth = computed(() => Math.min(480, viewportWidth.value))

const detail = ref<DetailPayload | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const copied = ref(false)

/* ---- 订阅块的状态（额度读数、移除、重新授权各管各的） ---- */

/** 「刷新读数」拿回的活读数。覆盖在详情自带的快照上：stale 标记只有这条路能给。 */
const quotaLive = ref<{ tiers: SubscriptionQuotaTier[]; fetched_at: string | null; stale: boolean } | null>(null)
const quotaLoading = ref(false)
/** 刷新读数失败的服务端原话（就地显示在订阅块里，旧读数不丢）。 */
const quotaError = ref<string | null>(null)

const revokeOpen = ref(false)
const revoking = ref(false)
const revokeError = ref<string | null>(null)

/** 定向重新授权的导入对话框（订阅块自己的实例，带着这条订阅的 id）。 */
const reauthOpen = ref(false)

async function load() {
  if (!props.name) return
  loading.value = true
  error.value = null
  try {
    detail.value = (await getGatewayModel(props.name, props.days)) as unknown as DetailPayload
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
  () => [props.modelValue, props.name, props.days] as const,
  ([open]) => {
    if (!open) {
      copied.value = false
      return
    }
    quotaLive.value = null
    quotaError.value = null
    revokeOpen.value = false
    revokeError.value = null
    void load()
  },
  { immediate: true }
)

const model = computed(() => detail.value?.model ?? null)

/** 订阅块的数据（没有订阅挂在这条模型上时是 null，整块不画）。 */
const sub = computed(() => model.value?.subscription ?? null)

/** 订阅状态 → 词条与灯色（与列表状态列同一份语义：绿 / 琥珀 / 红）。 */
const SUBSCRIPTION_STATUS_KEY: Record<string, string> = {
  active: 'models.table.subscriptionOk',
  refresh_failed: 'models.table.subscriptionRefreshFailed',
  reauth_required: 'models.table.subscriptionReauth',
}

const subStatusText = computed(() => {
  const status = sub.value?.status ?? ''
  return SUBSCRIPTION_STATUS_KEY[status] ? t(SUBSCRIPTION_STATUS_KEY[status]) : status
})

const subDotClass = computed(() => {
  const status = sub.value?.status
  if (status === 'active') return 'amdd__dot--ok'
  if (status === 'refresh_failed') return 'amdd__dot--warn'
  return 'amdd__dot--danger'
})

/** 额度读数：活读数（刚刷的）优先，否则详情自带的快照；都没有 = 「暂无额度读数」。 */
const quota = computed<{ tiers: SubscriptionQuotaTier[]; fetched_at: string | null; stale: boolean } | null>(() => {
  if (quotaLive.value) return quotaLive.value
  const snap = sub.value?.quota
  return snap ? { tiers: snap.tiers, fetched_at: snap.fetched_at, stale: false } : null
})

/** tier 名 → 词条。**字面量表**（拼接键会被 catalog.spec.ts 判死）。 */
const TIER_KEY: Record<string, string> = {
  five_hour: 'models.detail.subscription.tier.fiveHour',
  seven_day: 'models.detail.subscription.tier.sevenDay',
  thirty_day: 'models.detail.subscription.tier.thirtyDay',
}

function tierName(name: string): string {
  return TIER_KEY[name] ? t(TIER_KEY[name]) : t('models.detail.subscription.tier.other')
}

/** 一个未来时刻的紧凑本地时间（「{time} 重置 / 过期」里的 {time}）。
 *  Intl 按当前语言排日月与时分，不为两种语言各写一份拼串。 */
function ahead(iso: string | null | undefined): string {
  if (!iso) return '—'
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return '—'
  return new Intl.DateTimeFormat(locale.value, {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(time)
}

async function refreshQuota() {
  const current = sub.value
  if (!current || quotaLoading.value) return
  quotaLoading.value = true
  quotaError.value = null
  try {
    const result = await getSubscriptionQuota(current.id)
    // 等待期间用户可能已切到另一个模型 —— 上一条订阅的读数不能挂到新模型名下。
    if (sub.value?.id !== current.id) return
    quotaLive.value = { tiers: result.tiers, fetched_at: result.queried_at, stale: result.stale }
  } catch (e) {
    if (sub.value?.id !== current.id) return
    // 失败照原话就地显示；旧读数留在原处 —— 「没刷成」和「没有读数」是两件事。
    quotaError.value = e instanceof Error && e.message ? e.message : t('models.page.loadFailed')
  } finally {
    quotaLoading.value = false
  }
}

async function confirmRevoke() {
  const current = sub.value
  if (!current || revoking.value) return
  revoking.value = true
  revokeError.value = null
  try {
    await revokeSubscription(current.id)
    revokeOpen.value = false
    emit('changed')
    await load()
  } catch (e) {
    // 危险动作失败：框不关，原因照原话（服务端那句话里带着网关的原话）。
    revokeError.value = e instanceof Error && e.message ? e.message : t('models.page.loadFailed')
  } finally {
    revoking.value = false
  }
}

function onReauthImported() {
  emit('changed')
  void load()
}

const caps = computed(() => {
  const c = model.value?.capabilities ?? {}
  const rows: { key: string; label: string }[] = []
  if (c.reasoning) rows.push({ key: 'reasoning', label: t('models.capability.reasoning') })
  if (c.vision) rows.push({ key: 'vision', label: t('models.capability.vision') })
  if (c.adaptive_thinking) rows.push({ key: 'adaptive', label: t('models.capability.adaptiveThinking') })
  return rows
})

/** 天数轴上的标签写「9/15」：窗口在页头已经说了，轴上再铺全年月日只是噪音。 */
function dayLabel(date: string): string {
  const [, month, day] = date.slice(0, 10).split('-')
  return `${Number(month)}/${Number(day)}`
}

const xLabels = computed(() => (detail.value?.series ?? []).map((p) => dayLabel(p.date)))

const series = computed<ChartSeries[]>(() => [
  { name: t('models.detail.series.tokens'), values: (detail.value?.series ?? []).map((p) => p.tokens), style: 'solid' },
  // 副系列：花费。虚线空心圆与实线区分（图表系列不靠颜色区分），数据孪生表自动多一列。
  {
    name: t('models.detail.series.spend'),
    values: (detail.value?.series ?? []).map((p) => p.spend_usd),
    style: 'dashed',
  },
])

/** 失败率行：窗口的失败调用数与占比（0 请求画 `—`，「没用到」不是「没失败」）。 */
const failRate = computed(() => {
  const usage = model.value?.usage
  if (!usage || !usage.requests) return null
  return fmtPercent(usage.failed_requests / usage.requests)
})

async function copyConfig() {
  const text = model.value?.config_yaml
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
  } catch {
    // 剪贴板不可用（非安全上下文 / 权限被拒）不是错误到要弹窗的程度，静默即可 ——
    // 那段 YAML 本来就在屏幕上选得中。
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <v-navigation-drawer
    :model-value="modelValue"
    location="right"
    temporary
    :width="drawerWidth"
    @update:model-value="!$event && close()"
  >
    <div class="amdd">
      <header class="amdd__head">
        <div class="amdd__id">
          <span class="amdd__name t-title">{{ model?.label || name || t('models.detail.title') }}</span>
          <span v-if="model" class="amdd__slug t-meta-read">{{ model.name }}</span>
        </div>
        <v-btn icon="mdi-close" variant="text" size="small" :aria-label="t('models.detail.close')" @click="close" />
      </header>

      <div class="amdd__body">
        <div v-if="loading" class="amdd__skeleton">
          <v-skeleton-loader type="text" />
          <v-skeleton-loader type="image" />
        </div>

        <v-alert v-else-if="error" type="error" density="compact" variant="tonal" role="alert">
          {{ error }}
          <template #append>
            <v-btn variant="text" size="small" @click="load">{{ t('models.page.retry') }}</v-btn>
          </template>
        </v-alert>

        <template v-else-if="detail && model">
          <!-- 标签行：来源 / 上架 / 状态。三样各是一个小标签，不写成一段话。
               来源与列表同一口径：有订阅 overlay 时它是「订阅」，不是「运行时新增」。 -->
          <div class="amdd__tags">
            <span class="amdd__tag">{{
              sub
                ? t('models.table.origin.subscription')
                : model.origin === 'config'
                  ? t('models.table.origin.config')
                  : t('models.table.origin.runtime')
            }}</span>
            <span class="amdd__tag">{{
              model.offered ? t('models.table.offered.on') : t('models.table.offered.off')
            }}</span>
            <span v-if="model.blocked" class="amdd__tag amdd__tag--muted">{{ t('models.table.blocked') }}</span>
          </div>

          <p v-if="!model.offered && model.blocked_reason" class="amdd__reason t-meta-read">
            {{ model.blocked_reason }}
          </p>

          <!-- 订阅块：这条模型由一条导入的订阅喂养时，这里是管它的地方。 -->
          <section v-if="sub" class="amdd__block amdd__subblock">
            <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.detail.subscription.title') }}</h2>
            <dl class="amdd__kv">
              <dt>{{ t('models.detail.subscription.account') }}</dt>
              <dd>{{ sub.account_email || '—' }}</dd>
              <dt>{{ t('models.detail.subscription.status') }}</dt>
              <dd>
                <span class="amdd__substatus">
                  <span class="amdd__dot" :class="subDotClass" aria-hidden="true" />
                  {{ subStatusText }}
                </span>
              </dd>
            </dl>
            <p v-if="sub.token_expires_at" class="amdd__note t-meta-read t-num">
              {{ t('models.detail.subscription.tokenExpires', { time: ahead(sub.token_expires_at) }) }}
            </p>
            <!-- 上次刷新失败的原话（服务端那句），有才显示。 -->
            <p v-if="sub.last_refresh_error" class="amdd__suberror t-meta-read">{{ sub.last_refresh_error }}</p>
            <p class="amdd__note t-meta-read">{{ t('models.detail.subscription.estimateNote') }}</p>

            <!-- 额度条：每个速率窗口一条。轨道 --fill-2、填充 --text（单色，不引入
                 第二色族 —— 图表系列不靠颜色区分）。右侧 t-num 百分比 + 重置时间。 -->
            <div class="amdd__quota">
              <div class="amdd__quotahead">
                <h3 class="amdd__blocktitle t-eyebrow-read">{{ t('models.detail.subscription.quotaTitle') }}</h3>
                <v-btn variant="text" size="small" :loading="quotaLoading" @click="refreshQuota">
                  {{ t('models.detail.subscription.quotaRefresh') }}
                </v-btn>
              </div>
              <template v-if="quota && quota.tiers.length">
                <div v-for="tier in quota.tiers" :key="tier.name" class="amdd__tier">
                  <span class="amdd__tiername t-meta-read">{{ tierName(tier.name) }}</span>
                  <span class="amdd__tiertrack" role="presentation">
                    <span
                      class="amdd__tierfill"
                      :style="{ width: `${Math.min(100, Math.max(0, tier.utilization))}%` }"
                    />
                  </span>
                  <span class="amdd__tiernum t-num">{{ Math.round(tier.utilization) }}%</span>
                  <span class="amdd__tierreset t-meta-read">{{
                    t('models.detail.subscription.resetsAt', { time: ahead(tier.resets_at) })
                  }}</span>
                </div>
                <p class="amdd__note t-meta-read t-num">
                  {{
                    quota.stale && quota.fetched_at
                      ? t('models.detail.subscription.quotaStale', { time: ahead(quota.fetched_at) })
                      : quota.fetched_at
                        ? t('models.detail.subscription.quotaUpdated', { time: ahead(quota.fetched_at) })
                        : ''
                  }}
                </p>
              </template>
              <p v-else class="amdd__note t-meta-read">{{ t('models.detail.subscription.quotaEmpty') }}</p>
              <p v-if="quotaError" class="amdd__suberror t-meta-read" role="alert">{{ quotaError }}</p>
            </div>

            <div class="amdd__subactions">
              <v-btn v-if="sub.status === 'reauth_required'" variant="outlined" size="small" @click="reauthOpen = true">
                {{ t('models.detail.subscription.reauth') }}
              </v-btn>
              <!-- 移除是危险动作：先确认，确认框说清连带后果（停用网关模型）。 -->
              <v-btn variant="text" size="small" class="amdd__danger" @click="revokeOpen = true">
                {{ t('models.detail.subscription.revoke') }}
              </v-btn>
            </div>
          </section>

          <section class="amdd__block">
            <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.detail.upstream') }}</h2>
            <dl class="amdd__kv">
              <dt>{{ t('models.detail.upstreamModel') }}</dt>
              <dd class="t-num">{{ model.upstream.model }}</dd>
              <dt>{{ t('models.detail.upstreamHost') }}</dt>
              <dd class="t-num">{{ model.upstream.host || '—' }}</dd>
            </dl>
          </section>

          <section class="amdd__block">
            <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.table.column.price') }}</h2>
            <AdminModelPriceCell :priced="model.priced" :prices="model.prices" :reason="model.unpriced_reason" />
          </section>

          <section v-if="caps.length" class="amdd__block">
            <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.table.column.capabilities') }}</h2>
            <div class="amdd__caps">
              <span v-for="c in caps" :key="c.key" class="amdd__tag">{{ c.label }}</span>
            </div>
          </section>

          <section class="amdd__block">
            <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.detail.trend') }}</h2>
            <AdminLineChart :title="t('models.detail.series.tokens')" :x-labels="xLabels" :series="series" />
            <!-- 失败率行：与图同源（同一个窗口的 usage），0 请求时整行不画 ——
                 「没用到」不是「没失败」。 -->
            <dl v-if="model.usage && failRate !== null" class="amdd__kv">
              <dt>{{ t('models.detail.failedRequests') }}</dt>
              <dd class="t-num">{{ fmtNum(model.usage.failed_requests) }} · {{ failRate }}</dd>
            </dl>
          </section>

          <section class="amdd__block">
            <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.detail.platformUsage') }}</h2>
            <dl class="amdd__kv">
              <dt>{{ t('models.detail.calls') }}</dt>
              <dd class="t-num">{{ fmtNum(detail.platform_usage.calls) }}</dd>
              <dt>{{ t('models.detail.tokens') }}</dt>
              <dd class="t-num">{{ fmtNum(detail.platform_usage.tokens) }}</dd>
              <dt>{{ t('models.detail.cost') }}</dt>
              <dd class="t-num">{{ fmtCost(detail.platform_usage.cost_usd) }}</dd>
            </dl>
            <p v-if="detail.platform_usage.note" class="amdd__note t-meta-read">{{ detail.platform_usage.note }}</p>
          </section>

          <!-- 「怎么改」那段：只有 config 来源的模型有。它是**可复制的工具**，不是文档。 -->
          <section v-if="model.config_yaml" class="amdd__block">
            <div class="amdd__blockhead">
              <h2 class="amdd__blocktitle t-eyebrow-read">{{ t('models.detail.configYaml') }}</h2>
              <v-btn variant="text" size="small" @click="copyConfig">
                {{ copied ? t('models.detail.copied') : t('models.detail.copy') }}
              </v-btn>
            </div>
            <pre class="amdd__pre t-num">{{ model.config_yaml }}</pre>
            <p class="amdd__note t-meta-read">{{ t('models.detail.configNote') }}</p>
          </section>
        </template>

        <p v-else class="amdd__none t-meta-read">{{ t('models.detail.empty') }}</p>
      </div>
    </div>

    <!-- 移除订阅的确认。说清连带后果：断开订阅并停用网关模型，项目随即选不到它。
         失败框不关、原因照原话（服务端那句里带着网关的原话）。 -->
    <v-dialog
      :model-value="revokeOpen"
      max-width="440"
      :persistent="revoking"
      @update:model-value="revokeOpen = $event"
    >
      <v-card rounded="lg">
        <v-card-title class="px-4 pt-4 pb-2">{{ t('models.detail.subscription.revokeTitle') }}</v-card-title>
        <v-card-text class="px-4">
          {{ t('models.detail.subscription.revokeBody', { name: model?.name ?? '' }) }}
          <v-alert v-if="revokeError" type="error" density="compact" variant="tonal" class="mt-3" role="alert">
            {{ revokeError }}
          </v-alert>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" :disabled="revoking" @click="revokeOpen = false">{{ t('models.dialog.cancel') }}</v-btn>
          <v-btn color="primary" :loading="revoking" @click="confirmRevoke">
            {{ t('models.detail.subscription.revoke') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 定向重新授权：带着这条订阅的 id 开导入对话框（服务端校验同一身份）。 -->
    <AdminSubscriptionImportDialog
      v-if="sub"
      v-model="reauthOpen"
      :target-subscription-id="sub.id"
      @imported="onReauthImported"
    />
  </v-navigation-drawer>
</template>

<style scoped>
.amdd {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.amdd__head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 56px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--line);
}

.amdd__id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.amdd__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amdd__slug {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amdd__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
  padding: 16px;
  overflow-y: auto;
}

.amdd__skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.amdd__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.amdd__tag {
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.amdd__tag--muted {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.amdd__reason {
  margin: 0;
  color: var(--muted);
}

.amdd__block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.amdd__blockhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.amdd__blocktitle {
  margin: 0;
  color: var(--muted);
}

.amdd__kv {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 16px;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
}

.amdd__kv dt {
  color: var(--muted);
}

.amdd__kv dd {
  margin: 0;
  overflow: hidden;
  color: var(--ink);
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amdd__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.amdd__pre {
  margin: 0;
  padding: 12px;
  overflow-x: auto;
  background: var(--fill);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: pre;
}

.amdd__note {
  margin: 0;
  color: var(--muted);
}

/* 订阅块：与同抽屉其它块同一份排版，只是多一条状态灯与额度条。 */
.amdd__substatus {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.amdd__dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}

.amdd__dot--ok {
  background: var(--ok);
}

.amdd__dot--warn {
  background: var(--warn);
}

.amdd__dot--danger {
  background: var(--danger);
}

.amdd__suberror {
  margin: 0;
  color: var(--danger-ink);
}

.amdd__quota {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.amdd__quotahead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

/* 一条额度：名称 + 轨道 + 百分比 + 重置时间。轨道 --fill-2、填充 --text
   （单色 —— 用量多少不靠颜色说，数字就在右边）。 */
.amdd__tier {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px auto;
  gap: 4px 8px;
  align-items: center;
}

.amdd__tiername {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amdd__tiertrack {
  display: block;
  height: 6px;
  overflow: hidden;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.amdd__tierfill {
  display: block;
  height: 100%;
  background: var(--text);
  border-radius: var(--radius-pill);
}

.amdd__tiernum {
  color: var(--ink);
  text-align: right;
}

.amdd__tierreset {
  grid-column: 1 / -1;
  color: var(--muted);
}

.amdd__subactions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.amdd__danger {
  color: var(--danger-ink);
}

.amdd__none {
  margin: 0;
  color: var(--muted);
}
</style>
