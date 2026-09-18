import account from './account.json'
import comments from './comments.json'
import editor from './editor.json'
import global from './global.json'
import navigation from './navigation.json'
import notifications from './notifications.json'
import publicSite from './publicSite.json'
import users from './users.json'

// Namespaces absent here have no English translation yet. They are listed, key by
// key, in `frontend/src/i18n/untranslated.json` and asserted by
// `frontend/src/i18n/catalog.spec.ts` — do not add an empty namespace to silence
// that check; write the translation and delete the keys from that list instead.
export default { global, navigation, publicSite, account, editor, users, comments, notifications }
