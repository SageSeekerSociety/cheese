<template>
  <TemplateFormView :initial="initial" :is-editing="isEditing" :list-route="listRoute" @submit="saveTemplate" />
</template>

<script setup lang="ts">
// 题目模板这一页的容器：认路、读模板、保存后回列表。画面在 `TemplateFormView.vue`
// （场景规则见 docs/manual/dev/scenes.md）。
import type { TemplateDraft } from './templateDraft'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { blankTemplate } from './templateDraft'
import TemplateFormView from './TemplateFormView.vue'

import { closeOverlay } from '@/lib/backOut'
import { useSpaceStore } from '@/stores/space'

const router = useRouter()
const route = useRoute()
const spaceId = Number(route.params.spaceId)
const templateIndex = route.params.templateIndex ? Number(route.params.templateIndex) : undefined

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, templates } = storeToRefs(spaceStore)

const { t } = useI18n()

const isEditing = computed(() => templateIndex !== undefined)

const initial = ref<TemplateDraft>(blankTemplate())

onMounted(async () => {
  if (isEditing.value) {
    await loadTemplate()
  }
})

const loadTemplate = async () => {
  if (!currentSpace.value) return

  try {
    if (templateIndex !== undefined && templates.value[templateIndex]) {
      const templateData = templates.value[templateIndex]
      initial.value = {
        name: templateData.name,
        description: templateData.description,
        title: templateData.title,
        content: JSON.parse(templateData.content),
        submitterType: templateData.submitterType || null,
        rank: templateData.rank || null,
        minTeamSize: templateData.minTeamSize || 1,
        maxTeamSize: templateData.maxTeamSize || 10,
        defaultDeadline: templateData.defaultDeadline || null,
        requireRealName: templateData.requireRealName !== undefined ? templateData.requireRealName : null,
      }
    }
  } catch (error) {
    console.error(t('spaces.detail.templateForm.loadTemplateFailed'), error)
    toast.error(t('spaces.detail.templateForm.loadTemplateFailed'))
  }
}

const listRoute = { name: 'SpacesDetailSettingsTemplates', params: { spaceId } }

const saveTemplate = async (draft: TemplateDraft) => {
  if (!currentSpace.value) return

  try {
    const updatedTemplates = [...templates.value]

    // 转换为要保存的格式
    const updatedTemplate = {
      ...draft,
      content: JSON.stringify(draft.content),
    }

    if (isEditing.value && templateIndex !== undefined) {
      updatedTemplates[templateIndex] = updatedTemplate
    } else {
      updatedTemplates.push(updatedTemplate)
    }

    await spaceData.updateTemplates(updatedTemplates)

    toast.success(
      isEditing.value ? t('spaces.detail.templateForm.updateSuccess') : t('spaces.detail.templateForm.createSuccess')
    )
    // 表单已经收工了，再按回退键不该回到一张已经保存过的表单上。去向是定的（列表），
    // 所以走 closeOverlay：身后正是列表就退一格。**不能 replace** —— 进来是 push 的，
    // replace 换掉的是表单这一格，身后那条还是列表，于是连出两条一样的地址，← 按下去
    // 看不出变化，要按两下才出得去。
    closeOverlay(router, listRoute)
  } catch (error) {
    console.error(t('spaces.detail.templateForm.saveTemplateFailed'), error)
    toast.error(t('spaces.detail.templateForm.saveTemplateFailed'))
  }
}
</script>
