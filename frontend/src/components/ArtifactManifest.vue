<script setup lang="ts">
// 做出了什么 —— 这个项目交出去的东西，一项一行 (#1085 结论二、三)。
//
// 它在项目首页上，不在一个自己的页面里：产物的形态不统一（一份 PDF、一个网址、
// 一套 typst 工程），任何试图说出「它是什么」的类别名都不成立，所以界面上不出现
// 类别，只出现这几行东西本身。
//
// 清单为空时这一块整个不显示，加载中也不显示：清单由交付长出来，空清单的意思是
// 「还什么都没交出去」，那时候人要看的是下面那块板。先画一个空框再把它收掉，比
// 一次到位更晃眼。
//
// 能做的三件事都是人的判断，芝士 做不了：改名（它起错了名字）、合并（两项其实是
// 同一个东西）、删除（它本来就不该是一项）。
import type { ProjectArtifact } from '../api'

import { computed, ref, watch } from 'vue'

import { deleteProjectArtifact, listProjectArtifacts, mergeProjectArtifacts, renameProjectArtifact } from '../api'
import { relTime } from '../lib/relTime'

const props = defineProps<{ projectId: string }>()

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
    // 读不到清单不该把首页变成一条错误：板是这一页的主体，而这一块只在真有东西
    // 可摆时出现。下一次进这一页会再试一遍。
    if (props.projectId === projectId) rows.value = []
  }
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
  <section v-if="rows.length" class="made">
    <h2 class="made__title t-title">做出了什么</h2>
    <p v-if="actionError" role="alert" class="made__error t-meta">{{ actionError }}</p>
    <ul class="made__list">
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
  </section>
</template>

<style scoped>
/* 左右和下面那块板的头部对齐：板自己有 12px，头部再补 10px，所以这里也是 10px。 */
.made {
  flex: 0 0 auto;
  padding: 0 10px 14px;
}
.made__title {
  margin: 0 0 8px;
}
/* 错误是给人读的一行字，所以用墨色那一档：`--danger` 是记号（点、边、图标）的
   颜色，浅色主题下拿它写字只有 2.34:1，读不出来。 */
.made__error {
  margin: 0 0 8px;
  color: var(--danger-ink);
}
.made__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
/* 一行是一项：名字在左，版本贴着右边的操作按钮。两栏之间留白，行与行的两端因此
   对齐 —— 名字长短不一时，右边那一列仍然是一条直线。 */
.made-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 8px 8px 12px;
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
</style>
