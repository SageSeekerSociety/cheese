package host

import (
	"os"
	"path/filepath"
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
