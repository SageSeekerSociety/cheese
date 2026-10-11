<script setup lang="ts">
// 题目模板那一张表单的画面：一列字段绑在一份草稿上，保存时把草稿发出去。认路、读模板、
// 真保存都归容器 `TemplateForm.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { TemplateDraft } from './templateDraft'

import { defineAsyncComponent, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

const props = defineProps<{
  initial: TemplateDraft
  isEditing: boolean
  listRoute: { name: string; params: { spaceId: number } }
}>()

const emit = defineEmits<{ submit: [draft: TemplateDraft] }>()

const { t } = useI18n()

// 下面四行「字段名」要能点着把焦点送进输入框：Vuetify 把 `id` 落在内层那个原生 `<input>`
// 上，`<label for>` 认它。但 eslint 的 `vuejs-accessibility/label-has-for`（默认
// `every: [nesting, id]`）只认「标签里套着原生控件」，Vuetify 字段套不出来，静态写
// `<label>` 必红（`settings/BasicInfoView` 那几个就是一直躺在基线里的这类告警）。repo 里
// `components/base/SettingsRow.vue` 用的办法是把标签交给动态组件渲染：运行时就是真的
// `<label for>`，点击聚焦、读屏关联都照常，只是静态检查看不见那个标签。这里沿用同一条。
const labelTag = 'label'

// 表单改的是一份本地草稿，不是容器递进来的那一份（`initial` 只当落种）。容器把读回来
// 的模板换掉时（`initial` 变了），草稿跟着重下一遍种子。
const template = ref<TemplateDraft>({ ...props.initial })
watch(
  () => props.initial,
  (next) => {
    template.value = { ...next }
  }
)

function saveTemplate() {
  emit('submit', { ...template.value })
}
</script>

<template>
  <!-- 「题目模板」下一级的整页表单，画在空间设置那一层里：左上角回到模板列表。 -->
  <div class="tform">
    <header>
      <NavLink :to="listRoute" class="tform__crumb">
        <v-icon icon="mdi-chevron-left" size="16" />{{ t('spaces.settings.tabs.templates') }}
      </NavLink>
      <h1 class="t-page-title">
        {{ isEditing ? t('spaces.detail.templateForm.editTitle') : t('spaces.detail.templateForm.createTitle') }}
      </h1>
    </header>

    <v-form class="tform__form" @submit.prevent="saveTemplate">
      <section class="settings-card">
        <div class="srow">
          <component :is="labelTag" id="tpl-name-label" class="srow__k" for="tpl-name">
            {{ t('spaces.detail.templateForm.templateName') }}
          </component>
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
          <component :is="labelTag" id="tpl-desc-label" class="srow__k" for="tpl-desc">
            {{ t('spaces.detail.templateForm.templateDescription') }}
          </component>
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
          <component :is="labelTag" id="tpl-title-label" class="srow__k" for="tpl-title">
            {{ t('spaces.detail.templateForm.contestTitle') }}
          </component>
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
          <component :is="labelTag" id="tpl-deadline-label" class="srow__k" for="tpl-deadline">
            {{ t('tasks.form.defaultDeadline') }}
          </component>
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
        <BaseButton kind="ghost" :to="listRoute">{{ t('spaces.detail.templateForm.cancel') }}</BaseButton>
        <BaseButton kind="primary" type="submit">{{ t('spaces.detail.templateForm.save') }}</BaseButton>
      </div>
    </v-form>
  </div>
</template>

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

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .srow {
    grid-template-columns: minmax(0, 1fr);
  }

  .tform__editor {
    padding: 0 16px 16px;
  }
}
</style>
