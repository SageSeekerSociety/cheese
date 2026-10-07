<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import { useGatewayModelDetail } from '@/composables/useGatewayModelDetail'

import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminModelPriceCell from '@/components/admin/AdminModelPriceCell.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { blockedReasonText } from '@/lib/adminModels'
import { fmtCost, fmtNum, fmtPercent } from '@/lib/usageFormat'

// 一个模型的详情抽屉（契约 §3.2）。**它自己去拉数据**（收一个 `name`），不接一个塞满
// 字段的 props 对象 —— 详情比列表项多出 `series` 和 `platform_usage` 两块，让页面把这些
// 一起查好再传进来，页面就得同时管两份加载态，而这一层本来就需要自己的「正在加载」。
// 那趟请求和它的加载 / 出错态在 `useGatewayModelDetail`，这一层只画。
//
// 四态分开画（契约 §4）：loading（骨架）/ error（服务端原话 + 重试）/ empty（这一族没有
// 数据）在这里都有落点；ok 才是内容。日报主线画 **token**，副线画**花费**（dashed，与主线
// 同色族的虚线空心圆 —— 图表系列不靠颜色区分，靠线型）；可访问形式是图下那张
// <details> 数据表，副系列自动多一列。

const props = defineProps<{
  modelValue: boolean
  /** 要看的模型名。`null` 时抽屉不拉数据（关着的常态）。 */
  name: string | null
  /** 与页面统一的统计窗口，天。 */
  days: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const { t } = useI18n()

// 抽屉宽度：设计值是 480，但**不能超过视口** —— 390px 的手机上固定 480 会把左边
// 约 90px（标题正好在那儿）切到屏幕外。取两者的较小值，窄屏时它自然占满。
const { width: viewportWidth } = useDisplay()
const drawerWidth = computed(() => Math.min(480, viewportWidth.value))

const { detail, loading, error, load } = useGatewayModelDetail()
const copied = ref(false)

// 打开时拉一次，窗口变了（用户在页面上切了天数）也重拉 —— 抽屉开着时窗口不该是旧的。
watch(
  () => [props.modelValue, props.name, props.days] as const,
  ([open]) => {
    if (!open) {
      copied.value = false
      return
    }
    void load(props.name, props.days)
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
  if (c.mid_conversation_system === false)
    rows.push({ key: 'no-mid-system', label: t('models.capability.noMidConversationSystem') })
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
        <BaseButton icon="mdi-close" size="sm" :aria-label="t('models.detail.close')" @click="close" />
      </header>

      <div class="amdd__body">
        <div v-if="loading" class="amdd__skeleton">
          <v-skeleton-loader type="text" />
          <v-skeleton-loader type="image" />
        </div>

        <v-alert v-else-if="error" type="error" density="compact" variant="tonal" role="alert">
          {{ error }}
          <template #append>
            <BaseButton kind="secondary" size="sm" @click="load">{{ t('models.page.retry') }}</BaseButton>
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

          <p v-if="!model.offered && blockedReasonText(model)" class="amdd__reason t-meta-read">
            {{ blockedReasonText(model) }}
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
              <BaseButton kind="ghost" size="sm" @click="copyConfig">
                {{ copied ? t('models.detail.copied') : t('models.detail.copy') }}
              </BaseButton>
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

.amdd__none {
  margin: 0;
  color: var(--muted);
}
</style>
