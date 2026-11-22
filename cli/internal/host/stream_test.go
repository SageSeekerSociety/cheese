//go:build !windows

package host

import (
	"bufio"
	"encoding/base64"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

// A stream the server opens reaches the executor with its first line, bytes
// go both ways while it is open, and the executor closing it is said up.
func TestAStreamCarriesBytesBothWaysUntilTheExecutorEndsIt(t *testing.T) {
	state, listener := executorListener(t)
	accepted := make(chan net.Conn, 1)
	go func() {
		conn, err := listener.Accept()
		if err == nil {
			accepted <- conn
		}
	}()
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "stream.open", ID: "s1", Path: state, Header: `{"method":"terminal"}`})

	var conn net.Conn
	select {
	case conn = <-accepted:
	case <-time.After(5 * time.Second):
		t.Fatal("the executor was never dialled")
	}
	reader := bufio.NewReader(conn)
	if line, _ := reader.ReadString('\n'); line != "{\"method\":\"terminal\"}\n" {
		t.Fatalf("first line: %q", line)
	}

	_, _ = conn.Write([]byte("prompt$ "))
	up := until(t, server, "stream.data")
	if got, _ := base64.StdEncoding.DecodeString(up.Data); string(got) != "prompt$ " || up.ID != "s1" {
		t.Fatalf("up: %+v", up)
	}

	server.send(t, link.Msg{T: "stream.data", ID: "s1", Data: base64.StdEncoding.EncodeToString([]byte("ls\n"))})
	buf := make([]byte, 3)
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := reader.Read(buf); err != nil || string(buf) != "ls\n" {
		t.Fatalf("down: %q %v", buf, err)
	}

	conn.Close()
	closed := until(t, server, "stream.close")
	if closed.ID != "s1" || closed.Error != "" {
		t.Fatalf("close: %+v", closed)
	}
}

// Output past the window waits in the executor until the server acks what it
// passed on: a flood never fills the link the machine's sessions share.
func TestAStreamSendsNoMoreThanItsWindowUntilAcked(t *testing.T) {
	state, listener := executorListener(t)
	total := 3 * streamWindow
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		_, _ = bufio.NewReader(conn).ReadString('\n')
		_, _ = conn.Write([]byte(strings.Repeat("y", total)))
	}()
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "stream.open", ID: "s1", Path: state, Header: "{}"})

	received := 0
	read := func(wait time.Duration) bool {
		select {
		case m := <-server.in:
			if m.T == "stream.data" {
				data, _ := base64.StdEncoding.DecodeString(m.Data)
				received += len(data)
			}
			return true
		case <-time.After(wait):
			return false
		}
	}
	for read(500 * time.Millisecond) {
	}
	if received < streamWindow || received >= streamWindow+machineChunk {
		t.Fatalf("before any ack: %d bytes out, window %d", received, streamWindow)
	}

	for received < total {
		server.send(t, link.Msg{T: "stream.ack", ID: "s1", Bytes: int64(received)})
		if !read(5 * time.Second) {
			t.Fatalf("after an ack nothing more came: %d of %d", received, total)
		}
	}
}

// The server closing a stream closes the executor's connection.
func TestTheServerClosingAStreamHangsUpOnTheExecutor(t *testing.T) {
	state, listener := executorListener(t)
	hungUp := make(chan struct{})
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		reader := bufio.NewReader(conn)
		_, _ = reader.ReadString('\n')
		_, _ = conn.Write([]byte("x"))
		_, _ = reader.ReadByte()
		close(hungUp)
	}()
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "stream.open", ID: "s1", Path: state, Header: "{}"})
	until(t, server, "stream.data")
	server.send(t, link.Msg{T: "stream.close", ID: "s1"})
	select {
	case <-hungUp:
	case <-time.After(5 * time.Second):
		t.Fatal("the executor's connection stayed open")
	}
}
