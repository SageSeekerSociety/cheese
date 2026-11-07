package update

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// On Windows itself: a process still running the binary an earlier update moved
// aside (held open here, which Windows treats the same) makes the replace fail,
// and the download goes with the failure.
func TestReplaceWhileTheMovedAsideBinaryRunsRemovesTheDownload(t *testing.T) {
	forgetFailedBuild(t)
	self, tmp := installDir(t, []byte("the new build"))
	if err := os.WriteFile(self+".old", []byte("an older build"), 0o755); err != nil {
		t.Fatal(err)
	}
	held, err := os.Open(self + ".old")
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()

	if err := Replace(tmp, self); err == nil {
		t.Fatal("Replace while the moved-aside binary is open = nil, want an error")
	}
	if found := downloadsBeside(self); len(found) != 0 {
		t.Errorf("a failed replace left the download behind: %v", found)
	}
	if got, _ := os.ReadFile(self); string(got) != "the running build" {
		t.Errorf("a failed replace changed the executable to %q", got)
	}
}

// A download some process still has open is skipped, not an error, and is
// removed by a later start once it has been let go.
func TestCleanupSkipsADownloadStillOpen(t *testing.T) {
	dir := t.TempDir()
	self := filepath.Join(dir, "cheesehost.exe")
	path := filepath.Join(dir, ".cheese-update-1.exe")
	if err := os.WriteFile(path, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-2 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if removed, _ := Cleanup(self); removed != 0 {
		t.Errorf("Cleanup removed %d downloads while one was open, want 0", removed)
	}
	f.Close()
	if removed, _ := Cleanup(self); removed != 1 {
		t.Errorf("Cleanup after release removed %d, want 1", removed)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("download still there after release: %v", err)
	}
}
