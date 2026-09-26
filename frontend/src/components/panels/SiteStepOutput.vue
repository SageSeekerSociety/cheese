<script setup lang="ts">
// 摊开的那一步下面：它打印了什么。默认收着——参数是这一行的主体，输出是想追问
// 的人才看的；而且几 KB 的输出一摊开，前后的步骤就都被挤出屏幕了。第一次点开
// 才去取，后端只留了末尾一截（见 backend/app/domain/agent/step_output.py）。
import { computed, ref } from 'vue'

import { getStepOutput } from '../../api'

import { t } from '@/i18n'

const props = defineProps<{
  topicId: string
  blockId: string
  /** 整段输出有多长（字节）；后端留下的至多是末尾 8 KiB。 */
  bytes: number
}>()

const open = ref(false)
const text = ref<string | null>(null)
const failed = ref(false)

function size(n: number): string {
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`
}

// 留下的比整段短：说一声只看到了末尾，以及看到了多少。
const kept = computed(() => (text.value === null ? 0 : new TextEncoder().encode(text.value).length))
const truncated = computed(() => text.value !== null && kept.value < props.bytes)

async function toggle() {
  open.value = !open.value
  if (!open.value || text.value !== null) return
  failed.value = false
  try {
    text.value = (await getStepOutput(props.topicId, props.blockId)).output
  } catch {
    failed.value = true
  }
}
</script>

<template>
  <div class="site-output">
    <button type="button" class="site-output__toggle" :aria-expanded="open" @click="toggle">
      {{ open ? t('work.room.site.output.hide') : t('work.room.site.output.show', { size: size(bytes) }) }}
    </button>
    <template v-if="open">
      <p v-if="failed" class="site-output__note site-output__note--failed">{{ t('work.room.site.output.failed') }}</p>
      <p v-else-if="text === null" class="site-output__note">{{ t('work.room.site.output.loading') }}</p>
      <template v-else>
        <p v-if="truncated" class="site-output__note">
          {{ t('work.room.site.output.tail', { size: size(kept) }) }}
        </p>
        <pre class="site-output__text" data-testid="site-step-output">{{ text }}</pre>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* 缩进到参数那一列，和上面的参数、错误摘要对齐（同 .site-act__error 的算法）。 */
.site-output {
  flex: 0 0 100%;
  min-width: 0;
  padding-left: calc(5px + 8px + 4em + 8px);
}
.site-output__toggle {
  padding: 0;
  border: 0;
  background: none;
  font-family: var(--font-sans);
  font-size: 12px;
  color: var(--faint);
  cursor: pointer;
}
.site-output__toggle:hover {
  color: var(--text);
}
.site-output__note {
  margin: 4px 0 0;
  font-family: var(--font-sans);
  font-size: 12px;
  color: var(--faint);
}
.site-output__note--failed {
  color: var(--danger-ink);
}
/* 输出自己滚动：一次长的构建日志不该把整栏撑开。 */
.site-output__text {
  margin: 4px 0 2px;
  padding: 8px;
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--canvas);
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
