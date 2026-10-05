<script setup lang="ts">
// 「功能数据」目录页**画的那一半**：那行口径、列表三态（骨架 / 读不到 / 目录）。
//
// 取数（`getFeatureCatalogue`）、查注册表把每行拼成 `title`/`summary`/`to` 都在容器
// `AdminFeatureStatsPage.vue` 里。这里只吃 props、只往上发事件，所以能被单独挂起来看。
//
// 每一行用 `NavLink` 而不是全局的 `<router-link>`：它是组件边界那三条纪律的写法（见
// `components/common/NavLink.vue`），装了路由给 `href`、没装就画成不可点的，这一页
// 因此不读路由、能单独挂起来看。
import type { NavTarget } from '@/lib/navTarget'

import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import NavLink from '@/components/common/NavLink.vue'

interface FeatureRow {
  id: string
  /** 有页面才给去处；没有页面的那行是静的。 */
  to: NavTarget | null
  title: string
  summary: string
}

defineProps<{
  loading: boolean
  /** 读失败时是服务端原话（取不到就空串）；`null` 表示没失败。判据是 `!== null`。 */
  loadError: string | null
  rows: FeatureRow[]
}>()

defineEmits<{
  retry: []
}>()

const { t } = useI18n()
</script>

<template>
  <AdminPage :title="t('navigation.admin.featureStats')" :sub="t('featureStats.page.subtitle')">
    <div class="afs__body admin-page__body">
      <p class="afs__floor t-meta-read">{{ t('featureStats.page.noNumbers') }}</p>

      <div v-if="loading" class="afs__list">
        <span v-for="n in 3" :key="n" class="afs__bone" />
      </div>

      <BaseLoadError
        v-else-if="loadError !== null"
        :title="t('featureStats.page.loadFailed')"
        :error="loadError || undefined"
        :retry-label="t('featureStats.page.retry')"
        @retry="$emit('retry')"
      />

      <BaseEmptyState v-else-if="rows.length === 0" :title="t('featureStats.page.empty')" />

      <ul v-else class="afs__list">
        <li v-for="row in rows" :key="row.id" class="afs__item">
          <NavLink v-if="row.to" :to="row.to" class="afs__link">
            <span class="afs__name">{{ row.title }}</span>
            <span class="afs__desc">{{ row.summary }}</span>
            <span class="afs__go" aria-hidden="true">→</span>
          </NavLink>
          <div v-else class="afs__link afs__link--dim">
            <span class="afs__name">{{ row.title }}</span>
            <span class="afs__desc">{{ row.summary }}</span>
            <span class="afs__soon t-meta-read">{{ t('featureStats.page.notBuilt') }}</span>
          </div>
        </li>
      </ul>
    </div>
  </AdminPage>
</template>

<style scoped>
/* 「这一页只列功能」那句话是**口径**，不是装饰：放在列表上面一行，读完标题就读到它。
   常驻不收起（它防的误读是「这里怎么没数字」，而这个问题每个人第一次都会问）。 */
.afs__floor {
  margin: 0;
  color: var(--muted);
}

.afs__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}

.afs__item {
  margin: 0;
}

/* 一行：名字（定宽）+ 说明（吃掉剩下的）+ 去向。名字定宽而不是 auto：几个功能的
   说明左缘对齐，扫一眼就能比。 */
.afs__link {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: baseline;
  padding: 20px 24px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  color: inherit;
  text-decoration: none;
}

/* 可点的那一行：指针、hover 底色、Tab 顺序三样都有（`<a>` 自带的 Tab 与指针）。 */
@media (hover: hover) and (pointer: fine) {
  .afs__link:not(.afs__link--dim):hover {
    background: var(--fill);
  }
}

.afs__link:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* 没有页面那一行是静的：cursor 保持默认、不响应 hover、不进 Tab 顺序。 */
.afs__link--dim {
  cursor: default;
}

.afs__name {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.afs__desc {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.afs__go {
  color: var(--muted);
  font-size: 13px;
}

.afs__soon {
  color: var(--faint);
}

.afs__bone {
  height: 62px;
  background: var(--line-2);
  border-radius: var(--radius-lg);
}

@container (max-width: 559px) {
  .afs__link {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
