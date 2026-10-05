<script setup lang="ts">
import type { FeatureCatalogueEntry } from '@/views/admin/features/featureApi'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminFeatureStatsPageView from '@/views/admin/AdminFeatureStatsPageView.vue'
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
// 有页面才给链接（整行一个去哪的链接）。没有页面的那行是**静的**：指针、hover、
// Tab 三样一样都不给 —— 半个链接比没有链接更糟（见 `AdminKpiCard` 文件头那条纪律）。
//
// 这一只只做**取数、把每行拼成 title/summary/to**；画面在同一目录的
// `AdminFeatureStatsPageView.vue` 里，只吃 props、只往上发事件（`pnpm run lint:scenes`
// 把它当容器看，冻结的是那个视图）。
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
  <AdminFeatureStatsPageView :loading="loading" :load-error="loadError" :rows="rows" @retry="load" />
</template>
