<script setup lang="ts">
// 新手引导的画面（页面是同目录的 Welcome.vue）。
//
// 两问：主要用知是做什么、想先做成什么。每个回答都有去处，右边的「你的项目」随回答
// 长出来；点「建好项目」才把回答交出去（`finish`）。称呼用注册时填的昵称，邀请队友
// 和换电脑都在项目里做，这里不问。建项目、进「综合」由页面做（Welcome.vue），这里
// 只画两问。
import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BrandLockup from '@/components/common/BrandLockup.vue'
import { t } from '@/i18n'

export type WorkRole = 'student' | 'teacher' | 'office' | 'research' | 'maker' | 'other'

/** 「建好项目」交出去的东西：建项目要的回答，加上建好以后要做的事。 */
export interface WelcomeFinish {
  answers: {
    project_id: string
    project_name: string
    intent: string
    work_role?: WorkRole | null
    organization?: string
  }
  /** 跳过了引导：只建一个以名字命名的项目，不写请求、不记身份。 */
  skipAll: boolean
  /** 写进「综合」输入框的第一句（带 @芝士），和要附上的文件。 */
  draft: string
  files: File[]
}

const props = defineProps<{
  /** 从帮助菜单再走一遍：回答预填上次的，没有「跳过引导」。 */
  again?: boolean
  /** 账号上已有的：预填身份和单位，名字用来给项目起默认名。 */
  me: { username: string; nickname?: string | null; work_role?: string | null; organization?: string | null } | null
  busy: boolean
  /** 上一次没建成的原因；空串表示没有。 */
  failure: string
}>()
const emit = defineEmits<{ finish: [WelcomeFinish]; exit: [] }>()
const again = props.again
const me = props.me

// 每种身份四件事（文案在 welcome.tasks.<组>.<键>）；没对上的身份用通用的一组。
const TASKS = {
  student: ['report', 'project', 'review', 'group'],
  teacher: ['assignment', 'slides', 'feedback', 'course'],
  office: ['doc', 'table', 'tool', 'plan'],
  research: ['survey', 'data', 'section', 'reproduce'],
  general: ['tidy', 'research', 'build', 'plan'],
} as const
type TaskGroup = keyof typeof TASKS
const ROLES: WorkRole[] = ['student', 'teacher', 'office', 'research', 'maker', 'other']
const ROLE_ICONS: Record<WorkRole, string> = {
  student: 'mdi-school-outline',
  teacher: 'mdi-human-male-board',
  office: 'mdi-briefcase-outline',
  research: 'mdi-flask-outline',
  maker: 'mdi-rocket-launch-outline',
  other: 'mdi-dots-horizontal',
}
// 每件事一个图标：只是帮人一眼扫过四张卡，不承载意思。
const TASK_ICONS: Record<string, string> = {
  report: 'mdi-file-document-edit-outline',
  project: 'mdi-application-brackets-outline',
  review: 'mdi-notebook-outline',
  group: 'mdi-account-group-outline',
  assignment: 'mdi-clipboard-text-outline',
  slides: 'mdi-presentation',
  feedback: 'mdi-comment-check-outline',
  course: 'mdi-folder-multiple-outline',
  doc: 'mdi-file-document-edit-outline',
  table: 'mdi-table',
  tool: 'mdi-application-brackets-outline',
  plan: 'mdi-format-list-checks',
  survey: 'mdi-book-search-outline',
  data: 'mdi-chart-box-outline',
  section: 'mdi-text-box-edit-outline',
  reproduce: 'mdi-code-braces',
  tidy: 'mdi-file-document-edit-outline',
  research: 'mdi-book-search-outline',
  build: 'mdi-application-brackets-outline',
}

const step = ref(1)
const LAST = 2
const role = ref<WorkRole | null>((me?.work_role as WorkRole | null | undefined) ?? null)
const organization = ref(me?.organization ?? '')
const taskKey = ref<string | null>(null)
const own = ref('')
const files = ref<File[]>([])
const picker = ref<HTMLInputElement | null>(null)
// 每走一遍引导选一次：重试补完同一个项目，不会建出第二个。
const projectId = crypto.randomUUID()

const group = computed<TaskGroup>(() => (role.value && role.value in TASKS ? (role.value as TaskGroup) : 'general'))
const taskText = (key: string, part: 'label' | 'hint' | 'name' | 'text') =>
  t(`welcome.tasks.${group.value}.${key}.${part}`)
const displayName = computed(() => me?.nickname?.trim() || me?.username || '')
const fallbackName = computed(() => t('welcome.preview.nameFallback', { name: displayName.value }))

const projectName = computed(() => {
  const mine = own.value.trim()
  if (mine) return mine.length > 30 ? `${mine.slice(0, 30)}…` : mine
  return taskKey.value ? taskText(taskKey.value, 'name') : fallbackName.value
})
const intent = computed(() => own.value.trim() || (taskKey.value ? taskText(taskKey.value, 'label') : ''))
const request = computed(() => {
  const mine = own.value.trim()
  if (mine) return `${mine}\n${t('welcome.task.ownPlan')}`
  return taskKey.value ? taskText(taskKey.value, 'text') : ''
})
const aboutLine = computed(() =>
  [role.value && role.value !== 'other' ? t(`welcome.role.${role.value}Hint`) : '', organization.value.trim()]
    .filter(Boolean)
    .join(t('welcome.preview.aboutSep'))
)

function pickTask(key: string) {
  taskKey.value = taskKey.value === key ? null : key
}
function pickRole(next: WorkRole) {
  if (role.value !== next) taskKey.value = null
  role.value = next
}
function addFiles(list: FileList | null) {
  if (list) files.value = [...files.value, ...Array.from(list)]
}

function finish(skipAll = false) {
  const answers: WelcomeFinish['answers'] = skipAll
    ? {
        project_id: projectId,
        project_name: fallbackName.value,
        intent: '',
      }
    : {
        project_id: projectId,
        project_name: projectName.value,
        intent: intent.value,
        work_role: role.value,
        organization: organization.value,
      }
  // 输入框里那句和起手卡写进去的一样带 @：直接回车只发给成员，芝士不会接。
  const draft = !skipAll && request.value ? `@${t('work.room.defaultAgentName')} ${request.value}` : ''
  emit('finish', {
    answers,
    skipAll,
    draft,
    files: skipAll ? [] : files.value,
  })
}
</script>

<template>
  <div class="welcome">
    <main class="welcome__main">
      <BrandLockup class="welcome__brand" />
      <div class="welcome__form">
        <div class="welcome__steps">
          <div class="welcome__bar" aria-hidden="true">
            <i v-for="n in LAST" :key="n" :class="{ on: n <= step }" />
          </div>
          <span class="t-meta">{{ t('welcome.stepOf', { n: step, total: LAST }) }}</span>
        </div>

        <template v-if="step === 1">
          <p class="welcome__intro">{{ t('welcome.intro') }}</p>
          <h1 class="t-page-title">{{ t('welcome.role.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.role.why') }}</p>
          <div class="welcome__options">
            <button
              v-for="r in ROLES"
              :key="r"
              type="button"
              class="welcome__option"
              :aria-pressed="role === r"
              @click="pickRole(r)"
            >
              <span class="welcome__icon"><v-icon :icon="ROLE_ICONS[r]" size="20" /></span>
              <span class="welcome__label">
                <span class="t-title">{{ t(`welcome.role.${r}`) }}</span>
                <span v-if="r !== 'other'" class="welcome__hint">{{ t(`welcome.role.${r}Hint`) }}</span>
              </span>
            </button>
          </div>
          <label class="welcome__field" for="welcome-org">
            <span class="welcome__field-label">{{ t('welcome.role.org') }}</span>
            <input id="welcome-org" v-model="organization" class="welcome__input" autocomplete="off" />
          </label>
        </template>

        <template v-else>
          <h1 class="t-page-title">{{ t('welcome.task.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.task.why') }}</p>
          <div class="welcome__options">
            <button
              v-for="key in TASKS[group]"
              :key="key"
              type="button"
              class="welcome__option"
              :aria-pressed="taskKey === key && !own.trim()"
              @click="pickTask(key)"
            >
              <span class="welcome__icon"><v-icon :icon="TASK_ICONS[key]" size="20" /></span>
              <span class="welcome__label">
                <span class="t-title">{{ taskText(key, 'label') }}</span>
                <span class="welcome__hint">{{ taskText(key, 'hint') }}</span>
              </span>
            </button>
          </div>
          <label class="welcome__field" for="welcome-own">
            <span class="welcome__field-label">{{ t('welcome.task.own') }}</span>
            <input
              id="welcome-own"
              v-model="own"
              class="welcome__input"
              autocomplete="off"
              :placeholder="t('welcome.task.ownPlaceholder')"
            />
          </label>
          <input
            ref="picker"
            type="file"
            multiple
            hidden
            :aria-label="t('welcome.task.files')"
            @change="addFiles(($event.target as HTMLInputElement).files)"
          />
          <button
            type="button"
            class="welcome__drop"
            @click="picker?.click()"
            @dragover.prevent
            @drop.prevent="addFiles($event.dataTransfer?.files ?? null)"
          >
            <v-icon icon="mdi-paperclip" size="16" />
            <span>{{ t('welcome.task.files') }}</span>
          </button>
          <p class="welcome__why">{{ t('welcome.task.filesNote') }}</p>
        </template>

        <p v-if="failure" class="welcome__failure" role="alert">{{ failure }}</p>

        <div class="welcome__foot">
          <!-- 第一次走的时候有「跳过引导」（建一个占位项目）；从帮助菜单再看一遍的那次
               没有项目要建，给的是「退出引导」：看完就走，什么都不改。 -->
          <BaseButton v-if="step === 1 && !again" kind="ghost" :disabled="busy" @click="finish(true)">
            {{ t('welcome.skipAll') }}
          </BaseButton>
          <BaseButton v-else-if="again" kind="ghost" :disabled="busy" @click="emit('exit')">
            {{ t('welcome.exit') }}
          </BaseButton>
          <span v-else />
          <span class="welcome__go">
            <BaseButton v-if="step > 1" kind="ghost" :disabled="busy" @click="step -= 1">
              {{ t('welcome.back') }}
            </BaseButton>
            <BaseButton v-if="step < LAST" kind="primary" @click="step += 1">
              {{ t('welcome.next') }}
            </BaseButton>
            <BaseButton v-else kind="primary" :loading="busy" @click="finish()">
              {{ busy ? t('welcome.finishing') : t('welcome.finish') }}
            </BaseButton>
          </span>
        </div>
      </div>
    </main>

    <!-- 右边是一个缩小的知是窗口：项目格子、项目名、侧栏那几行，下面是这个项目已经
         知道的事。回答一项，就多一行。 -->
    <aside class="welcome__preview" :aria-label="t('welcome.preview.title')">
      <p class="t-eyebrow">{{ t('welcome.preview.title') }}</p>
      <div class="welcome__window">
        <div class="welcome__rail">
          <span class="welcome__tile">{{ (step >= 2 ? projectName : fallbackName).slice(0, 1) }}</span>
        </div>
        <div class="welcome__side">
          <p class="t-title welcome__pname">{{ step >= 2 ? projectName : fallbackName }}</p>
          <p class="welcome__nav">
            <v-icon icon="mdi-view-dashboard-outline" size="14" />{{ t('navigation.project.overview') }}
          </p>
          <p class="welcome__nav">
            <v-icon icon="mdi-folder-outline" size="14" />{{ t('navigation.project.library') }}
          </p>
          <p class="welcome__nav on"><span class="welcome__hash">#</span>{{ t('navigation.project.general') }}</p>
        </div>
        <dl class="welcome__facts">
          <template v-if="intent">
            <dt>{{ t('welcome.preview.brief') }}</dt>
            <dd>{{ intent }}</dd>
          </template>
          <template v-if="files.length">
            <dt>{{ t('welcome.preview.files') }}</dt>
            <dd>{{ files.map((f) => f.name).join(t('welcome.preview.listSep')) }}</dd>
          </template>
          <dt>{{ t('welcome.preview.members') }}</dt>
          <dd>{{ displayName }} · {{ t('work.room.defaultAgentName') }}</dd>
          <template v-if="aboutLine">
            <dt>{{ t('welcome.preview.about') }}</dt>
            <dd>{{ t('welcome.preview.aboutLine', { name: displayName, about: aboutLine }) }}</dd>
          </template>
        </dl>
      </div>
      <p class="welcome__why">{{ t('welcome.preview.hint') }}</p>
    </aside>
  </div>
</template>

<style scoped>
.welcome {
  min-height: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 42%);
  background: var(--surface);
}
.welcome__main {
  display: flex;
  flex-direction: column;
  padding: 28px 48px;
  min-width: 0;
}
.welcome__brand {
  height: 28px;
}
.welcome__form {
  flex: 1;
  width: min(560px, 100%);
  margin: 0 auto;
  padding: 40px 0 24px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 16px;
}
.welcome__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.welcome__field-label {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
}
.welcome__input {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  outline: none;
  transition: border-color var(--dur-quick) var(--ease-out);
}
.welcome__input::placeholder {
  color: var(--faint);
}
.welcome__input:hover {
  border-color: var(--muted);
}
.welcome__input:focus-visible {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
}
.welcome__steps {
  display: flex;
  align-items: center;
  gap: 12px;
}
.welcome__bar {
  display: flex;
  gap: 4px;
}
.welcome__bar i {
  width: 32px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--line-2);
}
.welcome__bar i.on {
  background: var(--ink);
}
.welcome__intro {
  margin: 0;
  color: var(--muted);
}
.welcome__why {
  margin: 0;
  color: var(--faint);
}
.welcome__options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.welcome__option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  text-align: left;
  padding: 14px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  cursor: pointer;
  transition:
    border-color var(--dur-quick) var(--ease-out),
    background-color var(--dur-quick) var(--ease-out);
}
.welcome__option:hover {
  background: var(--fill);
}
.welcome__option[aria-pressed='true'] {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
}
.welcome__icon {
  flex: none;
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--muted);
}
.welcome__option[aria-pressed='true'] .welcome__icon {
  background: var(--ink);
  color: var(--surface);
}
.welcome__label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.welcome__hint {
  color: var(--faint);
  font-size: 13px;
}
.welcome__drop {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 14px;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--faint);
  cursor: pointer;
}
.welcome__drop:hover {
  background: var(--fill);
}
.welcome__failure {
  color: var(--danger);
  margin: 0;
}
.welcome__foot {
  padding-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.welcome__go {
  display: flex;
  gap: 8px;
}
.welcome__preview {
  background: var(--canvas);
  border-left: 1px solid var(--line);
  padding: 32px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 12px;
}
.welcome__window {
  display: grid;
  grid-template-columns: 44px 150px minmax(0, 1fr);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-2);
  overflow: hidden;
  min-height: 260px;
}
.welcome__rail {
  background: var(--canvas);
  border-right: 1px solid var(--line);
  padding: 12px 0;
  display: flex;
  justify-content: center;
}
.welcome__tile {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  background: var(--ink);
  color: var(--surface);
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 13px;
}
.welcome__side {
  border-right: 1px solid var(--line);
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.welcome__pname {
  margin: 0 0 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.welcome__nav {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 13px;
}
.welcome__nav.on {
  background: var(--fill-2);
  color: var(--ink);
  font-weight: 600;
}
.welcome__hash {
  width: 14px;
  text-align: center;
  color: var(--faint);
}
.welcome__facts {
  margin: 0;
  padding: 14px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-content: start;
  gap: 6px 10px;
  font-size: 13px;
}
.welcome__facts dt {
  color: var(--faint);
}
.welcome__facts dd {
  margin: 0;
  color: var(--text);
  overflow-wrap: anywhere;
}
@media (max-width: 900px) {
  .welcome {
    grid-template-columns: minmax(0, 1fr);
  }
  .welcome__preview {
    border-left: 0;
    border-top: 1px solid var(--line);
    padding: 20px 16px;
  }
  .welcome__main {
    padding: 20px 16px;
  }
  .welcome__options {
    grid-template-columns: minmax(0, 1fr);
  }
  .welcome__window {
    grid-template-columns: 44px minmax(0, 1fr);
  }
  .welcome__side {
    display: none;
  }
}
</style>
