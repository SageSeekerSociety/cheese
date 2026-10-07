//go:build windows

package procscreen

import (
	"os"
	"strings"
	"testing"
	"time"
)

// On a real Windows machine: a screen's program gets the environment it was
// given, is found again by the next connector, and ends with its screen.
func TestAScreenRunsWithItsEnvironmentAndEndsWhenClosed(t *testing.T) {
	m, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s, err := m.Spawn("s1", []string{"cmd", "/c", "echo %CHEESE_PROBE% && ping -n 60 127.0.0.1 >nul"},
		[]string{"CHEESE_PROBE=from-the-launch"}, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for {
		out, _ := os.ReadFile(m.Log("s1"))
		if strings.Contains(string(out), "from-the-launch") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the program did not see its environment: %q", out)
		}
		time.Sleep(100 * time.Millisecond)
	}

	// The next connector finds it by its file.
	again, err := New(m.dir)
	if err != nil {
		t.Fatal(err)
	}
	if !again.HasSession("s1") {
		t.Fatal("a running screen was not found again")
	}
	rec, _ := again.read("s1")
	if !again.sys.running(rec.PID, rec.Started) {
		t.Fatal("the screen's program is not running")
	}

	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	deadline = time.Now().Add(10 * time.Second)
	for again.sys.running(rec.PID, rec.Started) {
		if time.Now().After(deadline) {
			t.Fatal("closing the screen left its program running")
		}
		time.Sleep(100 * time.Millisecond)
	}
	if again.HasSession("s1") {
		t.Fatal("a closed screen is still there")
	}
}
