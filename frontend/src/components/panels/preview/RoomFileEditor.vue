<script setup lang="ts">
// 在房间里直接改一份 Word、表格或幻灯片。
//
// 编辑器（OnlyOffice）改的就是这份 Office 文件本身：保存回来的是 .docx / .xlsx /
// .pptx，芝士下一次读的就是它，「导出」就是下载它。没有中间格式，也就没有「编辑器
// 里的样子」和「文件里的样子」对不上这回事。
//
// 这个组件自己要管的只有一件事：文件在编辑器开着的时候被别人改了。芝士改完会带一句
// 说明，这里把那句话和「载入新版本」摆出来——不自动重载，因为读者手上可能正有没存的
// 修改；他没存的那部分在他存的时候由后端另存一份，谁的都不丢。
//
// 会话、编辑器实例、盯文件那件事都在 `composables/useRoomFileEditor.ts` 里（外壳调
// 一次，整包递下来）；这一只画那条横条、几块提示、一个挂载点和一个历史栏。
import type { RoomFileEditorBundle } from '../../../composables/useRoomFileEditor'
import type { RoomFileHistoryBundle } from '../../../composables/useRoomFileHistory'

import { onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { useDisplay } from 'vuetify'

import { t } from '../../../i18n'
import { userRefRoute } from '../../../lib/userRef'

import RoomFileHistory from './RoomFileHistory.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRef.vue'

const props = defineProps<{
  /** 这一份编辑会话的取数（`composables/useRoomFileEditor.ts` 那一包）。 */
  editor: RoomFileEditorBundle
  /** 旁边那一栏历史（`composables/useRoomFileHistory.ts` 那一包，和预览那一处同一份）。 */
  fileHistory: RoomFileHistoryBundle
  /** 正在改哪一份。 */
  path: string
  /** 人名 chip 去成员页时要用（和别处那颗 chip 同一个去处）。 */
  projectId?: string | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  /** 点了一个人名：去他的主页这件事在会读路由的那一层做。 */
  (e: 'mention-click', handle: string): void
}>()

// 手机上放不下编辑器旁边再并排一栏 320px 的历史：历史打开时盖满编辑器那一块，
// 再点一下「历史」收起。
const { mdAndUp } = useDisplay()
const showHistory = ref(false)

// 编辑器要挂上去的那个元素就是下面那只 div：名字在这里生成，挂上之后交给取数那一层。
// 「另存一份」之后换成另一份文件，是宿主换了 `path`、这一只重新挂载，于是又有新名字。
const mountId = `cheese-room-file-editor-${useId()}`

onMounted(() => props.editor.begin(mountId))
onBeforeUnmount(() => props.editor.end())
</script>

<template>
  <div class="rfe" :class="{ 'rfe--phone': !mdAndUp }" data-testid="room-file-editor">
    <div class="rfe__bar">
      <v-icon size="18">mdi-file-edit-outline</v-icon>
      <span class="rfe__name">{{ props.path }}</span>
      <span v-if="props.editor.session.value?.enabled && !props.editor.session.value.editable" class="t-meta">
        {{ t('work.room.fileEditor.readOnly') }}
      </span>
      <span v-else-if="props.editor.unsaved.value" class="t-meta">{{ t('work.room.fileEditor.unsaved') }}</span>
      <span v-else-if="props.editor.savedSeq.value" class="t-meta">
        {{ t('work.room.fileEditor.savedAs', { seq: props.editor.savedSeq.value }) }}
      </span>
      <v-spacer />
      <BaseButton kind="ghost" size="sm" prepend-icon="mdi-history" @click="showHistory = !showHistory">
        {{ t('work.room.fileEditor.history') }}
      </BaseButton>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        :title="t('work.room.fileEditor.close')"
        @click="emit('close')"
      />
    </div>

    <v-alert v-if="props.editor.changedBy.value" type="info" density="compact" class="ma-2" data-testid="changed-by">
      <div>
        <i18n-t scope="global" keypath="work.room.fileEditor.changedBy" tag="span">
          <template #who>
            <UserRef
              v-if="props.editor.changedBy.value.author"
              :handle="props.editor.changedBy.value.author"
              :to="userRefRoute(props.editor.changedBy.value.author, props.projectId)"
              @navigate="emit('mention-click', props.editor.changedBy.value.author ?? '')"
            />
            <template v-else>{{
              props.editor.changedBy.value.author_kind === 'agent'
                ? t('work.room.defaultAgentName')
                : t('work.room.fileEditor.someone')
            }}</template>
          </template>
          <template #seq>{{ props.editor.changedBy.value.seq }}</template>
        </i18n-t>
        <template v-if="props.editor.changedBy.value.note">
          {{ t('work.room.fileEditor.changedNote', { note: props.editor.changedBy.value.note }) }}
        </template>
      </div>
      <div class="t-meta">
        {{ t('work.room.fileEditor.staleNote') }}
      </div>
      <BaseButton kind="secondary" size="sm" class="mt-1" @click="props.editor.start()">{{
        t('work.room.fileEditor.loadNew')
      }}</BaseButton>
    </v-alert>

    <v-alert v-if="props.editor.failure.value" type="warning" density="compact" class="ma-2">
      {{ props.editor.failure.value }}
    </v-alert>

    <div class="rfe__main">
      <div class="rfe__canvas">
        <div v-if="props.editor.loading.value" class="rfe__state">
          <v-progress-circular indeterminate color="primary" />
        </div>
        <div v-else-if="props.editor.session.value && !props.editor.session.value.enabled" class="rfe__state">
          <div>
            {{
              props.editor.session.value.reason
                ? t(`work.room.fileEditor.unavailable.${props.editor.session.value.reason}`)
                : ''
            }}
          </div>
          <div v-if="props.editor.session.value.copyable" class="mt-3 rfe__copy">
            <v-text-field
              :model-value="props.editor.copyName.value"
              density="compact"
              :label="t('work.room.fileEditor.copyName')"
              autocomplete="off"
              hide-details
              @update:model-value="(v: string) => props.editor.setCopyName(v)"
            />
            <BaseButton kind="secondary" class="mt-2" @click="props.editor.makeCopy()">{{
              t('work.room.fileEditor.makeCopy')
            }}</BaseButton>
            <div class="t-meta mt-1">{{ t('work.room.fileEditor.originalKept') }}</div>
          </div>
        </div>
        <!-- 编辑器把占位的那个 div 换成自己的 iframe，所以定位留在外面这一层。 -->
        <div v-show="props.editor.session.value?.enabled && !props.editor.loading.value" class="rfe__host">
          <div :id="mountId" />
        </div>
      </div>
      <aside v-if="showHistory" class="rfe__side">
        <RoomFileHistory
          :file-history="props.fileHistory"
          :project-id="props.projectId"
          @mention-click="emit('mention-click', $event)"
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.rfe {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
}
.rfe__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line);
}
.rfe__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rfe :deep(.v-alert) {
  flex: none;
}
.rfe__main {
  flex: 1;
  min-height: 0;
  display: flex;
}
.rfe__canvas {
  flex: 1;
  min-width: 0;
  position: relative;
}
.rfe__host {
  position: absolute;
  inset: 0;
}
.rfe__state {
  padding: 48px 16px;
  text-align: center;
  color: var(--muted);
}
.rfe__copy {
  max-width: 360px;
  margin: 0 auto;
}
.rfe__side {
  width: 320px;
  border-left: 1px solid var(--line);
  overflow: auto;
}
.rfe--phone .rfe__main {
  position: relative;
}
.rfe--phone .rfe__side {
  position: absolute;
  inset: 0;
  z-index: var(--z-raised);
  width: auto;
  border-left: 0;
  background: var(--surface);
}
</style>
