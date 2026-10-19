// Package place is the connector's copy of where the platform's files live on a
// machine it borrows — the Go side of `backend/app/domain/agent/place.py`.
//
// It holds one name and exists so that there is only one of it. Two packages
// need it: `daemoncmd`, whose `cheese uninstall` removes exactly this directory
// and nothing beside it, and `host`, which refuses to write a server-sent file
// anywhere else. A second copy does not fail loudly — an uninstall that reaches
// one directory over deletes nothing while reporting success, and a writer that
// spells it differently drops a file where nothing will ever look for it.
package place

import (
	"os"
	"strings"
)

// Root is the directory under the machine owner's home that the platform writes
// everything into: session homes, checkouts, the shared package store, the
// executor and its helpers, the launch scripts, the files the server stages for
// a session. The backend chooses the name in
// `backend/app/domain/agent/place.py`; this is a copy because nothing Python is
// importable from here, and `backend/tests/unit/test_footprint_root.py` fails if
// the two ever disagree.
//
// One directory, and it stays one. `place.py` also names `.claude`, and that is
// the platform's older directory INSIDE a session home — the session homes are
// already under this root, so they go with it. The `~/.claude` next to this one
// belongs to whoever owns the machine: their Claude Code credentials, settings
// and every transcript they have. An uninstall that reached for it would delete
// work the platform never wrote and cannot give back.
const Root = ".cheese"

const ProfileRootEnv = "CHEESE_PROFILE_ROOT"

// RootPlaceholder is how the backend spells the enrollment's root in the
// paths it sends; the connector names that root (CurrentRoot).
const RootPlaceholder = "${" + ProfileRootEnv + ":-" + Root + "}"

// ExpandRoot puts this enrollment's root where a path names the placeholder.
func ExpandRoot(value string) string {
	return strings.ReplaceAll(value, RootPlaceholder, CurrentRoot())
}

// CurrentRoot is relative to the machine owner's home, never a session home.
func CurrentRoot() string {
	if root := os.Getenv(ProfileRootEnv); root != "" {
		return root
	}
	return Root
}

// ClaudeLogin is the directory under CurrentRoot that holds the first Claude Code login
// the machine's owner gives the platform (`cheesehost claude login`), apart
// from their own `~/.claude`. The backend names it in `place.py`
// (`CLAUDE_LOGIN_DIR`) and `backend/tests/unit/test_footprint_root.py` fails if
// the two disagree.
const ClaudeLogin = "logins/claude-login"

// ClaudeLogins is the directory under CurrentRoot that holds every further login,
// one directory each, named by its id. `place.py` names it
// (`CLAUDE_LOGINS_DIR`), held to this one by the same test.
const ClaudeLogins = "logins/claude-logins"

// FirstClaudeLogin is the id of the login in ClaudeLogin, the one a machine had
// before it could hold several. The backend names it in `login_kind.py`
// (`FIRST_LOGIN`), held to this one by the same test.
const FirstClaudeLogin = "default"

// ModelService is the file in a login's directory that holds another model
// service the owner set instead of the login (`cheesehost claude login
// --base-url`). The backend's launch and probe name it in `place.py`
// (`MODEL_SERVICE_FILE`), held to this one by the same test.
const ModelService = "model-service.json"
