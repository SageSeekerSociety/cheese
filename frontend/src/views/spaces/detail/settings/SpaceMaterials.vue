<script setup lang="ts">
// 资料库：这块板上共用的文件。板上的人看得见、下得下来；只有管理员能传、能改
// 可见范围、能把它从板上拿下来。
//
// 两处刻意的做法：
// - **这一页不碰素材的公开 url。** 后端清单里刻意没有 `url` —— 那是
//   `/uploads/…` 下一条公开可猜的路径，交出去「仅管理员」就只剩一个标签 —— 所以
//   下载一律走 `downloadMaterial` 那条判权限的路由。这一页因而在「仅管理员」档
//   上是真的关着的。
// - 撤下来要点两下，第二下才是真的。它只从这块板上拿掉（素材行与字节都留着，
//   别的题可能还引用着），但学生那边的清单里当场就没了，误触没有回头路。
import type { SpaceMaterial, SpaceMaterialVisibility } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { formatFileSize } from '@/utils/materials'

import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { SpacesApi } from '@/network/api/spaces'

const route = useRoute()
const { t } = useI18n()
const spaceId = Number(route.params.spaceId)

const items = ref<SpaceMaterial[]>([])
/** 能不能传、改档、撤下来。服务端在清单里给 —— 不自己按「我是不是管理员」猜，
 *  那会变成第二份会走样的判据。 */
const canManage = ref(false)
const loading = ref(false)
const busy = ref(false)

/** 上传表单：文件 + 可见范围。两格都是必需的 —— 不定档就不知道该给谁看，
 *  所以不折进「高级选项」。 */
const uploadOpen = ref(false)
const pickedFile = ref<File | File[] | null>(null)
const newVisibility = ref<SpaceMaterialVisibility>('members')

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

async function refresh() {
  loading.value = true
  try {
    const res = await SpacesApi.listMaterials(spaceId)
    items.value = res.data.materials ?? []
    canManage.value = res.data.canManage === true
  } catch {
    toast.error(t('spaces.materials.toast.loadFailed'))
  } finally {
    loading.value = false
    confirmingId.value = null
  }
}

onMounted(refresh)

function closeUpload() {
  uploadOpen.value = false
  pickedFile.value = null
  newVisibility.value = 'members'
}

async function submit() {
  const picked = file.value
  if (busy.value || !picked) return
  busy.value = true
  try {
    await SpacesApi.uploadMaterial(spaceId, picked, newVisibility.value)
    closeUpload()
    await refresh()
    toast.success(t('spaces.materials.toast.uploaded'))
  } catch {
    toast.error(t('spaces.materials.toast.uploadFailed'))
  } finally {
    busy.value = false
  }
}

async function changeVisibility(item: SpaceMaterial, visibility: SpaceMaterialVisibility) {
  if (busy.value || visibility === item.visibility) return
  busy.value = true
  try {
    await SpacesApi.updateMaterialVisibility(spaceId, item.id, visibility)
    await refresh()
    toast.success(t('spaces.materials.toast.visibilityUpdated'))
  } catch {
    toast.error(t('spaces.materials.toast.visibilityFailed'))
  } finally {
    busy.value = false
  }
}

async function remove(item: SpaceMaterial) {
  if (busy.value) return
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    return
  }
  busy.value = true
  try {
    await SpacesApi.deleteMaterial(spaceId, item.id)
    await refresh()
    toast.success(t('spaces.materials.toast.removed'))
  } catch {
    toast.error(t('spaces.materials.toast.removeFailed'))
  } finally {
    busy.value = false
  }
}

/** 把取回来的字节存成文件。名字用素材自己的名字，不猜 MIME。 */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function download(item: SpaceMaterial) {
  if (busy.value) return
  busy.value = true
  try {
    saveBlob(await SpacesApi.downloadMaterial(spaceId, item.id), item.name)
  } catch {
    toast.error(t('spaces.materials.toast.downloadFailed'))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <SettingsToolbar>
    <v-btn
      v-if="canManage"
      variant="text"
      prepend-icon="mdi-upload"
      @click="(uploadOpen = true), (confirmingId = null)"
    >
      {{ t('spaces.materials.upload') }}
    </v-btn>
  </SettingsToolbar>

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
          v-model="newVisibility"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :items="VISIBILITIES.map((v) => ({ value: v, title: t(`spaces.materials.visibilityValue.${v}`) }))"
          :label="t('spaces.materials.visibility')"
          class="form-row__vis"
        />
      </div>
      <p class="form__hint">{{ t(`spaces.materials.hint.${newVisibility}`) }}</p>
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
      <v-list v-if="items.length > 0" class="settings-list" bg-color="transparent">
        <v-list-item v-for="item in items" :key="item.id">
          <v-list-item-title class="mat__head">
            <v-icon size="18" class="mat__icon">{{ iconOf(item) }}</v-icon>
            <span class="mat__name">{{ item.name }}</span>
          </v-list-item-title>

          <div class="mat__meta">
            <span>{{ t(`spaces.materials.type.${item.type}`, item.type) }}</span>
            <span v-if="item.size !== null">{{ formatFileSize(item.size) }}</span>
            <span>{{ t('spaces.materials.uploadedOn', { date: uploadedOn(item.createdAt) }) }}</span>
            <span>{{ t('spaces.materials.downloads', { n: item.downloadCount }) }}</span>
          </div>

          <template #append>
            <div class="mat__actions">
              <v-btn size="small" variant="text" :disabled="busy" @click="download(item)">
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
                      @click="changeVisibility(item, v)"
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
                  @click="remove(item)"
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
