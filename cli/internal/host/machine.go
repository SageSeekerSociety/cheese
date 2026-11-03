package host

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

// A session's sandbox reaches the platform through this connector, never with
// a credential of its own. The connector listens on one socket per state
// directory, in a directory the sandbox is shown read-only, and relays every
// connection made there as a machine.* stream over the device link, tagged
// with the state it arrived on. Which socket a stream came in on is what says
// whose it is: the sandbox can neither name another session's nor replace its
// own (backend `confinement.sandbox_argv`).
//
// The connector starts listening for a state when the backend first calls its
// executor (runExecutor), so a connector restarted under a running sandbox is
// listening again by the next tool call, before any command it starts runs.

// machineStreamLimit bounds the streams one connector relays at once.
const machineStreamLimit = 256

const machineChunk = 32 * 1024

var (
	errMachineBusy    = errors.New("too many platform streams on this machine")
	errMachineStalled = errors.New("the sandbox stopped reading its answer")
)

// machineQueue bounds the answer bytes waiting for one sandbox to read them.
// A sandbox that stops reading loses its own stream at this depth; it never
// holds the link's read loop, which every session on the machine shares.
const machineQueue = 64

type machineRelay struct {
	mu        sync.Mutex
	listeners map[string]net.Listener   // platform socket path -> its listener
	streams   map[string]*machineStream // stream id -> the sandbox's connection
}

type machineStream struct {
	conn     net.Conn
	down     chan []byte // answer bytes, written to conn by its own goroutine
	done     chan struct{}
	finished bool // down is closed; touched only on the link's read loop
}

// listenMachine makes sure the platform socket of the state the backend named
// as `named` (resolved to `resolved`) is being served.
func (h *Host) listenMachine(named, resolved string) {
	socket := platformSocket(resolved)
	if socket == "" {
		return
	}
	r := &h.machine
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.listeners == nil {
		r.listeners = map[string]net.Listener{}
		r.streams = map[string]*machineStream{}
	}
	// A session torn down takes its directory with it; its listener would
	// otherwise accept nothing for the life of this process.
	for path, listener := range r.listeners {
		if _, err := os.Lstat(path); err != nil {
			_ = listener.Close()
			delete(r.listeners, path)
		}
	}
	if _, ok := r.listeners[socket]; ok {
		return
	}
	// The install made this directory and bound it into the sandbox; without
	// it there is no sandbox to serve.
	if info, err := os.Lstat(filepath.Dir(socket)); err != nil || !info.IsDir() {
		return
	}
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cheese: cannot serve the platform socket %s: %v\n", socket, err)
		return
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		fmt.Fprintf(os.Stderr, "cheese: cannot restrict the platform socket %s: %v\n", socket, err)
	}
	r.listeners[socket] = listener
	go h.acceptMachine(listener, named)
}

func (h *Host) acceptMachine(listener net.Listener, named string) {
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		id, stream, err := h.machine.add(conn)
		if err != nil {
			_ = conn.Close()
			continue
		}
		go h.answerMachine(id, stream.conn, stream.down, stream.done)
		if h.conn.Send(link.MachineOpen(id, named)) != nil {
			h.machine.drop(id)
			continue
		}
		go h.pumpMachine(id, conn)
	}
}

// pumpMachine sends up what the sandbox writes. Its end of file is not the end
// of the stream: the answer is still to come down (machineFrame).
func (h *Host) pumpMachine(id string, conn net.Conn) {
	buf := make([]byte, machineChunk)
	for {
		n, err := conn.Read(buf)
		if n > 0 {
			if h.conn.Send(link.MachineData(id, buf[:n])) != nil {
				h.machine.drop(id)
				return
			}
		}
		if errors.Is(err, io.EOF) {
			// Without the link the answer can never come down.
			if h.conn.Send(link.MachineEnd(id)) != nil {
				h.machine.drop(id)
			}
			return
		}
		if err != nil {
			if h.machine.drop(id) {
				_ = h.conn.Send(link.MachineClose(id, err))
			}
			return
		}
	}
}

// machineFrame applies one machine.* frame the platform sent down. It runs on
// the link's read loop, so it only queues: the stream's own goroutine writes.
func (h *Host) machineFrame(m link.Msg) {
	stream := h.machine.get(m.ID)
	if stream == nil {
		return
	}
	switch m.T {
	case "machine.data":
		data, err := base64.StdEncoding.DecodeString(m.Data)
		if err != nil {
			h.failMachine(m.ID, err)
			return
		}
		if stream.finished {
			return
		}
		select {
		case stream.down <- data:
		case <-stream.done:
		default:
			h.failMachine(m.ID, errMachineStalled)
		}
	case "machine.close":
		// After what is queued: closing the queue ends the writer once it has
		// written the rest.
		h.machine.finish(m.ID)
	}
}

// answerMachine writes the platform's answer to the sandbox, in order.
func (h *Host) answerMachine(id string, conn net.Conn, down <-chan []byte, done <-chan struct{}) {
	for {
		select {
		case data, open := <-down:
			if !open {
				h.machine.drop(id)
				return
			}
			if _, err := conn.Write(data); err != nil {
				h.failMachine(id, err)
				return
			}
		case <-done:
			return
		}
	}
}

func (h *Host) failMachine(id string, err error) {
	if h.machine.drop(id) {
		_ = h.conn.Send(link.MachineClose(id, err))
	}
}

func (r *machineRelay) add(conn net.Conn) (string, *machineStream, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", nil, err
	}
	id := "machine-" + hex.EncodeToString(raw)
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.streams) >= machineStreamLimit {
		return "", nil, errMachineBusy
	}
	stream := &machineStream{conn: conn, down: make(chan []byte, machineQueue), done: make(chan struct{})}
	r.streams[id] = stream
	return id, stream, nil
}

func (r *machineRelay) get(id string) *machineStream {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.streams[id]
}

// finish ends a stream once what is queued for it is written.
func (r *machineRelay) finish(id string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if stream, ok := r.streams[id]; ok && !stream.finished {
		stream.finished = true
		close(stream.down)
	}
}

// drop closes and forgets a stream, reporting whether it was still open.
func (r *machineRelay) drop(id string) bool {
	r.mu.Lock()
	stream, ok := r.streams[id]
	delete(r.streams, id)
	r.mu.Unlock()
	if ok {
		close(stream.done)
		_ = stream.conn.Close()
	}
	return ok
}
