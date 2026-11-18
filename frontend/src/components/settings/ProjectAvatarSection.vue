<script setup lang="ts">
// 项目头像（项目设置「名称和头像」那一页）：左边是格子现在的样子，右边挑画什么（图标、表情、文字、图片）和底色。点图标、
// 点颜色立刻生效；表情和文字按保存（或回车）生效。保存和上传由页面去做
// （`composables/useProjectAvatar.ts`），这里只说用户挑了什么。
import { computed, ref, watch } from 'vue'

import {
  AVATAR_SWATCH_HUES,
  fitsTile,
  isOneEmoji,
  PROJECT_ICONS,
  projectAvatarColor,
  type ProjectAvatarKind,
  type ProjectAvatarSpec,
  projectInitials,
} from '@/utils/projectAvatar'

import BaseButton from '@/components/base/BaseButton.vue'
import ProjectAvatar from '@/components/common/ProjectAvatar.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ProjectAvatarSection' })

const props = defineProps<{
  projectId: string
  name: string
  avatar: ProjectAvatarSpec | null
  /** 不能改设置的人：只显示格子。 */
  readonly: boolean
  saving: boolean
  /** 上一次保存或上传为什么没成。 */
  error: string
}>()
const emit = defineEmits<{ save: [avatar: ProjectAvatarSpec]; upload: [file: File, hue: number | null] }>()

const DEFAULT: ProjectAvatarSpec = { kind: null, glyph: null, hue: null, image_id: null }
const current = computed<ProjectAvatarSpec>(() => props.avatar ?? DEFAULT)
const hue = computed(() => current.value.hue)

const KINDS: ProjectAvatarKind[] = ['icon', 'emoji', 'text', 'image']
const tab = ref<ProjectAvatarKind>(current.value.kind ?? 'icon')

const emojiDraft = ref('')
const textDraft = ref('')
watch(
  current,
  (a) => {
    emojiDraft.value = a.kind === 'emoji' ? a.glyph ?? '' : ''
    textDraft.value = a.kind === 'text' ? a.glyph ?? '' : ''
  },
  { immediate: true }
)

const emojiOk = computed(() => isOneEmoji(emojiDraft.value))
// 框里的就是存着的那个：保存按钮不可点。
const emojiSaved = computed(() => current.value.kind === 'emoji' && current.value.glyph === emojiDraft.value.trim())
const textSaved = computed(() =>
  textDraft.value.trim()
    ? current.value.kind === 'text' && current.value.glyph === textDraft.value.trim()
    : current.value.kind === null
)
const textOk = computed(() => !textDraft.value.trim() || fitsTile(textDraft.value))
const emojiError = computed(() =>
  emojiDraft.value.trim() && !emojiOk.value ? t('work.projectSettings.avatar.emojiInvalid') : ''
)
const textError = computed(() => (textOk.value ? '' : t('work.projectSettings.avatar.textInvalid')))

/** 格子预览：正在打的表情或文字合规就先画出来，否则画存着的那个。 */
const preview = computed<ProjectAvatarSpec>(() => {
  if (tab.value === 'emoji' && emojiOk.value)
    return { ...DEFAULT, kind: 'emoji', glyph: emojiDraft.value.trim(), hue: hue.value }
  if (tab.value === 'text' && textDraft.value.trim() && textOk.value)
    return { ...DEFAULT, kind: 'text', glyph: textDraft.value.trim(), hue: hue.value }
  return current.value
})

const initials = computed(() => projectInitials(props.name))
const swatches = computed(() => [
  { hue: null, color: projectAvatarColor(props.projectId, null) },
  ...AVATAR_SWATCH_HUES.map((h) => ({ hue: h, color: projectAvatarColor(props.projectId, h) })),
])

function pickDefault() {
  emit('save', { ...DEFAULT, hue: hue.value })
}
function pickIcon(name: string) {
  emit('save', { ...DEFAULT, kind: 'icon', glyph: name, hue: hue.value })
}
function pickHue(next: number | null) {
  if (next === hue.value) return
  emit('save', { ...current.value, hue: next })
}
function saveEmoji() {
  if (emojiOk.value && !emojiSaved.value)
    emit('save', { ...DEFAULT, kind: 'emoji', glyph: emojiDraft.value.trim(), hue: hue.value })
}
function saveText() {
  if (!textOk.value || textSaved.value) return
  const text = textDraft.value.trim()
  emit('save', text ? { ...DEFAULT, kind: 'text', glyph: text, hue: hue.value } : { ...DEFAULT, hue: hue.value })
}

const fileInput = ref<HTMLInputElement | null>(null)
function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('upload', file, hue.value)
  input.value = ''
}
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-image-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.avatar.title') }}</span>
    </div>
    <div class="page-section-body pav">
      <ProjectAvatar :id="projectId" :name="name" :avatar="preview" :size="64" class="pav__preview" />
      <div v-if="!readonly" class="pav__body">
        <v-btn-toggle
          v-model="tab"
          mandatory
          divided
          density="compact"
          variant="outlined"
          color="on-surface"
          class="pav__kinds"
        >
          <template v-for="k in KINDS" :key="k">
            <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
            <v-btn :value="k" size="small">{{ t(`work.projectSettings.avatar.kinds.${k}`) }}</v-btn>
          </template>
        </v-btn-toggle>

        <div
          v-if="tab === 'icon'"
          class="pav__grid"
          role="group"
          :aria-label="t('work.projectSettings.avatar.kinds.icon')"
        >
          <button
            type="button"
            class="pav__opt pav__opt--letters"
            :aria-pressed="current.kind === null"
            :aria-label="t('work.projectSettings.avatar.default')"
            :title="t('work.projectSettings.avatar.default')"
            :disabled="saving"
            data-user-content
            @click="pickDefault"
          >
            {{ initials }}
          </button>
          <button
            v-for="(iconClass, icon) in PROJECT_ICONS"
            :key="icon"
            type="button"
            class="pav__opt"
            :aria-pressed="current.kind === 'icon' && current.glyph === icon"
            :aria-label="icon"
            :title="icon"
            :disabled="saving"
            @click="pickIcon(icon)"
          >
            <v-icon :icon="iconClass" size="20" />
          </button>
        </div>

        <div v-else-if="tab === 'emoji'" class="pav__line">
          <v-text-field
            v-model="emojiDraft"
            :label="t('work.projectSettings.avatar.kinds.emoji')"
            density="compact"
            variant="outlined"
            autocomplete="off"
            :error-messages="emojiError ? [emojiError] : []"
            hide-details="auto"
            class="pav__input"
            @keydown.enter="saveEmoji"
          />
          <BaseButton
            kind="primary"
            size="sm"
            class="pav__save"
            :loading="saving"
            :disabled="!emojiOk || emojiSaved"
            @click="saveEmoji"
          >
            {{ t('work.projectSettings.avatar.save') }}
          </BaseButton>
        </div>

        <div v-else-if="tab === 'text'" class="pav__line">
          <v-text-field
            v-model="textDraft"
            :label="t('work.projectSettings.avatar.kinds.text')"
            :placeholder="initials"
            persistent-placeholder
            density="compact"
            variant="outlined"
            autocomplete="off"
            maxlength="8"
            :error-messages="textError ? [textError] : []"
            hide-details="auto"
            class="pav__input"
            @keydown.enter="saveText"
          />
          <BaseButton
            kind="primary"
            size="sm"
            class="pav__save"
            :loading="saving"
            :disabled="!textOk || textSaved"
            @click="saveText"
          >
            {{ t('work.projectSettings.avatar.save') }}
          </BaseButton>
        </div>

        <div v-else class="pav__line">
          <BaseButton kind="secondary" size="sm" :loading="saving" @click="fileInput?.click()">
            {{
              current.kind === 'image'
                ? t('work.projectSettings.avatar.replace')
                : t('work.projectSettings.avatar.upload')
            }}
          </BaseButton>
          <BaseButton
            v-if="current.kind === 'image'"
            kind="secondary"
            size="sm"
            :disabled="saving"
            @click="pickDefault"
          >
            {{ t('work.projectSettings.avatar.remove') }}
          </BaseButton>
          <input
            ref="fileInput"
            class="pav__file"
            type="file"
            accept="image/*"
            :aria-label="t('work.projectSettings.avatar.upload')"
            @change="onFile"
          />
        </div>

        <div
          v-if="tab !== 'image'"
          class="pav__swatches"
          role="group"
          :aria-label="t('work.projectSettings.avatar.color')"
        >
          <button
            v-for="(s, i) in swatches"
            :key="String(s.hue)"
            type="button"
            class="pav__swatch"
            :style="{ backgroundColor: s.color }"
            :aria-pressed="s.hue === hue"
            :aria-label="
              s.hue === null
                ? t('work.projectSettings.avatar.default')
                : t('work.projectSettings.avatar.colorN', { n: i })
            "
            :disabled="saving"
            @click="pickHue(s.hue)"
          />
        </div>

        <p v-if="error" class="t-body pav__error" role="alert">{{ error }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
<style scoped>
.pav {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}
.pav__preview {
  flex: none;
}
.pav__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  flex: 1;
}
.pav__kinds {
  align-self: flex-start;
}
.pav__grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 224px;
  overflow-y: auto;
}
.pav__opt {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  display: grid;
  place-items: center;
  border: 1px solid var(--line-2);
  color: var(--text);
  background: var(--surface);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.pav__opt--letters {
  font-weight: 600;
}
.pav__opt:hover:not(:disabled) {
  background: var(--fill);
}
.pav__opt[aria-pressed='true'] {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
}
.pav__line {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.pav__input {
  max-width: 220px;
}
/* 小号按钮比紧凑输入框矮 8px：上下各让一半，和输入框居中对齐。 */
.pav__save {
  margin-top: 4px;
}
.pav__file {
  display: none;
}
.pav__swatches {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pav__swatch {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-pill);
  border: 0;
}
.pav__swatch[aria-pressed='true'] {
  box-shadow:
    0 0 0 2px var(--surface),
    0 0 0 4px var(--ink);
}
.pav__error {
  color: var(--danger-ink);
}
@media (max-width: 600px) {
  .pav {
    flex-direction: column;
  }
}
</style>
