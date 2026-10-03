<script setup lang="ts">
import { computed, ref } from 'vue'

import LandingShell from './LandingShell.vue'
import LandingTopic from './LandingTopic.vue'

import BrandScene from '@/components/account/brandScene/BrandScene.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// The page for the people who bring Cheese into an organisation — schools,
// companies and research teams. The homepage speaks to the people who build the
// projects; this one speaks to whoever decides to adopt it, so it ends on a
// conversation instead of a sign-up.

const contactHref = 'mailto:ops@okcheese.com'

const roles = computed(() => [
  {
    icon: 'mdi-school-outline',
    name: t('publicSite.students'),
    title: t('publicSite.studentsTitle'),
    body: t('publicSite.studentsBody'),
    steps: [t('publicSite.studentsStep1'), t('publicSite.studentsStep2'), t('publicSite.studentsStep3')],
  },
  {
    icon: 'mdi-human-male-board',
    name: t('publicSite.teachers'),
    title: t('publicSite.teachersTitle'),
    body: t('publicSite.teachersBody'),
    steps: [t('publicSite.teachersStep1'), t('publicSite.teachersStep2'), t('publicSite.teachersStep3')],
  },
  {
    icon: 'mdi-domain',
    name: t('publicSite.companies'),
    title: t('publicSite.companiesTitle'),
    body: t('publicSite.companiesBody'),
    steps: [t('publicSite.companiesStep1'), t('publicSite.companiesStep2'), t('publicSite.companiesStep3')],
  },
])

// Who the platform is sold to. Keys are listed in full so the catalog check can
// see every one of them.
const solutions = computed(() => [
  {
    id: 'university',
    label: t('publicSite.solutions.university.label'),
    title: t('publicSite.solutions.university.title'),
    body: t('publicSite.solutions.university.body'),
    includes: [
      t('publicSite.solutions.university.include1'),
      t('publicSite.solutions.university.include2'),
      t('publicSite.solutions.university.include3'),
      t('publicSite.solutions.university.include4'),
    ],
    steps: [
      t('publicSite.solutions.university.step1'),
      t('publicSite.solutions.university.step2'),
      t('publicSite.solutions.university.step3'),
    ],
  },
  {
    id: 'company',
    label: t('publicSite.solutions.company.label'),
    title: t('publicSite.solutions.company.title'),
    body: t('publicSite.solutions.company.body'),
    includes: [
      t('publicSite.solutions.company.include1'),
      t('publicSite.solutions.company.include2'),
      t('publicSite.solutions.company.include3'),
      t('publicSite.solutions.company.include4'),
    ],
    steps: [
      t('publicSite.solutions.company.step1'),
      t('publicSite.solutions.company.step2'),
      t('publicSite.solutions.company.step3'),
    ],
  },
  {
    id: 'research',
    label: t('publicSite.solutions.research.label'),
    title: t('publicSite.solutions.research.title'),
    body: t('publicSite.solutions.research.body'),
    includes: [
      t('publicSite.solutions.research.include1'),
      t('publicSite.solutions.research.include2'),
      t('publicSite.solutions.research.include3'),
      t('publicSite.solutions.research.include4'),
    ],
    steps: [
      t('publicSite.solutions.research.step1'),
      t('publicSite.solutions.research.step2'),
      t('publicSite.solutions.research.step3'),
    ],
  },
])
const solutionId = ref('university')
const solution = computed(() => solutions.value.find((item) => item.id === solutionId.value)!)

function moveTab(event: KeyboardEvent) {
  const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const tabs = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const index = tabs.indexOf(event.target as HTMLButtonElement)
  if (index === -1) return
  event.preventDefault()
  const last = tabs.length - 1
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? last
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
  solutionId.value = solutions.value[next].id
  tabs[next].focus()
}

const growth = computed(() => [
  {
    icon: 'mdi-history',
    title: t('publicSite.solutionsPage.traceTitle'),
    body: t('publicSite.solutionsPage.traceBody'),
  },
  {
    icon: 'mdi-account-check-outline',
    title: t('publicSite.solutionsPage.reviewTitle'),
    body: t('publicSite.solutionsPage.reviewBody'),
  },
  {
    icon: 'mdi-book-open-page-variant-outline',
    title: t('publicSite.solutionsPage.unitsTitle'),
    body: t('publicSite.solutionsPage.unitsBody'),
  },
])

const trust = computed(() => [
  {
    icon: 'mdi-map-marker-outline',
    title: t('publicSite.solutionsPage.residencyTitle'),
    body: t('publicSite.solutionsPage.residencyBody'),
  },
  {
    icon: 'mdi-server-outline',
    title: t('publicSite.solutionsPage.deployTitle'),
    body: t('publicSite.solutionsPage.deployBody'),
  },
  {
    icon: 'mdi-account-group-outline',
    title: t('publicSite.solutionsPage.teamsTitle'),
    body: t('publicSite.solutionsPage.teamsBody'),
  },
])
</script>

<template>
  <LandingShell v-slot="{ entryHref }" page="solutions">
    <section class="hero">
      <div class="hero-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="hero-copy">
        <h1 class="hero-title">{{ t('publicSite.solutionsPage.heroTitle') }}</h1>
        <p class="hero-position">{{ t('publicSite.solutionsPage.heroBody') }}</p>
        <div class="hero-actions">
          <BaseButton :href="contactHref" kind="primary" size="lg" append-icon="mdi-email-outline">
            {{ t('publicSite.solutionsPage.contact') }}
          </BaseButton>
          <router-link class="text-link" :to="entryHref">
            {{ t('publicSite.solutionsPage.tryProduct') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </router-link>
        </div>
      </div>
    </section>

    <section id="people" class="people">
      <div class="people-head">
        <div class="people-intro">
          <h2 class="people-title">{{ t('publicSite.peopleTitle') }}</h2>
          <p class="people-lead">{{ t('publicSite.peopleLead') }}</p>
        </div>
        <LandingTopic class="people-topic" />
      </div>
      <div class="people-roles">
        <article v-for="role in roles" :key="role.name" class="role">
          <p class="role-name">
            <v-icon :icon="role.icon" size="20" />
            {{ role.name }}
          </p>
          <h3 class="role-title">{{ role.title }}</h3>
          <p class="role-body">{{ role.body }}</p>
          <ol class="role-steps">
            <li v-for="s in role.steps" :key="s">{{ s }}</li>
          </ol>
        </article>
      </div>
      <p class="people-outputs">{{ t('publicSite.outputs') }}</p>
    </section>

    <section id="solutions" class="solutions">
      <div class="solutions-head">
        <h2 class="solutions-title">{{ t('publicSite.solutions.title') }}</h2>
        <p class="solutions-lead">{{ t('publicSite.solutions.lead') }}</p>
      </div>
      <div class="solutions-tabs" role="tablist" :aria-label="t('publicSite.solutions.tabsLabel')" @keydown="moveTab">
        <button
          v-for="item in solutions"
          :id="`solution-${item.id}`"
          :key="item.id"
          type="button"
          role="tab"
          class="solutions-tab"
          :aria-selected="solutionId === item.id"
          :tabindex="solutionId === item.id ? 0 : -1"
          aria-controls="solution-panel"
          @click="solutionId = item.id"
        >
          {{ item.label }}
        </button>
      </div>
      <Transition name="solution-swap" mode="out-in">
        <div
          id="solution-panel"
          :key="solution.id"
          class="solution"
          role="tabpanel"
          :aria-labelledby="`solution-${solution.id}`"
          tabindex="0"
        >
          <div class="solution-main">
            <h3 class="solution-title">{{ solution.title }}</h3>
            <p class="solution-body">{{ solution.body }}</p>
          </div>
          <div class="solution-side">
            <div class="solution-block">
              <p class="solution-label">{{ t('publicSite.solutions.includes') }}</p>
              <ul class="solution-includes">
                <li v-for="item in solution.includes" :key="item">
                  <v-icon icon="mdi-check" size="16" />
                  {{ item }}
                </li>
              </ul>
            </div>
            <div class="solution-block">
              <p class="solution-label">{{ t('publicSite.solutions.flow') }}</p>
              <ol class="solution-flow">
                <li v-for="item in solution.steps" :key="item">{{ item }}</li>
              </ol>
            </div>
          </div>
        </div>
      </Transition>
    </section>

    <section class="solutions">
      <div class="solutions-head">
        <h2 class="solutions-title">{{ t('publicSite.solutionsPage.growthTitle') }}</h2>
        <p class="solutions-lead">{{ t('publicSite.solutionsPage.growthLead') }}</p>
      </div>
      <div class="resources">
        <div v-for="item in growth" :key="item.title" class="resources-item">
          <v-icon class="resources-icon" :icon="item.icon" size="24" />
          <h3>{{ item.title }}</h3>
          <p>{{ item.body }}</p>
        </div>
      </div>
    </section>

    <section class="solutions">
      <div class="solutions-head">
        <h2 class="solutions-title">{{ t('publicSite.solutionsPage.trustTitle') }}</h2>
        <p class="solutions-lead">{{ t('publicSite.solutionsPage.trustLead') }}</p>
      </div>
      <div class="resources">
        <div v-for="item in trust" :key="item.title" class="resources-item">
          <v-icon class="resources-icon" :icon="item.icon" size="24" />
          <h3>{{ item.title }}</h3>
          <p>{{ item.body }}</p>
        </div>
      </div>
    </section>

    <section id="vision" class="vision">
      <p class="vision-label">{{ t('publicSite.visionLabel') }}</p>
      <blockquote class="vision-quote">
        <p>{{ t('publicSite.quoteLine1') }}<br />{{ t('publicSite.quoteLine2') }}</p>
        <cite>{{ t('publicSite.quoteSource') }}</cite>
      </blockquote>
      <div class="vision-words">
        <p class="vision-lead">{{ t('publicSite.visionLead') }}</p>
        <p>{{ t('publicSite.visionBody') }}</p>
        <p class="vision-policy">{{ t('publicSite.visionPolicy') }}</p>
        <a
          class="text-link"
          href="https://hudong.moe.gov.cn/srcsite/A16/s3342/202604/t20260410_1433240.html"
          target="_blank"
          rel="noreferrer"
        >
          {{ t('publicSite.policyLink') }}
          <v-icon icon="mdi-arrow-top-right" size="14" />
        </a>
      </div>
    </section>

    <section class="cta">
      <div class="cta-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="cta-copy">
        <h2 class="cta-title">{{ t('publicSite.solutionsPage.ctaTitle') }}</h2>
        <p class="cta-body">{{ t('publicSite.solutionsPage.ctaBody') }}</p>
        <div class="hero-actions">
          <BaseButton :href="contactHref" kind="primary" size="lg" append-icon="mdi-email-outline">
            {{ t('publicSite.solutionsPage.contact') }}
          </BaseButton>
        </div>
      </div>
    </section>
  </LandingShell>
</template>

<style scoped src="./landing.css"></style>
