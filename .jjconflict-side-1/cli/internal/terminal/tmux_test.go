package terminal

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestHasSessionAndAdopt spins up a fully isolated private tmux server on its own
// temp socket (never the live service's), so it cannot touch any running screen.
// It verifies HasSession reflects an existing session and that Adopt wraps it
// without spawning. Skipped when no tmux binary is available.
func TestHasSessionAndAdopt(t *testing.T) {
	if _, err := findTmux(); err != nil {
		t.Skip("no tmux available")
	}
	m, err := NewManager()
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	defer m.KillServer()

	if m.HasSession("nope") {
		t.Error("HasSession returned true for a nonexistent session")
	}

	sess, err := m.Spawn("t1", []string{"sh", "-c", "sleep 30"}, nil, 80, 24)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	if !m.HasSession("t1") {
		t.Error("HasSession returned false for a live session")
	}

	adopted := m.Adopt("t1")
	if adopted == nil || adopted.name != "t1" {
		t.Fatalf("Adopt returned %+v, want a Session named t1", adopted)
	}

	_ = sess.Close()
	if m.HasSession("t1") {
		t.Error("HasSession returned true after the session was closed")
	}
}

// TestSpawnRunsTheExactArgvItWasHanded pins the property #90 is about: the
// argv handed to Spawn must reach the process unchanged.
//
// #90 claims tmux joins multiple command arguments with spaces and re-parses
// them through /bin/sh, so a server-sent ["bash","-lc","<script>"] never runs
// under bash. Measured on tmux 3.5a that is NOT what happens — a multi-argument
// command is exec'd directly, and an argument containing spaces arrives whole.
// Older tmux did join; this test does not care which, because it asserts the
// PROPERTY rather than the mechanism, so it fails either way if argv is ever
// mangled — including by "fixing" this by joining the argv into one shell
// string, which would put a /bin/sh re-parse back in the path.
//
// It looks only at results, never at the command line: the two things that
// break first under re-parsing are an argument with a space in it, and a
// bash-only builtin.
func TestSpawnRunsTheExactArgvItWasHanded(t *testing.T) {
	if _, err := findTmux(); err != nil {
		t.Skip("no tmux available")
	}
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("no bash available")
	}
	m, err := NewManager()
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	defer m.KillServer()

	dir := t.TempDir()
	out := filepath.Join(dir, "out.txt")
	// ${BASH_VERSION:-} 在 dash 下是空的 —— 证明它真的跑在 bash 里。
	// "$1" 带着空格 —— 证明参数边界没有被重新切词。
	script := `printf '%s|%s' "${BASH_VERSION:-NOT-BASH}" "$1" > ` + "'" + out + "'"
	argv := []string{"bash", "-lc", script, "bash", "a b c"}

	if _, err := m.Spawn("quoting", argv, nil, 80, 24); err != nil {
		t.Fatalf("Spawn: %v", err)
	}

	var data []byte
	for i := 0; i < 100; i++ {
		if data, err = os.ReadFile(out); err == nil && len(data) > 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if err != nil || len(data) == 0 {
		t.Fatalf("脚本没有产出任何东西（多半是根本没跑起来）：%v", err)
	}
	parts := strings.SplitN(string(data), "|", 2)
	if len(parts) != 2 {
		t.Fatalf("产出的内容不对：%q", data)
	}
	if parts[0] == "NOT-BASH" || parts[0] == "" {
		t.Errorf("跑在了 bash 之外的壳里：BASH_VERSION=%q", parts[0])
	}
	if parts[1] != "a b c" {
		t.Errorf("带空格的参数被重新切词了：$1=%q，要的是 %q", parts[1], "a b c")
	}
}
