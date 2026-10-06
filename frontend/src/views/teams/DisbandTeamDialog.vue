<script setup lang="ts">
// 「解散团队」：撤不回的那一下，所以照 GitHub 删组织的做法，要把团队名原样打一遍，
// 按钮才按得下去。被拒时（团队里还有没归档的项目）弹窗不关，那句理由就是下一步。
//
// 请求不在这里：按得下去之后只喊一声（`submit`），解散、说一句话、把这一行从侧栏上
// 摘掉都是外面的事。失败时外面把理由放回 `error`，弹窗留着不关。
import type { Team } from '@/types'

import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  modelValue: boolean
  team: Team
  /** 正在解散：按钮转起来，也挡住第二次提交。 */
  disbanding?: boolean
  /** 上一次为什么没解散成。 */
  error?: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: []
}>()

const typed = ref('')
const matches = computed(() => typed.value.trim() === props.team.name)

// 每次打开都是空的：上一次打了一半的名字、上一次的报错都不跟过来。
watch(
  () => props.modelValue,
  (open) => {
    if (open) typed.value = ''
  },
  { immediate: true }
)

function disband() {
  if (!matches.value || props.disbanding) return
  emit('submit')
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="t('home.nav.disbandTeamTitle', { name: team.name })"
    :primary-label="t('home.nav.disbandTeam')"
    :primary-loading="disbanding"
    :primary-disabled="!matches"
    primary-danger
    :max-width="480"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="disband"
  >
    <div class="t-body c-muted">
      {{ t('home.nav.disbandTeamBody') }}
      <div class="t-meta mt-4">{{ t('home.nav.disbandTeamTypeName', { name: team.name }) }}</div>
      <v-text-field
        v-model="typed"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        class="mt-2"
        :aria-label="t('home.nav.disbandTeamTypeName', { name: team.name })"
        @keyup.enter="disband"
      />
      <v-alert v-if="error" type="error" density="comfortable" class="mt-4">{{ error }}</v-alert>
    </div>
  </AdaptiveDialog>
</template>
