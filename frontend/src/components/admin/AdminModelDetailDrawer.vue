<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import { getGatewayModel } from '@/api'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminModelPriceCell from '@/components/admin/AdminModelPriceCell.vue'
import { fmtCost, fmtNum } from '@/lib/usageFormat'

// 一个模型的详情抽屉（契约 §3.2）。**它自己去拉数据**（收一个 `name`），不接一个塞满
// 字段的 props 对象 —— 详情比列表项多出 `series` 和 `platform_usage` 两块，让页面把这些
// 一起查好再传进来，页面就得同时管两份加载态，而这一层本来就需要自己的「正在加载」。
//
// 四态分开画（契约 §4）：loading（骨架）/ error（服务端原话 + 重试）/ empty（这一族没有
// 数据）在这里都有落点；ok 才是内容。日报一条线只画 **token** —— 钱、次数、token 各有
// 三个量级，三条线同轴会有一条贴着底走，而「贴着底的那条是不是 0」正是这张图要回答的
// （看板用量那一块同一个取舍）。

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

const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const { t } = useI18n()

// 抽屉宽度：设计值是 480，但**不能超过视口** —— 390px 的手机上固定 480 会把左边
// 约 90px（标题正好在那儿）切到屏幕外。取两者的较小值，窄屏时它自然占满。
const { width: viewportWidth } = useDisplay()
const drawerWidth = computed(() => Math.min(480, viewportWidth.value))

const detail = ref<DetailPayload | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const copied = ref(false)

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
    void load()
  },
  { immediate: true }
)

const model = computed(() => detail.value?.model ?? null)

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
])

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
          <!-- 标签行：来源 / 上架 / 状态。三样各是一个小标签，不写成一段话。 -->
          <div class="amdd__tags">
            <span class="amdd__tag">{{
              model.origin === 'config' ? t('models.table.origin.config') : t('models.table.origin.runtime')
            }}</span>
            <span class="amdd__tag">{{
              model.offered ? t('models.table.offered.on') : t('models.table.offered.off')
            }}</span>
            <span v-if="model.blocked" class="amdd__tag amdd__tag--muted">{{ t('models.table.blocked') }}</span>
          </div>

          <p v-if="!model.offered && model.blocked_reason" class="amdd__reason t-meta-read">
            {{ model.blocked_reason }}
          </p>

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
  color: var(--faint);
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

.amdd__none {
  margin: 0;
  color: var(--muted);
}
</style>
