<script setup lang="ts">
// 输入框上方一排「这一句带上的」：当前页面（点一下不带，再点一下带上），和人在页面上
// 选中、交给芝士的那段字（✕ 去掉）。照 Claude Code 在输入框边上写着当前文件和选中行的
// 做法：发出去之前就看得见芝士会读到什么。
import { t } from '@/i18n'

defineProps<{
  /** 这一页叫什么；页面没登记、芝士读不到这一页时是 null，不画这一枚。 */
  page: string | null
  pageOff: boolean
  selection: string | null
}>()

const emit = defineEmits<{
  togglePage: []
  clearSelection: []
}>()
</script>

<template>
  <div v-if="page || selection" class="ctx">
    <button
      v-if="page"
      type="button"
      class="ctx__chip ctx__page"
      :class="{ 'is-off': pageOff }"
      :aria-pressed="!pageOff"
      :title="t(pageOff ? 'tasks.assistant.context.pageOff' : 'tasks.assistant.context.pageOn')"
      @click="emit('togglePage')"
    >
      <v-icon size="14">{{ pageOff ? 'mdi-eye-off-outline' : 'mdi-eye-outline' }}</v-icon>
      <span class="ctx__k">{{ t('tasks.assistant.context.page') }}</span>
      <span class="ctx__v" data-user-content>{{ page }}</span>
    </button>
    <span v-if="selection" class="ctx__chip" :title="selection">
      <span class="ctx__k">{{ t('tasks.assistant.context.selection') }}</span>
      <span class="ctx__v" data-user-content>{{ selection }}</span>
      <button
        type="button"
        class="ctx__x"
        :aria-label="t('tasks.assistant.context.removeSelection')"
        :title="t('tasks.assistant.context.removeSelection')"
        @click="emit('clearSelection')"
      >
        <v-icon size="14">mdi-close</v-icon>
      </button>
    </span>
  </div>
</template>

<style scoped>
.ctx {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.ctx__chip {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  max-width: 100%;
  padding: 0 8px;
  font-size: 13px;
  line-height: 24px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.ctx__page {
  cursor: pointer;
}

.ctx__page:hover {
  background: var(--fill-2);
}

.ctx__page:focus-visible,
.ctx__x:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.ctx__k {
  flex-shrink: 0;
  color: var(--muted);
}

.ctx__v {
  min-width: 0;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ctx__page.is-off .ctx__v {
  color: var(--muted);
  text-decoration: line-through;
}

.ctx__x {
  display: grid;
  place-items: center;
  padding: 0;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ctx__x:hover {
  color: var(--ink);
}
</style>
