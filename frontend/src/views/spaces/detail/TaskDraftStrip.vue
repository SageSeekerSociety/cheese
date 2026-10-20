<template>
  <!-- One file split into several tasks: the drafts 芝士 wrote from it, side by side above the
       form. The open one is marked; a click opens another. -->
  <section class="strip" :aria-label="t('spaces.detail.publishTask.strip.label')" data-testid="draft-strip">
    <p v-if="source" class="strip__src">
      <v-icon size="14">mdi-file-document-outline</v-icon>
      <span data-user-content>{{ t('spaces.detail.publishTask.strip.from', { file: source }) }}</span>
    </p>
    <div class="strip__row">
      <ol class="strip__chips">
        <li
          v-for="(item, i) in items"
          :key="item.id ?? 'new'"
          class="chip"
          :class="{
            'chip--on': item.id === currentId,
            'chip--done': item.published,
            'chip--bad': flagged && !item.published && item.missing.length > 0,
          }"
        >
          <template v-if="asking !== null && asking === item.id">
            <span class="chip__ask">{{ t('spaces.detail.publishTask.strip.removeAsk') }}</span>
            <BaseButton
              size="sm"
              kind="ghost"
              :data-testid="`draft-strip-remove-yes-${item.id}`"
              @click="remove(item.id)"
              >{{ t('spaces.detail.publishTask.strip.remove') }}</BaseButton
            >
            <BaseButton size="sm" kind="ghost" @click="asking = null">{{ t('global.cancel') }}</BaseButton>
          </template>
          <template v-else>
            <button
              type="button"
              class="chip__go"
              :aria-current="item.id === currentId ? 'true' : undefined"
              :disabled="item.id === null"
              :data-testid="`draft-strip-${item.id ?? 'new'}`"
              @click="item.id !== null && item.id !== currentId && emit('open', item.id)"
            >
              <span class="chip__no">{{ i + 1 }}</span>
              <span class="chip__name" data-user-content>{{
                item.name || t('spaces.detail.publishTask.strip.untitled')
              }}</span>
              <span v-if="item.writing" class="chip__spin" :aria-label="t('spaces.detail.publishTask.strip.writing')" />
              <span v-else-if="item.published" class="chip__state chip__state--ok">{{
                t('spaces.detail.publishTask.strip.published')
              }}</span>
              <span
                v-else-if="flagged && item.missing.length"
                class="chip__state"
                :title="item.missing.join(t('spaces.detail.publishTask.strip.joiner'))"
                >{{ t('spaces.detail.publishTask.strip.missing', { n: item.missing.length }) }}</span
              >
            </button>
            <button
              v-if="item.id !== null && !item.published"
              type="button"
              class="chip__x"
              :aria-label="
                t('spaces.detail.publishTask.strip.removeOne', {
                  name: item.name || t('spaces.detail.publishTask.strip.untitled'),
                })
              "
              :title="t('spaces.detail.publishTask.strip.remove')"
              @click="asking = item.id"
            >
              <v-icon size="14">mdi-close</v-icon>
            </button>
          </template>
        </li>
      </ol>
      <BaseButton
        v-if="left > 1"
        kind="secondary"
        size="sm"
        :loading="publishing"
        data-testid="draft-strip-publish-all"
        @click="emit('publishAll')"
        >{{ t('spaces.detail.publishTask.strip.publishAll', { n: left }) }}</BaseButton
      >
    </div>
    <p v-if="note" class="strip__note" :class="{ 'strip__note--ok': note.ok }" role="status">{{ note.text }}</p>
  </section>
</template>

<script setup lang="ts">
// 发题页表单上方的一排：芝士从一份文件拆出的几道题（同一段对话建的几份新题目草稿）。
// 开着的那道深色描边；芝士正在写的那道转圈；点「全部发布」之后，没发出去的那道标出还缺几项。
// 只拿 props、只发事件：读草稿、删、发布都在容器里。
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

export interface StripItem {
  /** 还在建的那份（芝士正在写、服务端还没给 id）是 null。 */
  id: number | null
  name: string
  writing: boolean
  /** 发布前还缺的格子，写成表单上的名字。 */
  missing: string[]
  published: boolean
}

const props = defineProps<{
  items: StripItem[]
  currentId: number | null
  /** 从哪个文件拆出来的；没有就不写那一行。 */
  source: string
  /** 点过「全部发布」：缺项标出来。 */
  flagged: boolean
  publishing: boolean
  note: { text: string; ok: boolean } | null
}>()

const emit = defineEmits<{
  open: [id: number]
  remove: [id: number]
  publishAll: []
}>()

const { t } = useI18n()
const asking = ref<number | null>(null)

const left = computed(() => props.items.filter((item) => item.id !== null && !item.published).length)

function remove(id: number | null) {
  asking.value = null
  if (id !== null) emit('remove', id)
}
</script>

<style scoped>
.strip {
  position: sticky;
  top: var(--app-page-header-height);
  z-index: 3;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 0;
  margin-bottom: 8px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.strip__src {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.strip__row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.strip__chips {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  padding: 0;
  margin: 0;
  list-style: none;
}

.chip {
  display: inline-flex;
  align-items: center;
  max-width: 280px;
  height: 32px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}

.chip:hover {
  background: var(--fill);
}

.chip--on {
  border-color: var(--ink);
}

.chip--on:hover {
  background: var(--surface);
}

.chip--bad {
  border-color: var(--warn);
}

.chip--done {
  opacity: 0.72;
}

.chip__go {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  height: 100%;
  padding: 0 4px 0 10px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.chip--on .chip__go {
  font-weight: 600;
  color: var(--ink);
  cursor: default;
}

.chip__no {
  flex: none;
  font-family: var(--font-mono);
  font-weight: 400;
  color: var(--muted);
}

.chip__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip__state {
  flex: none;
  font-weight: 400;
  color: var(--warn-ink);
}

.chip__state--ok {
  color: var(--ok-ink);
}

.chip__spin {
  flex: none;
  width: 11px;
  height: 11px;
  border: 1.5px solid var(--line);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: chip-spin 0.8s linear infinite;
}

@keyframes chip-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .chip__spin {
    animation: none;
    border-color: var(--accent);
  }
}

.chip__x {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: 4px;
  color: var(--muted);
  border-radius: var(--radius-sm);
  opacity: 0;
  transition:
    opacity var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}

.chip:hover .chip__x,
.chip__x:focus-visible {
  opacity: 1;
}

.chip__x:hover {
  color: var(--ink);
  background: var(--fill-2);
}

.chip__ask {
  padding: 0 4px 0 10px;
  font-size: 13px;
  color: var(--text);
}

.strip__note {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--warn-ink);
}

.strip__note--ok {
  color: var(--ok-ink);
}

@media (hover: none) {
  .chip__x {
    opacity: 1;
  }
}
</style>
