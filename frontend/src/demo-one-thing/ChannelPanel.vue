<script setup lang="ts">
// The channel's own right panel when no 一件事 is open: 概览 (what the channel
// is for, the things going on in it, the ones done lately) and 文件 (the
// project files this channel talks about). Same tab strip and the same task
// rows as the product's channel overview.
import type { PanelTab } from '@/components/panels/PanelTabs.vue'

import { computed, ref } from 'vue'

import { CHANNELS } from './data'
import { ownerName, taskLine } from './shapes'
import { go, openThing, S, t4, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import { absDate } from '@/lib/dateText'
import { fileIcon } from '@/lib/fileKind'

const channel = computed(() => CHANNELS.find((c) => c.id === S.chan)!)
const things = computed(() => S.things.filter((t) => t.chan === S.chan))
const open = computed(() => things.value.filter((t) => t.status !== 'done'))
const done = computed(() => things.value.filter((t) => t.status === 'done'))

const FILES_OF: Record<string, string[]> = {
  'c-exp': ['docs/评测口径说明.md', 'eval/retrieval.py', 'eval/preprocess.py'],
  'c-papers': ['docs/papers/BLIP-精读.md', 'docs/papers/CLIP-精读.md'],
  'c-meeting': ['docs/组会纪要-11-07.md', 'report/中期报告.docx'],
  'c-general': ['README.md', 'scripts/download_coco.sh'],
}
const files = computed(() => {
  const list = [...(FILES_OF[S.chan] ?? [])]
  if (S.chan === 'c-exp' && t4()?.status === 'done') list.unshift('results/eval_results.md')
  return list
})

const tab = ref('overview')
const tabs = computed<PanelTab[]>(() => [
  {
    key: 'overview',
    label: '概览',
    icon: 'mdi-text-box-outline',
    signal: open.value.length ? { kind: 'count', count: open.value.length } : undefined,
  },
  { key: 'files', label: '文件', icon: 'mdi-paperclip', empty: !files.value.length },
])

const about = ref<Record<string, string>>({})
const editing = ref(false)
const draft = ref('')
function edit() {
  draft.value = about.value[S.chan] ?? channel.value.about
  editing.value = true
}
function save() {
  about.value = { ...about.value, [S.chan]: draft.value.trim() }
  editing.value = false
  toast('频道说明已保存')
}
</script>

<template>
  <aside class="ot-cp" data-zone="right">
    <div class="ot-cp__bar">
      <PanelTabs :tabs="tabs" :active="tab" class="ot-cp__tabs" @select="tab = $event" />
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        title="收起概览"
        aria-label="收起概览"
        @click="S.chanPanel = false"
      />
    </div>
    <div class="ot-cp__body">
      <template v-if="tab === 'overview'">
        <section class="ot-cp__sec">
          <div class="ot-cp__head">
            <h3 class="t-eyebrow-read">频道说明</h3>
            <button v-if="!editing" type="button" class="ot-cp__link" @click="edit">编辑</button>
          </div>
          <template v-if="editing">
            <v-textarea
              v-model="draft"
              autocomplete="off"
              density="compact"
              variant="outlined"
              rows="2"
              auto-grow
              hide-details
            />
            <div class="ot-cp__actions">
              <BaseButton kind="ghost" size="sm" @click="editing = false">取消</BaseButton>
              <BaseButton kind="primary" size="sm" @click="save">保存</BaseButton>
            </div>
          </template>
          <p v-else class="ot-cp__about">{{ about[S.chan] ?? channel.about }}</p>
        </section>

        <section class="ot-cp__sec">
          <div class="ot-cp__head">
            <h3 class="t-eyebrow-read">进行中的事 · {{ open.length }}</h3>
          </div>
          <div v-if="open.length" class="ot-cp__cards">
            <TaskCard v-for="t in open" :key="t.id" :task="taskLine(t)" :owner-name="ownerName(t)" @open="openThing" />
          </div>
          <p v-else class="ot-cp__none">没有进行中的事</p>
        </section>

        <section v-if="done.length" class="ot-cp__sec">
          <div class="ot-cp__head">
            <h3 class="t-eyebrow-read">最近完成</h3>
          </div>
          <button v-for="t in done" :key="t.id" type="button" class="ot-cp__done" @click="openThing(t.id)">
            <v-icon size="16" icon="mdi-check" class="ot-cp__tick" />
            <span class="ot-cp__done-title">{{ t.title }}</span>
            <span class="ot-cp__date">{{ absDate(Date.parse(t.at)) }}</span>
          </button>
        </section>
      </template>

      <template v-else>
        <div v-if="files.length" class="ot-cp__files">
          <button
            v-for="f in files"
            :key="f"
            type="button"
            class="ot-cp__file"
            @click="go('files', { file: f, ftab: 'history' })"
          >
            <v-icon size="18" :icon="fileIcon(f)" />
            <span class="ot-cp__fname">{{ f.split('/').pop() }}</span>
            <span class="ot-cp__fdir">{{ f.includes('/') ? f.slice(0, f.lastIndexOf('/')) : '' }}</span>
          </button>
        </div>
        <BaseEmptyState v-else size="compact" icon="mdi-paperclip" title="这个频道还没有文件" />
      </template>
    </div>
  </aside>
</template>

<style scoped>
.ot-cp {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.ot-cp__bar {
  display: flex;
  flex: none;
  align-items: center;
  padding-right: 8px;
  border-bottom: 1px solid var(--line);
}

.ot-cp__tabs {
  flex: 1;
  min-width: 0;
  border-bottom: 0 !important;
}

.ot-cp__body {
  flex: 1;
  min-height: 0;
  padding: 8px 16px 16px;
  overflow: auto;
}

.ot-cp__sec {
  padding: 12px 0;
}

.ot-cp__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.ot-cp__head h3 {
  margin: 0;
}

.ot-cp__link {
  padding: 0;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-cp__link:hover {
  color: var(--ink);
}

.ot-cp__about,
.ot-cp__none {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-cp__none {
  color: var(--muted);
}

.ot-cp__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 8px;
}

.ot-cp__cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ot-cp__done,
.ot-cp__file {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 6px 8px;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-cp__done:hover,
.ot-cp__file:hover {
  background: var(--fill);
}

.ot-cp__tick {
  color: var(--ok-ink);
}

.ot-cp__done-title,
.ot-cp__fname {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-cp__fname {
  flex: 0 1 auto;
  color: var(--ink);
}

.ot-cp__date,
.ot-cp__fdir {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.ot-cp__fdir {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-cp__file .v-icon {
  color: var(--muted);
}
</style>
