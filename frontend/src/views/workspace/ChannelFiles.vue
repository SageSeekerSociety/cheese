<script setup lang="ts">
// 一个频道自己的文件这一页（`/projects/:projectId/channels/:topicId/files`）。
//
// 取这一页的数据（列一遍房间的文件区、读一份的字节、拿去下载），画法在
// `ChannelFilesView`。取数的三条路都经过这里，是因为它们都要 topicId，而地址上
// 给的是频道的短名或编号——`addressProps` 换成 UUID 再交下来。
import type { RoomFile } from '@/api/roomFiles'

import { ref, watch } from 'vue'

import ChannelFilesView from './ChannelFilesView.vue'

import { attachmentRawUrl, downloadFile, listRoomFiles, previewFileBytes } from '@/api/roomFiles'

const props = defineProps<{ projectId: string; topicId: string }>()

const files = ref<RoomFile[]>([])
const truncated = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const listed = await listRoomFiles(props.topicId)
    files.value = listed.data
    truncated.value = listed.truncated
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.topicId,
  () => void load(),
  { immediate: true }
)

/** 一份的字节。`asPdf` 说的是这份格式浏览器画不了，要服务端先转成 PDF。 */
function read(file: RoomFile, asPdf: boolean): Promise<ArrayBuffer> {
  // 房间文件这一个库里没有「转换」这条路（转换是交付产物那条链上的），所以 `asPdf`
  // 在这里没有用武之地：调用方按后缀要它，但要不到，照原样读回去。
  void asPdf
  return previewFileBytes(props.topicId, file.path)
}

function download(file: RoomFile) {
  void downloadFile(attachmentRawUrl(props.topicId, file.path), file.path.split('/').pop() ?? file.path)
}
</script>

<template>
  <ChannelFilesView
    :files="files"
    :truncated="truncated"
    :loading="loading"
    :error="error"
    :read="read"
    @retry="load"
    @download="download"
  />
</template>
