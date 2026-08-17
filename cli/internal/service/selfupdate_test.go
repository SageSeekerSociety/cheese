package service

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The production incident (#501): binary in a root-owned directory, unit running
// as an ordinary user. Self-update needs to create a temp file in that directory
// and rename over the executable, so it failed every time — and silently, since
// a failed update correctly keeps the old binary running.
func TestInstallRefusedWhenTheServiceUserCannotWriteTheBinaryDir(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o555); err != nil { // r-xr-xr-x, owned by us
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	err := selfUpdatableIn(dir, os.Getuid(), "someuser")
	if err == nil {
		t.Fatal("an unwritable binary directory must refuse the install")
	}
	msg := err.Error()
	// The error has one job: make the next action obvious. Naming the directory
	// and the user is what turns it from "it broke" into "install it elsewhere".
	for _, want := range []string{dir, "someuser", "could never update itself", "install.sh"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("error should mention %q, got: %v", want, err)
		}
	}
	// And it must say WHY nobody would notice, or the reader assumes an update
	// failure would have been visible.
	if !strings.Contains(msg, "silent") {
		t.Fatalf("error should say the failure would be silent: %v", err)
	}
}

func TestInstallAllowedWhenTheDirectoryIsOwnedAndWritable(t *testing.T) {
	dir := t.TempDir() // owned by us, 0o700 by default
	if err := selfUpdatableIn(dir, os.Getuid(), "me"); err != nil {
		t.Fatalf("a directory this user owns and can write must be accepted: %v", err)
	}
}

// A world-writable directory is ugly but does not break self-update, and this
// check exists to protect updates — not to have opinions about permissions.
func TestWorldWritableDirectoryIsAccepted(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	// A uid that is not ours, so only the world bit can carry it.
	if err := selfUpdatableIn(dir, os.Getuid()+4242, "other"); err != nil {
		t.Fatalf("world-writable must be accepted: %v", err)
	}
}

// A root-run service can write anywhere; blocking it would be a false positive.
func TestRootServiceIsNeverBlocked(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	if err := selfUpdatableIn(dir, 0, "root"); err != nil {
		t.Fatalf("root must not be blocked: %v", err)
	}
}

// Never block an install because we could not work out the answer — a check that
// fails closed on missing information turns an unrelated oddity into an outage.
func TestUnknowableDirectoryDoesNotBlock(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "gone")
	if err := selfUpdatableIn(missing, os.Getuid(), "me"); err != nil {
		t.Fatalf("an unstattable directory must not block the install: %v", err)
	}
}
