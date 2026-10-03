<template>
  <IndexView
    :space-name="space?.name"
    :avatar="space?.avatarId ? getAvatarUrl(space.avatarId) : undefined"
    :groups="groups"
    :active="active"
    :title="inTemplateForm ? '' : activeLabel"
    :back-to="backTo"
    @close="close"
  >
    <router-view />
    <!-- 只有「分类与话题」一栏往这里放东西：话题管理画在分类下面。 -->
    <router-view name="topics" />
  </IndexView>
</template>

<script setup lang="ts">
// 空间设置：盖在整个窗口上的一层（components/common/SettingsOverlay），左边六栏，
// 每一栏是 `manage/settings` 下的一条子路由，可以单独链接。关掉回到打开之前的那一页；
// 直接从链接打开时回这个空间的题目列表。
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { storeToRefs } from 'pinia'

import { getAvatarUrl } from '@/utils/materials'

import IndexView from './IndexView.vue'

import { closeOverlay } from '@/lib/backOut'
import { pageBeforeSettings } from '@/lib/settingsReturn'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const { currentSpace: space } = storeToRefs(useSpaceStore())

const spaceId = computed(() => String(route.params.spaceId))

const SECTIONS = [
  { name: 'SpacesDetailSettingsBasic', label: 'spaces.settings.tabs.basic', icon: 'mdi-information-outline' },
  { name: 'SpacesDetailSettingsCategories', label: 'spaces.settings.tabs.categories', icon: 'mdi-shape-outline' },
  { name: 'SpacesDetailSettingsTemplates', label: 'spaces.settings.tabs.templates', icon: 'mdi-file-document-outline' },
  { name: 'SpacesDetailSettingsInviteCodes', label: 'spaces.settings.tabs.inviteCodes', icon: 'mdi-ticket-outline' },
  { name: 'SpacesDetailSettingsDomainGroups', label: 'spaces.settings.tabs.domainGroups', icon: 'mdi-email-outline' },
  { name: 'SpacesDetailSettingsGuidance', label: 'spaces.settings.tabs.guidance', icon: 'mdi-robot-outline' },
  { name: 'SpacesDetailSettingsMaterials', label: 'spaces.settings.tabs.materials', icon: 'mdi-folder-outline' },
]

const groups = computed(() => [
  {
    key: 'space',
    items: SECTIONS.map((s) => ({
      key: s.name,
      label: t(s.label),
      icon: s.icon,
      to: { name: s.name, params: { spaceId: spaceId.value } },
    })),
  },
])

const TEMPLATE_FORMS = ['SpacesDetailCreateTemplate', 'SpacesDetailEditTemplate']
const inTemplateForm = computed(() => TEMPLATE_FORMS.includes(String(route.name)))

/** 当前是哪一栏。模板表单算在「题目模板」下面；停在设置自己的地址上时没有当前栏
 *  （手机上那就是目录）。 */
const active = computed(() => {
  if (inTemplateForm.value) return 'SpacesDetailSettingsTemplates'
  return route.name === 'SpacesDetailSettings' ? null : String(route.name)
})

/** 手机上左上角返回：模板表单回模板列表，其余回目录。 */
const backTo = computed(() =>
  inTemplateForm.value
    ? { name: 'SpacesDetailSettingsTemplates', params: { spaceId: spaceId.value } }
    : { name: 'SpacesDetailSettings', params: { spaceId: spaceId.value } }
)

const activeLabel = computed(() => {
  const section = SECTIONS.find((s) => s.name === active.value)
  return section ? t(section.label) : ''
})

// 桌面上没有单独的目录页，目录一直在左边：落到第一栏。
watch(
  [() => route.name, mdAndUp],
  ([name, desktop]) => {
    if (name === 'SpacesDetailSettings' && desktop) {
      router.replace({ name: 'SpacesDetailSettingsBasic', params: { spaceId: spaceId.value } })
    }
  },
  { immediate: true }
)

function close() {
  closeOverlay(router, pageBeforeSettings({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))
}
</script>
