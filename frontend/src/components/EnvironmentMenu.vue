<script setup lang="ts">
// 行尾那颗「改」：点开是环境选择，选定后交给 `save`。名册里每个 AI 队友的会话一颗，
// 「新会话默认」那一行一颗；存到哪儿由 `save` 决定，这里只管开合、忙、错。
import type { Environment, EnvironmentDevice, SandboxSpec } from '../types/compute'

import { ref } from 'vue'

import { t } from '../i18n'

import EnvironmentPicker from './EnvironmentPicker.vue'

const props = defineProps<{
  heading: string
  hint?: string
  current: Environment | null
  devices: EnvironmentDevice[]
  specs: SandboxSpec[]
  save: (env: Environment) => Promise<{ proposal?: { content: string } | null } | void>
}>()
const emit = defineEmits<{ changed: [] }>()

const open = ref(false)
const saving = ref(false)
const error = ref('')
// 撞上项目策略时这次选择没有发生，换来一条等人点头的提议：不是错误，也不收菜单，
// 那句话就留在他刚按的地方。
const proposal = ref('')

async function pick(env: Environment) {
  saving.value = true
  error.value = ''
  proposal.value = ''
  try {
    const saved = await props.save(env)
    if (saved && saved.proposal) {
      proposal.value = saved.proposal.content
      return
    }
    open.value = false
    emit('changed')
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('compute.env.saveFailed')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <v-menu v-model="open" location="bottom end" :close-on-content-click="false">
    <template #activator="{ props: menuProps }">
      <button type="button" class="env-action" v-bind="menuProps">{{ t('compute.env.edit') }}</button>
    </template>
    <v-card class="env-menu">
      <div class="env-heading">{{ heading }}</div>
      <p v-if="hint" class="env-menu-hint">{{ hint }}</p>
      <EnvironmentPicker
        :current="current"
        :devices="devices"
        :specs="specs"
        :busy="saving"
        @select="pick"
        @cancel="open = false"
      />
      <p v-if="proposal" role="status" class="env-proposal">{{ proposal }}</p>
      <p v-if="error" role="alert" class="env-error">{{ error }}</p>
    </v-card>
  </v-menu>
</template>

<style scoped>
/* 名册里行尾那颗小动作，和「更换」同一档。 */
.env-action {
  flex: none;
  margin-left: auto;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.env-action:hover {
  color: var(--ink);
}
.env-menu {
  width: 420px;
  max-width: calc(100vw - 32px);
  max-height: 70vh;
  overflow-y: auto;
  padding: 8px;
}
.env-heading {
  padding: 8px;
  font-size: 14px;
  font-weight: 600;
}
.env-menu-hint {
  margin: 0 8px 8px;
  color: var(--muted);
  font-size: 13px;
}
.env-error {
  padding: 8px;
  color: var(--danger-ink);
  font-size: 13px;
}
.env-proposal {
  padding: 8px;
  color: var(--warn-ink);
  font-size: 13px;
}
</style>
