<script setup lang="ts">
// 资料库这一栏的画面：清单、上传表单、每行的操作。取数、判权限、下载都在同目录的
// `SpaceMaterials.vue`（容器）里，这里只吃 props、只发事件。
import type { SpaceMaterial, SpaceMaterialVisibility } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { formatFileSize } from '@/utils/materials'

import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'

defineProps<{
  materials: SpaceMaterial[]
  canManage: boolean
  loading: boolean
  busy: boolean
  uploadOpen: boolean
}>()

const emit = defineEmits<{
  'update:uploadOpen': [open: boolean]
  upload: [payload: { file: File; visibility: SpaceMaterialVisibility }]
  download: [item: SpaceMaterial]
  changeVisibility: [item: SpaceMaterial, visibility: SpaceMaterialVisibility]
  remove: [item: SpaceMaterial]
}>()

const { t } = useI18n()

/** 上传表单：文件 + 可见范围。两格都是必需的 —— 不定档就不知道该给谁看，
 *  所以不折进「高级选项」。 */
const pickedFile = ref<File | File[] | null>(null)
const visibility = ref<SpaceMaterialVisibility>('members')

/** 已经点过一下移除、正在等第二下的那一行。 */
const confirmingId = ref<number | null>(null)

const VISIBILITIES: SpaceMaterialVisibility[] = ['members', 'admins']

const ICONS: Record<string, string> = {
  image: 'mdi-file-image-outline',
  video: 'mdi-file-video-outline',
  audio: 'mdi-file-music-outline',
  file: 'mdi-file-outline',
}

/** `v-file-input` 单文件时也允许是数组，取第一个。 */
const file = computed<File | null>(() =>
  Array.isArray(pickedFile.value) ? pickedFile.value[0] ?? null : pickedFile.value
)

function iconOf(item: SpaceMaterial) {
  return ICONS[item.type] ?? ICONS.file
}

function uploadedOn(ms: number) {
  return dayjs(ms).format('YYYY-MM-DD')
}

/** 表单每次打开都从干净的一格开始：上一次传成功时表单是被外面收起来的，
 *  这里的文件没清过。 */
function openUpload() {
  pickedFile.value = null
  visibility.value = 'members'
  confirmingId.value = null
  emit('update:uploadOpen', true)
}

function closeUpload() {
  pickedFile.value = null
  visibility.value = 'members'
  emit('update:uploadOpen', false)
}

function submit() {
  const picked = file.value
  if (!picked) return
  emit('upload', { file: picked, visibility: visibility.value })
}

/** 要两下才是真的：头一下只是把这一行切成「确认撤下」。 */
function onRemove(item: SpaceMaterial) {
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    return
  }
  confirmingId.value = null
  emit('remove', item)
}

function onVisibility(item: SpaceMaterial, picked: SpaceMaterialVisibility) {
  if (picked === item.visibility) return
  emit('changeVisibility', item, picked)
}
</script>

<template>
  <SettingsToolbar>
    <v-btn v-if="canManage" variant="text" prepend-icon="mdi-plus" @click="openUpload">
      {{ t('spaces.materials.upload') }}
    </v-btn>
  </SettingsToolbar>

  <p class="settings-page__lede materials__lede">{{ t('spaces.materials.intro') }}</p>

  <div class="materials">
    <div v-if="uploadOpen" class="settings-card form">
      <div class="form__title">{{ t('spaces.materials.uploadTitle') }}</div>
      <v-file-input
        v-model="pickedFile"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        show-size
        :label="t('spaces.materials.pickFile')"
      />
      <div class="form-row mt-3">
        <v-select
          v-model="visibility"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :items="VISIBILITIES.map((v) => ({ value: v, title: t(`spaces.materials.visibilityValue.${v}`) }))"
          :label="t('spaces.materials.visibility')"
          class="form-row__vis"
        />
      </div>
      <p class="form__hint">{{ t(`spaces.materials.hint.${visibility}`) }}</p>
      <div class="form__actions">
        <v-btn variant="text" :disabled="busy" @click="closeUpload">
          {{ t('spaces.materials.cancel') }}
        </v-btn>
        <v-btn color="primary" variant="flat" :loading="busy" :disabled="!file" @click="submit">
          {{ t('spaces.materials.upload') }}
        </v-btn>
      </div>
    </div>

    <div v-if="loading" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <div v-else class="settings-card">
      <v-list v-if="materials.length > 0" class="settings-list" bg-color="transparent">
        <v-list-item v-for="item in materials" :key="item.id">
          <v-list-item-title class="mat__head">
            <v-icon size="18" class="mat__icon">{{ iconOf(item) }}</v-icon>
            <span class="mat__name">{{ item.name }}</span>
          </v-list-item-title>

          <div class="mat__meta">
            <span>{{ t(`spaces.materials.type.${item.type}`, item.type) }}</span>
            <span v-if="item.size !== null">{{ formatFileSize(item.size) }}</span>
            <span>{{ t('spaces.materials.uploadedOn', { date: uploadedOn(item.createdAt) }) }}</span>
            <span>{{ t('spaces.materials.downloads', { n: item.downloadCount }) }}</span>
            <!-- 主判据是 `canManage`：成员那一侧不该出现「未被引用」这种话，那会
                 读成「他也能删」。`!== undefined` 是保险，不是判据 —— 服务端一定
                 给能管的人补这个键（`spaces_materials.py` 那行 `counts.get(id, 0)`），
                 所以它今天永远为真；留着是防后端哪天不补了，那时宁可这一格不出现，
                 也不要拿 `undefined > 0` 落成一句「未被引用」——那是一句假话。 -->
            <span v-if="canManage && item.usedByCount !== undefined">
              {{
                item.usedByCount > 0
                  ? t('spaces.materials.usedBy', { n: item.usedByCount })
                  : t('spaces.materials.unused')
              }}
            </span>
          </div>

          <template #append>
            <div class="mat__actions">
              <v-btn size="small" variant="text" :disabled="busy" @click="emit('download', item)">
                {{ t('spaces.materials.download') }}
              </v-btn>

              <template v-if="canManage">
                <!-- 可见范围就地改：只有两档，改完这一行就是新档位，不再多一步确认。 -->
                <v-menu location="bottom end">
                  <template #activator="{ props: menuProps }">
                    <v-btn
                      v-bind="menuProps"
                      size="small"
                      variant="text"
                      append-icon="mdi-chevron-down"
                      :disabled="busy"
                    >
                      {{ t(`spaces.materials.visibilityValue.${item.visibility}`) }}
                    </v-btn>
                  </template>
                  <v-list density="compact">
                    <v-list-item
                      v-for="v in VISIBILITIES"
                      :key="v"
                      :active="v === item.visibility"
                      @click="onVisibility(item, v)"
                    >
                      <v-list-item-title>{{ t(`spaces.materials.visibilityValue.${v}`) }}</v-list-item-title>
                    </v-list-item>
                  </v-list>
                </v-menu>

                <v-btn
                  size="small"
                  :variant="confirmingId === item.id ? 'flat' : 'text'"
                  :color="confirmingId === item.id ? 'error' : undefined"
                  :disabled="busy"
                  @click="onRemove(item)"
                >
                  {{ confirmingId === item.id ? t('spaces.materials.confirmRemove') : t('spaces.materials.remove') }}
                </v-btn>
              </template>
            </div>
          </template>
        </v-list-item>
      </v-list>

      <p v-else class="settings-empty">{{ t('spaces.materials.empty') }}</p>
    </div>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.materials__lede {
  margin: 0 0 12px;
}

.materials {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form {
  padding: 16px 24px;
}

.form__title {
  margin-bottom: 12px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.form__hint {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.form__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 12px;
}

.form-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.form-row__vis {
  max-width: 240px;
}

.mat__head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.mat__icon {
  color: var(--muted);
}

.mat__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mat__meta {
  display: flex;
  flex-wrap: wrap;
  margin-top: 2px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.mat__meta > span + span::before {
  margin: 0 6px;
  color: var(--faint);
  content: '·';
}

.mat__actions {
  display: flex;
  gap: 4px;
  align-items: center;
}
</style>
