<script setup lang="ts">
// 现场顶上按队友筛的那一排：一个房间可以先后、甚至同时交给几个队友，人来看的往往
// 是其中一个在干什么。钉在滚动层顶上，切队友不该先滚回去。
//
// 这一只只画：名册、名字和「现在看的是谁」都由外面给 —— 那是攒出来的一份（每读
// 一页记下新露面的作者），不是每次从手上这一窗现算的；只看一个队友时现算的话，
// 这排会当场塌成一个 tab，人就切不回去了。
//
// 只有一个队友时不画：那时候没有可筛的。
import { t } from '@/i18n'
import { vRovingTabs } from '@/lib/rovingTabs'

defineProps<{
  /** 做过一步、说过一句的队友 handle，按露面的先后。 */
  agents: string[]
  /** handle → 页签上写的名字（重名的从第二个起带序号）。 */
  labels: Record<string, string>
  /** 现在看的是谁，null 是「全部」。 */
  viewing: string | null
  /** 页签是给谁切内容的：底下那个日志区的 id。 */
  controls: string
}>()

defineEmits<{ (e: 'select', handle: string | null): void }>()
</script>

<template>
  <div
    v-if="agents.length > 1"
    v-roving-tabs
    class="site-agents"
    role="tablist"
    :aria-label="t('work.room.site.agents.label')"
  >
    <button
      type="button"
      role="tab"
      class="site-agents__tab"
      :class="{ 'site-agents__tab--on': viewing === null }"
      :aria-selected="viewing === null"
      :aria-controls="controls"
      @click="$emit('select', null)"
    >
      {{ t('work.room.site.agents.all') }}
    </button>
    <button
      v-for="a in agents"
      :key="a"
      type="button"
      role="tab"
      class="site-agents__tab"
      :class="{ 'site-agents__tab--on': viewing === a }"
      :aria-selected="viewing === a"
      :aria-controls="controls"
      @click="$emit('select', a)"
    >
      {{ labels[a] }}
    </button>
  </div>
</template>

<style scoped>
/* 按队友看：一排文字按钮，选中的那个换底色和墨色，不用琥珀——这里不是主操作。 */
/* 钉住的是外层表头（`.site-head`），不是这一排自己：两个 sticky 同顶会互相盖住。 */
.site-agents {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 6px 12px;
  background: var(--surface);
}
.site-agents__tab {
  padding: 2px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.site-agents__tab:hover {
  background: var(--fill);
}
.site-agents__tab--on {
  background: var(--fill);
  color: var(--ink);
  font-weight: 600;
}
</style>
