<script setup lang="ts">
import type { ModelRow, ModelsListing } from '@/lib/adminModels'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminModelPriceCell from '@/components/admin/AdminModelPriceCell.vue'
import AdminSparkline from '@/components/admin/AdminSparkline.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import { MODEL_TIERS, TIER_KEY } from '@/lib/adminCredits'
import { blockedReasonText, displayName, failRate, originKey, statusQuiet } from '@/lib/adminModels'
import { fmtCost, fmtNum, fmtSI } from '@/lib/usageFormat'

// 模型那一段：网关上有哪些模型、上没上架、什么价、跑了多少。页面的主语。
//
// 七列里只有名字和操作两列是活的：名字是打开详情的入口（整行只有一个可聚焦的东西，
// 读屏不会在一行里听两遍同一个目的地），config 来源的行只给一个「查看改法」（说明在
// 那一格上，而不是画一个点了报错的按钮）。停用 / 删除都只**问**页面要不要确认 ——
// 确认框和接口都不在这一件里。
const props = defineProps<{
  /** `GET /model/info` 的响应；`null` = 还没到货（或读失败，见 `state`）。 */
  models: ModelsListing | null
  /** 主列表在加载中。骨架只在手上一条都没有时画。 */
  loading: boolean
  /** 表体画真行还是那条读失败的说明。 */
  state: 'rows' | 'error'
  /** 读失败的原话。页面直接显示它，不另写一句「加载失败」。 */
  error: string | null
  /** 网关读不出来时那句解释（不可达 / 没配管理密钥，两种原因两种修法）。 */
  gatewayDetail: string
}>()

const emit = defineEmits<{
  retry: []
  detail: [row: ModelRow]
  edit: [row: ModelRow]
  block: [row: ModelRow]
  delete: [row: ModelRow]
  tier: [row: ModelRow, tier: 'included' | 'premium' | 'frontier']
}>()

const { t } = useI18n()

const tierOptions = computed(() => MODEL_TIERS.map((value) => ({ value, title: t(TIER_KEY[value]) })))

/** 失败率那一格的 title。0 请求时是空串（那一格画的是 `—`，没有比率可解释）——
 *  这一句要 `t`，所以拼不成纯函数，留在画的那一边。 */
function rateTitle(row: ModelRow): string {
  if (!row.usage.requests) return ''
  return t('models.table.failRate', { rate: failRate(row).text })
}
</script>

<template>
  <div class="amd__gridwrap">
    <BaseTable
      :label="t('models.table.label')"
      :cols="[null, '96px', '150px', '150px', '210px', '180px', '110px', '140px']"
      :bone-widths="['64%', '54%', '70%', '60%', '58%', '62%', '50%', '46%']"
      :loading="props.loading && !props.models"
      :skeleton-rows="6"
      :state="props.state"
      cards
      :empty="props.models && !props.models.models.length ? t('models.table.empty') : null"
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('models.table.column.name') }}</th>
          <th scope="col">{{ t('models.table.column.origin') }}</th>
          <th scope="col">{{ t('models.table.column.price') }}</th>
          <th scope="col">{{ t('models.table.column.tier') }}</th>
          <th scope="col">{{ t('models.table.column.usage') }}</th>
          <th scope="col">{{ t('models.table.column.offered') }}</th>
          <th scope="col">{{ t('models.table.column.status') }}</th>
          <th scope="col" class="amd__num">{{ t('models.table.column.actions') }}</th>
        </tr>
      </template>

      <!-- 读不到网关：中性标题说清是哪一段，服务端原话（或网关为什么不可用）落到说明行，
       重试就在旁边。接口失败和「网关没配管理密钥」在这里合成**一处** —— 对读的人是同一个
       结果：这张表读不出来。原话当标题会被长句撑得不像标题。 -->
      <template #error>
        <BaseLoadError
          :title="t('models.table.loadFailed')"
          :error="props.error || props.gatewayDetail || undefined"
          :retry-label="t('models.page.retry')"
          @retry="emit('retry')"
        />
      </template>

      <template #empty>
        <BaseEmptyState size="compact" :title="t('models.table.empty')" />
      </template>

      <tr v-for="row in props.models?.models ?? []" :key="row.name" class="amd__row">
        <td class="amd__cell" data-card="primary">
          <!-- 名字本身是打开详情的入口：整行只有一个可聚焦的东西，读屏不会在一行里
           听两遍同一个目的地。 -->
          <button type="button" class="amd__name" @click="emit('detail', row)">
            <span class="amd__nameMain">{{ displayName(row) }}</span>
            <span v-if="row.label" class="amd__nameSlug t-meta-read">{{ row.name }}</span>
          </button>
        </td>
        <td class="amd__cell" :data-label="t('models.table.column.origin')">
          <span class="amd__tag">{{ t(originKey(row)) }}</span>
        </td>
        <td class="amd__cell" :data-label="t('models.table.column.price')">
          <AdminModelPriceCell :priced="row.priced" :prices="row.prices" :reason="row.unpriced_reason" />
        </td>
        <td class="amd__cell" :data-label="t('models.table.column.tier')">
          <!-- 配置文件里来的模型改不了档位（网关不支持改它），只显示。 -->
          <v-select
            autocomplete="off"
            :model-value="row.tier ?? 'included'"
            :items="tierOptions"
            item-title="title"
            item-value="value"
            variant="outlined"
            density="compact"
            hide-details
            :disabled="row.origin === 'config'"
            :aria-label="t('models.table.column.tier')"
            @update:model-value="emit('tier', row, $event)"
          />
        </td>
        <td class="amd__cell" :data-label="t('models.table.column.usage')">
          <span class="amd__usage">
            <span class="amd__usageRow">
              <span class="t-num amd__usageMain">{{ fmtCost(row.usage.spend_usd) }}</span>
              <span class="t-meta-read amd__dim">{{ fmtNum(row.usage.requests) }} {{ t('models.table.calls') }}</span>
            </span>
            <span class="amd__usageRow">
              <!-- 缩写是给人一眼看的，精确值挂在 title 上 —— 这是 fmtSI 那一条约定。 -->
              <span class="t-meta-read amd__dim" :title="fmtNum(row.usage.total_tokens)"
                >{{ fmtSI(row.usage.total_tokens) }} {{ t('models.usage.tokens') }}</span
              >
              <!-- 行内 sparkline：只承担「趋势长什么样」的一眼形状（aria-hidden）。
               逐日精确值的可访问形式是详情抽屉那张数据表（§2.7），不是给 17
               行各塞一个 <details> —— 同一列里就有精确总数的 title。 -->
              <span class="amd__spark" :title="t('models.table.sparklineHint')">
                <AdminSparkline :values="row.series ?? []" :height="20" />
              </span>
            </span>
          </span>
        </td>
        <td class="amd__cell" :data-label="t('models.table.column.offered')">
          <span class="amd__usage">
            <span v-if="row.blocked" class="amd__tag amd__tag--off">{{ t('models.table.blocked') }}</span>
            <span v-else-if="row.offered" class="amd__tag amd__tag--on">{{ t('models.table.offered.on') }}</span>
            <span v-else class="amd__tag">{{ t('models.table.offered.off') }}</span>
            <span
              v-if="!row.offered && blockedReasonText(row)"
              class="t-meta-read amd__dim amd__reason"
              :title="blockedReasonText(row)"
              >{{ blockedReasonText(row) }}</span
            >
          </span>
        </td>
        <td
          class="amd__cell"
          :data-card="statusQuiet(row) ? 'hide' : undefined"
          :data-label="t('models.table.column.status')"
        >
          <span class="amd__usage">
            <span class="t-num" :class="failRate(row).tone" :title="rateTitle(row)">{{ failRate(row).text }}</span>
          </span>
        </td>
        <td class="amd__cell amd__cell--actions" :data-label="t('models.table.column.actions')">
          <!-- config 模型只读：不给按钮，给一个**能点开改法**的入口（抽屉里有 config
           复制卡）。一句死「只读」是信息的终点，「查看改法」是起点。 -->
          <button v-if="row.origin === 'config'" type="button" class="amd__textbtn" @click="emit('detail', row)">
            {{ t('models.table.howToEdit') }}
          </button>
          <template v-else>
            <BaseButton
              icon="mdi-pencil-outline"
              size="sm"
              :aria-label="t('models.table.action.edit')"
              @click="emit('edit', row)"
            />
            <BaseButton
              :icon="row.blocked ? 'mdi-play-circle-outline' : 'mdi-cancel'"
              size="sm"
              :aria-label="row.blocked ? t('models.table.action.unblock') : t('models.table.action.block')"
              @click="emit('block', row)"
            />
            <BaseButton
              icon="mdi-trash-can-outline"
              size="sm"
              :aria-label="t('models.table.action.delete')"
              @click="emit('delete', row)"
            />
          </template>
        </td>
      </tr>
    </BaseTable>
  </div>
</template>

<style scoped>
/* 表格给自己一个高度上限，让表头**在里面 sticky**：不给上限的话整张表跟着页面长，
   表头会随页面滚走，表壳那套「一屏十七行还记得第 7 列是什么」的设计就落空了。
   （额度那一段是同一条规则加一档矮的，见 `AdminModelsBudgets`。） */
.amd__gridwrap {
  display: flex;
  max-height: 56vh;
  min-height: 220px;
}

.amd__row {
  height: 44px;
}

.amd__cell {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__num {
  text-align: right;
}

.amd__cell--actions {
  text-align: right;
  white-space: nowrap;
}

/* 这一格里的三颗图标按钮（编辑 / 封禁 / 删除）28px 一颗。触屏上每颗把能点的范围
   撑到 44×44（BaseButton 的 ::before），挨着排的话靠右那两颗的撑开部分会盖住中间
   那颗的右半边 —— 相邻中心要隔开 42px 才互不打架，28 + 16 = 44，所以留 16px（和
   房间输入框那一行同一个数）。 */
.amd__cell--actions :deep(.base-btn + .base-btn) {
  margin-left: 16px;
}

/* 名字是按钮：清掉按钮外观，让它读起来像一行标题而不是一个控件 —— 但它在 Tab 顺序里，
   键盘用户到得了（`.amd__name` 的 hover 只变色，不移位）。 */
.amd__name {
  display: flex;
  flex-direction: column;
  max-width: 100%;
  padding: 0;
  background: transparent;
  border: 0;
  text-align: left;
  cursor: pointer;
}

.amd__nameMain {
  overflow: hidden;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (hover: hover) and (pointer: fine) {
  .amd__name:hover .amd__nameMain {
    color: var(--text);
  }
}

.amd__nameSlug {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
}

.amd__dim {
  color: var(--muted);
}

.amd__tag {
  display: inline-block;
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

/* 「已上架 / 已停用」是页面里**唯一**需要一眼分辨的状态，所以只有这两处用状态三件套
   （底色 + 文字色；记号色那一档不写文字）。 */
.amd__tag--on {
  background: var(--ok-wash);
  color: var(--ok-ink);
}

.amd__tag--off {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.amd__usage {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.amd__usageMain {
  color: var(--ink);
}

.amd__usageRow {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 行内 sparkline 的槽位：固定 64×20，多出来的横向空间让给数字。 */
.amd__spark {
  flex: 0 0 auto;
  width: 64px;
}

.amd__reason {
  overflow: hidden;
  max-width: 100%;
  text-overflow: ellipsis;
}

.amd__rate--muted {
  color: var(--muted);
}

.amd__rate--warn {
  color: var(--warn-ink);
}

.amd__rate--danger {
  color: var(--danger-ink);
}

/* 文本按钮（config 行的「查看改法」）：看起来像一格文字，行为是一个按钮 ——
   hover 只变色不移位。 */
.amd__textbtn {
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  cursor: pointer;
}

@media (hover: hover) and (pointer: fine) {
  .amd__textbtn:hover {
    color: var(--accent-press);
  }
}
</style>
