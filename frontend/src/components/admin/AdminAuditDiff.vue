<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

// 审计行的「查看改动」：before/after 两份字段快照的字段级 diff。
//
// 审计区从「谁动了」升级成「动了什么」就靠这一块。三条规定：
//
//   1. **只显示有变化的字段**。两份快照各有十几个键，逐键全列出来，变化反而
//      淹没在没动的字段里。并集键集合里筛掉两边相等的，剩下的一行一条。
//   2. **字段名说人话**。`upstream_model` 这类键名是实现词，界面给「上游模型」。
//      映射是**字面量表**（catalog.spec.ts 靠 grep 认键，拼接键会被判死键）；
//      表里没有的键收进「其它字段」，值照原样给 —— 一个没认出的字段不该整行丢掉。
//   3. **值按类型格式化**：布尔是「是 / 否」，单价按每百万 token 的美元（和
//      `AdminModelPriceCell` 同一口径），美元数走 fmtCost，其余对象 JSON 紧凑串。
//      新增 / 删除的字段缺的那一侧画 `—`（数字契约里「没有」的那个记号，方向
//      仍然读得出来：左边是旧，右边是新）。

const props = defineProps<{
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}>()

const { t } = useI18n()

/** 字段名 → 词条。**字面量表**，见文件头第 2 条。 */
const FIELD_LABEL: Record<string, string> = {
  name: 'models.audit.diff.field.name',
  label: 'models.audit.diff.field.label',
  blocked: 'models.audit.diff.field.blocked',
  selectable: 'models.audit.diff.field.selectable',
  priced: 'models.audit.diff.field.priced',
  upstream_model: 'models.audit.diff.field.upstreamModel',
  api_base: 'models.audit.diff.field.apiBase',
  provider: 'models.audit.diff.field.provider',
  prices: 'models.audit.diff.field.prices',
  capabilities: 'models.audit.diff.field.capabilities',
  max_budget_usd: 'models.audit.diff.field.maxBudgetUsd',
  extra_headers: 'models.audit.diff.field.extraHeaders',
}

/** 一行变化。`has` 标记这一侧存不存在（新增 / 删除的字段只有一侧）。 */
interface DiffRow {
  field: string
  label: string
  oldText: string
  newText: string
  hasOld: boolean
  hasNew: boolean
}

/** 每 token → 每百万 token 的美元（`AdminModelPriceCell` 同一口径）。 */
function perMillion(v: unknown): string {
  return typeof v === 'number' ? `$${(v * 1_000_000).toFixed(2)}` : '—'
}

function formatUsd(v: unknown): string {
  return typeof v === 'number' ? `$${v.toFixed(2)}` : '—'
}

/** 一个字段一侧的值 → 显示文本。`present=false` 时调用方画 `—`，这里不管。 */
function formatValue(field: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') {
    return value ? t('models.audit.diff.yes') : t('models.audit.diff.no')
  }
  if (field === 'prices' && typeof value === 'object') {
    const parts = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => typeof v === 'number')
      .map(([k, v]) => `${k} ${perMillion(v)}`)
    return parts.length ? parts.join(' · ') : '—'
  }
  if (field === 'max_budget_usd') return formatUsd(value)
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value || '—'
  return JSON.stringify(value)
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}

const rows = computed<DiffRow[]>(() => {
  const before = props.before ?? {}
  const after = props.after ?? {}
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
  return keys
    .filter((key) => !same(before[key], after[key]))
    .map((key) => {
      const hasOld = props.before !== null && key in before
      const hasNew = props.after !== null && key in after
      return {
        field: key,
        label: t(FIELD_LABEL[key] ?? 'models.audit.diff.field.other'),
        oldText: hasOld ? formatValue(key, before[key]) : '—',
        newText: hasNew ? formatValue(key, after[key]) : '—',
        hasOld,
        hasNew,
      }
    })
})
</script>

<template>
  <div class="aadiff">
    <p v-if="!rows.length" class="aadiff__empty t-meta-read">{{ t('models.audit.diff.empty') }}</p>
    <ul v-else class="aadiff__rows">
      <li v-for="row in rows" :key="row.field" class="aadiff__row">
        <span class="aadiff__field">{{ row.label }}</span>
        <span class="aadiff__values t-num">
          <span class="aadiff__side" :class="{ 'aadiff__side--gone': !row.hasOld }" :title="row.oldText">{{
            row.oldText
          }}</span>
          <span class="aadiff__arrow" aria-hidden="true">→</span>
          <span
            class="aadiff__side aadiff__side--new"
            :class="{ 'aadiff__side--gone': !row.hasNew }"
            :title="row.newText"
            >{{ row.newText }}</span
          >
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.aadiff {
  padding: 4px 0 2px;
}

.aadiff__empty {
  margin: 0;
  color: var(--muted);
}

.aadiff__rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.aadiff__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  line-height: var(--lh-12);
}

.aadiff__field {
  flex: 0 0 auto;
  color: var(--muted);
}

.aadiff__values {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  color: var(--text);
}

.aadiff__side {
  overflow: hidden;
  max-width: 320px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aadiff__side--new {
  color: var(--ink);
}

.aadiff__side--gone {
  color: var(--muted);
}

.aadiff__arrow {
  color: var(--muted);
}
</style>
