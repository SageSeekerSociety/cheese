<script setup lang="ts">
// 文档的顶栏。左边是这篇现在的状态：平常是谁多久之前编辑的（点开是修改记录），断了网
// 说改动会在连上后同步，没有编辑权限时说只读。右边是在线的人、编辑方式（编辑 / 建议 /
// 只读）、待处理的建议、评论、对整篇问 AI 队友，和「⋯」。
//
// 格式不在这里：选中文字时浮条上有，块的样式在「/」和左边的 ＋ 里。
import type { DocAgentController } from '../../../composables/useDocAgent'
import type { DocConnection, DocPeer } from '../../../composables/useDocCollab'
import type { OutlineHeading } from '../../../lib/docOutline'

import { computed, ref, watch } from 'vue'

import { relTime } from '../../../lib/relTime'
import CheeseAvatar from '../../CheeseAvatar.vue'

import DocAgentBox from './DocAgentBox.vue'
import DocAgentResult from './DocAgentResult.vue'
import DocOutline from './DocOutline.vue'
import DocPresence from './DocPresence.vue'

import { t } from '@/i18n'

const props = defineProps<{
  loading: boolean
  connection: DocConnection
  peers: DocPeer[]
  /** 现在能不能改：自己切到只读、或者没有编辑权限时是 false。 */
  editable: boolean
  /** 没有编辑权限：左边说只读，没有编辑方式可切。 */
  readOnly: boolean
  /** 最近一次编辑：谁（已经读成名字）、什么时候。 */
  lastEdit: { name: string; at: string } | null
  suggestionCount: number
  suggestionsOpen: boolean
  commentCount: number
  commentsOpen: boolean
  agentName: string
  agentHandle?: string | null
  /** 对整篇找 AI 队友；认不出它时是 undefined，不给这个按钮。 */
  agent?: DocAgentController
  mentionNames: Record<string, string>
  /** 大纲：正文里的标题（h1–h3）；还没有标题时是空数组。 */
  headings: OutlineHeading[]
  /** 查找条开着没有，按钮据此发亮。 */
  findOpen: boolean
  /** 「⋯」里给不给「删除文档」：只有项目资料库里的文档能单独删。 */
  deletable?: boolean
  /** 「⋯」里给不给「导出 PDF」：接了打印那一条路时才有。 */
  pdfable?: boolean
  /** 只能提修改建议（导师）：编辑方式里只有「建议」和「只读」。 */
  suggestOnly?: boolean
  /** 打的字记成修订（建议），还是直接改正文（编辑）。 */
  suggesting?: boolean
}>()
const emit = defineEmits<{
  (e: 'toggle-suggestions'): void
  (e: 'toggle-comments'): void
  /** 在只读和能改之间切。 */
  (e: 'toggle-editable'): void
  (e: 'history'): void
  (e: 'export'): void
  /** 导出 PDF：同样的正文，印成 A4。 */
  (e: 'export-pdf'): void
  (e: 'open-thread', id: string): void
  (e: 'toggle-find'): void
  (e: 'outline-select', pos: number): void
  (e: 'delete'): void
  /** 「移动到…」：挪到自己名下、团队或团队的一个项目，挪之前页面先问。 */
  (e: 'move'): void
  /** 切到建议（true）或编辑（false）。 */
  (e: 'set-suggesting', on: boolean): void
  /** 「分享…」：给这份文档一条只读链接（#3774）。 */
  (e: 'share'): void
}>()

// 对整篇：菜单里先是输入框，交出去以后是那张卡。菜单关上就收起这一次（正在做的不收）。
const agentOpen = ref(false)
watch(agentOpen, (open) => {
  if (open) props.agent?.open()
  else if (!props.agent?.editing.value) props.agent?.close()
})
async function toComment() {
  const thread = await props.agent?.toComment()
  agentOpen.value = false
  if (thread) emit('open-thread', thread)
}

// 编辑方式：三个里挑一个。只读是「能不能改」那个开关，编辑和建议是能改时打的字记成什么。
type Mode = 'edit' | 'suggest' | 'read'
const modeName = computed(() =>
  !props.editable
    ? t('work.room.doc.mode.read')
    : props.suggesting
      ? t('work.room.doc.mode.suggest')
      : t('work.room.doc.mode.edit')
)
const modeIcon = computed(() =>
  !props.editable ? 'mdi-eye-outline' : props.suggesting ? 'mdi-file-document-edit-outline' : 'mdi-pencil-outline'
)
function pickMode(mode: Mode) {
  if ((mode === 'read') === props.editable) emit('toggle-editable')
  if (mode !== 'read' && !props.suggestOnly) emit('set-suggesting', mode === 'suggest')
}

// 大纲下拉：点了某一节，先收起菜单再往上发（滚到那一节是面板的事）。
const outlineOpen = ref(false)
function pickHeading(pos: number) {
  outlineOpen.value = false
  emit('outline-select', pos)
}
</script>

<template>
  <div class="doc-top-bar">
    <div class="doc-top-bar__state">
      <span v-if="connection === 'offline'" class="doc-top-bar__note doc-top-bar__note--warn">
        <span class="status-dot status-dot--warn" /><span class="doc-top-bar__note-text">{{
          t('work.room.doc.offlineSync')
        }}</span>
      </span>
      <span v-else-if="loading || connection === 'connecting'" class="doc-top-bar__note">
        <span class="doc-top-bar__note-text">{{
          loading ? t('work.room.doc.loading') : t('work.room.doc.connecting')
        }}</span>
      </span>
      <template v-else>
        <button
          v-if="readOnly"
          type="button"
          class="doc-top-bar__btn doc-top-bar__btn--chip"
          disabled
          :title="t('work.room.doc.noEditAccess')"
        >
          {{ t('work.room.doc.readOnly') }}
        </button>
        <button
          v-if="lastEdit"
          type="button"
          class="doc-top-bar__btn doc-top-bar__btn--quiet"
          :title="t('work.room.doc.history')"
          @click="emit('history')"
        >
          <!-- The ellipsis has to sit on THIS element. The button itself is
               `display: inline-flex`, so its text becomes an anonymous flex item
               and `text-overflow` on the flex container does nothing: measured at
               390px wide the label needs 118px, the button is given 88px, and the
               text refused to shrink. See .doc-top-bar__quiet-text. -->
          <span class="doc-top-bar__quiet-text">{{
            t('work.room.doc.lastEdit', { who: lastEdit.name, when: relTime(lastEdit.at) })
          }}</span>
        </button>
      </template>
    </div>
    <div class="doc-top-bar__actions">
      <DocPresence v-if="connection === 'connected'" :peers="peers" class="me-1" />
      <v-menu v-if="!loading" v-model="outlineOpen" location="bottom end" offset="6">
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="doc-top-bar__btn"
            :aria-label="t('work.room.doc.outline')"
            :title="t('work.room.doc.outline')"
          >
            <v-icon size="17">mdi-format-list-bulleted</v-icon>
          </button>
        </template>
        <DocOutline :headings="headings" @select="pickHeading" />
      </v-menu>
      <button
        v-if="!loading"
        type="button"
        class="doc-top-bar__btn"
        :aria-pressed="findOpen"
        :aria-label="t('work.room.doc.find')"
        :title="t('work.room.doc.find')"
        @click="emit('toggle-find')"
      >
        <v-icon size="17">mdi-magnify</v-icon>
      </button>
      <v-menu v-if="!readOnly && !loading" location="bottom end" offset="6">
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="doc-top-bar__btn"
            data-testid="doc-mode"
            :aria-label="modeName"
            :title="modeName"
          >
            <v-icon size="16">{{ modeIcon }}</v-icon>
            <span class="doc-top-bar__label">{{ modeName }}</span>
            <v-icon size="14">mdi-chevron-down</v-icon>
          </button>
        </template>
        <v-list density="compact" role="menu" :aria-label="t('work.room.doc.mode.label')">
          <v-list-item
            v-if="!suggestOnly"
            role="menuitemradio"
            :aria-checked="editable && !suggesting"
            :title="t('work.room.doc.mode.edit')"
            :subtitle="t('work.room.doc.mode.editHint')"
            prepend-icon="mdi-pencil-outline"
            @click="pickMode('edit')"
          />
          <v-list-item
            role="menuitemradio"
            :aria-checked="editable && !!suggesting"
            :title="t('work.room.doc.mode.suggest')"
            :subtitle="suggestOnly ? t('work.room.doc.mode.suggestOnly') : t('work.room.doc.mode.suggestHint')"
            prepend-icon="mdi-file-document-edit-outline"
            @click="pickMode('suggest')"
          />
          <v-list-item
            role="menuitemradio"
            :aria-checked="!editable"
            :title="t('work.room.doc.mode.read')"
            :subtitle="t('work.room.doc.mode.readHint')"
            prepend-icon="mdi-eye-outline"
            @click="pickMode('read')"
          />
        </v-list>
      </v-menu>
      <button
        v-if="suggestionCount > 0"
        type="button"
        class="doc-top-bar__btn"
        :aria-pressed="suggestionsOpen"
        @click="emit('toggle-suggestions')"
      >
        <span class="status-dot status-dot--ok" />{{ t('work.room.doc.suggestionCount', { n: suggestionCount }) }}
      </button>
      <button
        type="button"
        class="doc-top-bar__btn"
        :aria-label="t('work.room.comments.title')"
        :title="t('work.room.comments.title')"
        :aria-expanded="commentsOpen"
        @click="emit('toggle-comments')"
      >
        <v-icon size="17">mdi-comment-text-outline</v-icon>
        <span v-if="commentCount > 0">{{ commentCount }}</span>
      </button>
      <v-menu v-if="agent" v-model="agentOpen" location="bottom end" :close-on-content-click="false" offset="6">
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="doc-top-bar__btn doc-top-bar__btn--agent"
            :aria-label="agentName"
            :title="agentName"
          >
            <CheeseAvatar :size="16" :name="agentName" :handle="agentHandle" />
            <span class="doc-top-bar__label">{{ agentName }}</span>
          </button>
        </template>
        <div class="doc-top-bar__agent">
          <DocAgentBox
            v-if="agent.phase.value === 'asking'"
            :agent-name="agentName"
            scope="document"
            :context="agent.context.value"
            @run="agent.run"
            @say="agent.say"
            @cancel="agentOpen = false"
          />
          <DocAgentResult
            v-else-if="agent.phase.value !== 'idle'"
            :agent-name="agentName"
            :phase="agent.phase.value"
            :kind="agent.kind.value"
            :answer="agent.answer.value"
            :stopped="agent.stopped.value"
            :changed="agent.edits.value.length"
            :busy="agent.busy.value"
            commentable
            :mention-names="mentionNames"
            @stop="agent.stop"
            @undo="agent.undo"
            @redo="agent.redo"
            @say="agent.say"
            @comment="toComment"
            @close="agentOpen = false"
          />
        </div>
      </v-menu>
      <v-menu location="bottom end">
        <template #activator="{ props: menuProps }">
          <button
            v-bind="menuProps"
            type="button"
            class="doc-top-bar__btn"
            :title="t('work.room.menu.more')"
            :aria-label="t('work.room.menu.more')"
          >
            <v-icon size="17">mdi-dots-horizontal</v-icon>
          </button>
        </template>
        <v-list density="compact" role="menu" :aria-label="t('work.room.doc.options')">
          <v-list-item role="menuitem" :title="t('work.room.doc.history')" @click="emit('history')" />
          <v-list-item role="menuitem" :title="t('work.room.doc.exportMarkdown')" @click="emit('export')" />
          <v-list-item
            v-if="pdfable"
            role="menuitem"
            :title="t('work.room.doc.exportPdf')"
            @click="emit('export-pdf')"
          />
          <template v-if="deletable">
            <v-divider class="my-1" />
            <v-list-item role="menuitem" :title="t('work.room.doc.share')" @click="emit('share')" />
            <v-list-item role="menuitem" :title="t('work.room.doc.move')" @click="emit('move')" />
            <v-list-item
              role="menuitem"
              :title="t('work.room.doc.delete')"
              base-color="error"
              @click="emit('delete')"
            />
          </template>
        </v-list>
      </v-menu>
    </div>
  </div>
</template>

<style scoped>
.doc-top-bar {
  /* 窄的时候按钮上的字收起来（见文件末尾）：量的是这一条自己多宽，不是窗口。 */
  container-type: inline-size;
  display: flex;
  min-width: 0;
  min-height: 44px;
  padding: 0 8px 0 16px;
  align-items: center;
  gap: 8px;
}

.doc-top-bar__state {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.doc-top-bar__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
}

.doc-top-bar__note {
  display: inline-flex;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
  align-items: center;
  gap: 6px;
}

/* 省略号挂在这一层，不挂在上面那个 flex 容器上：容器里的字是匿名 flex item，
   `text-overflow` 在 flex 容器上不生效（同一个毛病见 `__quiet-text`）。 */
.doc-top-bar__note-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-top-bar__note--warn {
  color: var(--warn-ink);
}

.doc-top-bar__btn {
  display: inline-flex;
  height: 30px;
  min-width: 30px;
  padding: 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  justify-content: center;
  transition:
    background var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.doc-top-bar__btn:hover:not(:disabled),
.doc-top-bar__btn[aria-pressed='true'],
.doc-top-bar__btn[aria-expanded='true'] {
  color: var(--ink);
  background: var(--fill);
}

.doc-top-bar__btn:disabled {
  cursor: default;
}

.doc-top-bar__btn:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* 最近编辑那一行：是一句状态，点得开，所以只在悬停时像按钮；字和正文的左边对齐。
   它自己可以被让位（`flex: 0 1 auto` + `min-width: 0`），让出来的空间由里面那层
   用省略号收——**不能**把 `text-overflow` 写在这里：这颗是 `display: inline-flex`
   （见 `.doc-top-bar__btn`），字是匿名 flex item，省略号在 flex 容器上不生效。 */
.doc-top-bar__btn--quiet {
  flex: 0 1 auto;
  min-width: 0;
  margin-left: -8px;
  overflow: hidden;
  color: var(--faint);
}

.doc-top-bar__quiet-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-top-bar__btn--chip {
  color: var(--text);
  background: var(--fill);
}

.doc-top-bar__btn--agent {
  font-weight: 600;
  color: var(--ink);
}

.doc-top-bar__agent {
  width: min(420px, calc(100vw - 32px));
}

/* 手指点得中（设计系统 §10.1）：这几颗只有 30px 高，触屏上把能点的范围撑到 44×44，
   画出来的样子不变。撑开的部分会互相盖住，所以并排的几颗之间先拉开——一次只点中
   一颗。 */
@media (pointer: coarse) {
  .doc-top-bar__state {
    gap: 14px;
  }

  .doc-top-bar__actions {
    gap: 14px;
  }

  .doc-top-bar__btn {
    position: relative;
  }

  .doc-top-bar__btn::before {
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    content: '';
    transform: translate(-50%, -50%);
  }
}

/* 右边那一排不让位（`flex: 0 0 auto`），放不下时最右边的「⋯」被挤出这一条。窄的时候
   编辑方式和 AI 队友只留图标，名字还在 aria-label 和 title 里。 */
@container (max-width: 479px) {
  .doc-top-bar__label {
    display: none;
  }
}
</style>
