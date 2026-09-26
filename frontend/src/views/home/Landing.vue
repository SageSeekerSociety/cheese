<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import LandingRoom from './LandingRoom.vue'
import LandingTopic from './LandingTopic.vue'

import logo from '@/assets/logo-plain.svg?url'
import BrandScene from '@/components/account/brandScene/BrandScene.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import i18n, { t } from '@/i18n'
import AccountService from '@/services/account'

const loggedIn = computed(() => AccountService.loggedIn)
const entryHref = computed(() => (loggedIn.value ? '/' : '/account/signin'))
const entryLabel = computed(() => (loggedIn.value ? t('publicSite.openWorkspace') : t('publicSite.getStarted')))

// The manifesto lights up clause by clause as it scrolls through the viewport.
const manifesto = computed(() => [t('publicSite.manifesto1'), t('publicSite.manifesto2'), t('publicSite.manifesto3')])

const steps = computed(() => [
  { title: t('publicSite.memoryTitle'), body: t('publicSite.memoryBody') },
  { title: t('publicSite.agentsTitle'), body: t('publicSite.agentsBody') },
  { title: t('publicSite.previewTitle'), body: t('publicSite.previewBody') },
  { title: t('publicSite.reviewTitle'), body: t('publicSite.reviewBody') },
])

const resources = computed(() => [
  { icon: 'mdi-creation-outline', title: t('publicSite.modelsTitle'), body: t('publicSite.modelsBody') },
  { icon: 'mdi-cloud-outline', title: t('publicSite.cloudTitle'), body: t('publicSite.cloudBody') },
  { icon: 'mdi-laptop', title: t('publicSite.deviceTitle'), body: t('publicSite.deviceBody') },
])

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

// Which step of the story is in the middle of the screen drives the room.
const step = ref(0)
const stepEls = ref<HTMLElement[]>([])
let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) step.value = Number((entry.target as HTMLElement).dataset.step)
      }
    },
    { rootMargin: '-45% 0px -45% 0px' }
  )
  for (const el of stepEls.value) observer.observe(el)
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <main id="top" class="landing" :lang="i18n.global.locale.value">
    <header class="site-bar">
      <a class="brand" href="#top" :aria-label="t('publicSite.cheeseHome')">
        <span class="brand-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span class="brand-word">cheese</span>
        <span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('global.cheese') }}</span>
      </a>
      <nav class="site-nav" :aria-label="t('publicSite.mainNavigation')">
        <a href="#story">{{ t('publicSite.navProduct') }}</a>
        <a href="#solutions">{{ t('publicSite.navSolutions') }}</a>
        <a href="#vision">{{ t('publicSite.navVision') }}</a>
      </nav>
      <div class="site-actions">
        <LanguageToggle />
        <v-btn :href="entryHref" variant="outlined" append-icon="mdi-arrow-top-right">{{ entryLabel }}</v-btn>
      </div>
    </header>

    <section class="hero">
      <div class="hero-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="hero-copy">
        <h1 class="hero-title">{{ t('publicSite.slogan') }}</h1>
        <p class="hero-position">{{ t('publicSite.positioning') }}</p>
        <div class="hero-actions">
          <v-btn :href="entryHref" color="primary" variant="flat" size="x-large" append-icon="mdi-arrow-top-right">
            {{ entryLabel }}
          </v-btn>
          <a class="text-link" href="#solutions">
            {{ t('publicSite.solutionsLink') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </a>
        </div>
      </div>
    </section>

    <section class="manifesto" :aria-label="t('publicSite.manifestoLabel')">
      <p class="manifesto-text">
        <span v-for="(clause, i) in manifesto" :key="i" class="manifesto-clause">{{ clause }}</span>
      </p>
    </section>

    <section id="story" class="story">
      <div class="story-steps">
        <div
          v-for="(item, i) in steps"
          :key="i"
          ref="stepEls"
          class="story-step"
          :class="{ 'story-step-active': step === i }"
          :data-step="i"
        >
          <p class="story-index">{{ String(i + 1).padStart(2, '0') }}</p>
          <h2 class="story-title">{{ item.title }}</h2>
          <p class="story-body">{{ item.body }}</p>
        </div>
      </div>
      <div class="story-stage">
        <div class="story-room">
          <LandingRoom :step="step" />
        </div>
      </div>
    </section>

    <section class="resources">
      <div v-for="item in resources" :key="item.title" class="resources-item">
        <v-icon class="resources-icon" :icon="item.icon" size="24" />
        <h3>{{ item.title }}</h3>
        <p>{{ item.body }}</p>
      </div>
      <p class="resources-terms">{{ t('publicSite.resourceTerms') }}</p>
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
        <h2 class="cta-title">{{ t('publicSite.ctaTitle') }}</h2>
        <p class="cta-body">{{ t('publicSite.ctaBody') }}</p>
        <div class="hero-actions">
          <v-btn :href="entryHref" color="primary" variant="flat" size="x-large" append-icon="mdi-arrow-top-right">
            {{ entryLabel }}
          </v-btn>
          <a class="text-link" href="#solutions">
            {{ t('publicSite.solutionsLink') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </a>
        </div>
      </div>
    </section>

    <footer class="site-foot">
      <span class="brand">
        <span class="brand-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span class="brand-word">cheese</span>
        <span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('global.cheese') }}</span>
      </span>
      <span>{{ t('publicSite.slogan') }}</span>
      <nav class="site-foot-links" :aria-label="t('publicSite.legal')">
        <router-link to="/legal/terms">{{ t('publicSite.terms') }}</router-link>
        <router-link to="/legal/privacy">{{ t('publicSite.privacy') }}</router-link>
        <span>{{ t('publicSite.copyright') }}</span>
      </nav>
    </footer>
  </main>
</template>

<style scoped src="./landing.css"></style>
<style>
/* The workspace locks document scrolling; the public homepage scrolls as a document. */
html:has(.landing) {
  overflow-y: auto !important;
}

body:has(.landing),
#app:has(.landing) {
  height: auto;
  overflow: visible;
}
</style>
