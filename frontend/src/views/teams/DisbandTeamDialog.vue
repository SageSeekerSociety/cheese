<script setup lang="ts">
// 「解散团队」：撤不回的那一下，所以照 GitHub 删组织的做法，要把团队名原样打一遍，
// 按钮才按得下去。
//
// 解散本身在外面做（`composables/useTeamNav.ts`）：这一件只收名字、只在打对了以后让人
// 按下去（A 档）。被拒时（团队里还有没归档的项目）弹窗不关，那句理由由外面给回来。
import type { Team } from '@/types'

import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 要解散的团队。 */
  team: Team
  /** 正在解散：按钮转圈。 */
  busy?: boolean
  /** 上一次被拒的理由，原样画在下面。 */
  error?: string | null
}>()
const emit = defineEmits<{ confirm: [] }>()

const open = defineModel<boolean>({ required: true })

const typed = ref('')
const matches = computed(() => typed.value.trim() === props.team.name)

// 每次打开都从空开始：上一次打对过的名字不能替这一次作数。
watch(
  open,
  (now) => {
    if (now) typed.value = ''
  },
  { immediate: true }
)

function confirm() {
  if (matches.value && !props.busy) emit('confirm')
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('home.nav.disbandTeamTitle', { name: team.name })"
    :primary-label="t('home.nav.disbandTeam')"
    :primary-loading="busy"
    :primary-disabled="!matches"
    primary-danger
    :max-width="480"
    @primary="confirm"
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
        @keyup.enter="confirm"
      />
      <v-alert v-if="error" type="error" density="comfortable" class="mt-4">{{ error }}</v-alert>
    </div>
  </AdaptiveDialog>
</template>
