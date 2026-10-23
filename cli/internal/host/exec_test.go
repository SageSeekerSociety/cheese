package host

import (
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

func TestImmediateExecCancellationIsNotLost(t *testing.T) {
	previousProcs := runtime.GOMAXPROCS(1)
	t.Cleanup(func() { runtime.GOMAXPROCS(previousProcs) })

	marker := filepath.Join(t.TempDir(), "completed")
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{
		T:       "exec",
		ID:      "cancel-before-worker-starts",
		Command: []string{"sh", "-c", `sleep 2; printf completed > "$1"`, "sh", marker},
	})
	server.send(t, link.Msg{T: "exec.cancel", ID: "cancel-before-worker-starts"})

	result := awaitExecResult(t, server, "cancel-before-worker-starts")
	if result.Exit == 0 {
		t.Fatalf("the command reported success after its immediately following cancel frame: %+v", result)
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("the cancelled command completed its delayed side effect")
	} else if !os.IsNotExist(err) {
		t.Fatal(err)
	}
}

func awaitExecResult(t *testing.T, server *fakeServer, id string) link.Msg {
	t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		select {
		case message := <-server.in:
			if message.T == "exec.result" && message.ID == id {
				return message
			}
		case <-deadline:
			t.Fatalf("no exec.result for %q", id)
		}
	}
}

// An exec that asks for Stream hears what the command writes to stderr while it
// runs, a line at a time, and the result after the last of them still carries
// all of it: what is shown during an install and what its outcome is read from.
func TestAStreamedExecSendsEachStderrLineBeforeItsResult(t *testing.T) {
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{
		T:       "exec",
		ID:      "streamed",
		Stream:  true,
		Command: []string{"sh", "-c", `printf 'one\n' >&2; sleep 0.2; printf 'two\nthree' >&2; echo out`},
	})

	var lines []string
	deadline := time.After(5 * time.Second)
	for {
		select {
		case message := <-server.in:
			if message.T == "exec.output" && message.ID == "streamed" {
				lines = append(lines, message.Stderr)
				continue
			}
			if message.T == "exec.result" && message.ID == "streamed" {
				if want := []string{"one", "two"}; !reflect.DeepEqual(lines, want) {
					t.Fatalf("streamed lines before the result: got %q, want %q", lines, want)
				}
				if message.Stderr != "one\ntwo\nthree" || message.Stdout != "out\n" || message.Exit != 0 {
					t.Fatalf("the result no longer carries the whole command: %+v", message)
				}
				return
			}
		case <-deadline:
			t.Fatalf("no exec.result; streamed so far: %q", lines)
		}
	}
}

// Without Stream nothing but the result goes up, as before streaming existed.
func TestAnExecThatDoesNotAskIsNotStreamed(t *testing.T) {
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "exec", ID: "quiet", Command: []string{"sh", "-c", `printf 'one\n' >&2`}})

	deadline := time.After(5 * time.Second)
	for {
		select {
		case message := <-server.in:
			if message.T == "exec.output" {
				t.Fatalf("an exec that did not ask for it was streamed: %+v", message)
			}
			if message.T == "exec.result" && message.ID == "quiet" {
				return
			}
		case <-deadline:
			t.Fatal("no exec.result")
		}
	}
}
