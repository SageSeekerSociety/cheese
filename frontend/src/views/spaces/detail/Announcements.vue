<script setup lang="ts">
// 空间公告。**空间里的每个人都看得见**这一页；发、改、删、置顶只有所有者与管理员
// 能做，成员打开这一页是纯读的，操作按钮一个都不出现。
//
// 每条公告是服务端的一行，各有各的地址（`SpacesApi.*Announcement`）：改一条只写那
// 一条，两位管理员同时改两条互不覆盖。当前的在前（置顶的在最前），过了到期日的收进
// 最底下「已到期」那一行，点开才列出来 —— 哪条到期了由服务端答。
//
// 发布时通知空间里的其他人（站内动态，不发邮件），之后改不再通知；删掉一条，它发
// 出去的通知一起撤回。
import type { SpaceAnnouncement } from '@/types'

import { computed, defineAsyncComponent, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { dayOfExpiry, expiryFromDay } from '../model'

import AnnouncementCard from './AnnouncementCard.vue'
import { useSpaceAnnouncements } from './useSpaceAnnouncements'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

const { t } = useI18n()
const route = useRoute()
const dialog = useDialog()
const spaceStore = useSpaceStore()
const { isManager } = storeToRefs(spaceStore)

const spaceId = () => Number(route.params.spaceId)
// 谁是管理员是拿登录的人跟空间的管理员名单对出来的。
useSpaceData().fetchSpace(spaceId())

const { current, expired, notifyCount, loaded, reload } = useSpaceAnnouncements(spaceId)

const showExpired = ref(false)

const editing = ref(false)
/** 正在改的那一条；发布新公告时为空。 */
const editingId = ref<number | null>(null)
const draftTitle = ref('')
const draftContent = ref('')
const draftPinned = ref(false)
/** `<input type="date">` 的值，`YYYY-MM-DD`；空 = 不到期。 */
const draftExpiry = ref('')
const submitting = ref(false)

function openCreate() {
  editingId.value = null
  draftTitle.value = ''
  draftContent.value = ''
  draftPinned.value = false
  draftExpiry.value = ''
  editing.value = true
}

function openEdit(a: SpaceAnnouncement) {
  editingId.value = a.id
  draftTitle.value = a.title
  draftContent.value = a.content
  draftExpiry.value = a.expiresAt === null ? '' : dayOfExpiry(a.expiresAt)
  editing.value = true
}

/** 今天之前的日子不能选：发出去就已经到期的公告没有意义。 */
const earliestExpiry = dayOfExpiry(Date.now() + 1)

const notifyHint = computed(() =>
  notifyCount.value ? t('spaces.announcements.form.notifyHint', { count: notifyCount.value }) : ''
)

async function submit() {
  const title = draftTitle.value.trim()
  if (!title) return
  const expiresAt = draftExpiry.value ? expiryFromDay(draftExpiry.value) : null
  submitting.value = true
  try {
    if (editingId.value === null) {
      await SpacesApi.publishAnnouncement(spaceId(), {
        title,
        content: draftContent.value,
        pinned: draftPinned.value,
        expiresAt,
      })
      toast.success(t('spaces.announcements.toast.published'))
    } else {
      await SpacesApi.updateAnnouncement(spaceId(), editingId.value, {
        title,
        content: draftContent.value,
        expiresAt,
      })
      toast.success(t('spaces.announcements.toast.saved'))
    }
    editing.value = false
    await reload()
  } catch {
    toast.error(
      t(editingId.value === null ? 'spaces.announcements.toast.publishFailed' : 'spaces.announcements.toast.saveFailed')
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

<template>
  <PageHeader :title="t('spaces.detail.announcements')" show-on-mobile>
    <!-- 插槽本身不能带 v-if：PageHeader 只在挂上那一刻看有没有操作区插槽，空间读回来、
         知道你是管理员时它已经不再看了。条件放在按钮上。 -->
    <template #actions>
      <BaseButton v-if="isManager" kind="primary" @click="openCreate">
        {{ t('spaces.announcements.publish') }}
      </BaseButton>
    </template>
  </PageHeader>
  <div class="ann">
    <AnnouncementCard
      v-for="a in current"
      :key="a.id"
      :announcement="a"
      :manager="isManager"
      @pin="togglePin(a)"
      @edit="openEdit(a)"
      @remove="remove(a)"
    />

    <button
      v-if="expired.length"
      type="button"
      class="ann__fold t-body"
      :aria-expanded="showExpired"
      @click="showExpired = !showExpired"
    >
      {{ t('spaces.announcements.expiredFold', { count: expired.length }) }}
      <v-icon :icon="showExpired ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="18" class="ann__fold-icon" />
    </button>

    <template v-if="showExpired">
      <AnnouncementCard
        v-for="a in expired"
        :key="a.id"
        :announcement="a"
        expired
        :manager="isManager"
        @edit="openEdit(a)"
        @remove="remove(a)"
      />
    </template>

    <p v-if="loaded && !current.length && !expired.length" class="ann__empty t-body">
      {{ t('spaces.announcements.empty') }}
    </p>

    <AdaptiveDialog
      v-model="editing"
      :title="t(editingId === null ? 'spaces.announcements.publish' : 'spaces.announcements.editTitle')"
      :primary-label="t(editingId === null ? 'spaces.announcements.form.publish' : 'spaces.announcements.form.save')"
      :primary-loading="submitting"
      :primary-disabled="!draftTitle.trim()"
      @primary="submit"
    >
      <v-text-field
        v-model="draftTitle"
        autocomplete="off"
        :label="t('spaces.announcements.form.title')"
        variant="outlined"
        density="comfortable"
        maxlength="255"
        :counter="255"
        persistent-counter
      />
      <TipTapEditor v-model="draftContent" output="html" :aria-label="t('spaces.announcements.form.content')" />
      <div class="ann__opts">
        <!-- Pinning is only chosen while posting; a published announcement uses the card's own action. -->
        <v-checkbox
          v-if="editingId === null"
          v-model="draftPinned"
          :label="t('spaces.announcements.form.pinned')"
          density="comfortable"
          hide-details
          color="primary"
        />
        <v-text-field
          v-model="draftExpiry"
          type="date"
          :label="t('spaces.announcements.form.expiry')"
          variant="outlined"
          density="comfortable"
          hide-details
          clearable
          :min="earliestExpiry"
          class="ann__expiry"
        />
      </div>
      <p v-if="editingId === null && notifyHint" class="ann__hint">{{ notifyHint }}</p>
    </AdaptiveDialog>
  </div>
</template>

<style scoped>
.ann {
  display: flex;
  flex-direction: column;
  gap: 10px;
  /* 宽屏下封顶居中了，不再左贴：以前 max-width 之外没有 auto，右边会空出一条
     随窗口变宽的边。 */
  max-width: 880px;
  margin-inline: auto;
  padding: 16px;
}

.ann__empty {
  margin: 0;
  color: var(--muted);
}

.ann__fold {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--muted);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.ann__fold:hover {
  background: var(--fill);
}

.ann__fold-icon {
  margin-left: auto;
}

.ann__opts {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  align-items: center;
  margin-top: 16px;
}

.ann__expiry {
  flex: 1;
  min-width: 200px;
}

.ann__hint {
  margin: 16px 0 0;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
