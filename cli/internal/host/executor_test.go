package host

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

func executorListener(t *testing.T) (string, net.Listener) {
	t.Helper()
	state, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte(state))
	path := fmt.Sprintf("/tmp/cheese-execution-%d-%x.sock", os.Getuid(), digest[:12])
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	return state, listener
}

func TestExecutorRepeatedCallsAndLargeUnicodeResult(t *testing.T) {
	state, listener := executorListener(t)
	want := "{\"result\":\"" + strings.Repeat("中文", 400000) + "\"}\n"
	go func() {
		for i := 0; i < 2; i++ {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			_, _ = bufio.NewReader(conn).ReadString('\n')
			_, _ = conn.Write([]byte(want))
			conn.Close()
		}
	}()
	// A working executor needs no shell or Python executable for these calls.
	t.Setenv("PATH", t.TempDir())
	for i := 0; i < 2; i++ {
		var got bytes.Buffer
		err := executorExchange(context.Background(), state, `{"method":"ping"}`, func(chunk []byte) error {
			if len(chunk) > 64*1024 {
				t.Errorf("oversized frame: %d", len(chunk))
			}
			_, err := got.Write(chunk)
			return err
		})
		if err != nil {
			t.Fatal(err)
		}
		if got.String() != want {
			t.Fatal("response corrupted or truncated")
		}
	}
}

func TestExecutorDisconnectDoesNotRetry(t *testing.T) {
	state, listener := executorListener(t)
	go func() {
		conn, _ := listener.Accept()
		if conn != nil {
			conn.Close()
		}
	}()
	err := executorExchange(context.Background(), state, `{"method":"invoke"}`, func([]byte) error { return nil })
	if err == nil {
		t.Fatal("lost response must report failure")
	}
}

func TestExecutorRecordedHomePath(t *testing.T) {
	state, listener := executorListener(t)
	t.Setenv("HOME", filepath.Dir(state))
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		_, _ = bufio.NewReader(conn).ReadString('\n')
		_, _ = conn.Write([]byte("{\"result\":{\"ok\":true}}\n"))
	}()
	var got bytes.Buffer
	err := executorExchange(context.Background(), "$HOME/"+filepath.Base(state), `{"method":"ping"}`, func(data []byte) error {
		_, err := got.Write(data)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.String() != "{\"result\":{\"ok\":true}}\n" {
		t.Fatal(got.String())
	}
}

func TestExecutorCancellationClosesWaitingSocket(t *testing.T) {
	state, listener := executorListener(t)
	closed := make(chan struct{})
	go func() {
		conn, _ := listener.Accept()
		if conn == nil {
			return
		}
		defer conn.Close()
		reader := bufio.NewReader(conn)
		_, _ = reader.ReadString('\n')
		_, _ = reader.ReadByte()
		close(closed)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if executorExchange(ctx, state, `{"method":"ping"}`, func([]byte) error { return nil }) == nil {
		t.Fatal("expected cancellation")
	}
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("socket remained open")
	}
}

// The dispatch itself. `host.go`'s `case "execution.call"` is a hand-typed
// string that nothing else in this package drives, so misspelling it leaves
// every test on both sides of the seam green while the connector silently drops
// every call the backend makes and each one hangs to its timeout. The frame fed
// in here is therefore the committed fixture's, byte for byte, with only the
// state directory swapped for one that exists on this machine.
func TestTheFixturesExecutionCallReachesTheExecutor(t *testing.T) {
	state, listener := executorListener(t)
	m := executionCallFixture(t)
	m.Path = state

	got := make(chan string, 1)
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		line, _ := bufio.NewReader(conn).ReadString('\n')
		got <- line
	}()

	h := &Host{
		ctx:   context.Background(),
		conn:  link.New("", "", "", ""),
		execs: map[string]context.CancelFunc{},
	}
	h.onMsg(m)

	select {
	case line := <-got:
		// Verbatim, because the runner on the other end parses these bytes.
		if want := m.Stdin + "\n"; line != want {
			t.Fatalf("executor read %q, want the call's stdin %q", line, want)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the executor was never dialed — execution.call was not dispatched")
	}
}

// The one frame, read from the file both languages are held to. The Go contract
// test over in cli/internal/link reads the whole directory; here only this frame
// matters, and reading it straight keeps host from depending on that test file.
func executionCallFixture(t *testing.T) link.Msg {
	t.Helper()
	const path = "../../../backend/tests/fixtures/wire/execution-call.json"
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var fixture struct {
		Frame json.RawMessage `json:"frame"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	var m link.Msg
	if err := json.Unmarshal(fixture.Frame, &m); err != nil {
		t.Fatalf("%s is not a link.Msg: %v", path, err)
	}
	return m
}
