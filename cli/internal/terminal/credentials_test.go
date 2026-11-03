package terminal

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A session's environment carries its credentials (CHEESE_TOKEN and the
// like). A command line is readable by every user on the machine — `ps`, or
// /proc/<pid>/cmdline — so a credential must never be on one: not on any tmux
// invocation (the tmux server keeps the command line of the client that
// started it for as long as it runs), and not on the session's own processes.
// The program in the session must still receive it, and it must not be left
// behind in a file.
func TestSessionCredentialsNeverOnACommandLine(t *testing.T) {
	tmuxBin, err := findTmux()
	if err != nil {
		t.Skip("no tmux available")
	}
	isolate(t)

	// Every tmux invocation goes through this wrapper, which records its
	// arguments before running the real tmux.
	bin := t.TempDir()
	argvLog := filepath.Join(bin, "argv.log")
	wrapper := filepath.Join(bin, "tmux")
	script := "#!/bin/sh\nprintf '%s\\n' \"$*\" >> '" + argvLog + "'\nexec '" + tmuxBin + "' \"$@\"\n"
	if err := os.WriteFile(wrapper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CHEESE_TMUX", wrapper)

	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		t.Fatal(err)
	}
	needle := hex.EncodeToString(raw)
	// Quotes, `$`, a backslash and a tilde: what a careless quoting layer
	// would mangle on the way into the environment or into tmux.
	secret := `tok-` + needle + `-'q' "d" $HOME \x ~`

	m, err := NewManager()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(m.KillServer)

	out := filepath.Join(t.TempDir(), "seen")
	argv := []string{"sh", "-c", `printf %s "$CHEESE_TOKEN" > "$1"; exec sleep 30`, "sh", out}
	session, err := m.Spawn("seat", argv, []string{"CHEESE_TOKEN=" + secret}, 80, 24)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = session.Close() })

	var seen []byte
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if seen, err = os.ReadFile(out); err == nil && len(seen) > 0 {
			break
		}
	}
	if string(seen) != secret {
		t.Fatalf("the session's program saw CHEESE_TOKEN=%q, want %q", seen, secret)
	}

	// No `$` in the identity: tmux 3.4 itself reads an option holding one back
	// as `\$` (3.5 does not), however the value was set.
	saved := strings.ReplaceAll(secret, "$", "")
	identity, _ := json.Marshal(map[string]any{"sid": "seat", "env": map[string]string{"CHEESE_TOKEN": saved}})
	if err := m.SaveIdentity("seat", "https://backend.test", string(identity)); err != nil {
		t.Fatal(err)
	}
	identities, err := m.Identities("https://backend.test")
	if err != nil {
		t.Fatal(err)
	}
	if identities["seat"] != string(identity) {
		t.Fatalf("saved identity read back as %q, want %q", identities["seat"], identity)
	}

	logged, err := os.ReadFile(argvLog)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(logged), needle) {
		t.Errorf("a credential was on a tmux command line:\n%s", redact(string(logged), needle))
	}
	listing, err := exec.Command("ps", "-A", "-ww", "-o", "args=").Output()
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(listing), "\n") {
		if strings.Contains(line, needle) {
			t.Errorf("a running process shows a credential on its command line: %s", redact(line, needle))
		}
	}
	// The manager's private runtime dir, where the launch handed tmux its files.
	_ = filepath.WalkDir(filepath.Dir(m.sock), func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() || entry.Type()&fs.ModeSocket != 0 {
			return nil
		}
		if data, err := os.ReadFile(path); err == nil && strings.Contains(string(data), needle) {
			t.Errorf("a credential was left on disk in %s", path)
		}
		return nil
	})
}

func redact(s, needle string) string { return strings.ReplaceAll(s, needle, "<credential>") }
