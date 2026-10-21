<!--
  What the invite-friends page shows (Invites.vue): the person's own invite code
  with the seats it has left and the link to share, what a friend and they each
  get, and the friends who signed up with it. Reading the code and copying it
  happen in the page; this half only draws.
-->
<template>
  <div class="settings-page invites">
    <header>
      <h1 class="t-page-title">{{ t('account.invites.title') }}</h1>
      <p class="settings-page__lede">{{ t('account.invites.lede') }}</p>
    </header>

    <BaseLoadError
      v-if="failed"
      :title="t('account.invites.loadFailed')"
      :error="failingReason"
      :retry-label="t('account.invites.retry')"
      @retry="emit('retry')"
    />

    <!-- Held at the cards' height until the data arrives, so the page does not jump. -->
    <section v-else-if="!invites" class="settings-card invites__pending" aria-busy="true" />

    <template v-else>
      <!-- The code and the link to share it: what this page is for, so it comes first and has the one amber action. -->
      <section class="settings-card invites__share">
        <div class="invites__code-row">
          <div class="invites__code">
            <span class="t-eyebrow-read">{{ t('account.invites.code.label') }}</span>
            <span class="invites__code-value">{{ invites.code }}</span>
          </div>
          <div class="invites__seats">
            <span v-if="invites.max_uses <= 10" class="invites__pips" aria-hidden="true">
              <span
                v-for="seat in invites.max_uses"
                :key="seat"
                class="invites__pip"
                :class="{ 'invites__pip--used': seat <= invites.use_count }"
              />
            </span>
            <span class="invites__seats-text t-num" data-testid="invite-seats">{{
              t('account.invites.code.remaining', { left: seatsLeft, total: invites.max_uses })
            }}</span>
          </div>
        </div>
        <p v-if="!invites.unlocked" class="invites__hint" data-testid="invite-unlock-hint">
          {{ t('account.invites.code.unlockHint', { n: invites.unlock_uses }) }}
        </p>
        <div class="invites__actions">
          <BaseButton kind="primary" prepend-icon="mdi-link-variant" @click="emit('copyLink')">
            {{ t('account.invites.code.copyLink') }}
          </BaseButton>
          <BaseButton kind="secondary" prepend-icon="mdi-content-copy" @click="emit('copyCode')">
            {{ t('account.invites.code.copyCode') }}
          </BaseButton>
        </div>
      </section>

      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.invites.steps.title') }}</h2>
        <ol class="invites__steps">
          <li v-for="(step, i) in steps" :key="step.what" class="invites__step">
            <span class="invites__step-n t-num" aria-hidden="true">{{ i + 1 }}</span>
            <span class="invites__step-what">{{ step.what }}</span>
            <span class="invites__step-gain">{{ step.gain }}</span>
          </li>
        </ol>
        <p class="invites__note">{{ t('account.invites.steps.meaning') }}</p>
      </section>

      <section class="settings-card">
        <div class="settings-card__head">
          <h2 class="settings-card__title">{{ t('account.invites.people.title') }}</h2>
          <NavLink v-if="creditsTo" class="invites__link" :to="creditsTo">
            {{ t('account.invites.people.viewCredits') }}<v-icon icon="mdi-chevron-right" size="16" />
          </NavLink>
        </div>
        <dl class="invites__stats">
          <div class="invites__stat">
            <dt>{{ t('account.invites.people.invited') }}</dt>
            <dd class="t-num" data-testid="invite-count">{{ people.length }}</dd>
          </div>
          <div class="invites__stat">
            <dt>{{ t('account.invites.people.accepted') }}</dt>
            <dd class="t-num" data-testid="invite-accepted">{{ acceptedCount }}</dd>
          </div>
          <div class="invites__stat">
            <dt>{{ t('account.invites.people.credits') }}</dt>
            <dd class="t-num" data-testid="invite-credits">
              {{ t('account.invites.people.creditsValue', { n: invites.invite_credits }) }}
            </dd>
          </div>
        </dl>
        <BaseEmptyState
          v-if="!people.length"
          class="settings-empty invites__empty"
          size="inline"
          :title="t('account.invites.people.empty')"
        />
        <ul v-else class="invites__people">
          <li v-for="person in people" :key="person.handle" class="invitee">
            <UserAvatar :avatar="person.avatarUrl" :name="person.nickname || person.handle" size="32" />
            <span class="invitee__who">
              <span class="invitee__name" data-user-content>{{ person.nickname || person.handle }}</span>
              <span class="invitee__meta">
                <span>@{{ person.handle }}</span>
                <span class="t-num">{{
                  t('account.invites.people.registeredOn', { date: dateOf(person.registeredAt) })
                }}</span>
              </span>
            </span>
            <span class="invitee__stage">
              <span class="status-dot" :class="person.stage === 'activated' ? 'status-dot--ok' : 'status-dot--muted'" />
              {{ stageText(person) }}
            </span>
          </li>
        </ul>
        <p class="invites__note invites__note--foot">{{ t('account.invites.people.capNote') }}</p>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'
import type { InviteStage, MyInvites } from '@/types/invites'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import NavLink from '@/components/common/NavLink.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { absDate } from '@/lib/dateText'

/** One friend as drawn: the page has already turned their avatar id into a URL. */
export interface InviteeRow {
  handle: string
  nickname: string | null
  /** Empty when they have not chosen an avatar: the initial is drawn instead. */
  avatarUrl: string
  registeredAt: string
  stage: InviteStage
  /** Credits the person received for this friend once activated; 0 past the monthly cap. */
  credits: number
}

const props = defineProps<{
  /** Null until the code arrives (or when reading it failed). */
  invites: MyInvites | null
  people: InviteeRow[]
  failed: boolean
  failingReason?: string | null
  /** Where the person's own credits are shown; null when there is no signed-in handle to go by. */
  creditsTo: NavTarget | null
}>()

const emit = defineEmits<{ copyCode: []; copyLink: []; retry: [] }>()

const seatsLeft = computed(() => (props.invites ? Math.max(0, props.invites.max_uses - props.invites.use_count) : 0))

const acceptedCount = computed(() => props.people.filter((p) => p.stage === 'activated').length)

const steps = computed(() => [
  { what: t('account.invites.steps.signUp'), gain: t('account.invites.steps.signUpGain') },
  { what: t('account.invites.steps.project'), gain: t('account.invites.steps.projectGain') },
  { what: t('account.invites.steps.accept'), gain: t('account.invites.steps.acceptGain') },
])

const dateOf = (at: string) => absDate(Date.parse(at))

/** Accepted says what it actually earned: past the monthly cap it earned no credits. */
function stageText(person: InviteeRow): string {
  if (person.stage !== 'activated') return t(`account.invites.people.stage.${person.stage}`)
  return person.credits > 0
    ? t('account.invites.people.stage.activated', { n: person.credits })
    : t('account.invites.people.stage.activatedNoCredits')
}
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.invites__pending {
  min-height: 160px;
}

.invites__share {
  display: grid;
  gap: 16px;
  padding: 20px 24px;
}

.invites__code-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  align-items: flex-end;
  justify-content: space-between;
}

.invites__code {
  display: grid;
  gap: 4px;
}

.invites__code-value {
  font-family: var(--font-mono);
  font-size: 23px;
  font-weight: 600;
  line-height: var(--lh-23);
  letter-spacing: 0.04em;
  color: var(--ink);
}

.invites__seats {
  display: flex;
  gap: 8px;
  align-items: center;
}

.invites__pips {
  display: inline-flex;
  gap: 4px;
}

/* A seat still free is an outline; a used one is filled. Neutral, not amber: seats are a count, not an action. */
.invites__pip {
  width: 8px;
  height: 8px;
  border: 1px solid var(--muted);
  border-radius: var(--radius-pill);
}

.invites__pip--used {
  background: var(--muted);
}

.invites__seats-text {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invites__hint {
  margin: -4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invites__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.invites__steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  padding: 4px 24px 0;
  margin: 0;
  list-style: none;
}

.invites__step {
  display: grid;
  grid-template-rows: auto auto 1fr;
  gap: 4px;
  padding: 12px;
  background: var(--fill);
  border-radius: var(--radius-md);
}

.invites__step-n {
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  color: var(--muted);
}

.invites__step-what {
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
}

.invites__step-gain {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.invites__note {
  padding: 12px 24px 20px;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invites__note--foot {
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.invites__link {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  margin-top: 20px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.invites__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  padding: 4px 24px 16px;
  margin: 0;
}

.invites__stat {
  display: grid;
  gap: 2px;
}

.invites__stat dt {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invites__stat dd {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  color: var(--ink);
}

.invites__empty {
  border-top: 1px solid var(--line);
}

.invites__people {
  padding: 0;
  margin: 0;
  list-style: none;
}

.invitee {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 12px 24px;
  border-top: 1px solid var(--line);
}

.invitee__who {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.invitee__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.invitee__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invitee__stage {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  white-space: nowrap;
}

@media (max-width: 767.98px) {
  .invites__share {
    padding: 16px;
  }

  .invites__code-row {
    align-items: flex-start;
  }

  /* Three steps side by side leave a few characters a line on a phone: they stack. */
  .invites__steps {
    grid-template-columns: minmax(0, 1fr);
    padding: 4px 16px 0;
  }

  .invites__step {
    grid-template-rows: none;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 4px 12px;
  }

  .invites__step-gain {
    grid-column: 2;
  }

  .invites__note {
    padding: 12px 16px 16px;
  }

  .invites__link {
    margin-top: 16px;
  }

  .invites__stats {
    padding: 4px 16px 12px;
  }

  .invitee {
    grid-template-columns: auto minmax(0, 1fr);
    padding: 12px 16px;
  }

  /* The stage drops under the name instead of squeezing it to a few characters. */
  .invitee__stage {
    grid-column: 2;
  }
}
</style>
