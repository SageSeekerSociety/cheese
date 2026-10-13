<script setup lang="ts">
// 任务页顶上那一行：这个任务是从哪句话来的。引的是那句原话（谁说的、在哪说的、什么
// 时候），点它就回到那段对话、停在那一条上。自己新建的任务没有这句话，就照实说是谁
// 新建的；AI 队友建的而平台没记下它在回哪句话，也照实说没记下，不拿别的话顶替。
import type { RoomTask } from '@/cx_types'
import type { MemberFace, MemberFaces } from '@/lib/memberFaces'
import type { TaskSource } from '@/types/taskOrigin'

import { computed } from 'vue'

import UserRef from '@/components/common/UserRef.vue'
import { t } from '@/i18n'
import { isAgentHandle } from '@/lib/authorship'
import { plainRefs } from '@/lib/refChip'
import { relTime } from '@/lib/relTime'

const props = withDefaults(
  defineProps<{
    /** 没取到（还在取，或后端还没有这一项）是 undefined：那时什么都不画。 */
    source: TaskSource | null | undefined
    task: RoomTask
    memberNames: Record<string, string>
    /** 名册来的身份（头像 id、队友身份）。有它就不用拿名字反查；没有时退回 handle + 彩色首字母。 */
    faces?: MemberFaces
  }>(),
  { faces: () => ({}) }
)

const emit = defineEmits<{ (e: 'open', source: TaskSource): void }>()

function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
/** 要画的那个人：名册有这一行就用它，否则 null（视图退回 handle + 彩色首字母）。 */
function faceOf(handle: string): MemberFace | null {
  return props.faces[handle] ?? null
}
function faceName(handle: string): string {
  return faceOf(handle)?.name ?? nameOf(handle)
}

const where = computed(() => {
  const source = props.source
  if (!source) return ''
  return t(`work.task.source.${source.where}`, { title: source.title })
})
// 引的那句话里 @ 过的人、提过的话题、指过的文件，读成名字，和对话里 chip 上的字一样。
const quoteText = computed(() => {
  const source = props.source
  if (!source) return ''
  return plainRefs(source.content, { mentionNames: props.memberNames, topicTitles: {} })
})
// 自己新建的任务：照实说是谁建的。队友还是人决定用哪一句（一个是「没记下来源」，一个是
// 「不是从对话里转出来的」）；队友身份先认名册（faces），名册没有才按 handle 命名规则兜底。
const createdKey = computed(() =>
  props.faces[props.task.created_by ?? '']?.agent ?? isAgentHandle(props.task.created_by ?? '')
    ? 'work.task.source.byAgent'
    : 'work.task.source.byPerson'
)
</script>

<template>
  <button v-if="source" type="button" class="task-source" data-testid="task-source" @click="emit('open', source)">
    <span class="t-meta c-faint">{{ where }} · {{ relTime(source.created_at) }}</span>
    <span class="t-body task-source__quote">
      <i18n-t keypath="work.task.source.quote" scope="global" tag="span">
        <template #name>
          <UserRef
            :handle="source.author"
            :name="faceName(source.author)"
            :avatar-id="faceOf(source.author)?.avatarId"
            :agent="faceOf(source.author)?.agent"
          />
        </template>
        <template #text>{{ quoteText }}</template>
      </i18n-t>
    </span>
  </button>
  <div v-else-if="source === null" class="task-source task-source--none t-meta c-faint" data-testid="task-source">
    <i18n-t v-if="task.created_by" :keypath="createdKey" scope="global" tag="span">
      <template #name>
        <UserRef
          :handle="task.created_by"
          :name="faceName(task.created_by)"
          :avatar-id="faceOf(task.created_by)?.avatarId"
          :agent="faceOf(task.created_by)?.agent"
        />
      </template>
    </i18n-t>
    <span v-else>{{ t('work.task.source.unknown') }}</span>
    <span> · {{ relTime(task.created_at) }}</span>
  </div>
</template>

<style scoped>
.task-source {
  display: flex;
  flex-direction: column;
  /* 每行撑满容器宽，而不是按内容 shrink-to-fit：名字那颗 chip 是 nowrap，按内容量宽会
     让整行越过容器（窄屏横向溢出）。 */
  align-items: stretch;
  gap: 2px;
  width: 100%;
  padding: 10px 16px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: none;
  text-align: left;
}
button.task-source {
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
button.task-source:hover {
  background: var(--fill);
}
.task-source__quote {
  display: -webkit-box;
  /* 这一行 settle 到容器宽（配合 `.task-source` 的 stretch），名字那颗 chip 才收得住。 */
  min-width: 0;
  overflow: hidden;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  color: var(--text);
  white-space: pre-wrap;
}
</style>

<!-- 引文里那颗身份 chip 收得住（长名字省略号，不把这一行顶出容器）。见该文件里的说明。 -->
<style scoped src="@/styles/identity-inline.css"></style>
