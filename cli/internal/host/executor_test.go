package host

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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
