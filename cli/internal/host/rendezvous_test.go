package host

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestOnlyPromptTakesTheSocket(t *testing.T) {
	armed := &sess{rvPath: "/tmp/x.sock"}
	bare := &sess{}

	if !armed.usesRendezvous("prompt") {
		t.Fatal("an armed screen must deliver `prompt` over the socket")
	}
	// Every other exposed function is still the cheeselet's. Routing them here
	// would silently break any future script-side capability.
	for _, name := range []string{"snapshot", "compact", "choose", ""} {
		if armed.usesRendezvous(name) {
			t.Fatalf("%q must not be routed to the socket", name)
		}
	}
	// No socket configured (an old device, or a screen with no topic) falls
	// through to the script rather than pretending to deliver.
	if bare.usesRendezvous("prompt") {
		t.Fatal("a screen with no socket must not claim the rendezvous path")
	}
}

func TestReadRvTokenTrimsAndReturns(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "tok")
	// The launcher writes with `od | tr`, which can leave a trailing newline;
	// a token compared byte-for-byte on the other side must not carry it.
	if err := os.WriteFile(p, []byte("  deadbeef\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := readRvToken(p)
	if err != nil {
		t.Fatalf("readRvToken: %v", err)
	}
	if got != "deadbeef" {
		t.Fatalf("got %q, want %q", got, "deadbeef")
	}
}

func TestReadRvTokenWaitsForALateFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "tok")
	go func() {
		time.Sleep(300 * time.Millisecond) // the launcher is still booting
		_ = os.WriteFile(p, []byte("late-token"), 0o600)
	}()

	old := rvTokenWait
	rvTokenWait = 5 * time.Second
	defer func() { rvTokenWait = old }()

	got, err := readRvToken(p)
	if err != nil {
		t.Fatalf("readRvToken: %v", err)
	}
	if got != "late-token" {
		t.Fatalf("got %q", got)
	}
}

func TestReadRvTokenFailsLoudly(t *testing.T) {
	old := rvTokenWait
	rvTokenWait = 300 * time.Millisecond
	defer func() { rvTokenWait = old }()

	if _, err := readRvToken(""); err == nil {
		t.Fatal("an unconfigured token file must be an error, not an empty token")
	} else if !strings.Contains(err.Error(), envRvTokenFile) {
		t.Fatalf("the error should name the missing env var, got: %v", err)
	}

	_, err := readRvToken(filepath.Join(t.TempDir(), "never"))
	if err == nil {
		t.Fatal("a token file that never appears must be an error")
	}
	if !strings.Contains(err.Error(), "never appeared") {
		t.Fatalf("unclear error: %v", err)
	}
}

// An empty file is not a token: treating it as one would send an empty auth
// frame and the session would refuse every prompt with no obvious cause.
func TestReadRvTokenRejectsAnEmptyFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "tok")
	if err := os.WriteFile(p, []byte("   \n"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := rvTokenWait
	rvTokenWait = 300 * time.Millisecond
	defer func() { rvTokenWait = old }()

	if _, err := readRvToken(p); err == nil {
		t.Fatal("a whitespace-only token file must not pass as a token")
	}
}
