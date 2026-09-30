import account from './account.json'
import aiChat from './aiChat.json'
import comments from './comments.json'
import editor from './editor.json'
import featureStats from './featureStats.json'
import feedback from './feedback.json'
import global from './global.json'
import home from './home.json'
import integrations from './integrations.json'
import members from './members.json'
import models from './models.json'
import navigation from './navigation.json'
import notifications from './notifications.json'
import publicSite from './publicSite.json'
import questions from './questions.json'
import ratchet from './ratchet.json'
import roomNotice from './roomNotice.json'
import spaces from './spaces.json'
import tasks from './tasks.json'
import toolLabels from './toolLabels.json'
import users from './users.json'
import work from './work.json'

// Namespaces absent here have no English translation yet. They are listed, key by
// key, in `frontend/src/i18n/untranslated.json` and asserted by
// `frontend/src/i18n/catalog.spec.ts` — do not add an empty namespace to silence
// that check; write the translation and delete the keys from that list instead.
export default {
  aiChat,
  global,
  home,
  integrations,
  members,
  models,
  navigation,
  publicSite,
  ratchet,
  account,
  editor,
  feedback,
  featureStats,
  questions,
  users,
  comments,
  tasks,
  spaces,
  notifications,
  roomNotice,
  toolLabels,
  work,
}
