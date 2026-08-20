<script setup lang="ts">
// 这个话题现在交给哪个 AI 队友，以及把它交给别人。它长在成员名册里芝士那一行上
// （TopicMembers）——芝士是这个房间的成员，换掉它属于「这个房间里有谁」，不属于
// 「这条消息」，所以它不在输入区。
//
// 换人不丢任何东西：每个 agent 在这个房间里的对话是它自己的一行，换来的那个从头
// 开始，换走的那个原样留着，换回来还能接上。所以这里没有确认框——一个不需要付出
// 代价的动作弹窗问一遍，只是在教人无视弹窗。
import type { TopicAgent } from '../api'
import type { ProjectAgent } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { getTopicAgent, listProjectAgents, setTopicAgent } from '../api'

const props = defineProps<{ topicId: string; projectId: string }>()

const current = ref<TopicAgent | null>(null)
const agents = ref<ProjectAgent[]>([])
// 接口没上线（旧环境）就整个不显示 —— 一个点了没反应的入口比没有入口更糟。
const unavailable = ref(false)
const switching = ref(false)

async function load() {
  try {
    const [agent, list] = await Promise.all([getTopicAgent(props.topicId), listProjectAgents(props.projectId)])
    current.value = agent
    agents.value = list.data
    unavailable.value = false
  } catch {
    unavailable.value = true
  }
}

watch(() => [props.topicId, props.projectId], load, { immediate: true })

// 「跟着项目默认」和「就是选了这一个」在界面上必须分得开：换了项目默认，前者
// 会跟着变，后者不会。
const label = computed(() => {
  if (!current.value) return ''
  return current.value.inherited ? `${current.value.display_name}（默认）` : current.value.display_name
})

const others = computed(() => agents.value.filter((a) => a.id !== current.value?.instance_id))

async function swap(target: ProjectAgent) {
  switching.value = true
  try {
    current.value = await setTopicAgent(props.topicId, target.id)
  } finally {
    switching.value = false
  }
}
</script>

<template>
  <div v-if="!unavailable && current" class="d-inline-flex align-center">
    <v-menu location="bottom end">
      <template #activator="{ props: menu }">
        <!-- 它长在成员名册里芝士那一行上，和人那一行的角色按钮同一个样子：
             芝士就是这个房间的成员，换掉它和换一个人的角色是同一类动作。
             名字由名册那一行写，这里只说动作。 -->
        <button v-bind="menu" type="button" class="ap-swap" :disabled="switching">
          换
          <v-icon size="12">mdi-chevron-down</v-icon>
        </button>
      </template>
      <v-list density="compact" min-width="220">
        <v-list-subheader class="t-meta">当前：{{ label }}</v-list-subheader>
        <v-list-item v-for="a in others" :key="a.id ?? a.handle" @click="swap(a)">
          <v-list-item-title>{{ a.display_name }}</v-list-item-title>
          <template v-if="a.is_default" #append>
            <span class="t-meta c-muted">默认</span>
          </template>
        </v-list-item>
        <v-list-item v-if="!others.length" disabled>
          <v-list-item-title class="t-meta c-muted">这个项目只有一个队友</v-list-item-title>
        </v-list-item>
      </v-list>
    </v-menu>
  </div>
</template>

<style scoped>
/* 和名册里人那一行的角色按钮同一个样子（TopicMembers 的 .roster__role--btn）。 */
.ap-swap {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}
.ap-swap:hover {
  background: var(--fill);
}
.ap-swap:disabled {
  cursor: default;
  opacity: 0.6;
}
</style>
