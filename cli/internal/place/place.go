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
