<script setup lang="ts">
// 一台设备提供给哪些团队：每个团队一个勾。自己名下的项目也列在这里，不叫团队，叫「我自己的项目」
// （个人团队在界面上不当团队）。勾上或取消就改，交给调用方去存。
import type { MyTeam } from '@/cx_types'

import { t } from '@/i18n'

defineOptions({ name: 'DeviceTeamsPicker' })

const props = defineProps<{ teams: MyTeam[]; modelValue: number[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [ids: number[]] }>()

function label(team: MyTeam) {
  return team.personal ? t('account.thisDevice.ownProjects') : team.name
}

function toggle(id: number, on: boolean) {
  const next = props.modelValue.filter((x) => x !== id)
  emit('update:modelValue', on ? [...next, id] : next)
}
</script>

<template>
  <div class="teams-picker">
    <v-checkbox
      v-for="team in [...teams].sort((a, b) => Number(!!b.personal) - Number(!!a.personal))"
      :key="team.id"
      :model-value="modelValue.includes(team.id)"
      :label="label(team)"
      :disabled="disabled"
      density="compact"
      color="primary"
      hide-details
      @update:model-value="(on) => toggle(team.id, !!on)"
    />
  </div>
</template>

<style scoped>
.teams-picker {
  display: flex;
  flex-direction: column;
}
</style>
