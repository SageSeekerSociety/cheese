<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

// 交付主链的**活**四站：建卡 → 决议 → 合并 → 归档。
//
// **为什么不是五站、中间没有「闸门」**：机器闸门已退役（#296，`review/gate.py` 的
// 「create_card no longer mints a pending_gate」）。把它画成活的一站，等于在页面第一眼
// 的位置放一个永远空的站 —— 装饰，不是阅读。闸门那段时间只能作为历史注释（见
// `pipeline.py` 的模块 docstring 第 1 条）。
//
// **为什么是这四站**：它们各自有**活的**时间戳（`created_at` / `decided_at` /
// `pr_merged_at` / `topics.archived_at`）。`pr_merged_at` 只对骑 PR 的卡有意义 ——
// 无 PR 的卡永远是 NULL，所以「合并」那一站的分母是 `pr_number IS NOT NULL` 的子集，
// 不是全部。这一条写在组件下面的注脚上，不省略。
//
// 形制是**一条导轨 + 四段量带**（transit map 的语法，不是漏斗图）：漏斗要求各级是
// 同一批样本的逐级筛选，而这四站是**不同卡在不同时刻**的通过量 —— 画成漏斗会暗示
// 一个不存在的转化率。
const props = withDefaults(
  defineProps<{
    stages: {
      key: string
      label: string
      /** 这一站的**通过量**（窗口内或存量，由调用方口径注说清楚）。 */
      count: number
      /** 这一站的停留时长（秒），没有读数给 null（画破折号，不画 0）。 */
      dwellSeconds: number | null
      /** 分位数明细（秒）。给了就挂到停留行的 `title` 上 —— 站面只摆 p50，
       *  屏幕不被分位数淹；想知道尾巴有多长的人鼠标停一下就有。 */
      p90Seconds?: number | null
      maxSeconds?: number | null
    }[]
    /** 只在有东西卡住时出现的徽章行。 */
    stuck?: { label: string; count: number }[]
    loading?: boolean
  }>(),
  { loading: false, stuck: () => [] }
)

const { t } = useI18n()

const maxCount = computed(() => Math.max(1, ...props.stages.map((s) => s.count)))

function ribbonWidth(count: number): string {
  return `${Math.max(8, (count / maxCount.value) * 100)}%`
}

function dwellText(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} ${t('feedback.dashboard.dwell.minutes')}`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${t('feedback.dashboard.dwell.hours')}`
  const days = Math.floor(hours / 24)
  return `${days} ${t('feedback.dashboard.dwell.days')}`
}

/** 停留行的 `title` 明细：p90 与最长。两个都给空就返回 undefined（不挂 title）。 */
function dwellDetail(stage: { p90Seconds?: number | null; maxSeconds?: number | null }): string | undefined {
  if (stage.p90Seconds === null || stage.p90Seconds === undefined) return undefined
  if (stage.maxSeconds === null || stage.maxSeconds === undefined) return undefined
  return t('feedback.dashboard.spine.dwellDetail', {
    p90: dwellText(stage.p90Seconds),
    max: dwellText(stage.maxSeconds),
  })
}
</script>

<template>
  <section class="als">
    <h2 class="als__title t-eyebrow-read">{{ t('feedback.dashboard.spine.title') }}</h2>

    <div v-if="loading" class="als__skeleton">
      <v-skeleton-loader type="image" class="als__skel" />
    </div>

    <template v-else>
      <!-- 四站并排。移动端退成两列网格 —— 一条横向导轨在窄屏上会把四站压成四条短线。 -->
      <ol class="als__rail">
        <li v-for="stage in stages" :key="stage.key" class="als__station">
          <span class="als__label t-eyebrow-read">{{ stage.label }}</span>
          <span class="als__track" aria-hidden="true">
            <span class="als__ribbon" :style="{ width: ribbonWidth(stage.count) }" />
          </span>
          <span class="als__count t-console-title t-num">{{ stage.count }}</span>
          <span class="als__dwell t-meta-read t-num" :title="dwellDetail(stage)">{{
            dwellText(stage.dwellSeconds)
          }}</span>
        </li>
      </ol>

      <p v-if="stuck.length" class="als__stuck t-meta">
        <span v-for="s in stuck" :key="s.label" class="als__badge"> {{ s.label }} {{ s.count }} </span>
      </p>

      <!-- 口径注：**不省略**。少了它，四站会被读成一个漏斗的四级转化率。 -->
      <p class="als__note t-meta-read">{{ t('feedback.dashboard.spine.note') }}</p>
    </template>
  </section>
</template>

<style scoped>
.als {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.als__title {
  margin: 0 0 12px;
}

.als__rail {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.als__station {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.als__track {
  display: block;
  height: 10px;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

/* 量带用中性阶的 --muted，不用琥珀 —— 这是读数，不是操作。 */
.als__ribbon {
  display: block;
  height: 100%;
  background: var(--muted);
  border-radius: var(--radius-pill);
}

.als__count {
  line-height: 1.1;
}

.als__dwell {
  color: var(--muted);
}

.als__stuck {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 14px 0 0;
}

/* 徽章是「要人动」的那一类，是项目约定里状态色可以出现的第二处。 */
.als__badge {
  padding: 2px 8px;
  color: var(--warn-ink);
  background: var(--warn-wash);
  border-radius: var(--radius-pill);
}

.als__note {
  margin: 12px 0 0;
}

.als__skeleton {
  height: 120px;
}
.als__skel {
  height: 120px;
}

@media (max-width: 700px) {
  .als__rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
