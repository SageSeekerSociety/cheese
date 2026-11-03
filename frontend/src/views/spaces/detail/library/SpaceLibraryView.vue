<script setup lang="ts">
// 空间资料库这一页的画面：一列资料，点开在右边一栏预览（手机上是整一页）。
//
// 只摆，不取数：清单、开着的那一份、能不能管都由容器（SpaceLibrary.vue）给，点开、
// 下载、上传、改可见范围、移除都往上报。和项目资料库同一种长相：每行写谁传的、什么
// 时候、多大；管理的操作收在每行的 ⋯ 里，成员那一侧只有下载。
import type { MenuAction } from '@/components/common/menuAction'
import type { SpaceMaterial, SpaceMaterialVisibility } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { usePaneWidth } from '@/composables/usePaneWidth'
import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import PaneHandle from '@/components/common/PaneHandle.vue'
import { fmtBytes } from '@/lib/changesTree'
import { type Kind, KIND_ICONS, kindLabel, kindOf, KINDS } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  materials: SpaceMaterial[]
  canManage: boolean
  loading: boolean
  failed: boolean
  /** 读失败时服务端给的原因；没有是 `null`。 */
  error: string | null
  /** 右栏开着的那一份；没有是 `null`。 */
  selected: SpaceMaterial | null
  /** 桌面（列表加右栏）还是手机（开着一份时整页都是它）。 */
  wide: boolean
  /** 正在上传、改档或移除。 */
  busy: boolean
  /** 上传框开着没有：传成功才由容器关上，失败时选好的文件还在。 */
  uploadOpen: boolean
  read: (item: SpaceMaterial) => (asPdf: boolean) => Promise<ArrayBuffer>
}>()

const emit = defineEmits<{
  'update:uploadOpen': [open: boolean]
  open: [item: SpaceMaterial]
  close: []
  retry: []
  download: [item: SpaceMaterial]
  upload: [payload: { file: File; visibility: SpaceMaterialVisibility }]
  changeVisibility: [item: SpaceMaterial, visibility: SpaceMaterialVisibility]
  remove: [item: SpaceMaterial]
}>()

const { t } = useI18n()
const rowMenu = useRowMenu<number>()

// ---- 找：按名字、按类型 -------------------------------------------------------

const query = ref('')
const kind = ref<Kind>('all')
const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  return props.materials.filter(
    (item) => (!q || item.name.toLowerCase().includes(q)) && (kind.value === 'all' || kindOf(item.name) === kind.value)
  )
})
function clearFilters() {
  query.value = ''
  kind.value = 'all'
}

// ---- 每一行 ------------------------------------------------------------------

function actionsOf(item: SpaceMaterial): MenuAction[] {
  const actions: MenuAction[] = [
    {
      key: 'download',
      label: t('spaces.materials.download'),
      icon: 'mdi-download-outline',
      onSelect: () => emit('download', item),
    },
  ]
  if (!props.canManage) return actions
  const next: SpaceMaterialVisibility = item.visibility === 'admins' ? 'members' : 'admins'
  actions.push(
    {
      key: 'visibility',
      label: t(next === 'admins' ? 'spaces.materials.makeAdmins' : 'spaces.materials.makeMembers'),
      icon: next === 'admins' ? 'mdi-lock-outline' : 'mdi-account-multiple-outline',
      disabled: props.busy,
      onSelect: () => emit('changeVisibility', item, next),
    },
    {
      key: 'remove',
      label: t('spaces.materials.remove'),
      icon: 'mdi-delete-outline',
      danger: true,
      divider: true,
      disabled: props.busy,
      onSelect: () => (removing.value = item),
    }
  )
  return actions
}

// ---- 移除：先问一句 ------------------------------------------------------------

const removing = ref<SpaceMaterial | null>(null)
function confirmRemove() {
  if (removing.value) emit('remove', removing.value)
  removing.value = null
}

// ---- 上传：一个文件，一个可见范围 ------------------------------------------------

const VISIBILITIES: SpaceMaterialVisibility[] = ['members', 'admins']
const pickedFile = ref<File | File[] | null>(null)
const visibility = ref<SpaceMaterialVisibility>('members')
/** `v-file-input` 单文件时也可能给数组，取第一个。 */
const file = computed<File | null>(() =>
  Array.isArray(pickedFile.value) ? pickedFile.value[0] ?? null : pickedFile.value
)

/** 每次打开都从空白开始：上一次传成功时框是被容器关上的，选过的文件没清。 */
function openUpload() {
  pickedFile.value = null
  visibility.value = 'members'
  emit('update:uploadOpen', true)
}
function submitUpload() {
  if (file.value) emit('upload', { file: file.value, visibility: visibility.value })
}

const showList = computed(() => props.wide || !props.selected)

// 桌面上右边那一栏预览可以往左拖宽，和项目资料库记在同一份里（`usePaneWidth`）。
const detailRef = ref<HTMLElement | null>(null)
const pane = usePaneWidth('cheese.library.previewShare', detailRef)
</script>

<template>
  <PageHeader show-on-mobile :title="selected && !wide ? selected.name : t('spaces.materials.title')">
    <!-- The slot is always passed: PageHeader decides once whether it has actions,
         before the list says whether this viewer can manage it. -->
    <template #actions>
      <BaseButton
        v-if="canManage && showList"
        kind="primary"
        prepend-icon="mdi-upload-outline"
        data-testid="slib-upload"
        @click="openUpload"
      >
        {{ t('spaces.materials.upload') }}
      </BaseButton>
    </template>
  </PageHeader>

  <div class="slib" :class="{ 'slib--split': wide && selected }">
    <section v-if="showList" class="slib__list">
      <BaseLoadError
        v-if="failed"
        :title="t('spaces.materials.toast.loadFailed')"
        :error="error"
        @retry="emit('retry')"
      />

      <template v-else>
        <div v-if="materials.length" class="slib__tools">
          <v-text-field
            v-model="query"
            autocomplete="off"
            density="compact"
            variant="outlined"
            prepend-inner-icon="mdi-magnify"
            :placeholder="t('spaces.materials.search')"
            :aria-label="t('spaces.materials.search')"
            clearable
            hide-details
            class="slib__search"
          />
          <div class="slib__kinds" role="group" :aria-label="t('work.library.kindsLabel')">
            <button
              v-for="k in KINDS"
              :key="k"
              type="button"
              class="slib__kind t-meta"
              :aria-pressed="kind === k"
              @click="kind = k"
            >
              {{ kindLabel(k) }}
            </button>
          </div>
        </div>

        <div v-if="loading && !materials.length" class="py-8 text-center" role="status">
          <v-progress-circular indeterminate size="28" color="primary" />
        </div>

        <ul v-else-if="shown.length" class="slib__rows">
          <li v-for="item in shown" :key="item.id">
            <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- contextmenu bubbles here from the row's focused controls; the row's ⋯ button opens the same menu -->
            <div
              class="slib-row"
              :class="{ 'slib-row--on': selected?.id === item.id }"
              :data-testid="`slib-row-${item.id}`"
              @contextmenu="rowMenu.open(item.id, $event)"
            >
              <button type="button" class="slib-row__open" @click="emit('open', item)">
                <v-icon :icon="KIND_ICONS[kindOf(item.name)]" size="20" class="slib-row__icon" />
                <span class="slib-row__id">
                  <span class="slib-row__name t-body" data-user-content>{{ item.name }}</span>
                  <span class="slib-row__meta t-meta c-muted">
                    <span v-if="item.uploaderName" data-user-content>{{ item.uploaderName }}</span>
                    <span>{{ relTime(new Date(item.createdAt).toISOString()) }}</span>
                    <span v-if="item.size !== null">{{ fmtBytes(item.size) }}</span>
                  </span>
                </span>
              </button>
              <span v-if="item.visibility === 'admins'" class="chip-neutral slib-row__tag">
                <v-icon icon="mdi-lock-outline" size="12" />{{ t('spaces.materials.visibilityValue.admins') }}
              </span>
              <AdaptiveMenu v-bind="rowMenu.bind(item.id)" :actions="actionsOf(item)" :title="item.name">
                <template #activator="{ props: menuProps }">
                  <BaseButton
                    v-bind="menuProps"
                    icon="mdi-dots-horizontal"
                    size="sm"
                    class="tap-target"
                    :aria-label="t('spaces.materials.actionsOf', { name: item.name })"
                  />
                </template>
              </AdaptiveMenu>
            </div>
          </li>
        </ul>

        <BaseEmptyState
          v-else-if="materials.length"
          size="inline"
          align="center"
          class="slib__empty"
          :title="t('spaces.materials.noMatch')"
          :action="t('spaces.materials.clearFilters')"
          @action="clearFilters"
        />
        <BaseEmptyState v-else size="inline" align="center" class="slib__empty" :title="t('spaces.materials.empty')" />
      </template>
    </section>

    <!-- The open one: a pane beside the list on desktop, the whole page on a phone. -->
    <section
      v-if="selected"
      ref="detailRef"
      class="slib-detail"
      :class="{ 'slib-detail--phone': !wide }"
      :style="wide ? pane.style.value : undefined"
      data-testid="slib-detail"
    >
      <PaneHandle v-if="wide" :pane="pane" />
      <header class="slib-detail__head">
        <h2 v-if="wide" class="t-title slib-detail__name" data-user-content>{{ selected.name }}</h2>
        <p class="slib-detail__meta t-meta c-muted">
          <span v-if="selected.uploaderName" data-user-content>{{ selected.uploaderName }}</span>
          <span>{{ relTime(new Date(selected.createdAt).toISOString()) }}</span>
          <span v-if="selected.size !== null">{{ fmtBytes(selected.size) }}</span>
          <span v-if="selected.visibility === 'admins'" class="chip-neutral">
            <v-icon icon="mdi-lock-outline" size="12" />{{ t('spaces.materials.visibilityValue.admins') }}
          </span>
        </p>
        <p v-if="canManage" class="slib-detail__meta t-meta c-muted">
          <span>{{ t('spaces.materials.downloads', { n: selected.downloadCount }) }}</span>
          <span v-if="selected.usedByCount !== undefined">{{
            selected.usedByCount > 0
              ? t('spaces.materials.usedBy', { n: selected.usedByCount })
              : t('spaces.materials.unused')
          }}</span>
        </p>
        <div v-if="wide" class="slib-detail__actions">
          <BaseButton kind="primary" size="sm" prepend-icon="mdi-download-outline" @click="emit('download', selected)">
            {{ t('spaces.materials.download') }}
          </BaseButton>
          <BaseButton size="sm" prepend-icon="mdi-close" @click="emit('close')">
            {{ t('spaces.materials.close') }}
          </BaseButton>
        </div>
      </header>
      <div class="slib-detail__preview">
        <FileBytesPreview
          :filename="selected.name"
          :source="`space-material-${selected.id}`"
          :read="read(selected)"
          :convertible="false"
        />
      </div>
      <div v-if="!wide" class="slib-detail__bar">
        <BaseButton kind="primary" block @click="emit('download', selected)">{{
          t('spaces.materials.download')
        }}</BaseButton>
      </div>
    </section>
  </div>

  <ConfirmDialog
    :model-value="uploadOpen"
    :title="t('spaces.materials.upload')"
    :confirm-label="t('spaces.materials.submit')"
    :disabled="!file"
    :loading="busy"
    @update:model-value="(open: boolean) => emit('update:uploadOpen', open)"
    @confirm="submitUpload"
  >
    <div class="slib-upload">
      <v-file-input
        v-model="pickedFile"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        show-size
        prepend-icon=""
        prepend-inner-icon="mdi-paperclip"
        :label="t('spaces.materials.file')"
        data-testid="slib-file"
      />
      <v-radio-group v-model="visibility" :label="t('spaces.materials.visibility')" hide-details density="compact">
        <v-radio v-for="v in VISIBILITIES" :key="v" :value="v">
          <template #label>
            <span class="slib-upload__option">
              <span>{{ t(`spaces.materials.visibilityValue.${v}`) }}</span>
              <span class="t-meta c-muted">{{ t(`spaces.materials.hint.${v}`) }}</span>
            </span>
          </template>
        </v-radio>
      </v-radio-group>
    </div>
  </ConfirmDialog>

  <ConfirmDialog
    :model-value="removing !== null"
    :title="t('spaces.materials.removeTitle', { name: removing?.name ?? '' })"
    :confirm-label="t('spaces.materials.remove')"
    danger
    @update:model-value="(open: boolean) => !open && (removing = null)"
    @confirm="confirmRemove"
  >
    {{
      removing?.usedByCount
        ? t('spaces.materials.removeBodyUsed', { n: removing.usedByCount })
        : t('spaces.materials.removeBody')
    }}
  </ConfirmDialog>
</template>

<style scoped>
.slib {
  display: flex;
  gap: 0;
  align-items: flex-start;
  width: min(var(--page-w), 100%);
  margin: 0 auto;
}

.slib--split {
  width: 100%;
}

.slib__list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 8px 16px 48px;
}

.slib__tools {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.slib__search {
  flex: 1 1 240px;
  min-width: 0;
}

.slib__kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
}

.slib__kind {
  flex: none;
  height: 28px;
  padding: 0 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.slib__kind:hover {
  background: var(--fill);
}

.slib__kind[aria-pressed='true'] {
  color: var(--surface);
  background: var(--ink);
  border-color: var(--ink);
}

.slib__rows {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.slib__empty {
  padding: 32px 0;
}

.slib-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding-right: 4px;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.slib-row:hover {
  background: var(--fill);
}

.slib-row--on {
  background: var(--line-2);
}

.slib-row__open {
  display: flex;
  flex: 1 1 auto;
  gap: 12px;
  align-items: center;
  min-width: 0;
  min-height: 56px;
  padding: 8px 12px;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.slib-row__icon {
  flex: none;
  color: var(--muted);
}

.slib-row__id {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.slib-row__name {
  color: var(--ink);
  overflow-wrap: anywhere;
}

.slib-row__meta,
.slib-detail__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  min-width: 0;
}

.slib-row__tag {
  flex: none;
}

/* Desktop: a pane beside the list that stays in view while the list scrolls. */
.slib-detail {
  position: sticky;
  top: 0;
  display: flex;
  flex: none;
  flex-direction: column;
  width: 50%;
  min-width: min(480px, 100%);
  height: calc(100dvh - 120px);
  min-height: 360px;
  border-left: 1px solid var(--line);
}

.slib-detail--phone {
  position: static;
  flex: 1 1 auto;
  width: 100%;
  height: calc(100dvh - 120px);
  border-left: 0;
}

.slib-detail__head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 4px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--line);
}

.slib-detail--phone .slib-detail__head {
  padding: 10px 16px;
}

.slib-detail__name {
  margin: 0;
  overflow-wrap: anywhere;
}

.slib-detail__meta {
  margin: 0;
}

.slib-detail__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0 0 -8px;
}

.slib-detail__preview {
  flex: 1 1 auto;
  min-height: 0;
  background: var(--canvas);
}

.slib-detail__bar {
  flex: none;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  background: var(--surface);
  border-top: 1px solid var(--line);
}

.slib-upload {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}

.slib-upload__option {
  display: flex;
  flex-direction: column;
}

@media (pointer: coarse) {
  .slib__kinds {
    gap: 16px;
  }

  .slib__kind {
    position: relative;
  }

  .slib__kind::before {
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    content: '';
    transform: translate(-50%, -50%);
  }

  .slib__search :deep(.v-field) {
    min-height: 44px;
  }
}
</style>
