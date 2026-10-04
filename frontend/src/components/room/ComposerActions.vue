<script setup lang="ts">
// 输入框下面那一行：动作靠左，发送靠右。发送是这一行唯一的主操作，所以它是唯一的
// 实心按钮，其余一律是安静的图标。
//
// 从 `RoomComposer.vue` 里拆出来的（#2143）。它只吃 props、只往上发事件：挑文件、
// 挑照片、发送、开关召唤，四件事都不在这里决定。
//
// 位置负责表达语义：左边是「这条消息本身」的动作（贴什么上去），右边是「它会怎么
// 发出去」（谁在跑、叫不叫芝士、发）。话题级的设置——谁在跑、在哪跑——由外面从
// `chips` 插槽交进来，这一行不必认识算力池。
//
// 手机上这一行放不下每一颗：清单、提醒收进一颗 ⋯，从底部升起一个面板
// （设计系统 §10.4，`AdaptiveMenu`）。附件和照片留在外面，它们是最常点的。
import type { MenuAction } from '@/components/common/menuAction'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { openShortcutSheet } from '@/components/common/shortcutSheet'
import { t } from '@/i18n'

const props = defineProps<{
  /** 附件还在上传：那一刻发不出去，灰着而不是把字吞掉。 */
  uploading: boolean
  /** 现在能不能发（有字或者有附件）。 */
  canSend: boolean
  /** 桌面上不给「照片」那一颗——那儿贴一张截图或者拖进来就完事了。 */
  showImagePicker: boolean
  /** 回车是发送（触摸屏上是换行），这一行的间距跟着它走。 */
  enterSends: boolean
  /** 和芝士私聊：每条都是说给它听的，没有「交给它」这颗按钮。 */
  alwaysSummon: boolean
  /** 这条消息现在叫不叫它。读的是正文，不是一个单独存着的开关值。 */
  summonOn: boolean
  /** 这个房间的芝士是谁，现在知道了吗。名册还没到时这颗按钮关着。 */
  summonReady: boolean
  agentName: string
  /** 能不能在这儿发一张清单（房间给了发清单的路才有这一颗）。 */
  canChecklist?: boolean
  /** 「提醒我」那一颗。房间还没定下来（没有话题）时不给。 */
  canRemind?: boolean
  /** 窄屏：清单、提醒收进一颗 ⋯。 */
  collapseExtras?: boolean
}>()

const emit = defineEmits<{
  (e: 'files', files: File[]): void
  (e: 'pick-files'): void
  (e: 'pick-images'): void
  (e: 'checklist'): void
  (e: 'toggle-summon'): void
  (e: 'remind'): void
  (e: 'send'): void
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)

function pickFiles() {
  fileInput.value?.click()
}

// 手机上单开一个「照片」：系统的文件选择器里翻相册要好几步，而 accept=image/*
// 直接进相册/相机。桌面上不给这一颗——那儿贴一张截图或者拖进来就完事了。
function pickImages() {
  imageInput.value?.click()
}

function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) emit('files', Array.from(input.files))
  input.value = '' // allow re-picking the same file
}

// 清单、提醒：桌面上是两颗图标，手机上是 ⋯ 里的两行。只剩一样时不收，
// 一颗 ⋯ 里只有一行，比那一颗本身还多点一下。
const extras = computed<MenuAction[]>(() => {
  const list: MenuAction[] = []
  if (props.canChecklist)
    list.push({
      key: 'checklist',
      label: t('work.room.checklist.compose'),
      icon: 'mdi-format-list-checks',
      onSelect: () => emit('checklist'),
    })
  if (props.canRemind)
    list.push({
      key: 'remind',
      label: t('work.room.reminder.open'),
      icon: 'mdi-bell-outline',
      onSelect: () => emit('remind'),
    })
  return list
})
const extrasCollapsed = computed(() => !!props.collapseExtras && extras.value.length > 1)

// 那颗按钮上的字。窄屏收掉名字，只留「交给」；读屏读的一直是全名。
const summonText = computed(() => ({
  label: t('work.room.composer.summon', { name: props.agentName }),
  short: t('work.room.composer.summonShort'),
  on: t('work.room.composer.summonOn', { name: props.agentName }),
  off: t('work.room.composer.summonOff', { name: props.agentName }),
}))
</script>

<template>
  <!-- 下面一行：动作靠左，发送靠右。发送是这一行唯一的主操作，所以它是
       唯一的实心按钮，其余一律是安静的图标。 -->
  <div class="composer-actions d-flex align-center" :class="enterSends ? 'ga-1' : 'ga-4'">
    <!-- 这两个 input 是藏起来的，但**不能**用 display:none / visibility:hidden：
             iOS Safari 拒绝用脚本打开一个被隐藏掉的文件选择框，按钮点下去
             毫无反应。所以按 .visually-hidden 的老办法藏——留在布局里、只是
             看不见。旁边 components/common/FileSelect.vue 里也是这么藏的。 -->
    <input ref="fileInput" type="file" multiple class="visually-hidden" @change="onFilePicked" />
    <input ref="imageInput" type="file" accept="image/*" multiple class="visually-hidden" @change="onFilePicked" />
    <!-- 附件上传走的是 HTTP，和聊天那条 socket 是两回事：socket 断着的
           时候图片照样传得上去，所以这里不跟着 `connected` 一起禁用。 -->
    <BaseButton
      kind="ghost"
      class="composer-icon"
      icon="mdi-paperclip"
      size="sm"
      :title="t('work.room.composer.attachFiles')"
      @click="pickFiles"
    />
    <!-- 手机上多一颗「照片」：那儿没有截图可贴、也没有东西可拖，从文件
             选择器里翻相册要绕好几步。 -->
    <BaseButton
      v-if="showImagePicker"
      kind="ghost"
      class="composer-icon"
      icon="mdi-image-outline"
      size="sm"
      :title="t('work.room.composer.sendPhotos')"
      @click="pickImages"
    />
    <AdaptiveMenu v-if="extrasCollapsed" :actions="extras" location="top start">
      <template #activator="{ props: menu }">
        <BaseButton
          v-bind="menu"
          kind="ghost"
          class="composer-icon"
          icon="mdi-dots-horizontal"
          size="sm"
          :title="t('work.room.composer.more')"
          :aria-label="t('work.room.composer.more')"
        />
      </template>
    </AdaptiveMenu>
    <!-- 发一张自己的清单：也是「这条消息本身」，所以和附件站在左边。 -->
    <BaseButton
      v-if="canChecklist && !extrasCollapsed"
      kind="ghost"
      class="composer-icon"
      icon="mdi-format-list-checks"
      size="sm"
      :title="t('work.room.checklist.compose')"
      :aria-label="t('work.room.checklist.compose')"
      @click="emit('checklist')"
    />
    <!-- 「提醒我」：到点给自己发一条通知。它说的是这个房间里的一件事，不是这条
         消息本身，但和附件一样是安静的图标，不跟右边「怎么发出去」那几样并列。 -->
    <BaseButton
      v-if="canRemind && !extrasCollapsed"
      kind="ghost"
      class="composer-icon"
      icon="mdi-bell-outline"
      size="sm"
      :title="t('work.room.reminder.open')"
      :aria-label="t('work.room.reminder.open')"
      @click="emit('remind')"
    />
    <!-- The visible way into the shortcut sheet: Enter / Shift+Enter / Cmd+Enter used to live only in a title tooltip. -->
    <BaseButton
      v-if="!extrasCollapsed"
      kind="ghost"
      class="composer-icon"
      icon="mdi-keyboard-outline"
      size="sm"
      :title="t('global.shortcuts.open')"
      :aria-label="t('global.shortcuts.open')"
      @click="openShortcutSheet"
    />
    <v-spacer />
    <!-- 算力说的是「这条消息会在哪儿跑」，属于发送这一侧，不和左边那两个
           「这条消息本身」的动作并列。它是设置不是动作，所以最安静。 -->
    <slot name="chips" />
    <!-- 「交给芝士」：它不是一个自己存着状态的开关，它是正文的镜子——
           点一下把 @ 写进输入框（你看得见、也能自己删），手打 @ 它就自己
           亮。一个能和正文说不一样的话的开关（亮着、正文里却没有 @），会
           让「这条到底算不算叫了它」变成没人答得上来的问题。 -->
    <button
      v-if="!alwaysSummon"
      type="button"
      class="summon-btn"
      :class="{ 'summon-btn--on': summonOn }"
      :disabled="!summonReady"
      :aria-pressed="summonOn"
      :aria-label="summonText.label"
      :title="summonOn ? summonText.on : summonText.off"
      @click="emit('toggle-summon')"
    >
      <v-icon size="14">mdi-at</v-icon>
      <span class="summon-btn-label" aria-hidden="true">{{ summonText.label }}</span>
      <span class="summon-btn-short" aria-hidden="true">{{ summonText.short }}</span>
    </button>
    <!-- 断线时照样能发：消息进发件箱、立刻显示，连上就自己走 (§14.1)。
           按 `connected` 禁用会把「打字」和「后端此刻在不在」绑在一起。
           附件还在传时是例外：这一刻发出去会少带附件，所以要等，并在 title 里说清
           为什么按不动（灰着不解释，看起来像是它坏了）。 -->
    <BaseButton
      class="composer-send"
      kind="primary"
      icon="mdi-send"
      size="sm"
      :title="uploading ? t('work.room.composer.sendUploading') : t('work.room.composer.send')"
      :disabled="uploading || !canSend"
      @click="emit('send')"
    />
  </div>
</template>

<style scoped>
/* 动作行的规矩，三条：
   1. 一行一个高度。原来是 24 / 32 / 24 / 30 四种，这是它看起来像一堆零件的主因。
   2. 静止时谁也不画边框、不画底色——状态用墨色说，不用盒子说。
   3. 整行只有一个实心块，就是发送。
   位置负责表达语义：左边是「这条消息本身」的动作，右边是「它会怎么发出去」。 */
.composer-actions {
  min-height: 28px;
  margin-top: 2px;
}
.composer-icon,
.composer-send {
  width: 28px;
  height: 28px;
}
/* 能发了，灰色的键过渡成琥珀；按下去沉一下，不等松手。 */
.composer-send {
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard),
    opacity var(--dur-quick) var(--ease-standard),
    transform var(--dur-press) var(--ease-standard);
}
.composer-send:active:not(:disabled) {
  transform: scale(0.92);
}
/* 「交给芝士」。它和发送并排，但绝不能也是实心琥珀——一行里只有一个实心块，
   那个位置是发送的。亮起来只改一条描边和墨色，形态不变。 */
.summon-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  font-size: 13px;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  transition:
    color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}
.summon-btn:hover:not(:disabled) {
  background: var(--fill);
  color: var(--text);
}
.summon-btn:disabled {
  cursor: default;
  opacity: 0.5;
}
/* 开着的时候要一眼认得出：这条消息会真的开出一轮，和「只是说了句话」是两回事。
   一条 1px 的描边抢不过旁边那颗实心的发送按钮，所以开态是填充的：比 hover 深一档
   的底、墨色的字。只变底色，不加粗——字一加粗按钮就变宽，开关一下整行跟着挪。
   不用琥珀：琥珀是旁边那颗发送按钮的，两个琥珀的东西并排，人就分不出该点哪个。 */
.summon-btn--on {
  background: var(--line-2);
  color: var(--ink);
}
.summon-btn--on:hover:not(:disabled) {
  background: var(--line-2);
  color: var(--ink);
}
/* 窄屏上名字收掉，留「交给」两个字：只剩一个 @ 图标的话，它读起来是「插入一个
   @」，不是「这条交给它处理」。两个字加图标放得进这一行，不会换行。 */
.summon-btn-short {
  display: none;
}
@media (max-width: 480px) {
  .summon-btn-label {
    display: none;
  }
  .summon-btn-short {
    display: inline;
  }
}

/* 触屏上手指点得中（设计系统 §10.1）：几颗按钮画出来的样子不变，能点的范围撑到
   44×44（同 style.css 的 .tap-target）。撑开的范围不能互相盖住，所以按钮之间拉开到
   16px（模板里按输入方式换 ga-4）；这一行也长到 44px，不让它伸进上面的输入框。 */
@media (pointer: coarse) {
  .composer-actions {
    min-height: 44px;
  }
  .summon-btn {
    position: relative;
  }
  .composer-icon::before,
  .composer-send::before,
  .summon-btn::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    transform: translate(-50%, -50%);
  }
}
</style>
