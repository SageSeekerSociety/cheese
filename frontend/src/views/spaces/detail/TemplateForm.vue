<template>
  <!-- 「题目模板」下一级的整页表单，画在空间设置那一层里：左上角回到模板列表。 -->
  <div class="tform">
    <header>
      <router-link :to="listRoute" class="tform__crumb">
        <v-icon icon="mdi-chevron-left" size="16" />{{ t('spaces.settings.tabs.templates') }}
      </router-link>
      <h1 class="t-page-title">
        {{ isEditing ? t('spaces.detail.templateForm.editTitle') : t('spaces.detail.templateForm.createTitle') }}
      </h1>
    </header>

    <v-form class="tform__form" @submit.prevent="saveTemplate">
      <section class="settings-card">
        <div class="srow">
          <label class="srow__k" for="tpl-name">{{ t('spaces.detail.templateForm.templateName') }}</label>
          <v-text-field
            id="tpl-name"
            v-model="template.name"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            required
          />
        </div>
        <div class="srow srow--top">
          <label class="srow__k" for="tpl-desc">{{ t('spaces.detail.templateForm.templateDescription') }}</label>
          <v-textarea
            id="tpl-desc"
            v-model="template.description"
            autocomplete="off"
            density="compact"
            variant="outlined"
            rows="2"
            auto-grow
            hide-details
          />
        </div>
        <div class="srow">
          <label class="srow__k" for="tpl-title">{{ t('spaces.detail.templateForm.contestTitle') }}</label>
          <v-text-field
            id="tpl-title"
            v-model="template.title"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            required
          />
        </div>
      </section>

      <section class="settings-card">
        <div class="settings-card__title">{{ t('spaces.detail.templateForm.defaults') }}</div>
        <div class="settings-card__desc">{{ t('spaces.detail.templateForm.defaultsHint') }}</div>
        <div class="srow">
          <span class="srow__k">{{ t('tasks.form.participantType') }}</span>
          <v-radio-group v-model="template.submitterType" inline hide-details density="compact">
            <v-radio :label="t('tasks.form.individual')" value="USER" />
            <v-radio :label="t('tasks.form.team')" value="TEAM" />
            <v-radio :label="t('spaces.detail.templateForm.unset')" :value="null" />
          </v-radio-group>
        </div>
        <div v-if="template.submitterType === 'TEAM'" class="srow">
          <span class="srow__k">{{ t('spaces.detail.templateForm.teamSize') }}</span>
          <div class="tform__range">
            <v-text-field
              v-model.number="template.minTeamSize"
              :aria-label="t('spaces.detail.templateForm.minTeamSize')"
              type="number"
              min="1"
              density="compact"
              variant="outlined"
              hide-details
            />
            <span class="tform__to">{{ t('spaces.detail.templateForm.to') }}</span>
            <v-text-field
              v-model.number="template.maxTeamSize"
              :aria-label="t('spaces.detail.templateForm.maxTeamSize')"
              type="number"
              min="1"
              density="compact"
              variant="outlined"
              hide-details
            />
          </div>
        </div>
        <div class="srow">
          <span class="srow__k">{{ t('tasks.form.taskLevel') }}</span>
          <v-radio-group v-model="template.rank" inline hide-details density="compact">
            <v-radio :label="t('tasks.form.beginner')" :value="1" />
            <v-radio :label="t('tasks.form.intermediate')" :value="2" />
            <v-radio :label="t('tasks.form.advanced')" :value="3" />
            <v-radio :label="t('spaces.detail.templateForm.unset')" :value="null" />
          </v-radio-group>
        </div>
        <div class="srow">
          <label class="srow__k" for="tpl-deadline">{{ t('tasks.form.defaultDeadline') }}</label>
          <div class="tform__range">
            <span class="tform__to">{{ t('spaces.detail.templateForm.deadlinePrefix') }}</span>
            <v-text-field
              id="tpl-deadline"
              v-model.number="template.defaultDeadline"
              type="number"
              min="1"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              class="tform__deadline"
            />
            <span class="tform__to">{{ t('spaces.detail.templateForm.deadlineSuffix') }}</span>
          </div>
        </div>
        <div class="srow srow--top">
          <span class="srow__k">{{ t('spaces.detail.templateForm.realName') }}</span>
          <div>
            <v-radio-group v-model="template.requireRealName" inline hide-details density="compact">
              <v-radio :label="t('spaces.detail.templateForm.requireRealName')" :value="true" />
              <v-radio :label="t('spaces.detail.templateForm.noRealName')" :value="false" />
              <v-radio :label="t('spaces.detail.templateForm.unset')" :value="null" />
            </v-radio-group>
            <p v-if="template.requireRealName === true" class="tform__note">
              {{ t('spaces.detail.templateForm.realNameNote') }}
            </p>
          </div>
        </div>
      </section>

      <section class="settings-card">
        <div class="settings-card__title">{{ t('tasks.form.taskDescription') }}</div>
        <div class="tform__editor">
          <TipTapEditor
            v-model="template.content"
            output="json"
            :min-height="200"
            :max-height="1000"
            :aria-label="t('spaces.detail.templateForm.contestDescription')"
          />
        </div>
      </section>

      <div class="tform__actions">
        <v-btn variant="text" :to="listRoute">{{ t('spaces.detail.templateForm.cancel') }}</v-btn>
        <v-btn type="submit" color="primary" variant="flat">{{ t('spaces.detail.templateForm.save') }}</v-btn>
      </div>
    </v-form>
  </div>
</template>

<script setup lang="ts">
import type { JSONContent } from '@tiptap/core'

import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { closeOverlay } from '@/lib/backOut'
import { useSpaceStore } from '@/stores/space'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

const router = useRouter()
const route = useRoute()
const spaceId = Number(route.params.spaceId)
const templateIndex = route.params.templateIndex ? Number(route.params.templateIndex) : undefined

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, templates } = storeToRefs(spaceStore)

const isEditing = computed(() => templateIndex !== undefined)

const template = ref({
  name: '',
  description: '',
  title: '',
  content: {} as JSONContent,
  submitterType: null as 'USER' | 'TEAM' | null,
  rank: null as number | null,
  minTeamSize: 1,
  maxTeamSize: 10,
  defaultDeadline: null as number | null,
  requireRealName: null as boolean | null,
})

const { t } = useI18n()

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
      template.value = {
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

const saveTemplate = async () => {
  if (!currentSpace.value) return

  try {
    let updatedTemplates = [...templates.value]

    // 转换为要保存的格式
    const updatedTemplate = {
      ...template.value,
      content: JSON.stringify(template.value.content),
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

const listRoute = { name: 'SpacesDetailSettingsTemplates', params: { spaceId } }
</script>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.tform {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.tform__crumb {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-decoration: none;
}

.tform__crumb:hover {
  color: var(--ink);
}

.tform__form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.srow {
  grid-template-columns: 180px minmax(0, 1fr);
}

.srow--top {
  align-items: start;
}

.srow--top > .srow__k {
  padding-top: 8px;
}

.tform__range {
  display: flex;
  gap: 8px;
  align-items: center;
  max-width: 280px;
}

.tform__to {
  flex-shrink: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.tform__deadline {
  flex: 0 0 120px;
}

.tform__note {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.tform__editor {
  padding: 0 24px 20px;
}

.tform__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

@media (max-width: 599.98px) {
  .srow {
    grid-template-columns: minmax(0, 1fr);
  }

  .tform__editor {
    padding: 0 16px 16px;
  }
}
</style>
