<script setup lang="ts">
// 空间设置里「学情分析」这一栏的画面：开关、告知卡预览、知识点清单、隐私规则。
//
// 读写都不在这里 —— 容器 `LearningAnalysis.vue` 取数、把结果递进来，接住「保存」「加一条」
// 「改名」「删一条」去办（场景规则见 docs/manual/dev/scenes.md）。组件预览站里也能单独看。
//
// 四块的分工（照着可点原型 xueqing/demo.html 的 `vSettings()`）：
//
//   1. 开关：这一块内容一直在屏幕上，所以改完的结果**就地**说（§3.11 的 `SaveBar` +
//      `SaveStatus`），不靠一条几秒就走掉的 toast。开关是本地草稿，底下那条「保存」才
//      把它交给服务端 —— 开不开学情分析牵连的是全体成员，值当让人看一眼再落定。
//   2. 告知卡：内容是平台写死的（同一份 `LearningNoticeDialog`，成员进空间时看到的就是
//      它），这里只预览，点「预览」原样弹一张出来。
//   3. 知识点清单：所有题目共用，所以是**清单页**的样子 —— 标题下面一条工具行，一行一条，
//      行末两颗操作，和「邮箱域名组」同一形（`ManageDomainGroupsView.vue`）。
//   4. 隐私规则：三条平台规则，只读。空间里改不了，所以不给控件，只把值摆出来。
import type { SpaceKnowledgePoint } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseField from '@/components/base/BaseField.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveBar from '@/components/base/SaveBar.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import LearningNoticeDialog from '@/components/spaces/LearningNoticeDialog.vue'
import { useDialog } from '@/plugins/dialog'

const props = defineProps<{
  /** 这块板今天开没开学情分析。 */
  enabled: boolean
  /** 这块板的清单（所有题目共用）。 */
  knowledgePoints: SpaceKnowledgePoint[]
  /** 我在这块板能不能改设置：不能改时只摆，不给控件。 */
  canManage: boolean
  /** 这份配置正在读。 */
  loading: boolean
  /** 这份配置没读出来 —— 整块换成错误加一条重试的路（§3.10）。 */
  failed: boolean
  /** 保存开关的结果（§3.11 的三态由容器给）。 */
  saving: boolean
  saved: boolean
  saveError?: string | null
}>()

const emit = defineEmits<{
  save: [enabled: boolean]
  'add-point': [name: string]
  'rename-point': [payload: { id: number; name: string }]
  'remove-point': [id: number]
  retry: []
}>()

const { t } = useI18n()
const { confirm } = useDialog()

/** 开关这份草稿：读回来、换了一块板、保存后重新读回来，都照它重填。 */
const draft = ref(props.enabled)
watch(
  () => props.enabled,
  (next) => {
    draft.value = next
  }
)

/** 本地草稿和已保存的值不同：底下那条才展开（§3.11）。 */
const dirty = computed(() => draft.value !== props.enabled)

function revert() {
  draft.value = props.enabled
}

/** 点了预览：把成员进空间时看到的那一张原样弹出来。 */
const previewOpen = ref(false)

/** 「新建知识点」/「改个名字」那一张。`editing` 为空就是新建。 */
const nameOpen = ref(false)
const editing = ref<SpaceKnowledgePoint | null>(null)
const nameDraft = ref('')

const dialogTitle = computed(() =>
  editing.value
    ? t('spaces.learningAnalysis.knowledgePoint.rename', { name: editing.value.name })
    : t('spaces.learningAnalysis.knowledgePoint.add')
)

/** 三条平台规则：原文照平台写死的说法，一个字不改。 */
const privacyRows = computed(() => [
  { label: t('spaces.learningAnalysis.privacy.scope'), value: t('spaces.learningAnalysis.privacy.scopeValue') },
  { label: t('spaces.learningAnalysis.privacy.sample'), value: t('spaces.learningAnalysis.privacy.sampleValue') },
  { label: t('spaces.learningAnalysis.privacy.groups'), value: t('spaces.learningAnalysis.privacy.groupsValue') },
])

function openCreate() {
  editing.value = null
  nameDraft.value = ''
  nameOpen.value = true
}

function openRename(point: SpaceKnowledgePoint) {
  editing.value = point
  nameDraft.value = point.name
  nameOpen.value = true
}

function submitName() {
  const name = nameDraft.value.trim()
  if (!name) return
  if (editing.value) emit('rename-point', { id: editing.value.id, name })
  else emit('add-point', name)
  nameOpen.value = false
}

/** 删一条要问一句：删掉后新题目不再能选它，是个不可逆的动作（§3.11 的确认框）。 */
async function removePoint(point: SpaceKnowledgePoint) {
  const yes = await confirm(t('spaces.learningAnalysis.knowledgePoint.removeBody'), {
    title: t('spaces.learningAnalysis.knowledgePoint.removeTitle', { name: point.name }),
    confirmLabel: t('spaces.learningAnalysis.knowledgePoint.remove', { name: point.name }),
    danger: true,
  }).wait()
  if (yes) emit('remove-point', point.id)
}
</script>

<template>
  <p class="settings-page__lede la__lede">{{ t('spaces.learningAnalysis.intro') }}</p>

  <BaseLoadError v-if="failed" @retry="emit('retry')" />

  <div v-else-if="loading" class="settings-card la__loading">
    <v-progress-circular indeterminate color="primary" />
  </div>

  <template v-else>
    <!-- 1. 开关。改完的结果就地写在这一块底下（§3.11）。 -->
    <section class="settings-card">
      <div class="la__body">
        <SettingsRow
          for="learning-analysis-enabled"
          width="code"
          :label="t('spaces.learningAnalysis.switchTitle')"
          :description="t('spaces.learningAnalysis.switchHint')"
        >
          <v-switch
            id="learning-analysis-enabled"
            v-model="draft"
            :loading="saving"
            :disabled="!canManage || saving"
            color="primary"
            density="compact"
            inset
            hide-details
            data-testid="learning-analysis-enabled"
          />
        </SettingsRow>
      </div>

      <SaveBar
        :dirty="dirty"
        :saving="saving"
        :saved="saved"
        :error="saveError"
        :save-label="t('spaces.learningAnalysis.save')"
        :saved-text="t('spaces.learningAnalysis.saveSuccess')"
        :failed-text="t('spaces.learningAnalysis.saveFailed')"
        @revert="revert"
        @save="emit('save', draft)"
      />
    </section>

    <!-- 2. 告知卡：内容平台写死，这里只预览。 -->
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('spaces.learningAnalysis.notice.cardTitle') }}</h2>
      <p class="settings-card__desc">{{ t('spaces.learningAnalysis.notice.cardHint') }}</p>
      <div class="la__inline-foot">
        <BaseButton kind="secondary" data-testid="learning-notice-preview" @click="previewOpen = true">{{
          t('spaces.learningAnalysis.notice.preview')
        }}</BaseButton>
      </div>
    </section>

    <!-- 3. 知识点清单：所有题目共用，是清单页的样子。 -->
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('spaces.learningAnalysis.knowledgePoint.title') }}</h2>
      <p class="settings-card__desc">{{ t('spaces.learningAnalysis.knowledgePoint.hint') }}</p>

      <div v-if="canManage" class="la__inline-head">
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-plus"
          data-testid="learning-point-add"
          @click="openCreate"
        >
          {{ t('spaces.learningAnalysis.knowledgePoint.add') }}
        </BaseButton>
      </div>

      <v-list v-if="knowledgePoints.length > 0" class="settings-list" bg-color="transparent">
        <v-list-item v-for="point in knowledgePoints" :key="point.id" :title="point.name" data-testid="learning-point">
          <template #append>
            <template v-if="canManage">
              <BaseButton
                kind="ghost"
                icon="mdi-pencil-outline"
                size="sm"
                :aria-label="t('spaces.learningAnalysis.knowledgePoint.rename', { name: point.name })"
                @click="openRename(point)"
              />
              <BaseButton
                kind="ghost"
                icon="mdi-delete-outline"
                size="sm"
                :aria-label="t('spaces.learningAnalysis.knowledgePoint.remove', { name: point.name })"
                @click="removePoint(point)"
              />
            </template>
          </template>
        </v-list-item>
      </v-list>

      <BaseEmptyState
        v-else
        size="inline"
        class="settings-empty"
        :title="t('spaces.learningAnalysis.knowledgePoint.empty')"
      />
    </section>

    <!-- 4. 隐私规则：平台规则，只读。 -->
    <section class="settings-card">
      <h2 class="settings-card__title">{{ t('spaces.learningAnalysis.privacy.title') }}</h2>
      <p class="settings-card__desc">{{ t('spaces.learningAnalysis.privacy.fixed') }}</p>
      <div class="la__body la__rows">
        <SettingsRow v-for="row in privacyRows" :key="row.label" width="code" :label="row.label">
          <v-chip size="small" variant="tonal">{{ row.value }}</v-chip>
        </SettingsRow>
      </div>
    </section>
  </template>

  <LearningNoticeDialog v-model:open="previewOpen" />

  <AdaptiveDialog
    v-model="nameOpen"
    :title="dialogTitle"
    :primary-label="t('spaces.learningAnalysis.knowledgePoint.save')"
    :cancel-label="t('spaces.learningAnalysis.knowledgePoint.cancel')"
    :primary-disabled="!nameDraft.trim()"
    @primary="submitName"
  >
    <form @submit.prevent="submitName">
      <BaseField :label="t('spaces.learningAnalysis.knowledgePoint.nameLabel')" required>
        <template #default="{ id, describedby, invalid, required }">
          <v-text-field
            :id="id"
            v-model="nameDraft"
            autocomplete="off"
            variant="outlined"
            density="compact"
            hide-details="auto"
            :aria-describedby="describedby"
            :aria-invalid="invalid"
            :aria-required="required"
            :placeholder="t('spaces.learningAnalysis.knowledgePoint.namePlaceholder')"
          />
        </template>
      </BaseField>
    </form>
  </AdaptiveDialog>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.la__lede {
  margin: 0 0 12px;
}

.la__body {
  padding: 16px 24px 20px;
}

.la__loading {
  display: flex;
  padding: 32px;
  justify-content: center;
}

.la__rows {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 清单页那条工具行：动作摆在标题下面，和「邮箱域名组」同形。 */
.la__inline-head {
  display: flex;
  padding: 4px 24px 12px;
}

/* 告知卡那一块只有一句话加一颗按钮：按钮跟在正文后面，不用工具行。 */
.la__inline-foot {
  display: flex;
  padding: 0 24px 20px;
}

@media (max-width: 767.98px) {
  .la__body {
    padding: 16px;
  }

  .la__inline-head,
  .la__inline-foot {
    padding-inline: 16px;
  }
}
</style>
