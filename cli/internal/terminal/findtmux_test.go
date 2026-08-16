package terminal

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fakeTmuxIn drops an executable named tmux into dir and returns its path.
func fakeTmuxIn(t *testing.T, dir string) string {
	t.Helper()
	p := filepath.Join(dir, "tmux")
	if err := os.WriteFile(p, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}

// withWellKnown swaps the hard-coded install dirs for the test's own, since a
// test cannot write into /opt/homebrew.
func withWellKnown(t *testing.T, dirs ...string) {
	t.Helper()
	old := wellKnownTmuxDirs
	wellKnownTmuxDirs = dirs
	t.Cleanup(func() { wellKnownTmuxDirs = old })
}

// isolateConfigDir points os.UserConfigDir() at an empty directory.
//
// Without it these tests pass or fail depending on the developer's machine: a
// real installed private tmux at <config>/cheese/bin/tmux short-circuits the
// lookup before anything under test runs. (That copy is also why the reported
// LaunchAgent failure stopped reproducing on the machine that first hit it —
// something later dropped one in.)
func isolateConfigDir(t *testing.T) {
	t.Helper()
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
}

// The regression: a LaunchAgent runs with PATH=/usr/bin:/bin:/usr/sbin:/sbin,
// where no package manager puts tmux. Before this, the connector exited at
// startup and the machine just never came online.
func TestFindTmuxLooksBeyondAServiceManagersPath(t *testing.T) {
	isolateConfigDir(t)
	dir := t.TempDir()
	want := fakeTmuxIn(t, dir)
	withWellKnown(t, dir)
	t.Setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
	t.Setenv("CHEESE_TMUX", "")

	got, err := findTmux()
	if err != nil {
		t.Fatalf("findTmux: %v", err)
	}
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

// $CHEESE_TMUX stays the top of the list: an operator pointing at a specific
// binary must not be overridden by whatever is installed system-wide.
func TestExplicitOverrideWinsOverEverything(t *testing.T) {
	isolateConfigDir(t)
	explicit := t.TempDir()
	wellKnown := t.TempDir()
	want := fakeTmuxIn(t, explicit)
	fakeTmuxIn(t, wellKnown)
	withWellKnown(t, wellKnown)
	t.Setenv("CHEESE_TMUX", want)

	got, err := findTmux()
	if err != nil {
		t.Fatalf("findTmux: %v", err)
	}
	if got != want {
		t.Fatalf("got %q, want the explicit override %q", got, want)
	}
}

// An unset HOME is normal under a service manager. Expanding "$HOME/.nix-profile/bin"
// to "/.nix-profile/bin" would search a path nobody installs into — and, worse,
// report it as somewhere we looked.
func TestUnexpandedVariablesAreSkippedNotSearchedFromRoot(t *testing.T) {
	withWellKnown(t, "$HOME/.nix-profile/bin", "/etc/profiles/per-user/$USER/bin")
	t.Setenv("HOME", "")
	t.Setenv("USER", "")
	t.Setenv("PATH", "/nonexistent-for-this-test")
	t.Setenv("CHEESE_TMUX", "")

	_, err := findTmux()
	if err == nil {
		t.Fatal("expected failure with no tmux anywhere")
	}
	for _, bad := range []string{"/.nix-profile/bin", "/etc/profiles/per-user//bin"} {
		if strings.Contains(err.Error(), bad) {
			t.Fatalf("searched a nonsense path built from an empty variable: %q\n%v", bad, err)
		}
	}
}

// The old message named one remedy and no evidence, which made "which tmux is
// it not seeing?" a support round-trip.
func TestFailureNamesWhatWasSearched(t *testing.T) {
	isolateConfigDir(t)
	missing := filepath.Join(t.TempDir(), "nowhere")
	withWellKnown(t, missing)
	t.Setenv("PATH", "/nonexistent-for-this-test")
	t.Setenv("CHEESE_TMUX", "")

	_, err := findTmux()
	if err == nil {
		t.Fatal("expected failure")
	}
	msg := err.Error()
	for _, want := range []string{"Looked in:", missing, "PATH="} {
		if !strings.Contains(msg, want) {
			t.Fatalf("error should mention %q, got: %v", want, err)
		}
	}
	// It must still say how to fix it, not only what failed.
	if !strings.Contains(msg, "CHEESE_TMUX") {
		t.Fatalf("error dropped the remedy: %v", err)
	}
}

// A directory named `tmux` is not a tmux.
func TestADirectoryNamedTmuxIsNotAccepted(t *testing.T) {
	isolateConfigDir(t)
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "tmux"), 0o755); err != nil {
		t.Fatal(err)
	}
	withWellKnown(t, dir)
	t.Setenv("PATH", "/nonexistent-for-this-test")
	t.Setenv("CHEESE_TMUX", "")

	if got, err := findTmux(); err == nil {
		t.Fatalf("accepted a directory as the tmux binary: %q", got)
	}
}
