<script setup lang="ts">
import type { FeatureCatalogueEntry } from '@/views/admin/features/featureApi'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import { getFeatureCatalogue } from '@/views/admin/features/featureApi'
import { findFeatureView } from '@/views/admin/features/registry'

// 后台的「功能数据」（`/admin/feature-stats`）：**一扇门，不是一块看板**。
//
// 这一页只列「有哪些功能的数据页」，每个一行名字加一句说明。**它上面一个数字都没有**，
// 这是它的定义而不是还没做完：数字属于各自的功能页，在门口再放一遍就是第二份要跟着改
// 的东西，而且第二份总会晚一步 —— 到那时读的人相信的是他先看到的那一份。
//
// 每一行的文案优先用**前端注册表里的 i18n 键**（这样英文界面是英文），服务端给的那句
// 中文是兜底：一个只在服务端上架、前端还没有页面的功能也必须能被列出来，点进去画
// 「这一页还没做」，而不是从目录里凭空消失。
//
// 有页面才给链接（整行一个 `<RouterLink>`）。没有页面的那行是**静的**：指针、hover、
// Tab 三样一样都不给 —— 半个链接比没有链接更糟（见 `AdminKpiCard` 文件头那条纪律）。
defineOptions({ name: 'AdminFeatureStatsPage' })

const { t } = useI18n()

const features = ref<FeatureCatalogueEntry[]>([])
const loading = ref(true)
/** 读失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。
 *  两件事必须分开存：失败但原话为空时，界面仍要给出错态，不能落进「暂无功能页」那个空态。 */
const loadError = ref<string | null>(null)

/** 服务端的顺序就是目录的顺序（它是「有哪些目的地」的唯一来源），这里不重排。 */
const rows = computed(() =>
  features.value.map((feature) => {
    const known = findFeatureView(feature.id)
    return {
      id: feature.id,
      to: known ? `/admin/feature-stats/${feature.id}` : null,
      title: known ? t(known.titleKey) : feature.title,
      summary: known ? t(known.summaryKey) : feature.summary,
    }
  })
)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    features.value = (await getFeatureCatalogue()).features
  } catch (e) {
    // 读失败**不是**「还没有功能页」：两句话，两个画面（否则接口挂了会被读成一切正常）。
    // 原话存下来作说明行 —— 不用一句固定话把原因吞掉，失败未必是网络。
    features.value = []
    loadError.value = e instanceof Error && e.message ? e.message : ''
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="afs admin-page">
    <div class="afs__inner admin-page__col page-container--admin">
      <AdminPageHeader :title="t('featureStats.page.title')" :sub="t('featureStats.page.subtitle')" />

      <div class="afs__body admin-page__body">
        <p class="afs__floor t-meta-read">{{ t('featureStats.page.noNumbers') }}</p>

        <div v-if="loading" class="afs__list">
          <span v-for="n in 3" :key="n" class="afs__bone" />
        </div>

        <AdminEmptyState
          v-else-if="loadError !== null"
          :title="t('featureStats.page.loadFailed')"
          :desc="loadError || undefined"
          :action="t('featureStats.page.retry')"
          tone="error"
          @action="load"
        />

        <AdminEmptyState v-else-if="rows.length === 0" :title="t('featureStats.page.empty')" />

        <ul v-else class="afs__list">
          <li v-for="row in rows" :key="row.id" class="afs__item">
            <RouterLink v-if="row.to" :to="row.to" class="afs__link">
              <span class="afs__name">{{ row.title }}</span>
              <span class="afs__desc">{{ row.summary }}</span>
              <span class="afs__go" aria-hidden="true">→</span>
            </RouterLink>
            <div v-else class="afs__link afs__link--dim">
              <span class="afs__name">{{ row.title }}</span>
              <span class="afs__desc">{{ row.summary }}</span>
              <span class="afs__soon t-meta-read">{{ t('featureStats.page.notBuilt') }}</span>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 骨架三层（画布 + 1440 那一列 + 正文）由 `.admin-page` / `__col` / `__body` 给。
   以前这一页是「根上 `padding: 0 24px` + 页头负 margin 抵掉」，于是页头那道发丝线比
   列宽多出 24px，和卡片的两端对不上；现在页头住进 1440 那一列，三者同一条竖线。 */
.afs {
  display: flex;
  flex-direction: column;
}

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
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  color: inherit;
  text-decoration: none;
}

/* 可点的那一行：指针、hover 底色、Tab 顺序三样都有（`<a>` 自带的 Tab 与指针）。 */
@media (hover: hover) and (pointer: fine) {
  .afs__link:hover {
    background: var(--surface);
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
