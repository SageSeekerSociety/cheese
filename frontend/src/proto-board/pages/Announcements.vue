<script setup lang="ts">
// 空间公告。真平台上这一屏早就存在（侧栏那条「公告板」，
// `views/spaces/detail/Announcements.vue`），这一页是把同一件事按重设计的口径重画：
//
// - **谁能发**：所有者与管理员。判据和真平台一致 —— 公告没有自己的接口，它是随
//   空间一起 PATCH 下去的（`space.announcements`），而那条路只对管理员开
//   （`SpaceService.update_space` 里的 `_ensure_admin`）。所以普通成员这一页是纯读的。
// - **置顶**：真平台没有。公告就是一段数组，没有排序字段。这是这次新增的一条，
//   落真代码要给元素加一个布尔字段。
// - **正文**：真平台是 tiptap 出来的富文本 HTML，这里按纯文本折成段落与列表
//   （见 `announcementBlocks`），因为原型里没有编辑器。
import { computed, ref } from 'vue'

import { type Announcement, announcementBlocks } from '../fixtures'
import {
  announcementList,
  isManager,
  publishAnnouncement,
  removeAnnouncement,
  toggleAnnouncementPin,
  updateAnnouncement,
} from '../store'

/** 相对时间。和别处一样只说「今天 / N 天前」，不摆一串 ISO 时间。 */
function whenText(iso: string): string {
  const d = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 86_400_000))
  return d === 0 ? '今天' : `${d} 天前`
}

/** 卡片上那两行摘要。列表项的「- 」要先摘掉 —— 否则折成一行之后会读成
 *  「…重新提交。- 出题不再是权限」，一个破折号挂在句子中间。 */
function preview(content: string): string {
  const flat = content
    .replace(/^\s*-\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
  return flat.length > 110 ? `${flat.slice(0, 110)}…` : flat
}

const reading = ref<Announcement | null>(null)

const composing = ref(false)
const editingId = ref<string | null>(null)
const draftTitle = ref('')
const draftContent = ref('')
const draftPinned = ref(false)

const composingTitle = computed(() => (editingId.value ? '编辑公告' : '发布公告'))

function openCreate() {
  editingId.value = null
  draftTitle.value = ''
  draftContent.value = ''
  draftPinned.value = false
  composing.value = true
}

function openEdit(row: Announcement) {
  editingId.value = row.id
  draftTitle.value = row.title
  draftContent.value = row.content
  draftPinned.value = row.pinned
  composing.value = true
}

const canSubmit = computed(() => draftTitle.value.trim().length > 0 && draftContent.value.trim().length > 0)

function submit() {
  if (!canSubmit.value) return
  if (editingId.value) {
    updateAnnouncement(editingId.value, { title: draftTitle.value, content: draftContent.value })
    // 置顶是单独一个动作，编辑弹窗里改了也要落下去
    const row = announcementList.value.find((a) => a.id === editingId.value)
    if (row && row.pinned !== draftPinned.value) toggleAnnouncementPin(row.id)
  } else {
    publishAnnouncement({ title: draftTitle.value, content: draftContent.value, pinned: draftPinned.value })
  }
  composing.value = false
  editingId.value = null
}

const confirmingDelete = ref<Announcement | null>(null)

function confirmDelete() {
  if (confirmingDelete.value) removeAnnouncement(confirmingDelete.value.id)
  confirmingDelete.value = null
}
</script>

<template>
  <div class="ann">
    <div class="ann__hero">
      <div>
        <h1 class="ann__title">公告</h1>
        <p class="ann__sub">板上发出来的通知，谁都能看。只有所有者与管理员能发、能改、能删 —— 与今天一致。</p>
      </div>
      <v-btn v-if="isManager" color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">
        发布公告
      </v-btn>
    </div>

    <v-alert
      v-if="!isManager"
      class="ann__note"
      variant="tonal"
      density="comfortable"
      icon="mdi-information-outline"
      text="发布与编辑只有所有者和管理员能做，所以你这个身份看不到那些按钮。"
    />

    <div v-if="announcementList.length" class="ann__list">
      <v-card
        v-for="row in announcementList"
        :key="row.id"
        class="ann__card"
        elevation="0"
        rounded="lg"
        @click="reading = row"
      >
        <div class="ann__card-body">
          <div class="ann__card-head">
            <span class="ann__card-title">{{ row.title }}</span>
            <v-chip v-if="row.pinned" size="x-small" color="primary" variant="tonal" label>置顶</v-chip>
          </div>
          <p class="ann__card-preview">{{ preview(row.content) }}</p>
          <div class="ann__card-meta">
            <span class="ann__avatar">{{ row.publisher.name.slice(0, 1) }}</span>
            <span>{{ row.publisher.name }}</span>
            <span>· {{ whenText(row.createdAt) }}</span>
            <span v-if="row.updatedAt !== row.createdAt">· 已编辑</span>
          </div>
        </div>

        <div v-if="isManager" class="ann__card-actions">
          <v-btn
            size="small"
            variant="text"
            :prepend-icon="row.pinned ? 'mdi-pin-off-outline' : 'mdi-pin-outline'"
            @click.stop="toggleAnnouncementPin(row.id)"
          >
            {{ row.pinned ? '取消置顶' : '置顶' }}
          </v-btn>
          <v-btn size="small" variant="text" prepend-icon="mdi-pencil" @click.stop="openEdit(row)">编辑</v-btn>
          <v-btn
            size="small"
            variant="text"
            color="error"
            prepend-icon="mdi-delete"
            @click.stop="confirmingDelete = row"
          >
            删除
          </v-btn>
        </div>
      </v-card>
    </div>

    <v-empty-state v-else icon="mdi-bullhorn-outline" title="还没有公告" text="所有者或管理员发布之后，这里会出现。" />

    <!-- 读一条：正文按段落与列表渲染。真平台这段是富文本 HTML，交给 Viewer 渲染。 -->
    <v-dialog :model-value="reading !== null" max-width="680" scrollable @update:model-value="reading = null">
      <v-card v-if="reading" rounded="lg">
        <v-card-title class="d-flex align-center ga-3 pa-5 pb-2">
          <v-icon icon="mdi-bullhorn-outline" />
          <span class="text-body-1 font-weight-bold">{{ reading.title }}</span>
          <v-chip v-if="reading.pinned" size="x-small" color="primary" variant="tonal" label>置顶</v-chip>
          <v-spacer />
          <v-btn variant="text" icon="mdi-close" size="small" @click="reading = null" />
        </v-card-title>
        <v-card-subtitle class="px-5 pb-4 text-body-2">
          {{ reading.publisher.name }} · {{ whenText(reading.createdAt) }}
          <template v-if="reading.updatedAt !== reading.createdAt"> · 已编辑</template>
        </v-card-subtitle>
        <v-divider />
        <v-card-text class="pa-5 ann__prose">
          <template v-for="(block, i) in announcementBlocks(reading.content)" :key="i">
            <ul v-if="block.type === 'ul'" class="ann__ul">
              <li v-for="(item, j) in block.items" :key="j">{{ item }}</li>
            </ul>
            <p v-else>{{ block.items[0] }}</p>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>

    <!-- 发一条 / 改一条 -->
    <v-dialog v-model="composing" max-width="680" scrollable>
      <v-card rounded="lg">
        <v-card-title class="d-flex align-center ga-3 pa-5 pb-2">
          <v-icon :icon="editingId ? 'mdi-pencil' : 'mdi-plus'" />
          <span class="text-body-1 font-weight-bold">{{ composingTitle }}</span>
          <v-spacer />
          <v-btn variant="text" icon="mdi-close" size="small" @click="composing = false" />
        </v-card-title>
        <v-card-subtitle class="px-5 pb-4 text-body-2">
          发出去所有人都能看。正文里空一行分段，以「- 」开头的一行会排成列表。
        </v-card-subtitle>
        <v-divider />
        <v-card-text class="pa-5">
          <v-text-field
            v-model="draftTitle"
            autocomplete="off"
            density="compact"
            variant="outlined"
            label="标题"
            class="mb-3"
          />
          <v-textarea
            v-model="draftContent"
            autocomplete="off"
            density="compact"
            variant="outlined"
            label="正文"
            rows="8"
            auto-grow
          />
          <v-switch v-model="draftPinned" color="primary" density="compact" hide-details label="置顶" class="mt-2" />
          <p class="ann__hint">
            「置顶」这一项真平台现在没有：公告只是一段数组，没有排序字段。落真代码要给元素加一个布尔字段。
          </p>
        </v-card-text>
        <v-divider />
        <v-card-actions class="pa-4">
          <v-spacer />
          <v-btn variant="text" @click="composing = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!canSubmit" @click="submit">
            {{ editingId ? '保存' : '发布' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 删一条：删除是不可逆的，所以要问一次 -->
    <v-dialog :model-value="confirmingDelete !== null" max-width="420" @update:model-value="confirmingDelete = null">
      <v-card v-if="confirmingDelete" rounded="lg" class="pa-5">
        <h3 class="text-body-1 font-weight-bold mb-2">删除这条公告？</h3>
        <p class="text-body-2 text-medium-emphasis mb-4">
          「{{ confirmingDelete.title }}」会被移出公告列表，成员就看不到了。这一步不能撤销。
        </p>
        <div class="text-right">
          <v-btn variant="text" @click="confirmingDelete = null">取消</v-btn>
          <v-btn color="error" variant="flat" class="ml-2" @click="confirmDelete">删除</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.ann__hero {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.ann__title {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 650;
}

.ann__sub {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.86rem;
}

.ann__note {
  margin: 10px 0 14px;
}

.ann__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 14px;
}

.ann__card {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 18px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease;
}

.ann__card:hover {
  background: rgba(var(--v-theme-primary), 0.03);
  border-color: rgba(var(--v-theme-primary), 0.35);
}

.ann__card-body {
  min-width: 0;
  flex: 1;
}

.ann__card-head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.ann__card-title {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.35;
}

.ann__card-preview {
  margin: 8px 0 10px;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.85rem;
  line-height: 1.55;
}

.ann__card-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.76rem;
}

.ann__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-right: 2px;
  color: rgb(var(--v-theme-primary));
  font-size: 0.7rem;
  background: rgba(var(--v-theme-primary), 0.14);
  border-radius: 50%;
}

.ann__card-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
  align-items: center;
}

.ann__prose {
  color: rgba(var(--v-theme-on-surface), 0.85);
  font-size: 0.9rem;
  line-height: 1.75;
}

.ann__prose p {
  margin: 0 0 12px;
}

.ann__prose p:last-child {
  margin-bottom: 0;
}

.ann__ul {
  margin: 0 0 12px;
  padding-left: 20px;
}

.ann__ul li {
  margin-bottom: 4px;
}

.ann__hint {
  margin: 8px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.74rem;
  line-height: 1.6;
}

@media (max-width: 720px) {
  .ann__card {
    flex-direction: column;
  }
}
</style>
