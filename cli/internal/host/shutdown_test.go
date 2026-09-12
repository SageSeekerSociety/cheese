package host

import (
	"context"
	"os"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
)

// Stopping the connector is not a decision to end anybody's work — the service
// manager stops it to restart it, to apply an update, on a reboot. Before this,
// the exit path killed the tmux server, so "just restart the connector" ended
// every turn running on the machine, and nothing said so.
//
// The two tests below pin both halves of the distinction: shutting down lets go,
// and a close the server asked for still ends the session.

// isolatedManager gives the test its own tmux runtime dir. NewManager derives
// the socket from $TMPDIR, which on a machine that hosts agents is the LIVE
// connector's server — the one holding every running `claude`. A short path on
// purpose: a unix socket path is capped near 104 bytes.
func isolatedManager(t *testing.T) *terminal.Manager {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "cheesehost")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	t.Setenv("TMPDIR", dir)

	m, err := terminal.NewManager()
	if err != nil {
		t.Skipf("no tmux available: %v", err)
	}
	t.Cleanup(m.KillServer)
	return m
}

func TestRestartRestoresIdentityAndCanCloseWithoutReopening(t *testing.T) {
	m := isolatedManager(t)
	newHost := func(base string) *Host {
		return &Host{tm: m, base: base, conn: link.New("", "", "", ""),
			ctx: context.Background(), sessions: map[string]*sess{}}
	}
	h := newHost("https://backend.test")
	h.createSession(link.Msg{T: "session.create", Sid: "recover-me", Screen: "original-token",
		Command: []string{"sh", "-c", "sleep 60"},
		Env: map[string]string{"CHEESE_PROJECT": "project", "CHEESE_TOPIC": "topic",
			"CHEESE_TOKEN_EXPIRES": "1234567890", envRvSock: "/tmp/example.sock"}})
	if h.session("recover-me") == nil {
		t.Fatal("screen creation failed")
	}
	identities, identityErr := m.Identities(h.base)
	if identityErr != nil || len(identities) != 1 {
		t.Fatalf("saved identity: %v, %v", identities, identityErr)
	}
	h.releaseAll()
	restarted := newHost(h.base)
	screens, err := restarted.restoreSessions()
	if err != nil || len(screens) != 1 {
		t.Fatalf("restore: %v, %v", screens, err)
	}
	if screens[0].Screen != "original-token" || screens[0].Env["CHEESE_TOKEN_EXPIRES"] != "1234567890" {
		t.Fatalf("birth identity changed: %+v", screens[0])
	}
	if restarted.session("recover-me").rvPath != "/tmp/example.sock" {
		t.Fatal("restarted host cannot deliver to the original session")
	}
	other := newHost("https://other.test")
	if err := other.closeSession("recover-me"); err != nil || !m.HasSession("recover-me") {
		t.Fatal("a different backend closed this backend's session")
	}
	if err := newHost(h.base).closeSession("recover-me"); err != nil || m.HasSession("recover-me") {
		t.Fatalf("close after host restart: %v", err)
	}
	if err := restarted.closeSession("recover-me"); err != nil {
		t.Fatalf("repeated close: %v", err)
	}
}

// hostWithScreen builds a Host holding one live screen, without a server link.
func hostWithScreen(t *testing.T, name string) (*Host, *terminal.Manager) {
	t.Helper()
	m := isolatedManager(t)
	term, err := m.Spawn(name, []string{"sh", "-c", "sleep 60"}, nil, 80, 24)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	h := &Host{tm: m, ctx: context.Background(), sessions: map[string]*sess{
		name: {term: term},
	}}
	return h, m
}

func TestShuttingDownLeavesEveryScreenRunning(t *testing.T) {
	h, m := hostWithScreen(t, "screen-a")

	h.releaseAll()

	if !m.HasSession("screen-a") {
		t.Fatal("the connector's shutdown ended a screen; restarting the " +
			"connector would take every turn on the machine with it")
	}
	if len(h.sessions) != 0 {
		t.Fatal("shutdown must let go of the screens it no longer drives")
	}
}

func TestAServerRequestedCloseEndsTheScreen(t *testing.T) {
	h, m := hostWithScreen(t, "screen-b")

	h.closeSession("screen-b")

	if m.HasSession("screen-b") {
		t.Fatal("a close the server asked for left the session running")
	}
}
