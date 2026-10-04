<script setup lang="ts">
/**
 * 资料库里一份文件的每一版：版本号、什么时候、谁换上的、多大；每一版都能下载。
 *
 * 「恢复为当前」把选中的旧版**复制**成新的一版——历史只增不减，被恢复的那一版和它
 * 之后的几版都还在，所以只要一次轻确认。恢复之后引用这个名字的消息读到的就是它。
 */
import type { LibraryVersion } from '@/lib/libraryApi'

import { ref, watch } from 'vue'

import { downloadFile, libraryFileRawUrl } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { libraryVersionRawUrl, listLibraryVersions, restoreLibraryVersion } from '@/lib/libraryApi'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  projectId: string
  /** 看哪一份；null 时对话框关着。 */
  path: string | null
  /** 能不能恢复：放进、替换、恢复只有人能做。 */
  canRestore: boolean
  fmtBytes: (n: number) => string
}>()
const emit = defineEmits<{ close: []; restored: [path: string, version: number] }>()

const versions = ref<LibraryVersion[]>([])
const loading = ref(false)
const failed = ref<string | null>(null)
const actionError = ref('')
const confirming = ref<LibraryVersion | null>(null)
const restoring = ref(false)
let session = 0

async function load() {
  const path = props.path
  const id = ++session
  versions.value = []
  failed.value = null
  actionError.value = ''
  if (!path) return
  loading.value = true
  try {
    const list = await listLibraryVersions(props.projectId, path)
    if (id === session) versions.value = list
  } catch (e) {
    if (id === session) failed.value = e instanceof Error ? e.message : t('work.library.versionsFailed')
  } finally {
    if (id === session) loading.value = false
  }
}
watch(() => props.path, load, { immediate: true })

const fileName = () => (props.path ?? '').split('/').pop() || (props.path ?? '')

async function download(version: LibraryVersion) {
  if (!props.path) return
  actionError.value = ''
  try {
    // 记录表之前就在的那一份没有 id：它就是现在这一份。
    const url = version.id
      ? libraryVersionRawUrl(props.projectId, props.path, version.id)
      : libraryFileRawUrl(props.projectId, props.path)
    await downloadFile(url, fileName())
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.downloadFailed')
  }
}

async function restore() {
  const target = confirming.value
  const path = props.path
  confirming.value = null
  if (!target?.id || !path) return
  restoring.value = true
  actionError.value = ''
  try {
    await restoreLibraryVersion(props.projectId, path, target.id)
    emit('restored', path, target.version)
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.restoreFailed')
  } finally {
    restoring.value = false
  }
}
</script>

<template>
  <AdaptiveDialog
    :model-value="!!path"
    :title="t('work.library.versionsTitle', { name: path ?? '' })"
    :cancel-label="t('work.library.versionsClose')"
    size="md"
    @update:model-value="(open: boolean) => !open && emit('close')"
  >
    <BaseLoadError v-if="failed" :title="t('work.library.versionsFailed')" :error="failed" @retry="load" />
    <p v-else-if="loading" class="lv__loading t-meta">{{ t('work.library.versionsLoading') }}</p>
    <BaseEmptyState v-else-if="!versions.length" size="inline" :title="t('work.library.versionsEmpty')" />
    <ol v-else class="lv__list" :aria-label="t('work.library.versionsTitle', { name: path ?? '' })">
      <li v-for="v in versions" :key="v.id ?? v.version" class="lv__row">
        <div class="lv__main">
          <span class="lv__num">
            {{ t('work.library.versionNumber', { n: v.version }) }}
            <span v-if="v.current" class="lv__current">{{ t('work.library.versionCurrent') }}</span>
          </span>
          <span class="lv__meta t-meta">
            {{
              [
                v.added_by ?? t('work.library.unknownSource'),
                v.added_at ? relTime(v.added_at) : null,
                fmtBytes(v.bytes),
              ]
                .filter(Boolean)
                .join(' · ')
            }}
          </span>
        </div>
        <BaseButton size="sm" @click="download(v)">{{ t('work.library.download') }}</BaseButton>
        <BaseButton
          v-if="canRestore && !v.current && v.id"
          size="sm"
          kind="secondary"
          :loading="restoring && confirming === null"
          :disabled="restoring"
          @click="confirming = v"
        >
          {{ t('work.library.restoreVersion') }}
        </BaseButton>
      </li>
    </ol>
    <p v-if="actionError" class="lv__error" role="alert">{{ actionError }}</p>

    <!-- Restoring copies the old version as a new one; nothing is deleted, so one light confirm is enough. -->
    <ConfirmDialog
      :model-value="!!confirming"
      :title="t('work.library.restoreTitle', { n: confirming?.version ?? 0 })"
      :confirm-label="t('work.library.restoreVersion')"
      @update:model-value="confirming = null"
      @confirm="restore"
    >
      {{ t('work.library.restoreBody') }}
    </ConfirmDialog>
  </AdaptiveDialog>
</template>

<style scoped>
.lv__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.lv__row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
.lv__row:last-child {
  border-bottom: 0;
}
.lv__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.lv__num {
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
}
.lv__current {
  margin-inline-start: 6px;
  color: var(--ok-ink);
  font-size: 12px;
}
.lv__meta,
.lv__loading {
  color: var(--muted);
}
.lv__error {
  margin: 8px 0 0;
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
