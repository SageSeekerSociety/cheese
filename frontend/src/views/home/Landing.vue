<script setup lang="ts">
import { computed, ref } from 'vue'

import LandingSpark from '@/assets/landing-spark.svg?component'
import logo from '@/assets/logo-plain.svg?url'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import HomepageMessage from '@/components/home/HomepageMessage.vue'
import i18n, { t } from '@/i18n'
import AccountService from '@/services/account'

const loggedIn = computed(() => AccountService.loggedIn)
const entryHref = computed(() => (loggedIn.value ? '/' : '/account/signin'))
const entryLabel = computed(() => (loggedIn.value ? t('publicSite.openWorkspace') : t('publicSite.getStarted')))
const stages = computed(() => [
  { id: 'discuss', label: t('publicSite.discuss') },
  { id: 'build', label: t('publicSite.build') },
  { id: 'verify', label: t('publicSite.test') },
  { id: 'review', label: t('publicSite.deliver') },
])
const stage = ref('discuss')
const expanded = ref(false)
const sampleAnswer = ref(false)
const audiences = computed(() => [
  {
    id: 'enterprise',
    label: t('publicSite.forBusinesses'),
    title: t('publicSite.bringAiIntoYourTeamsProjects'),
    description: t('publicSite.equipYourTeamWithModelsAndCloud'),
    tags: [
      t('publicSite.modelsAndCloudResources'),
      t('publicSite.workTogetherFromStartToFinish'),
      t('publicSite.resourcesOnDemand'),
    ],
    steps: [
      t('publicSite.defineTheBusinessProblem'),
      t('publicSite.workOnItWithYourTeamAnd'),
      t('publicSite.evaluateTheResultsTogether'),
    ],
  },
  {
    id: 'institution',
    label: t('publicSite.universitiesAndInstitutions'),
    title: t('publicSite.learnThroughRealProjects'),
    description: t('publicSite.weAimToSupportFutureLearningCenters'),
    tags: [
      t('publicSite.projectbasedLearning'),
      t('publicSite.aiAssistance'),
      t('publicSite.collaborationAcrossTeams'),
    ],
    steps: [
      t('publicSite.organizeAProject'),
      t('publicSite.bringStudentsEducatorsAndAiTogether'),
      t('publicSite.keepARecordOfTheWorkAnd'),
    ],
  },
  {
    id: 'research',
    label: t('publicSite.researchTeams'),
    title: t('publicSite.keepTheDiscussionGoingAndTheResearch'),
    description: t('publicSite.discussResearchQuestionsReviewSourcesAndDevelop'),
    tags: [t('publicSite.researchDiscussions'), t('publicSite.literatureAndCode'), t('publicSite.reviewResults')],
    steps: [
      t('publicSite.startWithAQuestion'),
      t('publicSite.exploreApproachesWithYourTeamAndAi'),
      t('publicSite.reviewWhatYouHaveBuilt'),
    ],
  },
])
const audienceId = ref('enterprise')
const audience = computed(() => audiences.value.find((item) => item.id === audienceId.value)!)
function selectStage(id: string) {
  stage.value = id
  expanded.value = false
  sampleAnswer.value = false
}
function moveTab(event: KeyboardEvent, group: 'stage' | 'audience') {
  const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const buttons = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const index = buttons.indexOf(event.target as HTMLButtonElement)
  if (index === -1) return
  event.preventDefault()
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  if (group === 'stage') selectStage(stages.value[next].id)
  else audienceId.value = audiences.value[next].id
  buttons[next].focus()
}
</script>

<template>
  <main id="top" class="landing-page" :lang="i18n.global.locale.value">
    <a class="skip-link" href="#experience"> {{ t('publicSite.skipToProductDemo') }} </a>
    <header class="site-header wrap">
      <a class="brand" href="#top" :aria-label="t('publicSite.cheeseHome')">
        <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span>
          cheese<span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('global.cheese') }}</span>
        </span>
      </a>
      <nav :aria-label="t('publicSite.mainNavigation')">
        <a href="#experience">{{ t('publicSite.productDemo') }}</a>
        <a href="#content">{{ t('publicSite.createTogether') }}</a>
        <a href="#teams">{{ t('publicSite.forBusinesses') }}</a>
        <a href="#vision">{{ t('publicSite.ourVision') }}</a>
      </nav>
      <div class="header-actions">
        <LanguageToggle />
        <a class="nav-entry" :href="entryHref">
          {{ entryLabel }} <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="17" />
        </a>
      </div>
    </header>
    <section class="hero wrap">
      <div class="hero-copy">
        <p class="eyebrow"><span class="amber-line" /> {{ t('publicSite.workTogetherFromIdeaToDelivery') }}</p>
        <h1>
          {{ t('publicSite.takeYourIdea') }} <br />
          {{ t('publicSite.makeIt') }}
          <span class="highlight-word">
            {{ t('publicSite.happen')
            }}<LandingSpark class="landing-spark highlight-spark" aria-hidden="true" focusable="false" />
          </span>
          {{ t('publicSite.sentenceEnd') }}
        </h1>
        <p class="hero-description">
          {{ t('publicSite.discussCreateTestAndDeliverWithYour') }} <br class="desktop-break" />
          {{ t('publicSite.yourModelsCloudEnvironmentsAndCollaborationIn') }} <br class="desktop-break" />
          {{ t('publicSite.soYouCanFocusOnTheProject') }}
        </p>
        <div class="hero-actions">
          <a class="button button-dark" :href="entryHref">
            {{ entryLabel }}
            <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="18" />
          </a>
          <a class="watch-link" href="#teams">
            {{ t('publicSite.forBusinessesAndUniversities') }}
            <v-icon class="landing-icon" icon="mdi-arrow-right" size="16" />
          </a>
        </div>
        <p class="free-entry">{{ t('publicSite.startCollaboratingWithASelectionOfFree') }}</p>
        <div class="hero-audience">
          {{ t('publicSite.universitiesAndInstitutions') }} <span>·</span> {{ t('publicSite.businessPartnerships') }}
          <span>·</span> {{ t('publicSite.researchAndContentTeams') }}
        </div>
      </div>
      <figure class="collaboration-map" :aria-label="t('publicSite.peopleAiTeammatesKnowledgeAndResultsConnected')">
        <div class="map-grid" />
        <span class="map-coordinate">A SHARED SPACE FOR GREAT IDEAS</span>
        <svg viewBox="0 0 560 450" class="map-lines" aria-hidden="true">
          <circle class="orbit" cx="280" cy="224" r="155" />
          <circle class="orbit orbit-inner" cx="280" cy="224" r="105" />
          <path
            d="M100 115 C190 115 170 224 280 224 S420 320 480 320 M420 80 C350 80 380 224 280 224 S150 360 110 360"
          />
          <path class="signal-line" d="M100 115 C190 115 170 224 280 224 S420 320 480 320" />
          <path class="signal-line signal-second" d="M420 80 C350 80 380 224 280 224 S150 360 110 360" />
        </svg>
        <div class="map-core">
          <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
          <strong>{{ t('publicSite.oneProject') }}</strong>
          <span>{{ t('publicSite.sharedContext') }}</span>
        </div>
        <div class="map-node node-team">
          <v-icon class="landing-icon" icon="mdi-account-group-outline" size="20" />
          <div>
            <strong>{{ t('publicSite.yourTeam') }}</strong>
            <span>{{ t('publicSite.askQuestionsMakeDecisions') }}</span>
          </div>
        </div>
        <div class="map-node node-ai">
          <v-icon class="landing-icon" icon="mdi-creation" size="21" />
          <div>
            <strong>{{ t('publicSite.aiTeammate') }}</strong>
            <span>{{ t('publicSite.discussIdeasMoveWorkForward') }}</span>
          </div>
          <span class="live-dot" />
        </div>
        <div class="map-node node-knowledge">
          <v-icon class="landing-icon" icon="mdi-file-document-outline" size="20" />
          <div>
            <strong>{{ t('publicSite.projectKnowledge') }}</strong>
            <span>{{ t('publicSite.keepDiscussionsAndDecisions') }}</span>
          </div>
        </div>
        <div class="map-node node-output">
          <v-icon class="landing-icon" icon="mdi-code-tags" size="20" />
          <div>
            <strong>{{ t('publicSite.sharedResults') }}</strong>
            <span>{{ t('publicSite.traceIdeasThroughToResults') }}</span>
          </div>
        </div>
        <div class="map-foot"><span>+</span> {{ t('publicSite.humanJudgmentAiExecution') }} <span>+</span></div>
      </figure>
    </section>
    <section id="experience" class="demo-section wrap">
      <div class="section-intro">
        <div>
          <p class="eyebrow">{{ t('publicSite.workingTogether') }}</p>
          <h2>
            {{ t('publicSite.workTogether') }} <br />
            {{ t('publicSite.deliverSomethingReal') }}
          </h2>
          <p class="demo-tagline">{{ t('publicSite.yourTeamWithAnAiTeammateThat') }}</p>
        </div>
        <p>
          {{ t('publicSite.connectPlanningTasksTestingAndResultsIn') }} <br />
          <span class="muted">{{ t('publicSite.interactiveDemoSampleContent') }}</span>
        </p>
      </div>
      <div class="demo-tabs">
        <div
          class="stage-list"
          role="tablist"
          :aria-label="t('publicSite.projectStages')"
          @keydown="moveTab($event, 'stage')"
        >
          <button
            v-for="(item, index) in stages"
            :id="`stage-${item.id}`"
            :key="item.id"
            type="button"
            role="tab"
            :aria-selected="stage === item.id"
            :tabindex="stage === item.id ? 0 : -1"
            :aria-controls="`panel-${item.id}`"
            :data-active="stage === item.id || undefined"
            @click="selectStage(item.id)"
          >
            <span>0{{ index + 1 }}</span
            >{{ item.label }}
          </button>
        </div>
        <div class="workspace">
          <aside class="workspace-sidebar">
            <div class="workspace-name">
              <v-icon class="landing-icon" icon="mdi-layers-outline" size="19" /> {{ t('publicSite.knowledgeSearch') }}
            </div>
            <div class="sidebar-link">
              <v-icon class="landing-icon" icon="mdi-compass-outline" size="16" /> {{ t('publicSite.overview') }}
            </div>
            <div class="sidebar-link active">
              <v-icon class="landing-icon" icon="mdi-message-outline" size="16" />
              {{ t('publicSite.planningAndPrototype') }}
            </div>
            <div class="sidebar-link">
              <v-icon class="landing-icon" icon="mdi-file-document-outline" size="16" />
              {{ t('publicSite.projectNotes') }}
            </div>
            <div class="sidebar-caption">{{ t('publicSite.members') }}</div>
            <div class="member">
              <span class="avatar">{{ t('publicSite.b') }}</span> {{ t('publicSite.businessPartner') }}
            </div>
            <div class="member">
              <span class="avatar">{{ t('publicSite.r') }}</span> {{ t('publicSite.researcher') }}
            </div>
            <div class="member">
              <span class="avatar ai-avatar">
                <v-icon class="landing-icon" icon="mdi-creation" size="16" />
              </span>
              {{ t('global.cheese') }} <small>{{ t('publicSite.aiTeammate') }}</small>
            </div>
            <div class="sidebar-bottom">
              <v-icon class="landing-icon" icon="mdi-source-branch" size="15" />
              {{ t('publicSite.keepTheDiscussionWithTheWork') }}
            </div>
          </aside>
          <div class="workspace-main">
            <div class="workspace-bar">
              <span>
                <span class="hash">#</span> {{ t('publicSite.planningAndPrototype') }}
                <v-icon class="landing-icon" icon="mdi-chevron-right" size="14" />
              </span>
              <span class="member-dots">
                <span>{{ t('publicSite.b') }}</span>
                <span>{{ t('publicSite.r') }}</span>
                <span><LandingSpark class="landing-spark" aria-hidden="true" focusable="false" /></span>
              </span>
            </div>
            <div
              v-if="stage === 'discuss'"
              id="panel-discuss"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-discuss"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('publicSite.businessPartner')">
                  <p>{{ t('publicSite.canAKnowledgeBaseHelpNewTeam') }}</p>
                </HomepageMessage>
                <HomepageMessage :who="t('publicSite.researcher')">
                  <p>{{ t('publicSite.letsStartWithASmallPrototypeAnswers') }}</p>
                </HomepageMessage>
                <HomepageMessage :who="t('global.cheese')" ai>
                  <p>{{ t('publicSite.illOutlineTheEvaluationMethodThenBuild') }}</p>
                  <div class="inline-note">
                    <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" />
                    {{ t('publicSite.projectNotesFollowTheWork') }}
                  </div>
                </HomepageMessage>
                <div class="demo-composer">
                  <span>{{ t('publicSite.continueTheConversationInYourProject') }}</span>
                  <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="18" />
                </div>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" />
                  {{ t('publicSite.projectNotes') }}
                </div>
                <h3>{{ t('publicSite.knowledgeSearchPrototype') }}</h3>
                <p class="doc-label">{{ t('publicSite.currentGoal') }}</p>
                <p>{{ t('publicSite.testASearchPrototypeThatCitesIts') }}</p>
                <p class="doc-label">{{ t('publicSite.agreedSoFar') }}</p>
                <p>
                  <v-icon class="landing-icon" icon="mdi-check" size="14" />
                  {{ t('publicSite.testTheApproachBeforeExpandingIt') }}
                </p>
                <p class="doc-label">{{ t('publicSite.nextStep') }}</p>
                <p>{{ t('publicSite.agreeOnEvaluationExamples') }}</p>
                <div class="doc-bottom">{{ t('publicSite.keepTrackOfWhatTheTeamAgrees') }}</div>
              </aside>
            </div>
            <div
              v-if="stage === 'build'"
              id="panel-build"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-build"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('global.cheese')" ai>
                  <p>{{ t('publicSite.theSearchPrototypeAndEvaluationPlanAre') }}</p>
                </HomepageMessage>
                <div class="task-rows">
                  <div>
                    <v-icon class="landing-icon" icon="mdi-code-tags" size="19" />
                    <span>{{ t('publicSite.searchPrototype') }}</span>
                    <small>{{ t('publicSite.inProgress') }}</small>
                  </div>
                  <div>
                    <v-icon class="landing-icon" icon="mdi-message-outline" size="19" />
                    <span>{{ t('publicSite.evaluationPlan') }}</span>
                    <small>{{ t('publicSite.toDiscuss') }}</small>
                  </div>
                  <div>
                    <v-icon class="landing-icon" icon="mdi-file-document-outline" size="19" />
                    <span>{{ t('publicSite.sourceReview') }}</span>
                    <small>
                      <v-icon class="landing-icon" icon="mdi-check" size="13" /> {{ t('publicSite.complete') }}
                    </small>
                  </div>
                </div>
                <p class="demo-note">{{ t('publicSite.theseAreSampleTasksTheyDoNot') }}</p>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-source-branch" size="15" /> {{ t('publicSite.taskProgress') }}
                </div>
                <h3>{{ t('publicSite.separateTasksSharedProgress') }}</h3>
                <p>{{ t('publicSite.discussThePrototypeAndEvaluationSeparatelyThen') }}</p>
                <p class="doc-label">{{ t('publicSite.nextForTheTeam') }}</p>
                <p>{{ t('publicSite.agreeOnWhatThePrototypeShouldTest') }}</p>
              </aside>
            </div>
            <div
              v-if="stage === 'verify'"
              id="panel-verify"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-verify"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('global.cheese')" ai
                  ><p>{{ t('publicSite.thePrototypeIsReadyReviewTheResults') }}</p></HomepageMessage
                >
                <div class="prototype-preview">
                  <span class="eyebrow">{{ t('publicSite.searchPrototypeInteractiveExample') }}</span>
                  <h3>{{ t('publicSite.whereShouldANewTeamMemberStart') }}</h3>
                  <button
                    type="button"
                    class="button button-dark"
                    :aria-expanded="sampleAnswer"
                    @click="sampleAnswer = !sampleAnswer"
                  >
                    {{ sampleAnswer ? t('publicSite.hideSampleAnswer') : t('publicSite.showSampleAnswer')
                    }}<v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
                  </button>
                  <p v-if="sampleAnswer">
                    {{ t('publicSite.readTheProjectOverviewThenCheckThe')
                    }}<span class="sample-citation">{{
                      t('publicSite.sampleSourceProjectGettingStartedGuideSection')
                    }}</span>
                  </p>
                </div>
                <p class="demo-note">{{ t('publicSite.thisSamplePrototypeDoesNotAccessReal') }}</p>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">{{ t('publicSite.runAndPreviewInTheCloud') }}</div>
                <h3>{{ t('publicSite.reviewTheSameResultTogether') }}</h3>
                <p>{{ t('publicSite.theTeamReviewsThePrototypeAiUpdates') }}</p>
                <p class="doc-label">{{ t('publicSite.nextStep') }}</p>
                <p>{{ t('publicSite.confirmTheResultsAndSubmitThemFor') }}</p>
              </aside>
            </div>
            <div
              v-if="stage === 'review'"
              id="panel-review"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-review"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('global.cheese')" ai>
                  <p>{{ t('publicSite.thePrototypeIsReadyForReviewThe') }}</p>
                </HomepageMessage>
                <div class="review-card">
                  <span class="eyebrow">{{ t('publicSite.readyForTeamReview') }}</span>
                  <h3>{{ t('publicSite.searchPrototypeAndEvaluationNotes') }}</h3>
                  <p>{{ t('publicSite.inCollaborationModePeopleReviewAndAccept') }}</p>
                  <button class="button button-dark" :aria-expanded="expanded" @click="expanded = !expanded">
                    {{ expanded ? t('publicSite.hideSampleResult') : t('publicSite.showSampleResult') }}
                    <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
                  </button>

                  <p v-if="expanded" class="expanded-result">
                    {{ t('publicSite.thePrototypeShowsSourceCitationsTheEvaluation') }}
                  </p>
                </div>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" />
                  {{ t('publicSite.deliveryNotes') }}
                </div>
                <h3>{{ t('publicSite.seeTheReasoningBehindTheWork') }}</h3>
                <p>{{ t('publicSite.seeTheResultAlongsideTheDiscussionsAnd') }}</p>
                <p class="doc-label">{{ t('publicSite.nextStep') }}</p>
                <p>{{ t('publicSite.reviewTogetherThenPlanTheNextRound') }}</p>
              </aside>
            </div>
          </div>
        </div>
      </div>
      <div class="capabilities">
        <article>
          <v-icon class="landing-icon" icon="mdi-account-group-outline" />
          <h3>{{ t('publicSite.keepContextInOnePlace') }}</h3>
          <p>{{ t('publicSite.keepDiscussionsDocumentsAndResultsTogetherNew') }}</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-creation" />
          <h3>{{ t('publicSite.putTheDiscussionIntoAction') }}</h3>
          <p>{{ t('publicSite.reviewSourcesWriteCodeAndReportsAnd') }}</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-source-branch" />
          <h3>{{ t('publicSite.reviewTheWorkBeforeDeciding') }}</h3>
          <p>{{ t('publicSite.inCollaborationModeAiSubmitsWorkFor') }}</p>
        </article>
      </div>
    </section>
    <section id="resources" class="resources-section wrap">
      <div>
        <p class="eyebrow">{{ t('publicSite.modelsAndCloudEnvironments') }}</p>
        <h2>{{ t('publicSite.getTheResourcesReady') }}<br />{{ t('publicSite.soYourTeamCanStartWorkingTogether') }}</h2>
        <p>{{ t('publicSite.aiTeammatesCanUseThePlatformsModels') }}</p>
      </div>
      <div class="resource-options">
        <article>
          <v-icon class="landing-icon" icon="mdi-creation" size="24" />
          <h3>{{ t('publicSite.modelAccess') }}</h3>
          <p>{{ t('publicSite.useTheModelsAvailableToYourTeam') }}</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-server-outline" size="24" />
          <h3>{{ t('publicSite.cloudEnvironments') }}</h3>
          <p>{{ t('publicSite.runCodeAndPrototypesInAShared') }}</p>
        </article>
        <p class="resource-terms">{{ t('publicSite.aSelectionOfResourcesIsFreeTo') }}</p>
      </div>
    </section>
    <section id="teams" class="teams-section">
      <div class="wrap">
        <div class="section-intro">
          <div>
            <p class="eyebrow">{{ t('publicSite.forBusinessesAndUniversities2') }}</p>
            <h2>
              {{ t('publicSite.startWithAProject') }} <br />
              {{ t('publicSite.supportTheWholeTeam') }}
            </h2>
          </div>
          <p>
            {{ t('publicSite.chooseTheServicesAndResourcesYouNeed') }} <br />
            {{ t('publicSite.forResearchContentCreationAndHandsonProjects') }}
          </p>
        </div>
        <div class="audience-tabs">
          <div
            class="audience-list"
            role="tablist"
            :aria-label="t('publicSite.whoItIsFor')"
            @keydown="moveTab($event, 'audience')"
          >
            <button
              v-for="item in audiences"
              :id="`audience-${item.id}`"
              :key="item.id"
              type="button"
              role="tab"
              :aria-selected="audience.id === item.id"
              :tabindex="audience.id === item.id ? 0 : -1"
              aria-controls="audience-panel"
              :data-active="audience.id === item.id || undefined"
              @click="audienceId = item.id"
            >
              {{ item.label }}<v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
            </button>
          </div>
          <div id="audience-panel" role="tabpanel" :aria-labelledby="`audience-${audience.id}`" tabindex="0">
            <div class="audience-content">
              <div>
                <h3>{{ audience.title }}</h3>
                <p>{{ audience.description }}</p>
                <div class="audience-tags">
                  <span v-for="tag in audience.tags" :key="tag">{{ tag }}</span>
                </div>
              </div>
              <div class="collaboration-flow">
                <div v-for="(step, index) in audience.steps" :key="step">
                  <span>{{
                    [t('publicSite.start'), t('publicSite.collaborate'), t('publicSite.deliver2')][index]
                  }}</span
                  ><strong>{{ step }}</strong
                  ><v-icon v-if="index < 2" class="landing-icon" icon="mdi-arrow-right" />
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="organization-direction">
          <span class="eyebrow">{{ t('publicSite.anApproachForInstitutions') }}</span>
          <p>{{ t('publicSite.aPlatformAiTeammatesProjectContentAnd') }}</p>
          <span> {{ t('publicSite.weAimToConnectProjectWorkContent') }} </span>
        </div>
      </div>
    </section>

    <section id="content" class="content-section wrap">
      <div class="content-layout">
        <div>
          <p class="eyebrow">{{ t('publicSite.createContentTogether') }}</p>
          <h2>
            {{ t('publicSite.turnSharedThinking') }} <br />
            {{ t('publicSite.intoWorkWorthSharing') }}
          </h2>
          <p>{{ t('publicSite.workWithYourTeamAndAiTo') }}</p>
          <p>
            {{ t('publicSite.turnProjectExperienceIntoLearningMaterials') }} <br />
            {{ t('publicSite.thenRefineThemThroughTeamDiscussionAnd') }}
          </p>
        </div>
        <div class="content-artifacts" :aria-label="t('publicSite.examplesOfContentCreatedTogether')">
          <div class="content-sheet">
            <small>{{ t('publicSite.contentCollaborationExamples') }}</small>
            <h3>{{ t('publicSite.oneProjectManyWaysToShare') }}</h3>
            <div>
              <v-icon class="landing-icon" icon="mdi-file-document-outline" /> {{ t('publicSite.researchReports') }}
              <span>{{ t('publicSite.explainQuestionsAndFindings') }}</span>
            </div>
            <div>
              <v-icon class="landing-icon" icon="mdi-layers-outline" /> {{ t('publicSite.caseStudies') }}
              <span>{{ t('publicSite.showTheProcessAndMethods') }}</span>
            </div>
            <div>
              <v-icon class="landing-icon" icon="mdi-compass-outline" /> {{ t('publicSite.learningMaterials') }}
              <span>{{ t('publicSite.shareWhatOthersCanUse') }}</span>
            </div>
          </div>
          <div class="content-caption">
            <v-icon class="landing-icon" icon="mdi-creation" size="15" />
            {{ t('publicSite.createdWithAiReviewedByTheTeam') }}
          </div>
        </div>
      </div>
    </section>
    <section id="vision" class="vision-section wrap">
      <p class="eyebrow">{{ t('publicSite.ourVisionFutureLearningCenters') }}</p>
      <div class="vision-layout">
        <h2>
          {{ t('publicSite.eachCollaboration') }} <br />
          {{ t('publicSite.becomesAStartingPoint') }} <br />
          <span>{{ t('publicSite.forTheNextDiscovery') }}</span>
        </h2>
        <div>
          <LandingSpark class="landing-spark vision-symbol" aria-hidden="true" focusable="false" />
          <p>{{ t('publicSite.withAiAsAnOngoingTeammateTeams') }}</p>
          <p>{{ t('publicSite.cheeseAimsToTurnThatThinkingInto') }}</p>
          <a
            class="policy-link"
            href="https://hudong.moe.gov.cn/srcsite/A16/s3342/202604/t20260410_1433240.html"
            target="_blank"
            rel="noreferrer"
          >
            {{ t('publicSite.readChinasPolicyOnFutureLearningCenters') }}
            <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="12" />
          </a>
          <span class="vision-english">
            THINK TOGETHER.
            <br />
            BUILD TOGETHER.
            <br />
            GROW TOGETHER.
          </span>
        </div>
      </div>
    </section>
    <section class="closing">
      <div class="wrap">
        <p>
          {{ t('publicSite.bringYourQuestion') }} <br />
          {{ t('publicSite.startWithYourTeam') }}
        </p>
        <a class="button button-light" :href="entryHref">
          {{ entryLabel }}
          <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="19" />
        </a>
      </div>
    </section>
    <footer class="wrap">
      <a class="brand" href="#top" :aria-label="t('publicSite.cheeseHome')">
        <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span>
          cheese<span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('global.cheese') }}</span>
        </span>
      </a>
      <p>{{ t('publicSite.peopleAndAiGettingWorkDoneTogether') }}</p>
      <span> {{ t('publicSite.2026Cheese') }} </span>
    </footer>
  </main>
</template>
<style scoped src="./landing.css"></style>
<style>
/* The workspace locks document scrolling; the public homepage scrolls as a document. */
html:has(.landing-page) {
  overflow-y: auto !important;
}
body:has(.landing-page),
#app:has(.landing-page) {
  height: auto;
  overflow: visible;
}
</style>
