// The grant set on disk. The owner's machine is not always online and the platform
// is not always reachable, so 本机离线时照常可用 depends on this file being right —
// and on it never becoming a way to *add* a grant.
package localfs

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStoreRoundTripsAGrantSet(t *testing.T) {
	path := filepath.Join(t.TempDir(), "localfs-grants.json")
	set := mustSet(t, Grant{
		ID: "g1", Path: "/home/alice/MyDocs", Platform: PlatformLinux,
		Mode: ModeReadWrite, Scope: ScopeProject, ProjectID: "project-a",
	})
	if err := set.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := LoadStore(path)
	if err != nil {
		t.Fatalf("LoadStore: %v", err)
	}
	if loaded == nil {
		t.Fatal("a saved set must load")
	}
	if loaded.Fingerprint != set.Fingerprint {
		t.Errorf("fingerprint = %q, want %q", loaded.Fingerprint, set.Fingerprint)
	}
	// The loaded set must decide the same way — the fingerprint matching while the
	// decision differs would be the worst of both.
	got := Decide(loaded, ask("/home/alice/MyDocs/x", ModeReadWrite, "project-a"))
	if !got.Allowed() {
		t.Errorf("the reloaded set did not allow what the saved one did: %s", got.Reason)
	}
	if got := Decide(loaded, ask("/home/alice/MyDocs/x", ModeReadWrite, "project-b")); got.Allowed() {
		t.Error("the reloaded set lost the project scope")
	}
}

func TestTheStoreFileIsNotWorldReadable(t *testing.T) {
	// The file names the folders on this disk that the assistant may open.
	path := filepath.Join(t.TempDir(), "localfs-grants.json")
	set := mustSet(t, Grant{
		ID: "g1", Path: "/home/alice/MyDocs", Platform: PlatformLinux,
		Mode: ModeRead, Scope: ScopeUser,
	})
	if err := set.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Errorf("mode = %o, want 600", perm)
	}
}

func TestAMissingStoreIsNotAnEmptySet(t *testing.T) {
	// 「还没收到过授权」 and 「授权里没有这个目录」 are different answers, and the
	// person reading them has to be able to tell which one they got.
	path := filepath.Join(t.TempDir(), "localfs-grants.json")
	loaded, err := LoadStore(path)
	if err != nil {
		t.Fatalf("a missing store is not an error: %v", err)
	}
	if loaded != nil {
		t.Fatal("a missing store must load as nil, not as an empty set")
	}
	got := Decide(loaded, ask("/home/alice/notes.txt", ModeRead, ""))
	if got.Reason != "no_grant_set" {
		t.Errorf("reason = %q, want no_grant_set", got.Reason)
	}
}

func TestACorruptStoreIsAnErrorNotASilentEmptySet(t *testing.T) {
	// Quietly acting as if this machine had never been granted anything would hide
	// the fact that it can no longer say what it is allowed to touch.
	path := filepath.Join(t.TempDir(), "localfs-grants.json")
	if err := os.WriteFile(path, []byte("{ not json"), 0o600); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if _, err := LoadStore(path); err == nil {
		t.Fatal("a corrupt store must be an error")
	}
}

func TestAHandEditedRootGrantIsRefusedOnLoad(t *testing.T) {
	// The store is a cache, and a cache that could hand back a grant that never
	// went through Normalize would be a way to put one there. Someone editing this
	// file to grant / must not get a machine that allows /.
	path := filepath.Join(t.TempDir(), "localfs-grants.json")
	body := `{"device_id":"d1","grants":[{"id":"g1","path":"/","platform":"linux","mode":"read_write","scope":"user"}]}`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("seed: %v", err)
	}
	loaded, err := LoadStore(path)
	if err != nil {
		t.Fatalf("LoadStore: %v", err)
	}
	if got := Decide(loaded, ask("/etc/passwd", ModeReadWrite, "")); got.Allowed() {
		t.Fatal("a hand-edited root grant must not authorize anything")
	}
}

func TestClearRemovesTheStore(t *testing.T) {
	path := filepath.Join(t.TempDir(), "localfs-grants.json")
	set := mustSet(t, Grant{
		ID: "g1", Path: "/home/alice/MyDocs", Platform: PlatformLinux,
		Mode: ModeRead, Scope: ScopeUser,
	})
	if err := set.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if err := Clear(path); err != nil {
		t.Fatalf("Clear: %v", err)
	}
	// Clearing twice is harmless: a device can be disconnected more than once.
	if err := Clear(path); err != nil {
		t.Fatalf("Clear twice: %v", err)
	}
	if loaded, err := LoadStore(path); err != nil || loaded != nil {
		t.Errorf("after Clear: %v, %v", loaded, err)
	}
}

func TestStorePathFollowsTheConfigDirectory(t *testing.T) {
	// Disabled config means disabled storage, matching the screen-count state file.
	if got := StorePath(""); got != "" {
		t.Errorf("StorePath(\"\") = %q, want empty", got)
	}
	got := StorePath("/etc/cheese/config.json")
	if want := "/etc/cheese/localfs-grants.json"; got != want {
		t.Errorf("StorePath = %q, want %q", got, want)
	}
}
