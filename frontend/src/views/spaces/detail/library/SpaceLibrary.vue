<template>
  <SpaceLibraryView
    v-model:upload-open="uploadOpen"
    :materials="items"
    :can-manage="canManage"
    :loading="loading"
    :failed="failed"
    :error="errorDetail"
    :selected="selected"
    :wide="mdAndUp"
    :busy="busy"
    :read="read"
    @open="open"
    @close="close"
    @retry="refresh"
    @download="download"
    @upload="upload"
    @change-visibility="changeVisibility"
    @remove="remove"
  />
</template>

<script setup lang="ts">
// 空间资料库的容器：取清单、判能不能管、开哪一份记在地址上（`?file=`），传、改档、
// 撤下、下载都在这里。画面在 `SpaceLibraryView.vue`。
//
// 这一页不碰素材的公开 url：清单里刻意没有它（那是一条公开可猜的 `/uploads/…` 路径，
// 交出去「仅管理员」就只剩一个标签），预览和下载都走判权限的那条路由。预览带
// `preview`，看一眼不记一次下载。
import type { SpaceMaterial, SpaceMaterialVisibility } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import SpaceLibraryView from './SpaceLibraryView.vue'

import { SpacesApi } from '@/api/spaces'
import { useTopBarBack } from '@/components/common/topBarBack'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { mdAndUp } = useDisplay()
const spaceId = Number(route.params.spaceId)

const items = ref<SpaceMaterial[]>([])
/** 能不能传、改档、撤下。服务端在清单里给，不在这里按身份猜。 */
const canManage = ref(false)
const loading = ref(false)
const busy = ref(false)
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const uploadOpen = ref(false)

const selected = computed(() => {
  const id = Number(route.query.file)
  return items.value.find((item) => item.id === id) ?? null
})

function open(item: SpaceMaterial) {
  void router.replace({ query: { ...route.query, file: String(item.id) } })
}
function close() {
  const next = { ...route.query }
  delete next.file
  void router.replace({ query: next })
}

useTopBarBack(() =>
  !mdAndUp.value && selected.value ? { label: t('spaces.materials.backToList'), onBack: close } : null
)

async function refresh() {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const res = await SpacesApi.listMaterials(spaceId)
    items.value = res.materials ?? []
    canManage.value = res.canManage === true
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

function read(item: SpaceMaterial) {
  return async () => (await SpacesApi.downloadMaterial(spaceId, item.id, true)).arrayBuffer()
}

async function upload(payload: { file: File; visibility: SpaceMaterialVisibility }) {
  if (busy.value) return
  busy.value = true
  try {
    await SpacesApi.uploadMaterial(spaceId, payload.file, payload.visibility)
    uploadOpen.value = false
    await refresh()
    toast.success(t('spaces.materials.toast.uploaded'))
  } catch {
    toast.error(t('spaces.materials.toast.uploadFailed'))
  } finally {
    busy.value = false
  }
}

async function changeVisibility(item: SpaceMaterial, visibility: SpaceMaterialVisibility) {
  if (busy.value) return
  busy.value = true
  try {
    await SpacesApi.updateMaterialVisibility(spaceId, item.id, visibility)
    await refresh()
    toast.success(t(`spaces.materials.toast.madeVisible.${visibility}`))
  } catch {
    toast.error(t('spaces.materials.toast.visibilityFailed'))
  } finally {
    busy.value = false
  }
}

async function remove(item: SpaceMaterial) {
  if (busy.value) return
  busy.value = true
  try {
    await SpacesApi.deleteMaterial(spaceId, item.id)
    if (selected.value?.id === item.id) close()
    await refresh()
    toast.success(t('spaces.materials.toast.removed'))
  } catch {
    toast.error(t('spaces.materials.toast.removeFailed'))
  } finally {
    busy.value = false
  }
}

/** 把取回来的字节存成文件，名字用资料自己的名字。 */
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
  try {
    saveBlob(await SpacesApi.downloadMaterial(spaceId, item.id), item.name)
  } catch {
    toast.error(t('spaces.materials.toast.downloadFailed'))
  }
}
</script>
