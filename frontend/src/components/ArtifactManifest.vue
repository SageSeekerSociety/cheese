<script setup lang="ts">
// 做出了什么 —— 这个项目交出去的东西，一项一行 (#1085 结论二、三)。
//
// 它是看板上**最右边那一列**。四列从左到右读是一条流水线（未开始 → 进行中 →
// 检查中 → 待处理），产物正是这条流水线吐出来的东西，所以它接在后面而不是摞在板上面：摞
// 上面要占竖直高度，有几项就占多高，板会被挤到只剩一张卡，而这一页不滚。作为一
// 列，它和别的列一样自己内部滚动，再多产物也挤不着板。
//
// 界面上不出现产物的类别：形态不统一（一份 PDF、一个网址、一套 typst 工程），
// 任何试图说出「它是什么」的类别名都不成立，只出现这几行东西本身。
//
// 空的时候这一列留着。它曾经是整块隐藏（#1085 结论三），那是它还摞在板上面的时
// 候——一列凭空消失会让整个网格错位，而板的规矩是位置本身就是信息。
//
// 已发布的网站钉在最上面一行：它也是这个项目交出去的东西，只是只有一个。
//
// 列的框和列头由 RunningWorkView 出（`.board-col`），这里只是那一列的内容：四列的
// 边、圆角、列头语法因此只有一份，改一处四列一起变。件数走 `count` 事件上去——列头
// 属于那块网格，件数属于这里。
//
// 能做的三件事都是人的判断，芝士 做不了：改名（它起错了名字）、合并（两项其实是
// 同一个东西）、删除（它本来就不该是一项）。
import type { ProjectArtifact } from '../api'
import type { MenuAction } from './common/menuAction'

import { computed, ref, watch } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'

import { deleteProjectArtifact, listProjectArtifacts, mergeProjectArtifacts, renameProjectArtifact } from '../api'
import { t } from '../i18n'
import { relTime } from '../lib/relTime'

import AdaptiveDialog from './common/AdaptiveDialog.vue'
import AdaptiveMenu from './common/AdaptiveMenu.vue'
import NavLink from './common/NavLink.vue'
import PublishedSite from './PublishedSite.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{ count: [number] }>()

const rows = ref<ProjectArtifact[]>([])
const actionError = ref('')
const busy = ref('')
const rowMenu = useRowMenu<string>()

// 窄的时候这一块摞在板上面，只列前几项，其余的收着，点开才全列出来。宽的时候它是
// 一整列，自己滚，全列着——收起只在窄的那一档起作用（见样式里的 @container）。
const FOLDED = 3
const expanded = ref(false)

const renaming = ref<ProjectArtifact | null>(null)
const newName = ref('')
const merging = ref<ProjectArtifact | null>(null)
const mergeInto = ref('')
const removing = ref<ProjectArtifact | null>(null)

/** 一项的 ⋯：合并要有别的项可以并进去才出现；删除先过确认框。 */
function rowActions(row: ProjectArtifact): MenuAction[] {
  return [
    {
      key: 'rename',
      label: t('project.artifacts.rename'),
      icon: 'mdi-pencil-outline',
      onSelect: () => openRename(row),
    },
    ...(rows.value.length > 1
      ? [
          {
            key: 'merge',
            label: t('project.artifacts.mergeInto'),
            icon: 'mdi-call-merge',
            onSelect: () => openMerge(row),
          },
        ]
      : []),
    {
      key: 'remove',
      label: t('project.artifacts.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => (removing.value = row),
    },
  ]
}

/** 合并的目标只能是清单上**别的**那几项。 */
const mergeTargets = computed(() =>
  rows.value.filter((row) => row.id !== merging.value?.id).map((row) => ({ title: row.name, value: row.id }))
)

async function load() {
  const projectId = props.projectId
  try {
    const listed = await listProjectArtifacts(projectId)
    if (props.projectId !== projectId) return
    rows.value = listed.data
  } catch {
    // 读不到清单不该把首页变成一条错误：板是这一页的主体。下一次进这一页会再试
    // 一遍，这一列这次显示成空的。
    if (props.projectId === projectId) rows.value = []
  }
  if (props.projectId === projectId) emit('count', rows.value.length)
}

function version(row: ProjectArtifact): string {
  if (!row.version) return t('project.artifacts.notDelivered')
  const when = row.delivered_at ? ` · ${relTime(row.delivered_at)}` : ''
  return `${t('project.artifacts.version', { version: row.version })}${when}`
}

function openRename(row: ProjectArtifact) {
  renaming.value = row
  newName.value = row.name
  actionError.value = ''
}

function openMerge(row: ProjectArtifact) {
  merging.value = row
  mergeInto.value = ''
  actionError.value = ''
}

async function act(id: string, run: () => Promise<unknown>, failed: string) {
  busy.value = id
  actionError.value = ''
  try {
    await run()
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : failed
  } finally {
    busy.value = ''
  }
}

async function rename() {
  const row = renaming.value
  if (!row) return
  renaming.value = null
  await act(
    row.id,
    () => renameProjectArtifact(props.projectId, row.id, newName.value),
    t('project.artifacts.renameFailed')
  )
}

async function merge() {
  const row = merging.value
  const into = mergeInto.value
  if (!row || !into) return
  merging.value = null
  await act(row.id, () => mergeProjectArtifacts(props.projectId, row.id, into), t('project.artifacts.mergeFailed'))
}

async function remove() {
  const row = removing.value
  if (!row) return
  removing.value = null
  await act(row.id, () => deleteProjectArtifact(props.projectId, row.id), t('project.artifacts.deleteFailed'))
}

watch(
  () => props.projectId,
  () => {
    rows.value = []
    actionError.value = ''
    expanded.value = false
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="made" :class="{ 'made--expanded': expanded }">
    <!-- 网站钉在最上面：它也是交出去的东西，但只有一个，所以不排进下面那张清单。 -->
    <PublishedSite :project-id="projectId" />
    <p v-if="actionError" role="alert" class="made__error t-meta">{{ actionError }}</p>
    <ul class="made__list">
      <li v-if="!rows.length" class="made__empty t-body">{{ t('project.artifacts.empty') }}</li>
      <li
        v-for="(row, index) in rows"
        :key="row.id"
        class="made-row"
        :class="{ 'made-row--folded': index >= FOLDED }"
        @contextmenu="rowMenu.open(row.id, $event)"
      >
        <!-- 点进去是这一项自己那一页：版本历史、下载当时交出去的那一份。 -->
        <div class="made-row__what">
          <NavLink
            class="made-row__name t-body"
            :to="{ name: 'project-artifact', params: { projectId, artifactId: row.id } }"
          >
            {{ row.name }}
          </NavLink>
          <!-- 这是什么东西、给谁的。判断「这两项是不是同一个东西」要的正是它：
               两个名字并排摆着，人也看不出什么。没人写过的就不占一行。 -->
          <span v-if="row.about" class="made-row__about t-meta c-faint">{{ row.about }}</span>
          <span class="made-row__when t-meta c-faint">{{ version(row) }}</span>
        </div>
        <AdaptiveMenu v-bind="rowMenu.bind(row.id)" :actions="rowActions(row)" :title="row.name">
          <template #activator="{ props: menu }">
            <BaseButton
              v-bind="menu"
              icon="mdi-dots-horizontal"
              size="sm"
              :loading="busy === row.id"
              :aria-label="t('project.artifacts.actionsOf', { name: row.name })"
            />
          </template>
        </AdaptiveMenu>
      </li>
      <li v-if="rows.length > FOLDED" class="made__fold t-meta">
        <button type="button" class="made__fold-btn tap-target" :aria-expanded="expanded" @click="expanded = !expanded">
          {{
            expanded ? t('project.artifacts.collapse') : t('project.artifacts.expand', { count: rows.length - FOLDED })
          }}
        </button>
      </li>
    </ul>

    <!-- Rename: the card points at this item, not the name, so already-delivered versions still count as its own. -->
    <AdaptiveDialog
      :model-value="!!renaming"
      :title="t('project.artifacts.rename')"
      :primary-label="t('global.save')"
      :primary-disabled="!newName.trim()"
      size="sm"
      @update:model-value="renaming = null"
      @primary="rename"
    >
      <v-text-field
        v-if="renaming"
        v-model="newName"
        :label="t('project.artifacts.nameLabel')"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        autofocus
        @keyup.enter="rename"
      />
    </AdaptiveDialog>

    <!-- Merge: the same thing was declared as two items; this folds them back into one. -->
    <AdaptiveDialog
      :model-value="!!merging"
      :title="t('project.artifacts.mergeTitle', { name: merging?.name ?? '' })"
      :primary-label="t('project.artifacts.merge')"
      :primary-disabled="!mergeInto"
      size="sm"
      @update:model-value="merging = null"
      @primary="merge"
    >
      <template v-if="merging">
        <v-select
          v-model="mergeInto"
          :items="mergeTargets"
          autocomplete="off"
          :label="t('project.artifacts.mergeLabel')"
          density="compact"
          variant="outlined"
          hide-details
        />
        <p class="t-meta c-faint mt-3">{{ t('project.artifacts.mergeHint', { name: merging.name }) }}</p>
      </template>
    </AdaptiveDialog>

    <!-- Deleting an item is not reversible: ask before it happens. -->
    <ConfirmDialog
      :model-value="!!removing"
      :title="t('project.artifacts.deleteTitle', { name: removing?.name ?? '' })"
      :confirm-label="t('project.artifacts.delete')"
      danger
      @update:model-value="removing = null"
      @confirm="remove"
    >
      {{ t('project.artifacts.deleteBody') }}
    </ConfirmDialog>
  </div>
</template>

<style scoped>
/* 这一格是列头下面剩下的全部高度：`min-height: 0` 那条链要传到 .made__list，它才
   真的会滚而不是把列撑长。 */
.made {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* 错误是给人读的一行字，所以用墨色那一档：`--danger` 是记号（点、边、图标）的
   颜色，浅色主题下拿它写字只有 2.34:1，读不出来。 */
.made__error {
  flex: 0 0 auto;
  margin: 0;
  padding: 8px 12px 0;
  color: var(--danger-ink);
}
/* 清单自己滚，和任务列的 .board-col__list 同一套（`min-height: 0` 才传得下去）。 */
.made__list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
/* 一行是一项：名字一行，说明和第几版在它下面，操作按钮在右边。版本不和名字抢同
   一行：宽屏上这一列只有两百来像素，并排的话版本那一截是定宽的，名字会被挤成零。
   行不画成卡片：旁边三列里的卡是「一条活」，这里是活交出来的东西，长得一样的话
   读的人会把这一列也当成一种状态。 */
.made-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px 6px 10px;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.made-row:hover {
  background: var(--fill);
}
.made-row__what {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.made-row__about {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.made-row__name {
  min-width: 0;
  color: var(--text);
  align-self: flex-start;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
}
.made-row__name:hover {
  text-decoration: underline;
}
.made-row__when {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
/* 收起、展开只在窄的那一档：这一块摞在板上面，全列出来就把板往下推。宽的时候它是
   一整列，自己滚，全列着，按钮也不出现。 */
.made__fold {
  display: none;
}
.made__fold-btn {
  position: relative;
  padding: 4px;
  border: 0;
  font: inherit;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.made__fold-btn:hover {
  color: var(--ink);
}
@container (width < 1000px) {
  .made:not(.made--expanded) .made-row--folded {
    display: none;
  }
  .made__fold {
    display: block;
  }
}
/* 空列自己说它空。和任务列的空行同一个观感（同样的内边距、同样的 --muted）。 */
.made__empty {
  padding: 8px 4px;
  color: var(--muted);
}
</style>
