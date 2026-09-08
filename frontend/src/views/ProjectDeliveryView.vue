<script setup lang="ts">
import type { ProjectSiteInfo } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { ApiError, getProjectSite, publishProjectSite } from '../api'

const props = defineProps<{ projectId: string }>()
const info = ref<ProjectSiteInfo | null>(null)
const directory = ref('')
const loading = ref(false)
const publishing = ref(false)
const loadError = ref<string | null>(null)
const publishError = ref<string | null>(null)
const notice = ref<string | null>(null)

const candidates = computed(() =>
  (info.value?.candidates ?? []).map((candidate) => ({
    title: candidate.entry_file,
    value: candidate.directory,
  }))
)
const selected = computed(() => info.value?.candidates.find((candidate) => candidate.directory === directory.value))
const isCurrent = computed(
  () =>
    !!info.value?.site &&
    info.value.site.source_revision === info.value.source_revision &&
    info.value.site.directory === directory.value
)
const canPublish = computed(
  () =>
    info.value?.can_publish &&
    !!info.value.source_revision &&
    !!selected.value &&
    !info.value.unavailable_reason &&
    !loadError.value &&
    !loading.value &&
    !publishing.value &&
    !isCurrent.value
)

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = null
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
  } catch (error) {
    if (props.projectId === projectId) {
      loadError.value = error instanceof Error ? error.message : '加载发布信息失败'
    }
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

async function publish() {
  const current = info.value
  if (!canPublish.value || !current?.source_revision) return
  const projectId = props.projectId
  publishing.value = true
  publishError.value = null
  notice.value = null
  try {
    const site = await publishProjectSite(projectId, {
      directory: directory.value,
      expected_source_revision: current.source_revision,
    })
    if (props.projectId !== projectId) return
    info.value = { ...current, site }
    notice.value = '网站已发布'
  } catch (error) {
    if (props.projectId !== projectId) return
    if (error instanceof ApiError && error.status === 409) {
      await load()
      if (props.projectId === projectId) {
        publishError.value = '项目版本已变化，请确认当前版本后重新发布'
      }
    } else {
      publishError.value = error instanceof Error ? error.message : '发布失败'
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
    publishError.value = null
    notice.value = null
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="delivery-page pa-4 pa-md-6">
    <div class="delivery-content">
      <header class="d-flex align-center justify-space-between ga-4 mb-6">
        <h1 class="t-page-title">导出与发布</h1>
        <v-btn variant="text" color="on-surface-variant" :loading="loading" :disabled="publishing" @click="load"
          >刷新</v-btn
        >
      </header>

      <p v-if="loadError" role="alert" class="delivery-error t-body mb-4">{{ loadError }}</p>
      <div v-if="loading && !info" class="py-8 text-center" role="status" aria-label="加载发布信息">
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-if="info">
        <section class="delivery-section pa-4 pa-md-6" aria-labelledby="site-title">
          <header class="d-flex align-center justify-space-between flex-wrap ga-3 mb-4">
            <h2 id="site-title" class="t-title">Site</h2>
            <span class="t-body c-muted">仅项目成员可访问</span>
          </header>

          <template v-if="info.site">
            <div class="d-flex align-center justify-space-between flex-wrap ga-3 mb-4">
              <a class="site-link t-body" :href="info.site.url" target="_blank" rel="noopener">{{ info.site.url }}</a>
              <v-btn
                :href="info.site.url"
                target="_blank"
                rel="noopener"
                variant="outlined"
                color="on-surface-variant"
                size="small"
              >
                打开网站
              </v-btn>
            </div>
            <dl class="site-details t-body">
              <dt>线上版本</dt>
              <dd>
                <code :title="info.site.source_revision">{{ info.site.source_revision.slice(0, 8) }}</code>
              </dd>
              <dt>发布时间</dt>
              <dd>{{ new Date(info.site.published_at).toLocaleString() }}</dd>
              <dt>发布人</dt>
              <dd>{{ info.site.published_by }}</dd>
            </dl>
          </template>
          <p v-else class="t-body c-muted">暂无已发布的网站</p>

          <div class="publish-form mt-6 pt-6">
            <h3 class="t-title mb-3">项目已采纳版本</h3>
            <p v-if="info.source_revision" class="t-body mb-4">
              <code :title="info.source_revision">{{ info.source_revision.slice(0, 8) }}</code>
            </p>
            <p class="t-body c-muted mb-4">发布后，网站可持续访问；后续修改需发布更新后生效</p>

            <p v-if="info.unavailable_reason" role="status" class="t-body c-muted mb-4">
              {{ info.unavailable_reason }}
            </p>
            <p v-else-if="!candidates.length" class="t-body c-muted mb-4">
              项目已采纳版本中暂无可发布的网站，请让芝士准备静态网站并提交验收
            </p>
            <template v-else>
              <v-select
                v-if="candidates.length > 1"
                v-model="directory"
                :items="candidates"
                :disabled="publishing || loading"
                label="网站入口"
                variant="outlined"
                density="compact"
                hide-details
                class="mb-4"
              />
              <p v-else class="t-body mb-4">
                网站入口：<code>{{ selected?.entry_file }}</code>
              </p>
              <p v-if="isCurrent" class="t-body c-muted mb-4">网站已是当前版本</p>
            </template>

            <p v-if="publishError" role="alert" class="delivery-error t-body mb-4">{{ publishError }}</p>
            <p v-if="notice" role="status" class="delivery-success t-body mb-4">{{ notice }}</p>
            <v-btn
              v-if="info.can_publish"
              color="primary"
              :disabled="!canPublish"
              :loading="publishing"
              @click="publish"
            >
              {{ info.site ? '发布更新' : '发布为 Site' }}
            </v-btn>
            <p v-else class="t-body c-muted">由项目负责人发布网站</p>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.delivery-page {
  height: 100%;
  overflow-y: auto;
}

.delivery-content {
  max-width: 800px;
  margin: 0 auto;
}

.delivery-section {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.site-link {
  overflow-wrap: anywhere;
  color: var(--text);
}

.site-details {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px 16px;
}

.site-details dt {
  color: var(--muted);
}

.site-details dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.publish-form {
  border-top: 1px solid var(--line);
}

.publish-form code {
  overflow-wrap: anywhere;
}

.delivery-error {
  color: var(--danger-ink);
}

.delivery-success {
  color: var(--ok-ink);
}
</style>
