<script setup lang="ts">
// 做出了什么 —— 这个项目交出去的东西，一项一行 (#1085 结论二、三)。
//
// 它是看板上**最右边那一列**。三列从左到右读是一条流水线（施工中 → 交付中 →
// 待处理），产物正是这条流水线吐出来的东西，所以它接在后面而不是摞在板上面：摞
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

import { computed, ref, watch } from 'vue'

import { deleteProjectArtifact, listProjectArtifacts, mergeProjectArtifacts, renameProjectArtifact } from '../api'
import { relTime } from '../lib/relTime'

import PublishedSite from './PublishedSite.vue'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{ count: [number] }>()

const rows = ref<ProjectArtifact[]>([])
const actionError = ref('')
const busy = ref('')

const renaming = ref<ProjectArtifact | null>(null)
const newName = ref('')
const merging = ref<ProjectArtifact | null>(null)
const mergeInto = ref('')
const removing = ref<ProjectArtifact | null>(null)

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
  if (!row.version) return '尚未交付'
  const when = row.delivered_at ? ` · ${relTime(row.delivered_at)}` : ''
  return `第 ${row.version} 版${when}`
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
  await act(row.id, () => renameProjectArtifact(props.projectId, row.id, newName.value), '未能改名')
}

async function merge() {
  const row = merging.value
  const into = mergeInto.value
  if (!row || !into) return
  merging.value = null
  await act(row.id, () => mergeProjectArtifacts(props.projectId, row.id, into), '未能合并')
}

async function remove() {
  const row = removing.value
  if (!row) return
  removing.value = null
  await act(row.id, () => deleteProjectArtifact(props.projectId, row.id), '未能删除')
}

watch(
  () => props.projectId,
  () => {
    rows.value = []
    actionError.value = ''
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="made">
    <!-- 网站钉在最上面：它也是交出去的东西，但只有一个，所以不排进下面那张清单。 -->
    <PublishedSite :project-id="projectId" />
    <p v-if="actionError" role="alert" class="made__error t-meta">{{ actionError }}</p>
    <ul class="made__list">
      <li v-if="!rows.length" class="made__empty t-body">暂无产物</li>
      <li v-for="row in rows" :key="row.id" class="made-row">
        <!-- 点进去是这一项自己那一页：版本历史、下载当时交出去的那一份。 -->
        <div class="made-row__what">
          <router-link
            class="made-row__name t-body"
            :to="{ name: 'project-artifact', params: { projectId, artifactId: row.id } }"
          >
            {{ row.name }}
          </router-link>
          <!-- 这是什么东西、给谁的。判断「这两项是不是同一个东西」要的正是它：
               两个名字并排摆着，人也看不出什么。没人写过的就不占一行。 -->
          <span v-if="row.about" class="made-row__about t-meta c-faint">{{ row.about }}</span>
        </div>
        <span class="made-row__when t-meta c-faint">{{ version(row) }}</span>
        <v-menu location="bottom end">
          <template #activator="{ props: menu }">
            <v-btn
              v-bind="menu"
              icon="mdi-dots-horizontal"
              size="x-small"
              variant="text"
              color="on-surface-variant"
              :loading="busy === row.id"
              :aria-label="`${row.name} 的操作`"
            />
          </template>
          <v-list density="compact" min-width="140">
            <v-list-item title="重命名" @click="openRename(row)" />
            <v-list-item v-if="rows.length > 1" title="合并到…" @click="openMerge(row)" />
            <v-list-item title="删除" @click="removing = row" />
          </v-list>
        </v-menu>
      </li>
    </ul>

    <!-- 改名。卡指着的是这一项，不是这个名字，所以已经交付过的那几版照样算它的。 -->
    <v-dialog :model-value="!!renaming" max-width="420" @update:model-value="renaming = null">
      <v-card v-if="renaming">
        <v-card-title class="t-title">重命名</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="newName"
            label="名字"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            autofocus
            @keyup.enter="rename"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="renaming = null">取消</v-btn>
          <v-btn variant="text" color="primary" :disabled="!newName.trim()" @click="rename">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 合并：同一样东西被声明成了两项，这是把它们收回一项。 -->
    <v-dialog :model-value="!!merging" max-width="420" @update:model-value="merging = null">
      <v-card v-if="merging">
        <v-card-title class="t-title">合并《{{ merging.name }}》</v-card-title>
        <v-card-text>
          <v-select
            v-model="mergeInto"
            :items="mergeTargets"
            autocomplete="off"
            label="合并到"
            density="compact"
            variant="outlined"
            hide-details
          />
          <p class="t-meta c-faint mt-3">《{{ merging.name }}》的版本记录归入所选的那一项，它不再单独列出</p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="merging = null">取消</v-btn>
          <v-btn variant="text" color="primary" :disabled="!mergeInto" @click="merge">合并</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!removing" max-width="420" @update:model-value="removing = null">
      <v-card v-if="removing">
        <v-card-title class="t-title">删除《{{ removing.name }}》</v-card-title>
        <v-card-text class="t-body">删除后它不再列在这里，已完成的交付记录保留</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="removing = null">取消</v-btn>
          <v-btn variant="text" color="error" @click="remove">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
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
  gap: 6px;
}
/* 一行是一项：名字在左，版本贴着右边的操作按钮。两栏之间留白，行与行的两端因此
   对齐 —— 名字长短不一时，右边那一列仍然是一条直线。 */
.made-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 4px 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
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
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
/* 空列自己说它空。和任务列的空行同一个观感（同样的内边距、同样的 --muted）。 */
.made__empty {
  padding: 8px 4px;
  color: var(--muted);
}
</style>
