<script setup lang="ts">
// 网站 —— 这个项目对外的那个地址，以及把已采纳的版本发上去的那一下。
//
// 它钉在首页「做出了什么」那一列的最上面，不在一个叫「导出与发布」的页面里：一个
// 发布出去的网站就是交出去的东西之一，和那一列里的几项答的是同一个问题。而那一页
// 除了这一块之外什么都没有，于是「导出」两个字答应了一件它从来没做过的事。
//
// 它不排进下面那张清单，因为它只有一个：清单上一项是一样东西的历代版本，而网站只
// 有「线上这一版」这一个状态。
//
// 没发布过、也没有东西可发布时这一行不出现——那一列此时只剩产物，或者「暂无产物」。
// 芝士 在已采纳的版本里备好一个静态站点，这一行自己就出现了。
//
// 发布入口和已采纳的版本摆在一个对话框里：一个季度按几次的动作不值得在首页常驻一
// 个表单，而该发哪一版、发哪个入口是按下去之前必须看清的两件事。
import type { ProjectSiteInfo } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { ApiError, getProjectSite, publishProjectSite } from '@/api'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ projectId: string }>()

const info = ref<ProjectSiteInfo | null>(null)
const directory = ref('')
const publishing = ref(false)
const publishError = ref('')
const asking = ref(false)

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
  asking.value = false
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
    asking.value = false
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <div v-if="shown && info" class="site">
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
      <v-btn
        v-if="info.can_publish"
        class="site-row__act"
        size="x-small"
        variant="text"
        color="primary"
        :disabled="!canPublish"
        :loading="publishing"
        @click="asking = true"
      >
        {{ info.site ? '发布更新' : '发布' }}
      </v-btn>
    </div>
    <p v-if="info.site" class="site-row__when t-meta c-faint">
      <code :title="info.site.source_revision">{{ info.site.source_revision.slice(0, 8) }}</code>
      · {{ info.site.published_by }} · {{ relTime(info.site.published_at) }}
    </p>
    <p v-if="publishError" role="alert" class="site__error t-meta">{{ publishError }}</p>

    <!-- 发布。按下去之前要看清的是两件事：发的是哪一版，发的是哪个入口。 -->
    <v-dialog :model-value="asking" max-width="440" @update:model-value="asking = false">
      <v-card>
        <v-card-title class="t-title">{{ info.site ? '发布更新' : '发布网站' }}</v-card-title>
        <v-card-text>
          <p v-if="info.unavailable_reason" role="status" class="t-body c-muted">{{ info.unavailable_reason }}</p>
          <p v-else-if="!info.candidates.length" class="t-body c-muted">项目已采纳的版本里没有可发布的网站</p>
          <template v-else>
            <p class="t-body mb-4">
              发布项目已采纳的版本
              <code v-if="info.source_revision" :title="info.source_revision">
                {{ info.source_revision.slice(0, 8) }}
              </code>
            </p>
            <v-select
              v-if="candidates.length > 1"
              v-model="directory"
              autocomplete="off"
              :items="candidates"
              :disabled="publishing"
              label="网站入口"
              variant="outlined"
              density="compact"
              hide-details
            />
            <p v-else-if="selected" class="t-body">
              网站入口：<code>{{ selected.entry_file }}</code>
            </p>
            <p v-if="isCurrent" class="t-meta c-faint mt-4">线上的就是已采纳的这一版</p>
            <p v-else class="t-meta c-faint mt-4">发布之后网站持续可访问，后续修改需要再发布一次</p>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="asking = false">取消</v-btn>
          <v-btn variant="text" color="primary" :disabled="!canPublish" :loading="publishing" @click="publish">
            发布
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
/* 钉在这一列最上面，和下面那张清单之间一条分界线 —— 它和产物不是一类东西。 */
.site {
  flex: 0 0 auto;
  padding: 10px 12px 8px;
  border-bottom: 1px solid var(--line);
}
.site__error {
  margin: 6px 0 0;
  color: var(--danger-ink);
}
.site-row {
  display: flex;
  align-items: center;
  gap: 6px;
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
.site-row__act {
  flex: 0 0 auto;
}
/* 线上是哪一版、谁发的、什么时候 —— 第二行，因为这一列窄，挤在地址后面会把地址
   截成一小段。 */
.site-row__when {
  margin: 2px 0 0;
}
.site-row__when code {
  overflow-wrap: anywhere;
}
</style>
