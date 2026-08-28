package terminal

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// isolate points NewManager at a runtime dir of this test's own. NewManager
// derives its socket from $TMPDIR, and on a machine that hosts agents the
// default one is the LIVE connector's server — which holds every running
// `claude` on the box, and which these tests kill on the way out. A short path
// on purpose: a unix socket path is capped near 104 bytes.
func isolate(t *testing.T) {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "cheeseterm")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	t.Setenv("TMPDIR", dir)
}

// TestHasSessionAndAdopt spins up a private tmux server on a socket of its own,
// so it cannot touch any running screen. It verifies HasSession reflects an
// existing session and that Adopt wraps it without spawning. Skipped when no
// tmux binary is available.
func TestHasSessionAndAdopt(t *testing.T) {
	if _, err := findTmux(); err != nil {
		t.Skip("no tmux available")
	}
	isolate(t)
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
	isolate(t)
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

// TestWriteLongMessage is upstream micro-connector's T-058: a long message is
// never delivered to the agent. The applet hands the whole message to
// `tmux send-keys -l -- <text>` in one go, and tmux caps how long a single
// command may be ("command too long"). Nothing upstream is told — the error
// goes to the connector's log, which nobody reads — so the agent simply never
// hears what was said to it. That silence is the bug; the length limit is only
// how it starts. Asserts both halves: the write must not fail, and the bytes
// must actually arrive. The program runs with `stty raw` because the tty line
// discipline has a ~4KB/line limit of its own in canonical mode that would
// otherwise be mistaken for this one.
func TestWriteLongMessage(t *testing.T) {
	if _, err := findTmux(); err != nil {
		t.Skip("no tmux available")
	}
	isolate(t)
	m, err := NewManager()
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	defer m.KillServer()

	out := t.TempDir() + "/heard.txt"
	s, err := m.Spawn("long1", []string{"sh", "-c", "stty raw -echo; cat > " + out + "; sleep 30"},
		nil, 80, 24)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	defer s.Close()
	time.Sleep(500 * time.Millisecond) // let `stty raw` take effect before typing

	// Comfortably past tmux's limit; half Chinese, because multi-byte runes are
	// what a naive split corrupts and most messages here are Chinese.
	body := strings.Repeat("The quick brown fox jumps over the lazy dog. ", 250) +
		strings.Repeat("这是一条很长的中文消息，用来确认分片不会把一个字劈成两半。", 250)
	if err := s.Write([]byte(body)); err != nil {
		t.Fatalf("Write of a %d-byte message failed: %v", len(body), err)
	}

	deadline := time.Now().Add(10 * time.Second)
	for {
		got, err := os.ReadFile(out)
		if err == nil && len(got) >= len(body) {
			if string(got[:len(body)]) != body {
				t.Fatalf("the program received %d bytes but they are not what was sent", len(got))
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("the program received %d of %d bytes", len(got), len(body))
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// TestChunkEnd covers the boundary the long-message fix turns on: a chunk must
// never end mid-rune, because the halves become arguments to two separate tmux
// commands and neither is valid text.
func TestChunkEnd(t *testing.T) {
	han := []byte("中")            // 3 bytes
	body := bytes.Repeat(han, 10) // 30 bytes
	for max := 1; max <= len(body); max++ {
		n := chunkEnd(body, max)
		if max >= 3 && n%3 != 0 {
			t.Errorf("chunkEnd(max=%d) = %d, which splits a rune", max, n)
		}
		if n > max {
			t.Errorf("chunkEnd(max=%d) = %d, longer than asked", max, n)
		}
		if max >= 3 && n == 0 {
			t.Errorf("chunkEnd(max=%d) made no progress", max)
		}
	}
	if got := chunkEnd([]byte("ab"), 8); got != 2 {
		t.Errorf("chunkEnd of a short input = %d, want 2", got)
	}
	if got := chunkEnd([]byte{0x80, 0x80, 0x80, 0x80}, 2); got != 2 {
		t.Errorf("chunkEnd of invalid UTF-8 = %d, want 2 (no stall)", got)
	}
}
