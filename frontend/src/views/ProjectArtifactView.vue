<script setup lang="ts">
// 清单上这一项自己的那一页 (#1085 结论二)。
//
// 人来这一页，先要的是这一项现在长什么样：所以打开就是当前这一版本身——文档、表格、
// 图片直接预览，网址直接给，代码给这一版改了哪些文件。版本历史在旁边（手机上从底下
// 升起来），每一版说清谁认的、在哪次对话里做的，要看它比上一版改了什么从那一行进去。
//
// 一版是一次交付，所以这一页上没有「保存」「上传新版本」——版本由交付长出来，和清单
// 本身一样。能下载的是**当时交出去的那一份**，不是现在从源重建一次的结果：半年后依
// 赖变了，重建出来的可能和当时交出去的不是同一个东西。
//
// 比较两版是同一页的另一个样子（地址上带 `?before=&after=`），刷新、后退、把链接发给
// 别人都回到同一处。
import type { ArtifactComparison, ArtifactVersion, ProjectArtifactDetail } from '../api'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { artifactVersionFileUrl, compareArtifactVersions, downloadFile, getProjectArtifact } from '../api'
import ArtifactVersionPreview from '../components/ArtifactVersionPreview.vue'
import { t } from '../i18n'
import { relTime } from '../lib/relTime'

import ArtifactChanges from './artifact/ArtifactChanges.vue'
import ArtifactCompare from './artifact/ArtifactCompare.vue'
import ArtifactVersionList from './artifact/ArtifactVersionList.vue'

import { useCommands } from '@/commands'
import { copyLink, linkOf } from '@/commands/copy'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AppPage from '@/components/common/AppPage.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import { usePageTitleStore } from '@/stores/title'

const props = defineProps<{ projectId: string; artifactId: string }>()

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()

const artifact = ref<ProjectArtifactDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const downloading = ref('')
const historyOpen = ref(false)

const versions = computed(() => artifact.value?.versions ?? [])
const current = computed<ArtifactVersion | null>(() => versions.value.at(-1) ?? null)
const previous = computed(() => versions.value.at(-2) ?? null)

// ---- 比较：地址上的两版 ------------------------------------------------------

const before = computed(() => (typeof route.query.before === 'string' ? route.query.before : ''))
const after = computed(() => (typeof route.query.after === 'string' ? route.query.after : ''))
const comparing = computed(() => !!before.value && !!after.value)
const afterVersion = computed(() => versions.value.find((v) => v.card_id === after.value))
const beforeVersion = computed(() => versions.value.find((v) => v.card_id === before.value))

function compare(version: ArtifactVersion) {
  const earlier = versions.value.find((v) => v.number === version.number - 1)
  if (!earlier) return
  historyOpen.value = false
  void router.push({ query: { ...route.query, before: earlier.card_id, after: version.card_id } })
}

function pickBefore(cardId: string) {
  void router.replace({ query: { ...route.query, before: cardId } })
}

// ---- 读 --------------------------------------------------------------------

async function load() {
  const { projectId, artifactId } = props
  loading.value = true
  loadError.value = ''
  try {
    const found = await getProjectArtifact(projectId, artifactId)
    if (props.artifactId !== artifactId || props.projectId !== projectId) return
    artifact.value = found
  } catch (e) {
    if (props.artifactId !== artifactId || props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : t('tasks.artifact.loadError')
  } finally {
    if (props.artifactId === artifactId && props.projectId === projectId) loading.value = false
  }
}

watch(
  [() => props.projectId, () => props.artifactId],
  () => {
    artifact.value = null
    actionError.value = ''
    void load()
  },
  { immediate: true }
)

// 交出去的是一次合并时，「这一版」就是它比上一版改了的那些文件。
const changes = ref<ArtifactComparison | null>(null)
const changesError = ref('')
let changesAsked = 0
watch(
  () => [current.value?.card_id, previous.value?.card_id, comparing.value] as const,
  async () => {
    const ask = ++changesAsked
    changes.value = null
    changesError.value = ''
    const now = current.value
    const earlier = previous.value
    if (comparing.value || now?.kind !== 'merge' || earlier?.kind !== 'merge') return
    try {
      const result = await compareArtifactVersions(props.projectId, props.artifactId, earlier.card_id, now.card_id)
      if (ask === changesAsked) changes.value = result
    } catch (e) {
      if (ask === changesAsked)
        changesError.value = e instanceof Error ? e.message : t('tasks.artifactComparison.loadError')
    }
  },
  { immediate: true }
)

// ---- 拿走 ------------------------------------------------------------------

async function download(version: ArtifactVersion) {
  if (!version.filename) return
  downloading.value = version.card_id
  actionError.value = ''
  try {
    await downloadFile(artifactVersionFileUrl(props.projectId, props.artifactId, version.card_id), version.filename)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('tasks.artifact.downloadError')
  } finally {
    downloading.value = ''
  }
}

function openLink(version: ArtifactVersion) {
  if (version.url) window.open(version.url, '_blank', 'noopener,noreferrer')
}

useCommands(() => {
  const now = current.value
  if (comparing.value || !artifact.value) return []
  return [
    {
      id: 'artifact.copyLink',
      title: t('tasks.artifact.copyLink'),
      icon: 'mdi-link-variant',
      palette: false,
      header: {},
      run: () =>
        void copyLink(
          linkOf(router, {
            name: 'project-artifact',
            params: { projectId: props.projectId, artifactId: props.artifactId },
          })
        ),
    },
    ...(now?.kind === 'file'
      ? [
          {
            id: 'artifact.download',
            title: t('tasks.artifact.downloadVersion', { number: now.number }),
            icon: 'mdi-download-outline',
            palette: false as const,
            loading: downloading.value === now.card_id,
            // 手机上拿走这一版的那颗按钮贴在底边，顶栏不再放一颗。
            header: { accent: true },
            run: () => void download(now),
          },
        ]
      : now?.kind === 'link' && now.url
        ? [
            {
              id: 'artifact.open',
              title: t('tasks.artifact.open'),
              icon: 'mdi-open-in-new',
              palette: false as const,
              header: { accent: true },
              run: () => openLink(now),
            },
          ]
        : []),
  ]
})

// ---- 页头 ------------------------------------------------------------------

const title = computed(() =>
  comparing.value ? t('tasks.artifactComparison.title') : artifact.value?.name ?? t('tasks.artifact.fallbackTitle')
)
const parent = computed(() =>
  comparing.value && artifact.value
    ? {
        label: artifact.value.name,
        to: { name: 'project-artifact', params: { projectId: props.projectId, artifactId: props.artifactId } },
      }
    : {
        label: t('navigation.project.overview'),
        to: { name: 'workspace-overview', params: { projectId: props.projectId } },
      }
)

// 手机顶栏写的是这一项的名字，不是「产物」这个类别。
const titles = usePageTitleStore()
watch(title, (value) => titles.setDynamicTitle(value, 'project-artifact'), { immediate: true })

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/** 这一版是什么：第几版、什么时候交的、谁认的、是哪一份。 */
const facts = computed(() => {
  const now = current.value
  if (!now || !artifact.value) return ''
  return [
    t('tasks.artifactComparison.version', { number: now.number }),
    now.delivered_at ? t('tasks.artifact.deliveredAt', { when: relTime(now.delivered_at) }) : '',
    now.decided_by ? t('tasks.artifact.acceptedBy', { who: now.decided_by }) : '',
    now.filename ?? '',
    now.bytes !== null && now.bytes !== undefined ? fmtBytes(now.bytes) : '',
  ]
    .filter(Boolean)
    .join(' · ')
})
</script>

<template>
  <AppPage :title="title" :parent="parent" width="full">
    <template v-if="comparing && beforeVersion && afterVersion" #meta>
      {{ t('tasks.artifactComparison.version', { number: beforeVersion.number }) }} →
      {{ t('tasks.artifactComparison.version', { number: afterVersion.number }) }}
    </template>

    <p v-if="loadError" role="alert" class="t-body c-danger pa-4">{{ loadError }}</p>

    <div v-if="loading && !artifact" class="py-8 text-center" role="status" :aria-label="t('tasks.artifact.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <!-- 比较两版 -->
    <div v-else-if="artifact && comparing" class="artifact-compare" :class="{ 'artifact-compare--phone': !mdAndUp }">
      <ArtifactCompare
        :project-id="projectId"
        :artifact-id="artifactId"
        :versions="versions"
        :before="before"
        :after="after"
        @update:before="pickBefore"
      />
    </div>

    <div v-else-if="artifact" class="artifact" :class="{ 'artifact--phone': !mdAndUp }">
      <section class="artifact__main">
        <div class="artifact__facts">
          <p v-if="artifact.about" class="t-body c-muted artifact__about">{{ artifact.about }}</p>
          <p class="t-meta c-faint artifact__line">{{ current ? facts : t('tasks.artifact.notDelivered') }}</p>
          <p v-if="actionError" role="alert" class="t-meta c-danger artifact__line">{{ actionError }}</p>
        </div>

        <!-- 这一版本身 -->
        <div class="artifact__view">
          <BaseEmptyState
            v-if="!current"
            size="inline"
            align="center"
            class="artifact__empty"
            :title="t('tasks.artifact.noVersions')"
          />
          <ArtifactVersionPreview
            v-else-if="current.kind === 'file'"
            bare
            :project-id="projectId"
            :artifact-id="artifactId"
            :version="current"
          />
          <div v-else-if="current.kind === 'link' && current.url" class="artifact__link">
            <p class="t-meta c-faint">{{ t('tasks.artifact.linkDelivered') }}</p>
            <a :href="current.url" target="_blank" rel="noopener noreferrer" class="t-body">{{ current.url }}</a>
          </div>
          <div v-else-if="current.kind === 'merge'" class="artifact__merge">
            <h2 class="t-title">{{ t('tasks.artifact.thisVersion') }}</h2>
            <p class="t-body">{{ current.subject || t('tasks.artifact.noSubject') }}</p>
            <p v-if="!previous" class="t-meta c-faint">{{ t('tasks.artifact.firstVersion') }}</p>
            <p v-else-if="changesError" class="t-meta c-danger" role="alert">{{ changesError }}</p>
            <p v-else-if="!changes" class="t-meta c-faint" role="status">{{ t('tasks.artifactComparison.loading') }}</p>
            <ArtifactChanges v-else :files="changes.files" />
          </div>
          <BaseEmptyState
            v-else
            size="inline"
            align="center"
            class="artifact__empty"
            :title="t('tasks.artifact.notRetained')"
          />
        </div>

        <!-- 手机上：版本历史从底下升起来，拿走这一版的那颗按钮贴着底边。 -->
        <div v-if="!mdAndUp && current" class="artifact__bar">
          <BaseButton kind="secondary" class="flex-grow-1" @click="historyOpen = true">
            {{ t('tasks.artifact.historyCount', { n: versions.length }) }}
          </BaseButton>
          <BaseButton
            v-if="current.kind === 'file'"
            kind="primary"
            class="flex-grow-1"
            :loading="downloading === current.card_id"
            @click="download(current)"
          >
            {{ t('tasks.artifact.download') }}
          </BaseButton>
          <BaseButton
            v-else-if="current.kind === 'link' && current.url"
            kind="primary"
            class="flex-grow-1"
            :href="current.url"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ t('tasks.artifact.open') }}
          </BaseButton>
        </div>
      </section>

      <aside v-if="mdAndUp" class="artifact__history">
        <h2 class="t-title artifact__history-title">
          {{ t('tasks.artifact.history') }}
          <span v-if="versions.length" class="t-meta c-faint"
            >· {{ t('tasks.artifact.total', { n: versions.length }) }}</span
          >
        </h2>
        <ArtifactVersionList
          :project-id="projectId"
          :versions="versions"
          :downloading="downloading"
          @download="download"
          @compare="compare"
        />
      </aside>
      <MobileActionSheet v-else v-model="historyOpen" :title="t('tasks.artifact.history')">
        <div class="artifact__sheet">
          <ArtifactVersionList
            :project-id="projectId"
            :versions="versions"
            :downloading="downloading"
            @download="download"
            @compare="compare"
          />
        </div>
      </MobileActionSheet>
    </div>
  </AppPage>
</template>

<style scoped>
.artifact {
  display: flex;
  height: 100%;
  min-height: 0;
}
.artifact__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.artifact__facts {
  flex: none;
  padding: 12px 24px;
  border-bottom: 1px solid var(--line);
}
.artifact--phone .artifact__facts {
  padding: 10px 16px;
}
.artifact__about,
.artifact__line {
  margin: 0;
  overflow-wrap: anywhere;
}
/* 预览贴满下面整块：它就是这一页的主体，不再套一圈外框。 */
.artifact__view {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  background: var(--canvas);
}
.artifact__empty {
  padding: 48px 24px;
  text-align: center;
}
.artifact__link,
.artifact__merge {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 880px;
  padding: 24px;
  overflow-wrap: anywhere;
}
.artifact__merge h2,
.artifact__merge p,
.artifact__link p {
  margin: 0;
}
.artifact__link a {
  color: var(--accent-ink);
}
.artifact__bar {
  display: flex;
  flex: none;
  gap: 8px;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--surface);
}
.artifact__history {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 12px;
  width: 400px;
  padding: 16px 20px 24px;
  overflow-y: auto;
  border-left: 1px solid var(--line);
}
.artifact__history-title {
  margin: 0;
}
.artifact__sheet {
  padding: 0 12px 12px;
}
.artifact-compare {
  max-width: 1040px;
  padding: 24px;
}
.artifact-compare--phone {
  padding: 16px;
}
</style>
