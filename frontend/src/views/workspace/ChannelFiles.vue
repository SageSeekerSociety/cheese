<script setup lang="ts">
// 一个频道自己的文件这一页（`/projects/:projectId/channels/:topicId/files`）。
//
// 取这一页的数据（列一遍房间的文件区、读一份的字节、拿去下载），画法在
// `ChannelFilesView`。取数的三条路都经过这里，是因为它们都要 topicId，而地址上
// 给的是频道的短名或编号——`addressProps` 换成 UUID 再交下来。
import type { RoomFile } from '@/api/roomFiles'
import type { TopicMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import ChannelFilesView from './ChannelFilesView.vue'

import { attachmentRawUrl, downloadFile, listRoomFiles, previewFileBytes } from '@/api/roomFiles'
import { agentNames, memberName } from '@/lib/agentNames'
import { queryClient } from '@/query/client'
import { roomMembersQuery } from '@/query/room'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string; topicId: string }>()

const store = useWorkspaceStore()

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

/**
 * 这一页上署名的规矩：每份文件的作者只存着 handle，名字在这里换出来。房间名册先答
 * （AI 队友的座位只在房间名册上，项目名册上那行共用的不是它），项目名册补上不在这间
 * 房里的队友，和对话栏署名同一个出处（`agentNames`）。
 */
const roster = useQuery(computed(() => roomMembersQuery(props.topicId)), queryClient)
const roomMembers = computed<TopicMemberRow[]>(() => roster.data.value?.data ?? [])
const memberNames = computed<Record<string, string>>(() => ({
  ...Object.fromEntries(roomMembers.value.map((m) => [m.member_handle, memberName(m) || m.member_handle])),
  ...Object.fromEntries(agentNames(roomMembers.value, store.members)),
}))

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
    :member-names="memberNames"
    :truncated="truncated"
    :loading="loading"
    :error="error"
    :read="read"
    @retry="load"
    @download="download"
  />
</template>
