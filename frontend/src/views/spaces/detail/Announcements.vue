<script setup lang="tsx">
// 空间公告。**所有成员都看得见**这一页（导航里不设管理员门槛）—— 公告的作用就是
// 「谁来都能看到」，只在管理员那儿开一个入口是不够的。
//
// 它不是一个模型：公告是 `space.announcements` 这个 jsonb 数组里的一段，随空间一起
// 写下去（`PATCH /spaces/{id}`），所以**发 / 改 / 删 / 置顶只有所有者与管理员能做** ——
// 那条路只对管理员开。成员打开这一页是纯读的，几个操作按钮一个都不出现。
//
// 「置顶」（2026-09-26 这一批加上的）：公告元素多了一个可选的 `pinned` 布尔字段。
// **后端一个字都没改** —— 服务端从头到尾只检查 announcements 是不是一个数组
// （`_expect_list` / `_normalize_json_list`），元素里的键原样透传，所以没有迁移。
// 排序口径只有一条，写在 `../model.ts` 的 `compareAnnouncements` 里。
import type { SpaceAnnouncement } from '@/types'

import { computed, defineAsyncComponent, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { compareAnnouncements } from '../model'

import PageHeader from '@/components/common/PageHeader.vue'
import { relTime } from '@/lib/relTime'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))
const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))

const { t } = useI18n()
const route = useRoute()
const dialog = useDialog()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { announcements, isManager } = storeToRefs(spaceStore)

// 公告是空间的一部分：直接读空间 store 的那一份，不另开一条取数的路 —— 换空间时
// 它自己会跟着换（`fetchSpace` 按 id 判过一次）。
spaceData.fetchSpace(Number(route.params.spaceId))

const editing = ref(false)
const editingIndex = ref<number | undefined>(undefined)
const draftTitle = ref('')
const draftContent = ref('')
/** 「发布」弹窗里那个置顶开关。**只在发的时候有** —— 已经发出去的公告要置顶，
 *  走卡片上那个单独的动作，见 `togglePin`。 */
const draftPinned = ref(false)

function openCreate() {
  draftTitle.value = ''
  draftContent.value = ''
  draftPinned.value = false
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

/** 一条公告上的时间：**说相对时间，不摆一串日期**。口径借 `lib/relTime.ts` —— 它给的
 *  正是这一屏要的「刚刚 / N 分钟前 / 昨天 / N 天前」，超过一周退回日期。公告的
 *  `createdAt` / `updatedAt` 是 epoch 毫秒，那个工具吃的是 ISO 串。 */
function whenText(at: number): string {
  return relTime(new Date(at).toISOString())
}

/** 「已编辑」只对**改过**的公告显示：`updatedAt` 与 `createdAt` 是同一个时刻就是没动过，
 *  老公告缺 `updatedAt` 也当没动过 —— 与卡片上那句同一个判据。 */
function isEdited(a: SpaceAnnouncement): boolean {
  return Boolean(a.updatedAt) && a.updatedAt !== a.createdAt
}

function read(announcement: SpaceAnnouncement) {
  // 读一条时要答「这是谁、什么时候发的、改过没有」—— 弹窗里跟卡片上那句同一口径，
  // 只是这里补上了发布人（卡片正文那一行没有它的位置）。
  dialog.custom(
    announcement.title,
    () => (
      <div>
        <p class="t-body c-muted mb-3" data-testid="announcement-meta">
          {announcement.publisher} · {whenText(announcement.createdAt)}
          {isEdited(announcement) ? ' · 已编辑' : ''}
        </p>
        <TipTapViewer value={announcement.content} />
      </div>
    ),
    { showCancel: false }
  )
}

async function remove(index: number) {
  const ok = await dialog.confirm('删除这条公告？').wait()
  if (!ok) return
  await spaceData.deleteAnnouncement(index)
  toast.success('已删除')
}

/** 置顶 / 取消置顶。**单独一个动作，不塞进编辑弹窗** —— 编辑弹窗里的东西是攒在
 *  一起提交的，置顶混进去就会跟着别的字段一起丢（取消编辑、或者哪次改动出事都算）。
 *
 *  写回走的是 `updateAnnouncement(index, …)`：它按**下标**改，所以这里传的是这条公告
 *  在 store **原始数组**里的下标，不是排过序的位置。 */
async function togglePin(index: number) {
  const target = announcements.value[index]
  if (!target) return
  const next = !target.pinned
  try {
    // 整条写回：`updateAnnouncement` 是按下标替换的，只带 `pinned` 会把标题正文弄丢。
    await spaceData.updateAnnouncement(index, { ...target, pinned: next })
    toast.success(next ? '已置顶' : '已取消置顶')
  } catch {
    toast.error(next ? '置顶失败' : '取消置顶失败')
  }
}

async function submit() {
  if (!isManager.value) {
    toast.error('只有所有者与管理员能发公告')
    return
  }
  const now = Date.now()
  const target = editingIndex.value === undefined ? undefined : announcements.value[editingIndex.value]
  const next: SpaceAnnouncement = {
    title: draftTitle.value,
    content: draftContent.value,
    // 编辑时保留原来的发布时刻，只推进 `updatedAt` —— 否则「什么时候发的」会被改没。
    createdAt: target ? target.createdAt : now,
    updatedAt: now,
    publisher: AccountService._user.value?.nickname || '',
    // 置顶只由两个地方决定：发的时候那个开关，和卡片上那个单独的动作。编辑弹窗里
    // 没有这一项，所以编辑时**原样带过去** —— 不然改个错别字就把置顶弄丢了。
    pinned: target ? target.pinned : draftPinned.value,
  }
  try {
    if (editingIndex.value === undefined) await spaceData.addAnnouncement(next)
    else await spaceData.updateAnnouncement(editingIndex.value, next)
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

/** 排过序的展示列表：**带上原始下标，一起排**。下标是给「编辑 / 删除 / 置顶」用的，
 *  必须指回 store 里那条 —— 所以这里排的是副本，store 里那份数组的顺序一个字节
 *  都不动（`sortAnnouncements` 也是这个道理，只是它不带下标）。 */
const sorted = computed(() =>
  announcements.value.map((a, index) => ({ a, index })).sort((x, y) => compareAnnouncements(x.a, y.a))
)
</script>

<template>
  <PageHeader :title="t('spaces.detail.announcements')" show-on-mobile>
    <!-- 插槽本身不能带 v-if：PageHeader 只在挂上那一刻看有没有操作区插槽，空间读回来、
         知道你是管理员时它已经不再看了。条件放在按钮上。 -->
    <template #actions>
      <v-btn v-if="isManager" color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">
        发布公告
      </v-btn>
    </template>
  </PageHeader>
  <div class="ann">
    <div v-if="sorted.length" class="ann__grid">
      <v-card v-for="{ a, index } in sorted" :key="index" flat rounded="lg" class="acard" @click="read(a)">
        <div class="acard__head">
          <h3 class="acard__title">{{ a.title }}</h3>
          <v-chip v-if="a.pinned" size="x-small" color="primary" variant="tonal" label>置顶</v-chip>
        </div>
        <p class="acard__preview">{{ preview(a.content) }}</p>
        <div class="acard__foot">
          <span>{{ a.publisher }}</span>
          <span>{{ whenText(a.createdAt) }}</span>
          <span v-if="isEdited(a)" class="acard__edited">已编辑</span>
          <v-spacer />
          <template v-if="isManager">
            <!-- 置顶是**单独一个动作**，不藏在编辑弹窗里：弹窗里的东西是一起提交的，
                 和别的字段混在一起就容易一起丢。 -->
            <v-btn
              :icon="a.pinned ? 'mdi-pin-off-outline' : 'mdi-pin-outline'"
              :title="a.pinned ? '取消置顶' : '置顶'"
              size="x-small"
              variant="text"
              @click.stop="togglePin(index)"
            />
            <v-btn icon="mdi-pencil" size="x-small" variant="text" @click.stop="openEdit(index)" />
            <v-btn icon="mdi-delete" size="x-small" variant="text" color="error" @click.stop="remove(index)" />
          </template>
        </div>
      </v-card>
    </div>

    <p v-else class="ann__empty">暂无公告</p>

    <v-dialog v-model="editing" max-width="760">
      <v-card rounded="lg" class="pa-4">
        <h3 class="text-body-1 font-weight-bold mb-3">{{ editingIndex === undefined ? '发布公告' : '编辑公告' }}</h3>
        <v-text-field v-model="draftTitle" autocomplete="off" label="标题" variant="outlined" density="comfortable" />
        <TipTapEditor v-model="draftContent" output="html" label="正文" />
        <!-- 置顶开关只在**发布**时出现。已经发出去的公告要置顶走卡片上那个动作，
             那是一条独立的路 —— 混在这个弹窗里就会跟着别的字段一起丢。 -->
        <v-switch
          v-if="editingIndex === undefined"
          v-model="draftPinned"
          color="primary"
          density="comfortable"
          hide-details
          label="置顶"
          class="mt-2"
        />
        <div class="d-flex justify-end ga-2 mt-4">
          <v-btn variant="text" @click="editing = false">取消</v-btn>
          <v-btn color="primary" variant="flat" @click="submit">发布</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.ann {
  padding: 16px;
}

.ann__empty {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
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

.acard__head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
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
