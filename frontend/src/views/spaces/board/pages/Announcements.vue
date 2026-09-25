<script setup lang="tsx">
// 空间公告。**所有成员都看得见**这一页（导航里不设管理员门槛）—— 公告的作用就是
// 「谁来都能看到」，只在管理员那儿开一个入口是不够的。
//
// 它不是一个模型：公告是 `space.announcements` 这个 jsonb 数组里的一段，随空间一起
// 写下去（`PATCH /spaces/{id}`），所以**发 / 改 / 删只有所有者与管理员能做** —— 那条
// 路只对管理员开。成员打开这一页是纯读的，四个操作按钮一个都不出现。
//
// 与原型那一版的差别：**没有「置顶」**。真平台的公告元素里没有排序字段，加一个
// 布尔字段是改模型的事，还没拍板（原型里那条开关的说明也是这么写的）。
import type { SpaceAnnouncement } from '@/types'

import { computed, defineAsyncComponent, ref } from 'vue'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { isManager } from '../store'

import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))
const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))

const route = useRoute()
const dialog = useDialog()
const spaceStore = useSpaceStore()
const { announcements } = storeToRefs(spaceStore)

// 公告是空间的一部分：直接读空间 store 的那一份，不另开一条取数的路 —— 换空间时
// 它自己会跟着换（`fetchSpace` 按 id 判过一次）。
spaceStore.fetchSpace(Number(route.params.spaceId))

const editing = ref(false)
const editingIndex = ref<number | undefined>(undefined)
const draftTitle = ref('')
const draftContent = ref('')

function openCreate() {
  draftTitle.value = ''
  draftContent.value = ''
  editingIndex.value = undefined
  editing.value = true
}

function openEdit(index: number) {
  const target = announcements.value[index]
  if (!target) return
  draftTitle.value = target.title
  draftContent.value = target.content
  editingIndex.value = index
  editing.value = true
}

function read(announcement: SpaceAnnouncement) {
  dialog.custom(announcement.title, () => <TipTapViewer value={announcement.content} />, { showCancel: false })
}

async function remove(index: number) {
  const ok = await dialog.confirm('删除这条公告？').wait()
  if (!ok) return
  await spaceStore.deleteAnnouncement(index)
  toast.success('已删除')
}

async function submit() {
  if (!isManager.value) {
    toast.error('只有所有者与管理员能发公告')
    return
  }
  const now = Date.now()
  const next: SpaceAnnouncement = {
    title: draftTitle.value,
    content: draftContent.value,
    // 编辑时保留原来的发布时刻，只推进 `updatedAt` —— 否则「什么时候发的」会被改没。
    createdAt: editingIndex.value === undefined ? now : announcements.value[editingIndex.value].createdAt,
    updatedAt: now,
    publisher: AccountService._user.value?.nickname || '',
  }
  try {
    if (editingIndex.value === undefined) await spaceStore.addAnnouncement(next)
    else await spaceStore.updateAnnouncement(editingIndex.value, next)
    editing.value = false
    toast.success('已发布')
  } catch {
    toast.error('发布失败')
  }
}

function preview(content: string, length = 110) {
  const text = content.replace(/<[^>]*>/g, '').trim()
  return text.length > length ? `${text.slice(0, length)}…` : text
}

const sorted = computed(() =>
  announcements.value
    .map((a, index) => ({ a, index }))
    .sort((x, y) => (y.a.updatedAt ?? y.a.createdAt) - (x.a.updatedAt ?? x.a.createdAt))
)
</script>

<template>
  <div class="ann">
    <div class="ann__head">
      <div>
        <h1>公告</h1>
        <p>这个空间里的通知。所有成员都能看，只有所有者与管理员能发。</p>
      </div>
      <v-btn v-if="isManager" color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">
        发布公告
      </v-btn>
    </div>

    <div v-if="sorted.length" class="ann__grid">
      <v-card v-for="{ a, index } in sorted" :key="index" flat rounded="lg" class="acard" @click="read(a)">
        <h3 class="acard__title">{{ a.title }}</h3>
        <p class="acard__preview">{{ preview(a.content) }}</p>
        <div class="acard__foot">
          <span>{{ a.publisher }}</span>
          <span>{{ new Date(a.createdAt).toLocaleDateString() }}</span>
          <span v-if="a.updatedAt && a.updatedAt !== a.createdAt" class="acard__edited">已编辑</span>
          <v-spacer />
          <template v-if="isManager">
            <v-btn icon="mdi-pencil" size="x-small" variant="text" @click.stop="openEdit(index)" />
            <v-btn icon="mdi-delete" size="x-small" variant="text" color="error" @click.stop="remove(index)" />
          </template>
        </div>
      </v-card>
    </div>

    <v-empty-state
      v-else
      icon="mdi-bullhorn-outline"
      title="还没有公告"
      :text="isManager ? '发布一条公告，让成员了解最新动态。' : '这里会显示所有者与管理员发布的公告。'"
    />

    <v-dialog v-model="editing" max-width="760">
      <v-card rounded="lg" class="pa-4">
        <h3 class="text-body-1 font-weight-bold mb-3">{{ editingIndex === undefined ? '发布公告' : '编辑公告' }}</h3>
        <v-text-field v-model="draftTitle" autocomplete="off" label="标题" variant="outlined" density="comfortable" />
        <TipTapEditor v-model="draftContent" output="html" label="正文" />
        <div class="d-flex justify-end ga-2 mt-4">
          <v-btn variant="text" @click="editing = false">取消</v-btn>
          <v-btn color="primary" variant="flat" @click="submit">发布</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.ann__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.ann__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.ann__head p {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.84rem;
}

.ann__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.acard {
  padding: 16px;
  cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  transition: border-color 0.15s ease;
}

.acard:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
}

.acard__title {
  margin: 0 0 6px;
  font-size: 0.98rem;
  font-weight: 600;
}

.acard__preview {
  display: -webkit-box;
  margin: 0 0 12px;
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.82rem;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.acard__foot {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-top: 10px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.acard__edited {
  font-style: italic;
}
</style>
