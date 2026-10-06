<template>
  <AnnouncementsView
    v-model:open="editing"
    :current="current"
    :expired="expired"
    :loaded="loaded"
    :is-manager="isManager"
    :submitting="submitting"
    :notify-count="notifyCount"
    @submit="submitAnnouncement"
    @pin="togglePin"
    @remove="remove"
  />
</template>

<script setup lang="ts">
// 空间公告这一页的容器：读公告、发布/改/删/置顶的调用、删前确认。画面在
// `AnnouncementsView.vue`（场景规则见 docs/manual/dev/scenes.md）。
//
// 空间里的每个人都看得见这一页；发、改、删、置顶只有所有者与管理员能做，成员打开这一
// 页是纯读的，操作按钮一个都不出现。每条公告是服务端的一行，各有各的地址
// （`SpacesApi.*Announcement`）：改一条只写那一条。
import type { SpaceAnnouncement } from '@/types'
import type { AnnouncementDraft } from './AnnouncementsView.vue'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import AnnouncementsView from './AnnouncementsView.vue'
import { useSpaceAnnouncements } from './useSpaceAnnouncements'

import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const dialog = useDialog()
const spaceStore = useSpaceStore()
const { isManager } = storeToRefs(spaceStore)

const spaceId = () => Number(route.params.spaceId)
// 谁是管理员是拿登录的人跟空间的管理员名单对出来的。
useSpaceData().fetchSpace(spaceId())

const { current, expired, notifyCount, loaded, reload } = useSpaceAnnouncements(spaceId)

const editing = ref(false)
const submitting = ref(false)

async function submitAnnouncement(draft: AnnouncementDraft) {
  submitting.value = true
  try {
    if (draft.id === null) {
      await SpacesApi.publishAnnouncement(spaceId(), {
        title: draft.title,
        content: draft.content,
        pinned: draft.pinned,
        expiresAt: draft.expiresAt,
      })
      toast.success(t('spaces.announcements.toast.published'))
    } else {
      await SpacesApi.updateAnnouncement(spaceId(), draft.id, {
        title: draft.title,
        content: draft.content,
        expiresAt: draft.expiresAt,
      })
      toast.success(t('spaces.announcements.toast.saved'))
    }
    editing.value = false
    await reload()
  } catch {
    toast.error(
      t(draft.id === null ? 'spaces.announcements.toast.publishFailed' : 'spaces.announcements.toast.saveFailed')
    )
  } finally {
    submitting.value = false
  }
}

/** 置顶是卡片上单独一个动作：只改这一条的 `pinned`。 */
async function togglePin(a: SpaceAnnouncement) {
  const next = !a.pinned
  try {
    await SpacesApi.updateAnnouncement(spaceId(), a.id, { pinned: next })
    toast.success(t(next ? 'spaces.announcements.toast.pinned' : 'spaces.announcements.toast.unpinned'))
    await reload()
  } catch {
    toast.error(t(next ? 'spaces.announcements.toast.pinFailed' : 'spaces.announcements.toast.unpinFailed'))
  }
}

async function remove(a: SpaceAnnouncement) {
  const ok = await dialog
    .confirm(t('spaces.announcements.confirmDelete'), {
      confirmLabel: t('spaces.announcements.delete'),
      danger: true,
    })
    .wait()
  if (!ok) return
  try {
    await SpacesApi.deleteAnnouncement(spaceId(), a.id)
    toast.success(t('spaces.announcements.toast.deleted'))
    await reload()
  } catch {
    toast.error(t('spaces.announcements.toast.deleteFailed'))
  }
}
</script>
