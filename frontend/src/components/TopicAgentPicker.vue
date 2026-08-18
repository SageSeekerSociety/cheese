<script setup lang="ts">
// 这个话题现在交给哪个 AI 队友，以及把它交给别人。它长在成员名册里芝士那一行上
// （TopicMembers）——芝士是这个房间的成员，换掉它属于「这个房间里有谁」，不属于
// 「这条消息」，所以它不在输入区。
//
// 换人**要丢掉这个话题的会话**（一段由别人说过话的对话，被另一个身份接着往下
// 说，就是它在自信地记得自己没说过的事）。所以这不是一个开关，是一个有代价的
// 动作 —— 换之前必须让人知道，后端也把这件事写进了返回值（session_reset）。
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
const confirming = ref<ProjectAgent | null>(null)

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

async function confirmSwitch() {
  const target = confirming.value
  if (!target) return
  switching.value = true
  try {
    current.value = await setTopicAgent(props.topicId, target.id)
    confirming.value = null
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
        <v-list-item v-for="a in others" :key="a.id ?? a.handle" @click="confirming = a">
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

    <!-- 代价说在前面。换完再告诉人「对话没了」，那是通知不是选择。 -->
    <v-dialog :model-value="confirming !== null" max-width="420" @update:model-value="confirming = null">
      <v-card>
        <v-card-title class="t-title">换成「{{ confirming?.display_name }}」？</v-card-title>
        <v-card-text class="t-body">
          这个话题现在的对话会重新开始 —— 换一个队友接着说同一段对话，它会记得自己没说过的话。
          已经写下的消息、文档和改动都还在。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirming = null">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="switching" @click="confirmSwitch">换</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
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
