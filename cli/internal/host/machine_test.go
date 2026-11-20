//go:build !windows

package host

import (
	"bufio"
	"encoding/base64"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

// The executor the call reaches answers once, so the call ends.
func answerOnce(listener net.Listener) {
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		_, _ = bufio.NewReader(conn).ReadString('\n')
		_, _ = conn.Write([]byte("{\"result\":null}\n"))
		conn.Close()
	}()
}

func until(t *testing.T, server *fakeServer, frameType string) link.Msg {
	t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		select {
		case m := <-server.in:
			if m.T == frameType {
				return m
			}
		case <-deadline:
			t.Fatalf("no %s frame", frameType)
		}
	}
}

func platformDir(t *testing.T, state string) string {
	t.Helper()
	socket := platformSocket(state)
	if err := os.Mkdir(filepath.Dir(socket), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(filepath.Dir(socket)) })
	return socket
}

func dialEventually(t *testing.T, socket string) *net.UnixConn {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		conn, err := net.Dial("unix", socket)
		if err == nil {
			return conn.(*net.UnixConn)
		}
		if time.Now().After(deadline) {
			t.Fatalf("nothing listens on %s: %v", socket, err)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// A connection the sandbox makes to its platform socket goes up as one stream
// tagged with the state the backend named, and the platform's answer comes
// back down it.
func TestASandboxStreamGoesUpTaggedWithItsStateAndTheAnswerComesBack(t *testing.T) {
	state, listener := executorListener(t)
	socket := platformDir(t, state)
	answerOnce(listener)
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "execution.call", ID: "call-1", Path: state, Stdin: `{"method":"ping"}`})
	until(t, server, "execution.result")

	conn := dialEventually(t, socket)
	defer conn.Close()
	if _, err := conn.Write([]byte("GET /whoami HTTP/1.1\r\n\r\n")); err != nil {
		t.Fatal(err)
	}
	_ = conn.CloseWrite()

	open := until(t, server, "machine.open")
	if open.Path != state {
		t.Fatalf("stream tagged %q, want the state the backend named %q", open.Path, state)
	}
	data := until(t, server, "machine.data")
	if data.ID != open.ID {
		t.Fatalf("data on stream %q, opened %q", data.ID, open.ID)
	}
	if got, _ := base64.StdEncoding.DecodeString(data.Data); string(got) != "GET /whoami HTTP/1.1\r\n\r\n" {
		t.Fatalf("bytes changed on the way up: %q", got)
	}
	if end := until(t, server, "machine.end"); end.ID != open.ID {
		t.Fatalf("end on stream %q, opened %q", end.ID, open.ID)
	}

	server.send(t, link.MachineData(open.ID, []byte("HTTP/1.1 200 OK\r\n\r\n中文")))
	server.send(t, link.MachineClose(open.ID, nil))
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	answer, err := io.ReadAll(conn)
	if err != nil {
		t.Fatal(err)
	}
	if string(answer) != "HTTP/1.1 200 OK\r\n\r\n中文" {
		t.Fatalf("answer changed on the way down: %q", answer)
	}
}

// Without the directory the install makes there is no sandbox to serve, and
// the connector does not make one.
func TestNoPlatformSocketWithoutTheInstallsDirectory(t *testing.T) {
	state, listener := executorListener(t)
	answerOnce(listener)
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "execution.call", ID: "call-1", Path: state, Stdin: `{"method":"ping"}`})
	until(t, server, "execution.result")
	if _, err := os.Lstat(filepath.Dir(platformSocket(state))); !os.IsNotExist(err) {
		t.Fatalf("the connector made the platform directory itself: %v", err)
	}
}

// A sandbox that stops reading its answer loses its own stream; the link's
// read loop, which every session on the machine shares, keeps going.
func TestASandboxThatStopsReadingDoesNotHoldTheLink(t *testing.T) {
	state, listener := executorListener(t)
	socket := platformDir(t, state)
	answerOnce(listener)
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "execution.call", ID: "call-1", Path: state, Stdin: `{"method":"ping"}`})
	until(t, server, "execution.result")

	stalled := dialEventually(t, socket)
	defer stalled.Close()
	_, _ = stalled.Write([]byte("GET / HTTP/1.1\r\n\r\n"))
	open := until(t, server, "machine.open")
	chunk := make([]byte, 1<<20)
	for i := 0; i < 2*machineQueue; i++ {
		server.send(t, link.MachineData(open.ID, chunk))
	}
	if closed := until(t, server, "machine.close"); closed.ID != open.ID || closed.Error == "" {
		t.Fatalf("the stalled stream was not ended with a reason: %+v", closed)
	}

	// The read loop still serves the next frame: an exec answers.
	server.send(t, link.Msg{T: "exec", ID: "after", Command: []string{"true"}})
	if result := until(t, server, "exec.result"); result.ID != "after" {
		t.Fatalf("got %+v", result)
	}
}
