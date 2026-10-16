<script setup lang="ts">
// 改一条自己发过的消息：正文原地换成输入框，下面「取消」「保存」。房间的对话和
// 卡下的对话都用它，所以两处的手势一样：打开时放进原文、光标落在末尾；Enter 保存、
// Shift+Enter 换行、Esc 放弃。输入法组字时的 Enter 是选字，不算。
//
// 框和发消息的输入区是同一个（ComposerEditor）：原文里点过的人、引用的频道和文件
// 照原样放进去，画成标签，保存时也照原样存回去。
//
// 框里的 `@` 和发消息那个输入区是同一套候选（`useRoomMentionPicker`）：改一条消息
// 和发一条消息对「@ 到了谁」是同一件事，编辑时不弹候选，人就得自己拼 handle——拼错
// 了这条消息看起来点到了人，其实没有。
//
// 只 @ 人和话题，不给资料库那一级：一份文件的 `@` 是把它附在这条消息上，而这个框只
// 改正文、没有待发条，收下的文件无处可去。所以这里不传 `topic`，picker 也就不去取
// 资料库、文件那一类根本出不来。
import type { MentionPoolEntry } from '@/composables/useRoomMentionPicker'
import type { Topic } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'

import { computed, onMounted, ref } from 'vue'

import { useComposerText } from '@/composables/useComposerText'
import { useRoomMentionPicker } from '@/composables/useRoomMentionPicker'

import { expandComposerMentions } from '../../lib/composerMentions'

import ComposerEditor from './ComposerEditor.vue'
import MentionMenu from './MentionMenu.vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 打开时框里放的字：这条消息存着的原文。 */
  text: string
  /** 保存请求还在路上。 */
  saving: boolean
  /** 原文里的引用画成标签时写的名字。 */
  refs: RefNames
  /** @ 得到的人：这个房间里的，加上项目里还没进这个房间的。 */
  mentionPool?: MentionPoolEntry[]
  /** @ 得到的话题，用来把「@话题名」展开成 <#id>。 */
  topicList?: Topic[]
}>()

const emit = defineEmits<{
  (e: 'save', text: string): void
  (e: 'cancel'): void
}>()

const draft = ref(props.text)
const caret = ref(props.text.length)
const input = ref<InstanceType<typeof ComposerEditor> | null>(null)

const topics = computed(() => (props.topicList ?? []).filter((tp) => tp.kind !== 'root'))
const { beforeCaret } = useComposerText({
  draft,
  caret,
  expand: (text) => expandComposerMentions(text, null, props.mentionPool ?? [], topics.value),
  composing: () => !!input.value?.composing(),
})
// 这里的标签是这条消息点过名的人：发出去的时候通知过他们。
const names = computed<RefNames>(() => {
  const mentionNames = { ...props.refs.mentionNames }
  for (const m of props.mentionPool ?? []) mentionNames[m.handle] ??= m.label
  return { ...props.refs, mentionNames, notified: true }
})

const mentionMenu = ref<InstanceType<typeof MentionMenu> | null>(null)
const picker = useRoomMentionPicker({
  draft: beforeCaret,
  // 没有资料库那一级：见文件头上那段说明。
  topic: () => null,
  mentionPool: () => props.mentionPool ?? [],
  topicList: () => props.topicList ?? [],
  focus: () => input.value?.focus(),
  scrollActiveIntoView: () => mentionMenu.value?.scrollActiveIntoView(),
  writeTokens: true,
})
const {
  matches: mentionMatches,
  menuVisible: mentionMenuVisible,
  menuOpen: mentionMenuOpen,
  activeIndex: mentionActiveIndex,
} = picker

function isImeKey(e: KeyboardEvent) {
  return e.isComposing || e.keyCode === 229
}

onMounted(() => input.value?.focus())

function onKey(e: KeyboardEvent) {
  if (isImeKey(e)) return
  // 和发消息的输入区同一套：↑/↓ 换高亮（输入法选字时让给输入法）；Esc 收起候选、
  // `@` 还留在正文里；有候选时 Enter 挑人，没有则照旧保存这条编辑。
  const arrow = e.key === 'ArrowDown' || e.key === 'ArrowUp'
  if (arrow && mentionMenuOpen.value) {
    e.preventDefault()
    picker.move(e.key === 'ArrowDown' ? 1 : -1)
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    // 菜单开着时那一下是「这次 @ 先不挑了」，收起候选；再按一下才是放弃这次编辑。
    if (mentionMenuVisible.value) picker.close()
    else emit('cancel')
    return
  }
  if (e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  if (picker.pickActive()) return
  save()
}

function save() {
  emit('save', expandComposerMentions(draft.value, null, props.mentionPool ?? [], topics.value))
}
</script>

<template>
  <div class="msg-edit">
    <ComposerEditor
      ref="input"
      v-model="draft"
      v-model:caret="caret"
      class="msg-edit__input"
      :names="names"
      :label="t('work.room.message.edit')"
      @keydown="onKey"
    />
    <!-- 永远是一级：这里不取资料库，没有二级菜单可翻。 -->
    <MentionMenu
      ref="mentionMenu"
      :open="mentionMenuVisible"
      :matches="mentionMatches"
      :active-index="mentionActiveIndex"
      level="root"
      :enter-sends="false"
      @pick="picker.pick"
      @hover="picker.hover"
    />
    <div class="msg-edit__actions">
      <button type="button" class="msg-edit__btn" @click="emit('cancel')">
        {{ t('work.room.message.cancel') }}
      </button>
      <button type="button" class="msg-edit__btn" :disabled="saving || !draft.trim()" @click="save">
        {{ t('work.room.message.save') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.msg-edit {
  /* @ 候选菜单是 absolute，参照的是最近的定位祖先：这里必须是它，否则菜单会跳到
     页面上别的地方去。 */
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 2px;
}
.msg-edit__input {
  /* 长到 12 行封顶，再长就在框里滚，不把这一条撑满整屏。 */
  --composer-lines: 12;
  padding: 0 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
/* 聚焦只把边提一档，和发消息的输入框一样：边本身就是焦点的指示。 */
.msg-edit__input:focus-within {
  border-color: var(--faint);
}
.msg-edit__actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}
/* 和发送失败那一行同一种中性小按钮。 */
.msg-edit__btn {
  flex: none;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.msg-edit__btn:hover:not(:disabled) {
  background: var(--fill);
  border-color: var(--faint);
}
.msg-edit__btn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
