<script setup lang="ts">
// 新手引导：一个项目都没有、也没走过引导的人登录后落在这里（router/home.ts
// `landingForMember`），帮助菜单「新手引导」带 `?again=1` 再走一遍。
//
// 五问：怎么称呼、主要用知是做什么、想先做成什么、和谁一起、芝士在哪干活。每个
// 回答都有去处，右边的「你的项目」随回答长出来；点「建好项目」才真的建
// （`POST /users/me/onboarding`），然后进它的「综合」，第一句请求已经写在输入框里，
// 新手引导的浮窗接着带（composables/useProjectTour.ts）。
import type { OnboardingAnswers, WorkRole } from '@/api'

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { finishOnboarding, uploadAttachment } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BrandLockup from '@/components/common/BrandLockup.vue'
import { t } from '@/i18n'
import { saveComposerDraft } from '@/lib/composerDrafts'
import { desktopBridge, startConnecting } from '@/lib/desktop'
import { inDesktopApp } from '@/lib/desktopApp'
import { refreshProjects } from '@/query/projects'
import AccountService from '@/services/account'

const props = defineProps<{
  /** 从帮助菜单再走一遍：从身份开始，回答预填上次的。 */
  again?: boolean
}>()
const router = useRouter()
const again = props.again
const me = AccountService.user

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

// 再走一遍的人已经回答过称呼，从身份开始。
const step = ref(again ? 2 : 1)
const LAST = 5
const nickname = ref(me?.nickname ?? '')
const role = ref<WorkRole | null>((me?.work_role as WorkRole | null | undefined) ?? null)
const organization = ref(me?.organization ?? '')
const taskKey = ref<string | null>(null)
const own = ref('')
const files = ref<File[]>([])
const picker = ref<HTMLInputElement | null>(null)
const solo = ref(true)
const inviteText = ref('')
const desktop = inDesktopApp()
const onThisComputer = ref(false)
const busy = ref(false)
const failure = ref('')
// 每走一遍引导选一次：重试补完同一个项目，不会建出第二个。
const projectId = crypto.randomUUID()

const group = computed<TaskGroup>(() => (role.value && role.value in TASKS ? (role.value as TaskGroup) : 'general'))
const taskText = (key: string, part: 'label' | 'hint' | 'name' | 'text') =>
  t(`welcome.tasks.${group.value}.${key}.${part}`)
const displayName = computed(() => nickname.value.trim() || me?.username || '')
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
const invitees = computed(() => (solo.value ? [] : inviteText.value.split(/[\s,\uff0c]+/).filter(Boolean)))
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

async function finish(skipAll = false) {
  busy.value = true
  failure.value = ''
  const answers: OnboardingAnswers = skipAll
    ? {
        project_id: projectId,
        project_name: fallbackName.value,
        intent: '',
        nickname: nickname.value,
        invitees: [],
        solo: true,
      }
    : {
        project_id: projectId,
        project_name: projectName.value,
        intent: intent.value,
        nickname: nickname.value,
        work_role: role.value,
        organization: organization.value,
        invitees: invitees.value,
        solo: solo.value,
      }
  try {
    const wantsDevice = !skipAll && desktop && onThisComputer.value
    const done = await finishOnboarding({ ...answers, device_id: wantsDevice ? await thisComputer() : null })
    if (wantsDevice && !done.on_device) toast.warning(t('welcome.deviceFailed'))
    rememberOnAccount(skipAll)
    const room = done.root_topic_id
    if (room) {
      const atts = []
      for (const file of files.value) atts.push(await uploadAttachment(room, file))
      // 输入框里那句和起手卡写进去的一样带 @：直接回车只发给成员，芝士不会接。
      const draft = request.value ? `@${t('work.room.defaultAgentName')} ${request.value}` : ''
      if (!skipAll && (draft || atts.length)) saveComposerDraft(room, { draft, reply: null, atts })
    }
    if (done.not_invited.length)
      toast.warning(t('welcome.notInvited', { names: done.not_invited.join(t('welcome.preview.listSep')) }))
    await refreshProjects()
    await router.replace(
      room
        ? { name: 'workspace-topic', params: { projectId: done.project_id, topicId: room } }
        : { name: 'workspace-project', params: { projectId: done.project_id } }
    )
  } catch (e) {
    failure.value = t('welcome.failed', { reason: e instanceof Error ? e.message : String(e) })
  } finally {
    busy.value = false
  }
}

// 账号上记下这次的回答：登录态里那份用户记录也要跟着变，否则同一个标签页回到首页
// 时还以为没走过引导。
function rememberOnAccount(skipAll: boolean) {
  const user = AccountService.user
  if (!user) return
  AccountService.user = {
    ...user,
    onboarded_at: user.onboarded_at ?? new Date().toISOString(),
    nickname: nickname.value.trim() || user.nickname,
    ...(skipAll ? {} : { work_role: role.value, organization: organization.value.trim() || null }),
  }
  localStorage.setItem('user', JSON.stringify(AccountService.user))
}

// 桌面端选了「用这台电脑」：先接上这台电脑，建项目时把它带上，服务端把它加进项目、
// 让「综合」里的芝士在它上面的隔离环境里干活。接不上就留在云端，说一声，不拦着人进项目。
async function thisComputer(): Promise<string | null> {
  try {
    await startConnecting({ quiet: true })
    return (await desktopBridge()?.thisDevice()) ?? null
  } catch {
    return null
  }
}

const canGoOn = computed(() => step.value !== 1 || !!nickname.value.trim())
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
          <h1 class="t-page-title">{{ t('welcome.name.q') }}</h1>
          <input v-model="nickname" class="welcome__input" autocomplete="nickname" :aria-label="t('welcome.name.q')" />
          <p class="welcome__why">{{ t('welcome.name.why') }}</p>
        </template>

        <template v-else-if="step === 2">
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

        <template v-else-if="step === 3">
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

        <template v-else-if="step === 4">
          <h1 class="t-page-title">{{ t('welcome.who.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.who.why') }}</p>
          <div class="welcome__options">
            <button type="button" class="welcome__option" :aria-pressed="solo" @click="solo = true">
              <span class="welcome__icon"><v-icon icon="mdi-account-outline" size="20" /></span>
              <span class="welcome__label">
                <span class="t-title">{{ t('welcome.who.solo') }}</span>
                <span class="welcome__hint">{{ t('welcome.who.soloHint') }}</span>
              </span>
            </button>
            <button type="button" class="welcome__option" :aria-pressed="!solo" @click="solo = false">
              <span class="welcome__icon"><v-icon icon="mdi-account-multiple-outline" size="20" /></span>
              <span class="welcome__label">
                <span class="t-title">{{ t('welcome.who.team') }}</span>
                <span class="welcome__hint">{{ t('welcome.who.teamHint') }}</span>
              </span>
            </button>
          </div>
          <label v-if="!solo" class="welcome__field" for="welcome-invitees">
            <span class="welcome__field-label">{{ t('welcome.who.invitees') }}</span>
            <input id="welcome-invitees" v-model="inviteText" class="welcome__input" autocomplete="off" />
          </label>
        </template>

        <template v-else>
          <h1 class="t-page-title">{{ t('welcome.where.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.where.why') }}</p>
          <div class="welcome__options welcome__options--stack">
            <button
              type="button"
              class="welcome__option"
              :aria-pressed="!onThisComputer"
              @click="onThisComputer = false"
            >
              <span class="welcome__icon"><v-icon icon="mdi-cloud-outline" size="20" /></span>
              <span class="welcome__label">
                <span class="t-title">{{ t('welcome.where.cloud') }}</span>
                <span class="welcome__hint">{{ t('welcome.where.cloudHint') }}</span>
              </span>
            </button>
            <button
              v-if="desktop"
              type="button"
              class="welcome__option"
              :aria-pressed="onThisComputer"
              @click="onThisComputer = true"
            >
              <span class="welcome__icon"><v-icon icon="mdi-laptop" size="20" /></span>
              <span class="welcome__label">
                <span class="t-title">{{ t('welcome.where.device') }}</span>
                <span class="welcome__hint">{{ t('welcome.where.deviceHint') }}</span>
              </span>
            </button>
          </div>
          <!-- 网页上不能把这台电脑接给芝士：给一张同样的卡，说要先装桌面端，按钮去下载页。 -->
          <div v-if="!desktop" class="welcome__option welcome__option--static">
            <span class="welcome__icon"><v-icon icon="mdi-laptop" size="20" /></span>
            <span class="welcome__label">
              <span class="t-title">{{ t('welcome.where.deviceWeb') }}</span>
              <span class="welcome__hint">{{ t('welcome.where.webNote') }}</span>
            </span>
            <BaseButton kind="secondary" size="sm" :to="{ name: 'Download' }">{{
              t('welcome.where.download')
            }}</BaseButton>
          </div>
        </template>

        <p v-if="failure" class="welcome__failure" role="alert">{{ failure }}</p>

        <div class="welcome__foot">
          <BaseButton v-if="step === 1 && !again" kind="ghost" :disabled="busy" @click="finish(true)">
            {{ t('welcome.skipAll') }}
          </BaseButton>
          <BaseButton v-else-if="step > 1 && step < LAST" kind="ghost" :disabled="busy" @click="step += 1">
            {{ t('welcome.skip') }}
          </BaseButton>
          <span v-else />
          <span class="welcome__go">
            <BaseButton v-if="step > (again ? 2 : 1)" kind="ghost" :disabled="busy" @click="step -= 1">
              {{ t('welcome.back') }}
            </BaseButton>
            <BaseButton v-if="step < LAST" kind="primary" :disabled="!canGoOn" @click="step += 1">
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
          <span class="welcome__tile">{{ (step >= 3 ? projectName : fallbackName).slice(0, 1) }}</span>
        </div>
        <div class="welcome__side">
          <p class="t-title welcome__pname">{{ step >= 3 ? projectName : fallbackName }}</p>
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
          <dd>
            {{ displayName }} · {{ t('work.room.defaultAgentName') }}
            <template v-for="name in invitees" :key="name"> · {{ t('welcome.preview.invited', { name }) }}</template>
          </dd>
          <template v-if="aboutLine">
            <dt>{{ t('welcome.preview.about') }}</dt>
            <dd>{{ t('welcome.preview.aboutLine', { name: displayName, about: aboutLine }) }}</dd>
          </template>
          <template v-if="step >= LAST">
            <dt>{{ t('welcome.preview.where') }}</dt>
            <dd>{{ onThisComputer ? t('welcome.preview.device') : t('welcome.preview.cloud') }}</dd>
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
.welcome__options--stack {
  grid-template-columns: minmax(0, 1fr);
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
.welcome__option--static {
  align-items: center;
  cursor: default;
}
.welcome__option--static:hover {
  background: var(--surface);
}
.welcome__option--static .welcome__label {
  flex: 1;
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
