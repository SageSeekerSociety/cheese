package daemoncmd

import (
	"os"
	"path/filepath"
	"testing"
)

// After an uninstall the machine is the machine its owner lent us, with nothing
// of ours on it. That is what `cheese uninstall` promises in words, and it used
// to mean only the connector's own config and binary: everything the backend
// had written over the link — session homes, worktrees, the shared package
// store, the executor and its helpers — stayed behind under the footprint root,
// on a machine that no longer had anything installed that could find it.
func TestUninstallLeavesNothingBehind(t *testing.T) {
	home := t.TempDir()
	configDir := filepath.Join(t.TempDir(), "cheese")

	// What a machine that has run rooms actually holds, spelled as the launcher
	// writes it rather than as a single directory, so the assertion below is
	// about the whole footprint and not about one path we remembered.
	written := []string{
		filepath.Join(home, ".cheese", "home", "project", "room", ".claude", "settings.json"),
		filepath.Join(home, ".cheese", "work", "project", "room", "README.md"),
		filepath.Join(home, ".cheese", "store", "project", "uv", "package"),
		filepath.Join(home, ".cheese", "launch", "room.sh"),
		filepath.Join(home, ".cheese", "executor-releases", "abc", "runtime.py"),
		filepath.Join(home, ".cheese", "cheese-tunnel.token"),
		filepath.Join(configDir, "config.json"),
	}
	for _, path := range written {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// Something of the owner's, next to ours: an uninstall that took this too
	// would pass every assertion below and be a far worse bug than the one this
	// test is here for.
	theirs := filepath.Join(home, "notes.txt")
	if err := os.WriteFile(theirs, []byte("mine"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := removeFootprint(configDir, home); err != nil {
		t.Fatalf("removeFootprint: %v", err)
	}

	entries, err := os.ReadDir(home)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.Name() != filepath.Base(theirs) {
			t.Errorf("uninstall left %s behind in the home directory", entry.Name())
		}
	}
	if _, err := os.Stat(configDir); !os.IsNotExist(err) {
		t.Errorf("uninstall left the config directory behind: %v", err)
	}
	if _, err := os.Stat(theirs); err != nil {
		t.Errorf("uninstall removed something that was not ours: %v", err)
	}
}
