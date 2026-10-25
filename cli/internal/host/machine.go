package host

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
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

var errMachineBusy = errors.New("too many platform streams on this machine")

type machineRelay struct {
	mu        sync.Mutex
	listeners map[string]net.Listener // platform socket path -> its listener
	streams   map[string]net.Conn     // stream id -> the sandbox's connection
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
		r.streams = map[string]net.Conn{}
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
		return
	}
	_ = os.Chmod(socket, 0o600)
	r.listeners[socket] = listener
	go h.acceptMachine(listener, named)
}

func (h *Host) acceptMachine(listener net.Listener, named string) {
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		id, err := h.machine.add(conn)
		if err != nil {
			_ = conn.Close()
			continue
		}
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
			_ = h.conn.Send(link.MachineEnd(id))
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

// machineFrame applies one machine.* frame the platform sent down.
func (h *Host) machineFrame(m link.Msg) {
	conn := h.machine.get(m.ID)
	if conn == nil {
		return
	}
	switch m.T {
	case "machine.data":
		data, err := base64.StdEncoding.DecodeString(m.Data)
		if err == nil {
			_, err = conn.Write(data)
		}
		if err != nil && h.machine.drop(m.ID) {
			_ = h.conn.Send(link.MachineClose(m.ID, err))
		}
	case "machine.close":
		h.machine.drop(m.ID)
	}
}

func (r *machineRelay) add(conn net.Conn) (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	id := "machine-" + hex.EncodeToString(raw)
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.streams) >= machineStreamLimit {
		return "", errMachineBusy
	}
	r.streams[id] = conn
	return id, nil
}

func (r *machineRelay) get(id string) net.Conn {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.streams[id]
}

// drop closes and forgets a stream, reporting whether it was still open.
func (r *machineRelay) drop(id string) bool {
	r.mu.Lock()
	conn, ok := r.streams[id]
	delete(r.streams, id)
	r.mu.Unlock()
	if ok {
		_ = conn.Close()
	}
	return ok
}
