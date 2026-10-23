<template>
  <TeamSettingsView
    :name="team.name"
    :handle="team.handle"
    :avatar="getAvatarUrl(team.avatarId)"
    :personal="team.personal"
    :groups="groups"
    :active="active"
    :title="activeLabel"
    :actions="headerCommands"
    :back-to="{ name: 'TeamsDetailSettings', params: { handle: team.handle } }"
    @close="close"
  >
    <slot />
  </TeamSettingsView>
</template>

<script setup lang="ts">
// 团队设置：盖在整个窗口上的一层（components/common/SettingsOverlay），和个人、空间、
// 项目的设置同一个外框。左边成员、设备、模型、额度四栏，每一栏是团队下的一条路由；
// 自己名下谁也加不进来，没有「成员」那一栏。关掉回到打开之前的那一页，直接从链接
// 打开时回这个团队的项目。各栏用 useCommands 登记的页头动作（模型那一栏的「重新检查」）
// 画在这一栏的标题旁边：这一层没有团队页的页头。
import type { Team } from '@/types'

import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'

import TeamSettingsView from './TeamSettingsView.vue'

import { headerCommands } from '@/commands'
import { t } from '@/i18n'
import { closeOverlay } from '@/lib/backOut'
import { pageBeforeSettings } from '@/lib/settingsReturn'

const props = defineProps<{ team: Team }>()

defineSlots<{ default?: () => unknown }>()

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()

const SECTIONS = [
  { name: 'TeamsDetailMembers', label: 'home.nav.teamMembers', icon: 'mdi-account-multiple-outline' },
  { name: 'TeamsDetailCompute', label: 'home.nav.teamCompute', icon: 'mdi-laptop' },
  { name: 'TeamsDetailModels', label: 'home.nav.teamModels', icon: 'mdi-creation-outline' },
  { name: 'TeamsDetailCredits', label: 'home.nav.teamCredits', icon: 'mdi-wallet-outline' },
] as const

const sections = computed(() => SECTIONS.filter((s) => !(props.team.personal && s.name === 'TeamsDetailMembers')))

const groups = computed(() => [
  {
    key: 'team',
    items: sections.value.map((s) => ({
      key: s.name,
      label: t(s.label),
      icon: s.icon,
      to: { name: s.name, params: { handle: props.team.handle } },
    })),
  },
])

/** 当前是哪一栏；停在 `settings` 自己的地址上时没有（手机上那就是目录）。 */
const active = computed(() => (route.name === 'TeamsDetailSettings' ? null : String(route.name)))

const activeLabel = computed(() => {
  const section = SECTIONS.find((s) => s.name === active.value)
  return section ? t(section.label) : ''
})

// 桌面上目录一直在左边，没有单独的目录页：落到第一栏。
watch(
  [() => route.name, mdAndUp],
  ([name, desktop]) => {
    const first = sections.value[0]
    if (name === 'TeamsDetailSettings' && desktop && first) {
      void router.replace({ name: first.name, params: { handle: props.team.handle } })
    }
  },
  { immediate: true }
)

function close() {
  closeOverlay(router, pageBeforeSettings({ name: 'TeamsDetailDefault', params: { handle: props.team.handle } }))
}
</script>
