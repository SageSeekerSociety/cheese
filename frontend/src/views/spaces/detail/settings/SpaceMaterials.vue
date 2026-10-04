<template>
  <BaseLoadError v-if="failed" :title="t('spaces.materials.toast.loadFailed')" :error="errorDetail" @retry="refresh" />
  <SpaceMaterialsView
    v-else
    v-model:upload-open="uploadOpen"
    :materials="items"
    :can-manage="canManage"
    :loading="loading"
    :busy="busy"
    @upload="upload"
    @download="download"
    @change-visibility="changeVisibility"
    @remove="remove"
  />
</template>

<script setup lang="ts">
// 资料库这一栏的容器：读地址、取数、判权限、传与撤。画面在 `SpaceMaterialsView.vue`。
//
// 两处刻意的做法：
// - **这一页不碰素材的公开 url。** 后端清单里刻意没有 `url` —— 那是
//   `/uploads/…` 下一条公开可猜的路径，交出去「仅管理员」就只剩一个标签 —— 所以
//   下载一律走 `downloadMaterial` 那条判权限的路由。这一页因而在「仅管理员」档
//   上是真的关着的。
// - 上传表单的开合在这里：传成功才收起来，失败留着，人不用重新选一遍文件。
//   选中的文件与可见范围在视图里，表单收起来再打开时它们自己重来。
import type { SpaceMaterial, SpaceMaterialVisibility } from '@/types'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'

import SpaceMaterialsView from './SpaceMaterialsView.vue'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
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
// 读失败和「资料库是空的」是两件事：失败替换掉整块，空状态才交给它自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)

/** 上传表单开着没有。表单里的文件和可见范围在视图那边。 */
const uploadOpen = ref(false)

async function refresh() {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const res = await SpacesApi.listMaterials(spaceId)
    items.value = res.data.materials ?? []
    canManage.value = res.data.canManage === true
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

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
    toast.success(t('spaces.materials.toast.visibilityUpdated'))
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
