<script setup lang="ts">
import type { StatsUsage } from '@/lib/adminStats'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseTable from '@/components/base/BaseTable.vue'
import IdentityView from '@/components/common/IdentityView.vue'
import NavLink from '@/components/common/NavLink.vue'
import { usageRouteLabel } from '@/lib/adminStats'
import { fmtCost, fmtNum, fmtPercent } from '@/lib/usageFormat'
import { userRefRoute } from '@/lib/userRef'

// 用量那一屏的下半：谁触发了多少、每个项目用了哪些模型、最近的用量记录。三张表都只吃
// `/admin/stats/usage` 那一份响应；口径（命中率只算记了缓存的行、按触发的人算）在
// 后端 `platform_stats/usage_detail.py` 的模块 docstring，这里只负责把它说出来。
const props = defineProps<{
  data: StatsUsage | null
  loading: boolean
}>()

const { t } = useI18n()

const users = computed(() => props.data?.top_users ?? [])
const projects = computed(() => props.data?.top_projects ?? [])
const recent = computed(() => props.data?.recent ?? [])

/** 「平台自己发起的」那一句：没有触发者的花费单独说，不摊给任何人。 */
const usersNote = computed(() => {
  const rest = props.data?.unattributed
  return t('feedback.dashboard.usage.byUserNote', {
    tokens: fmtNum(rest?.tokens ?? 0),
    cost: fmtCost(rest?.cost_usd ?? 0),
  })
})

/** 一个项目的模型拆分写成一行字：模型名和它占这个项目 token 的份额。 */
function modelSplit(row: StatsUsage['top_projects'][number]): string {
  const total = row.tokens || 1
  return (row.models ?? [])
    .map((m) => `${m.model || t('feedback.dashboard.usage.unknownModel')} ${fmtPercent(m.tokens / total)}`)
    .join(' · ')
}

/** 一行账本的命中率：没记缓存（`null`）画 —，不画 0%。 */
function rowCache(row: NonNullable<StatsUsage['recent']>[number]): string {
  if (row.cache_read_tokens === null || row.input_tokens === 0) return fmtPercent(null)
  return fmtPercent(row.cache_read_tokens / row.input_tokens)
}

/** 后端给的是北京时间带 +08:00 的串，直接取「月-日 时:分」，不经浏览器的时区再换一次。 */
function when(at: string): string {
  return `${at.slice(5, 10)} ${at.slice(11, 16)}`
}
</script>

<template>
  <section class="adw">
    <h2 class="adw__title">{{ t('feedback.dashboard.usage.byUser') }}</h2>
    <BaseTable
      :label="t('feedback.dashboard.usage.byUser')"
      :cols="[null, '120px', '88px', '104px', '88px']"
      :loading="loading && !data"
      :skeleton-rows="3"
      :empty="data && !users.length ? t('feedback.dashboard.empty.title') : null"
      min-width="560px"
      cards
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('feedback.dashboard.usage.col.person') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.tokens') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.calls') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.cost') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.cache') }}</th>
        </tr>
      </template>
      <tr v-for="row in users" :key="row.handle">
        <td data-card="primary">
          <NavLink :to="userRefRoute(row.handle)" class="adw__link">
            <IdentityView
              :handle="row.handle"
              :name="row.name"
              :avatar-id="row.avatar_id ?? null"
              :agent="row.is_agent ?? null"
              :size="18"
              handle-line
            />
          </NavLink>
        </td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.tokens')">{{ fmtNum(row.tokens) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.calls')">{{ fmtNum(row.calls) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.cost')">{{ fmtCost(row.cost_usd) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.cache')">
          {{ fmtPercent(row.cache_hit) }}
        </td>
      </tr>
    </BaseTable>
    <p v-if="data" class="adw__note t-meta-read">{{ usersNote }}</p>
  </section>

  <section class="adw">
    <h2 class="adw__title">{{ t('feedback.dashboard.usage.projectModels') }}</h2>
    <BaseTable
      :label="t('feedback.dashboard.usage.projectModels')"
      :cols="['200px', null, '120px', '104px', '88px']"
      :loading="loading && !data"
      :skeleton-rows="3"
      :empty="data && !projects.length ? t('feedback.dashboard.empty.title') : null"
      min-width="680px"
      cards
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('feedback.dashboard.usage.col.project') }}</th>
          <th scope="col">{{ t('feedback.dashboard.usage.col.models') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.tokens') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.cost') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.cache') }}</th>
        </tr>
      </template>
      <tr v-for="row in projects" :key="row.project_id">
        <td data-card="primary">
          <NavLink :to="{ path: `/projects/${row.project_id}` }" class="adw__link">
            <span class="adw__name">{{ row.name }}</span>
          </NavLink>
        </td>
        <td class="adw__models" :data-label="t('feedback.dashboard.usage.col.models')">{{ modelSplit(row) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.tokens')">{{ fmtNum(row.tokens) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.cost')">{{ fmtCost(row.cost_usd) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.cache')">
          {{ fmtPercent(row.cache_hit) }}
        </td>
      </tr>
    </BaseTable>
  </section>

  <section class="adw">
    <h2 class="adw__title">{{ t('feedback.dashboard.usage.recent') }}</h2>
    <BaseTable
      :label="t('feedback.dashboard.usage.recent')"
      :cols="['120px', null, '120px', '160px', '128px', '96px', '80px', '88px', '88px']"
      :loading="loading && !data"
      :skeleton-rows="5"
      :empty="data && !recent.length ? t('feedback.dashboard.empty.title') : null"
      min-width="1024px"
      cards
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('feedback.dashboard.usage.col.time') }}</th>
          <th scope="col">{{ t('feedback.dashboard.usage.col.project') }}</th>
          <th scope="col">{{ t('feedback.dashboard.usage.col.person') }}</th>
          <th scope="col">{{ t('feedback.dashboard.usage.col.model') }}</th>
          <th scope="col">{{ t('feedback.dashboard.usage.col.route') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.input') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.output') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.cache') }}</th>
          <th scope="col" class="adw__num">{{ t('feedback.dashboard.usage.col.cost') }}</th>
        </tr>
      </template>
      <tr v-for="(row, i) in recent" :key="i">
        <td class="t-num" data-card="primary">{{ when(row.at) }}</td>
        <td :data-label="t('feedback.dashboard.usage.col.project')">{{ row.project || '—' }}</td>
        <td :data-label="t('feedback.dashboard.usage.col.person')">
          <!-- Same identity as the by-user column above: face + display name, falling
               back to the handle. Rows with no triggerer are platform-initiated and
               keep the plain note. -->
          <NavLink v-if="row.handle" :to="userRefRoute(row.handle)" class="adw__link">
            <IdentityView
              :handle="row.handle"
              :name="row.name"
              :avatar-id="row.avatar_id ?? null"
              :agent="row.is_agent ?? null"
              :size="18"
              handle-line
            />
          </NavLink>
          <template v-else>{{ t('feedback.dashboard.usage.byPlatform') }}</template>
        </td>
        <td class="adw__model" :data-label="t('feedback.dashboard.usage.col.model')" :title="row.model">
          {{ row.model || t('feedback.dashboard.usage.unknownModel') }}
        </td>
        <td :data-label="t('feedback.dashboard.usage.col.route')">
          {{ usageRouteLabel(t, row.route) }}
          <!-- 网关的一行不是一次请求：是从这把 key 上次读到这次之间、一个模型的花费合计
               （`gateway_spend.py`），可能是很多次请求。账本不记次数，这里也不写。 -->
          <span v-if="row.route === 'gateway'" class="adw__batch" :title="t('feedback.dashboard.usage.batchTitle')">
            · {{ t('feedback.dashboard.usage.batch') }}
          </span>
        </td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.input')">
          {{ fmtNum(row.input_tokens) }}
        </td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.output')">
          {{ fmtNum(row.output_tokens) }}
        </td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.cache')">{{ rowCache(row) }}</td>
        <td class="adw__num t-num" :data-label="t('feedback.dashboard.usage.col.cost')">{{ fmtCost(row.cost_usd) }}</td>
      </tr>
    </BaseTable>
    <p v-if="data" class="adw__note t-meta-read">
      {{ t('feedback.dashboard.usage.recentNote', { n: recent.length }) }}
    </p>
  </section>
</template>

<style scoped>
.adw {
  margin-top: 24px;
}

.adw__title {
  margin: 0 0 12px;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}

.adw__num {
  text-align: right;
}

.adw__link {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  overflow: hidden;
  color: var(--ink);
  text-decoration: none;
}

/* 名字在共用的 IdentityView 里，悬停划线要穿进子组件那一层。 */
.adw__link:hover :deep(.identity-view__name) {
  text-decoration: underline;
}

.adw__models,
.adw__model {
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.adw__note {
  margin: 8px 0 0;
}

.adw__batch {
  color: var(--muted);
  white-space: nowrap;
}
</style>
