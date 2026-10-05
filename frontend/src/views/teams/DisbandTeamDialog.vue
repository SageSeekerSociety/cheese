<script setup lang="ts">
// 「解散团队」：撤不回的那一下，所以照 GitHub 删组织的做法，要把团队名原样打一遍，
// 按钮才按得下去。被拒时（团队里还有没归档的项目）弹窗不关，那句理由就是下一步。
import type { Team } from '@/types'

import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'

const props = defineProps<{ team: Team }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ disbanded: [team: Team] }>()

const typed = ref('')
const disbanding = ref(false)
const error = ref<string | null>(null)
const matches = computed(() => typed.value.trim() === props.team.name)

watch(
  open,
  (v) => {
    if (!v) return
    typed.value = ''
    error.value = null
  },
  { immediate: true }
)

async function disband() {
  if (!matches.value) return
  disbanding.value = true
  error.value = null
  try {
    await TeamsApi.del(props.team.id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('home.nav.disbandTeamFailed')
    disbanding.value = false
    return
  }
  disbanding.value = false
  open.value = false
  emit('disbanded', props.team)
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('home.nav.disbandTeamTitle', { name: team.name })"
    :primary-label="t('home.nav.disbandTeam')"
    :primary-loading="disbanding"
    :primary-disabled="!matches"
    primary-danger
    :max-width="480"
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
