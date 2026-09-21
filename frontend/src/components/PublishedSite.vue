<script setup lang="ts">
// 网站 —— 这个项目对外的那个地址，以及把已采纳的版本发上去的那一下。
//
// 它在项目首页上「做出了什么」旁边，不在一个叫「导出与发布」的页面里：一个发布出
// 去的网站就是交出去的东西之一，和清单上那几项答的是同一个问题。而那一页除了这一
// 块之外什么都没有，于是「导出」两个字答应了一件它从来没做过的事。
//
// 没发布过、也没有东西可发布时整块不出现——和「做出了什么」同一条规矩：首页上的每
// 一块都该是这个项目现在真有的东西。芝士 在已采纳的版本里备好一个静态站点，这一块
// 自己就出现了。
import type { ProjectSiteInfo } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { ApiError, getProjectSite, publishProjectSite } from '@/api'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ projectId: string }>()

const info = ref<ProjectSiteInfo | null>(null)
const directory = ref('')
const publishing = ref(false)
const publishError = ref('')

const candidates = computed(() =>
  (info.value?.candidates ?? []).map((candidate) => ({ title: candidate.entry_file, value: candidate.directory }))
)
const selected = computed(() => info.value?.candidates.find((candidate) => candidate.directory === directory.value))

/** 发布过，或者现在有东西可发布——两样都没有就整块不出现。 */
const shown = computed(() => {
  const current = info.value
  if (!current) return false
  return !!current.site || (current.can_publish && current.candidates.length > 0)
})

/** 线上那一份已经是已采纳的这一版，同一个入口——没有可发的更新了。 */
const isCurrent = computed(
  () =>
    !!info.value?.site &&
    info.value.site.source_revision === info.value.source_revision &&
    info.value.site.directory === directory.value
)
const canPublish = computed(
  () =>
    !!info.value?.can_publish &&
    !!info.value.source_revision &&
    !!selected.value &&
    !info.value.unavailable_reason &&
    !publishing.value &&
    !isCurrent.value
)

async function load() {
  const projectId = props.projectId
  try {
    const result = await getProjectSite(projectId)
    if (props.projectId !== projectId) return
    info.value = result
    if (!result.candidates.some((candidate) => candidate.directory === directory.value)) {
      directory.value =
        result.candidates.find((candidate) => candidate.directory === result.site?.directory)?.directory ??
        result.candidates[0]?.directory ??
        ''
    }
  } catch {
    // 读不到发布信息不该把首页变成一条错误：这一块只在真有网站可说时出现。
    if (props.projectId === projectId) info.value = null
  }
}

async function publish() {
  const current = info.value
  if (!canPublish.value || !current?.source_revision) return
  const projectId = props.projectId
  publishing.value = true
  publishError.value = ''
  try {
    const site = await publishProjectSite(projectId, {
      directory: directory.value,
      expected_source_revision: current.source_revision,
    })
    if (props.projectId !== projectId) return
    info.value = { ...current, site }
  } catch (error) {
    if (props.projectId !== projectId) return
    if (error instanceof ApiError && error.status === 409) {
      // 已采纳的版本在这中间变了。把新的读回来摆在人眼前，再发布是他下一次点击的
      // 事——替他决定发哪一版，等于替他决定发了什么。
      await load()
      if (props.projectId === projectId) publishError.value = '项目已采纳的版本变了，确认之后再发布'
    } else {
      publishError.value = error instanceof Error ? error.message : '未能发布'
    }
  } finally {
    if (props.projectId === projectId) publishing.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    info.value = null
    directory.value = ''
    publishing.value = false
    publishError.value = ''
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <section v-if="shown && info" class="site">
    <h2 class="site__title t-title">网站</h2>
    <p v-if="publishError" role="alert" class="site__error t-meta">{{ publishError }}</p>

    <div class="site-row">
      <a
        v-if="info.site"
        class="site-row__url t-body"
        :href="info.site.url"
        target="_blank"
        rel="noopener"
        :title="info.site.url"
      >
        {{ info.site.url }}
      </a>
      <span v-else class="site-row__url t-body c-muted">暂无已发布的网站</span>
      <span v-if="info.site" class="site-row__when t-meta c-faint">
        <code :title="info.site.source_revision">{{ info.site.source_revision.slice(0, 8) }}</code>
        · {{ info.site.published_by }} · {{ relTime(info.site.published_at) }}
      </span>
      <v-btn
        v-if="info.can_publish"
        size="small"
        variant="outlined"
        color="primary"
        :disabled="!canPublish"
        :loading="publishing"
        @click="publish"
      >
        {{ info.site ? '发布更新' : '发布网站' }}
      </v-btn>
    </div>

    <template v-if="info.can_publish">
      <p v-if="info.unavailable_reason" role="status" class="site__note t-meta c-muted">
        {{ info.unavailable_reason }}
      </p>
      <p v-else-if="!info.candidates.length" class="site__note t-meta c-muted">项目已采纳的版本里没有可发布的网站</p>
      <p v-else-if="isCurrent" class="site__note t-meta c-faint">线上的就是已采纳的这一版</p>
      <v-select
        v-if="candidates.length > 1"
        v-model="directory"
        class="site__entry"
        autocomplete="off"
        :items="candidates"
        :disabled="publishing"
        label="网站入口"
        variant="outlined"
        density="compact"
        hide-details
      />
      <p v-else-if="selected && !info.site" class="site__note t-meta c-muted">
        网站入口：<code>{{ selected.entry_file }}</code>
      </p>
    </template>
  </section>
</template>

<style scoped>
/* 和「做出了什么」、下面那块板左右对齐。 */
.site {
  flex: 0 0 auto;
  padding: 0 10px 14px;
}
.site__title {
  margin: 0 0 8px;
}
.site__error {
  margin: 0 0 8px;
  color: var(--danger-ink);
}
.site__note {
  margin: 8px 0 0;
}
.site__entry {
  margin-top: 10px;
  max-width: 360px;
}
/* 一行：地址在左，线上那一版贴着右边的按钮——和「做出了什么」的行同一个语法。 */
.site-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 8px 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.site-row__url {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
}
a.site-row__url:hover {
  text-decoration: underline;
}
.site-row__when {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
</style>
