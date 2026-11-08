<script setup lang="ts">
// 资料库文档「⋯」里的分享、移动、删除：三张框和它们背后的动作，和资料库那一页用的是
// 同一套（`useDocumentShare` / `useDocumentMove` / `useLibraryDocuments` 的删除，删除前
// 问的是同一句）。打开文档的地方拿着 ref 叫 `share` / `move` / `remove`；删掉、挪走之后
// 告诉它，关哪一格、去哪儿由它定。
//
// 调用点：工作面板自由区里的资料库文档页签（`components/WorkPanel.vue`）。
import type { RouteLocationRaw } from 'vue-router'

import { ref } from 'vue'

import { useDocumentMove } from '../../composables/useDocumentMove'
import { useDocumentShare } from '../../composables/useDocumentShare'
import { useLibraryDocuments } from '../../composables/useLibraryDocuments'
import DocMoveDialog from './DocMoveDialog.vue'
import DocShareDialog from './DocShareDialog.vue'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
import { useDialog } from '@/plugins/dialog'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{
  /** 删掉了：这份文档已经不在了。 */
  removed: [documentId: string]
  /** 挪走了：`to` 是它的新地址。 */
  moved: [documentId: string, to: RouteLocationRaw]
}>()

const dialog = useDialog()
// 只用它的删除；不找，所以搜索词恒为空。
const docs = useLibraryDocuments(
  () => props.projectId,
  () => ''
)
const mover = useDocumentMove((to) => emit('moved', mover.state.documentId, to))
const sharer = useDocumentShare()

const confirming = ref<{ id: string; title: string } | null>(null)
const removing = ref(false)

async function remove() {
  const doc = confirming.value
  if (!doc) return
  removing.value = true
  try {
    await docs.remove(doc.id)
    confirming.value = null
    emit('removed', doc.id)
  } catch (e) {
    confirming.value = null
    dialog.alert(e instanceof Error && e.message ? e.message : t('work.library.deleteFailed'), {
      title: t('work.library.deleteFailed'),
    })
  } finally {
    removing.value = false
  }
}

defineExpose({
  share: (documentId: string) => sharer.open(documentId),
  move: (documentId: string) => mover.open(documentId),
  /** 先问一句，答应了才删。 */
  remove: (doc: { id: string; title: string }) => {
    confirming.value = doc
  },
})
</script>

<template>
  <ConfirmDialog
    :model-value="!!confirming"
    :title="t('work.library.deleteTitle', { name: confirming?.title || t('work.room.doc.untitled') })"
    :confirm-label="t('work.library.delete')"
    :loading="removing"
    danger
    @update:model-value="confirming = null"
    @confirm="remove"
  >
    {{ t('work.library.deleteDocBody') }}
  </ConfirmDialog>
  <DocMoveDialog
    v-model="mover.state.open"
    :title="mover.state.title"
    :places="mover.places.value"
    :current="mover.state.current"
    :saving="mover.state.saving"
    :error="mover.state.error"
    @move="mover.move"
  />
  <DocShareDialog
    v-model="sharer.state.open"
    :title="sharer.state.title"
    :loading="sharer.state.loading"
    :may-share="sharer.state.mayShare"
    :url="sharer.url.value"
    :busy="sharer.state.busy"
    :error="sharer.state.error"
    @share="sharer.share"
    @unshare="sharer.unshare"
    @copy="sharer.copy"
  />
</template>
