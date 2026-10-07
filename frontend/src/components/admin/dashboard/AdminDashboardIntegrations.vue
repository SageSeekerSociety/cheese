<script setup lang="ts">
import type { KpiRow, StatsIntegrations } from '@/lib/adminStats'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminShareBar from '@/components/admin/AdminShareBar.vue'
import { num } from '@/lib/adminStats'
import { relTime } from '@/lib/relTime'

// 集成健康那一屏：静默降级。**没有 days** —— 凭据与投递是存量问题。
//
// 四条「今天算不出来」的事（`unavailable`）带理由画：名字是 snake_case，词条键在这张
// 表里写全（理由同 `TAB_KEY`：拼出来的键在源码里没有一处字面量出现，会被判成死键）。
const props = defineProps<{
  /** `/admin/stats/integrations` 的响应；`null` = 还没到货。 */
  data: StatsIntegrations | null
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const { t } = useI18n()

const integrations = computed(() => props.data)

const integrationKpis = computed<KpiRow[]>(() => [
  {
    key: 'expired',
    label: t('feedback.dashboard.integrations.oauth.expired'),
    value: num(integrations.value?.oauth.expired),
    loading: props.loading,
  },
  {
    key: 'norefresh',
    label: t('feedback.dashboard.integrations.oauth.noRefresh'),
    value: num(integrations.value?.oauth.no_refresh_token),
    loading: props.loading,
  },
  {
    key: 'passkey',
    label: t('feedback.dashboard.integrations.passkey.title'),
    value:
      integrations.value?.passkey.coverage === null || integrations.value?.passkey.coverage === undefined
        ? ''
        : `${Math.round(integrations.value.passkey.coverage * 100)}%`,
    loading: props.loading,
  },
  {
    key: 'dead',
    label: t('feedback.dashboard.integrations.delivery.dead'),
    value: num(integrations.value?.delivery.dead_letters),
    loading: props.loading,
  },
])

const UNAVAILABLE_KEY: Record<string, string> = {
  github_app_permission_gaps: 'feedback.dashboard.integrations.unavailable.githubAppPermissionGaps',
  github_app_mint_failure_rate: 'feedback.dashboard.integrations.unavailable.githubAppMintFailureRate',
  login_lockout_stock_and_rate: 'feedback.dashboard.integrations.unavailable.loginLockoutStockAndRate',
  metering_post_freeze: 'feedback.dashboard.integrations.unavailable.meteringPostFreeze',
}

const integrationsUnavailable = computed(
  () =>
    integrations.value?.unavailable.map((row) => ({
      name: row.name,
      text: t(UNAVAILABLE_KEY[row.name] ?? row.name),
    })) ?? []
)
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
  <div class="ad__kpis">
    <AdminKpiCard
      v-for="kpi in integrationKpis"
      :key="kpi.key"
      :label="kpi.label"
      :value="kpi.value"
      :loading="kpi.loading"
    />
  </div>

  <div class="ad__row ad__row--equal">
    <AdminShareBar
      :title="t('feedback.dashboard.integrations.oauth.title')"
      :segments="
        integrations
          ? [
              {
                label: t('feedback.dashboard.integrations.oauth.expired'),
                value: integrations.oauth.expired,
                shade: 'ink' as const,
              },
              {
                label: t('feedback.dashboard.integrations.oauth.expiring'),
                value: integrations.oauth.expiring_7d,
                shade: 'muted' as const,
              },
              {
                label: t('feedback.dashboard.integrations.oauth.noRefresh'),
                value: integrations.oauth.no_refresh_token,
                shade: 'faint' as const,
              },
            ].filter((s) => s.value > 0)
          : []
      "
      :note="t('feedback.dashboard.integrations.oauth.note')"
      :loading="loading"
    />
    <AdminMeterBar
      :label="t('feedback.dashboard.integrations.passkey.title')"
      :value-text="integrations ? `${integrations.passkey.with_passkey} / ${integrations.passkey.accounts}` : ''"
      :limit="integrations?.passkey.accounts ?? null"
      :ratio="integrations?.passkey.coverage ?? 0"
      :hint="
        integrations?.passkey.coverage === null || integrations?.passkey.coverage === undefined
          ? ''
          : `${Math.round(integrations.passkey.coverage * 100)}%`
      "
      :note="t('feedback.dashboard.integrations.passkey.note')"
      :loading="loading"
    />
  </div>

  <p class="ad__block-note t-meta-read">
    {{ t('feedback.dashboard.integrations.oauth.total') }} {{ integrations?.oauth.total ?? '—' }}
  </p>

  <AdminShareBar
    :title="t('feedback.dashboard.integrations.delivery.title')"
    :segments="
      integrations
        ? [
            {
              label: t('feedback.dashboard.integrations.delivery.unsent'),
              value: integrations.delivery.unsent,
              shade: 'ink' as const,
            },
            {
              label: t('feedback.dashboard.integrations.delivery.dead'),
              value: integrations.delivery.dead_letters,
              shade: 'muted' as const,
            },
          ].filter((s) => s.value > 0)
        : []
    "
    :note="t('feedback.dashboard.integrations.delivery.note')"
    :loading="loading"
  />
  <!-- 最早待补发的那一封是多久以前的（相对时间）。`null` = 没有待补发，不画。 -->
  <p v-if="integrations?.delivery.oldest_unsent_at" class="ad__block-note t-meta-read">
    {{
      t('feedback.dashboard.integrations.delivery.oldest', {
        time: relTime(integrations.delivery.oldest_unsent_at),
      })
    }}
  </p>

  <section class="ad__split">
    <h2 class="ad__block-title">
      {{ t('feedback.dashboard.integrations.unavailable.title')
      }}<AdminNoteTip :text="t('feedback.dashboard.integrations.unavailable.note')" />
    </h2>
    <p v-for="row in integrationsUnavailable" :key="row.name" class="ad__none-desc t-meta-read">
      {{ row.text }}
    </p>
  </section>
</template>

<style scoped>
/* KPI 网格：N 张卡合成**一条整面板**（一个外框 + 内部分隔线，卡片自己的边框
   与写死高度在 `.ad__kpis` 作用域内关掉，见 AdminKpiCard 的对应块）。边框数量
   从 N 个变 1 个，行高对齐是天生的 —— 不再需要 92/108px 那档妥协。
   面板向左、向下各多伸 1px：第一列格子的左边线与末行格子的下边线被推出外边框、
   由 overflow 裁掉，留下的就全是「缝」。
   窄 2 列 → ≥560 交 `auto-fit`，断点是**容器查询**：量的是面板实际拿到多宽（后台页
   的内容列 `.app-page__column--admin`），不是视口 —— 侧栏折叠省出的宽度视口查询
   看不见。 */

.ad__kpis {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: 16px 0 -1px -1px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

/* 列数交给 `auto-fit`，**不写死 4 列**：各类卡数不一样（交付 / 平台 5 张，其余
   4 张），写死 4 列时 5 张卡排成 4 + 1 —— 第二行那一张右边空着三格，面板底色里
   就是一个洞（1440 视口下正好落在这一档）。`auto-fit` 按容器宽度自己定列数，
   4 张卡 4 等分、5 张卡铺满，都不留空轨。 */

@container (min-width: 560px) {
  .ad__kpis {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}

/* **只有两列这一档**（窄屏）才把落单的末位铺满整行：2 列 5 张 = 2 + 2 + 1，最后一行
   半格空白看着像出了错；铺满以后是「2 + 2 + 一整行」，读起来是有意的。
   这里**不能**用「张数是奇数就跨列」这种更宽的判据：它只在列数是偶数时对 —— 5 张卡在
   5 列里本来就铺满一整行（1440 视口下 auto-fit 正好是 5 列），跨列反而把它拆成
   「4 + 1」，那正是这一版要修掉的那个洞。 */

@container (max-width: 559px) {
  .ad__kpis > :deep(*:last-child:nth-child(odd)) {
    grid-column: 1 / -1;
  }
}

/* 格子换成「缝」：gap 0，分隔线用每格自己的上边线 + 左边线，面板负 margin 把
   第一行/列的线推出外边框裁掉（overflow: hidden）。面板内的 hover 底色由卡片
   自己加宽 1px 盖住左侧那条缝（见 AdminKpiCard 的整面板块）。 */

.ad__kpis > :deep(*) {
  border-top: 1px solid var(--line);
  border-left: 1px solid var(--line);
}

.ad__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

/* 两块并排要各到 ~300px 以上，图里的刻度才不互相压 —— 所以双栏从 720 容器宽开始。 */

@container (min-width: 720px) {
  .ad__row {
    grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  }
  .ad__row--equal {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}

.ad__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* 四栏计数。四格并排，和 KPI 行同一套格子，但高度矮一档 —— 它们是同一个总数的四个
   筛选视角，不该和「四个各自独立的数」争同一档视觉重量。 */

.ad__split {
  margin-top: 16px;
}

.ad__split-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

@container (min-width: 560px) {
  .ad__split-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container (min-width: 1320px) {
  .ad__split-grid {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}

.ad__split-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.ad__split-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__split-value {
  color: var(--ink);
  font-size: 20px;
}

/* 块标题：标题 + （可选的）口径 tip 同一行。 */

.ad__block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}

.ad__block-note {
  margin: 12px 0 0;
}
</style>
