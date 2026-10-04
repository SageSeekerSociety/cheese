<script setup lang="ts">
// 项目本体里那张「开始清单」：四步，勾完自己就不见了。
//
// 这个组件只画：有哪几步、各自做没做、去哪一步。判据全在
// `composables/useGettingStarted.ts` 里，从房间里和项目里已有的状态推出来，
// 不落字段、不加迁移。
//
// 跳转走 `useNavigation()`，不 import vue-router：宿主没装路由时它是 `null`，
// 那时按钮点了也不动，但卡片照旧画得出来。
import type { GettingStartedStep, GettingStartedStepKey } from '@/composables/useGettingStarted'
import type { NavTarget } from '@/lib/navTarget'

import { useNavigation } from '@/composables/useNavigation'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  steps: GettingStartedStep[]
  projectId: string
}>()
const emit = defineEmits<{ (e: 'dismiss'): void }>()

const nav = useNavigation()

// 每一步做掉它要打开的那一页。第一步不在别处做——它就在下面那个输入框里——所以
// 它没有去处，右边给一句提示。
const DESTINATIONS: Partial<Record<GettingStartedStepKey, (projectId: string) => NavTarget>> = {
  materials: (projectId) => ({ name: 'project-library', params: { projectId } }),
  repo: (projectId) => ({ name: 'project-settings', params: { projectId, section: 'repository' } }),
  people: (projectId) => ({ name: 'project-members', params: { projectId } }),
}

function go(key: GettingStartedStepKey, projectId: string) {
  const destination = DESTINATIONS[key]
  if (destination) nav?.navigate(destination(projectId))
}
</script>

<template>
  <section class="gs" :aria-label="t('work.room.gettingStarted.title')">
    <div class="gs__head">
      <h2 class="gs__title">{{ t('work.room.gettingStarted.title') }}</h2>
      <button type="button" class="gs__dismiss" @click="emit('dismiss')">
        {{ t('work.room.gettingStarted.dismiss') }}
      </button>
    </div>

    <ul class="gs__steps">
      <li v-for="step in steps" :key="step.key" class="gs__step">
        <v-icon
          :icon="step.done ? 'mdi-check-circle' : 'mdi-circle-outline'"
          size="16"
          class="gs__mark"
          :class="{ 'gs__mark--done': step.done }"
        />
        <span class="gs__label" :class="{ 'gs__label--done': step.done }">
          {{ t(`work.room.gettingStarted.${step.key}`) }}
        </span>
        <template v-if="!step.done">
          <BaseButton v-if="DESTINATIONS[step.key]" kind="ghost" size="sm" @click="go(step.key, projectId)">
            {{ t(`work.room.gettingStarted.${step.key}Action`) }}
          </BaseButton>
          <span v-else class="gs__hint">{{ t(`work.room.gettingStarted.${step.key}Hint`) }}</span>
        </template>
      </li>
    </ul>

    <p class="gs__note">{{ t('work.room.gettingStarted.note') }}</p>
  </section>
</template>

<style scoped>
/* 和验收卡一样贴在输入框上方：一条边把它和对话分开，界面底色和对话栏一致。 */
.gs {
  margin: 0 12px 8px;
  padding: 10px 12px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.gs__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.gs__title {
  margin: 0;
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}
.gs__dismiss {
  flex: none;
  border: none;
  background: none;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  color: var(--faint);
  font-size: 12px;
  cursor: pointer;
}
.gs__dismiss:hover {
  color: var(--muted);
}
.gs__steps {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}
.gs__step {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}
.gs__mark {
  flex: none;
  color: var(--faint);
}
.gs__mark--done {
  color: var(--ok);
}
.gs__label {
  flex: 1;
  min-width: 0;
  color: var(--ink);
  font-size: 13px;
}
.gs__label--done {
  color: var(--faint);
  text-decoration: line-through;
}
.gs__hint {
  flex: none;
  color: var(--faint);
  font-size: 12px;
}
.gs__note {
  margin: 8px 0 0;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 12px;
}
</style>
