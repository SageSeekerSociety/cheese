package host

import (
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

// A stream the server opens to an executor and keeps open: stream.open names
// the state directory, as an execution.call does, and the first line to
// write; from there the bytes go both ways as they come, which is what a
// person's terminal needs and a one-shot call cannot carry. What the bytes
// mean is the executor's and the server's business: this side relays them.
//
// Output is sent on credit. The server acks what it has passed on
// (stream.ack), and once streamWindow bytes are out unacked this side stops
// reading the executor's socket until it does: whatever floods the stream
// waits in the executor, not in the link every session on this machine
// shares. Bytes coming down are queued for the executor and written by the
// stream's own goroutine; an executor that stops reading loses its stream.

// streamWindow is how much output may be out unacknowledged on one stream.
const streamWindow = 256 * 1024

// streamLimit bounds the streams one connector holds at once.
const streamLimit = 64

// streamQueue bounds the frames waiting to be written to one executor.
const streamQueue = 64

var (
	errStreamBusy    = errors.New("too many streams on this machine")
	errStreamStalled = errors.New("the executor stopped reading its stream")
)

type streamRelay struct {
	mu      sync.Mutex
	streams map[string]*hostStream
}

type hostStream struct {
	down chan []byte
	done chan struct{}

	mu      sync.Mutex
	credit  *sync.Cond
	unacked int64
	conn    net.Conn // nil until the executor's socket is dialled
	closed  bool
}

func (s *hostStream) setConn(conn net.Conn) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return false
	}
	s.conn = conn
	return true
}

// wait returns once the window has room; false once the stream is closed.
func (s *hostStream) wait() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	for s.unacked >= streamWindow && !s.closed {
		s.credit.Wait()
	}
	return !s.closed
}

func (s *hostStream) spend(n int) {
	s.mu.Lock()
	s.unacked += int64(n)
	s.mu.Unlock()
}

func (s *hostStream) ack(n int64) {
	s.mu.Lock()
	s.unacked -= n
	if s.unacked < 0 {
		s.unacked = 0
	}
	s.mu.Unlock()
	s.credit.Broadcast()
}

// streamFrame applies one stream.* frame from the server. It runs on the
// link's read loop, so it only queues and starts goroutines.
func (h *Host) streamFrame(m link.Msg) {
	switch m.T {
	case "stream.open":
		stream, err := h.streams.add(m.ID)
		if err != nil {
			_ = h.conn.Send(link.StreamClose(m.ID, err))
			return
		}
		if stream != nil {
			go h.runStream(m, stream)
		}
	case "stream.data":
		stream := h.streams.get(m.ID)
		if stream == nil {
			return
		}
		data, err := base64.StdEncoding.DecodeString(m.Data)
		if err != nil {
			h.failStream(m.ID, err)
			return
		}
		select {
		case stream.down <- data:
		case <-stream.done:
		default:
			h.failStream(m.ID, errStreamStalled)
		}
	case "stream.ack":
		if stream := h.streams.get(m.ID); stream != nil {
			stream.ack(m.Bytes)
		}
	case "stream.close":
		h.streams.drop(m.ID)
	}
}

// runStream dials the executor, writes the stream's first line, and relays
// until either side ends it.
func (h *Host) runStream(m link.Msg, stream *hostStream) {
	resolved, err := resolveState(m.Path)
	if err != nil {
		h.failStream(m.ID, err)
		return
	}
	conn, err := dialExecutor(h.ctx, resolved)
	if err != nil {
		h.failStream(m.ID, err)
		return
	}
	if !stream.setConn(conn) {
		_ = conn.Close()
		return
	}
	if _, err := io.WriteString(conn, m.Header+"\n"); err != nil {
		h.failStream(m.ID, err)
		return
	}
	go h.feedStream(m.ID, conn, stream)
	buf := make([]byte, machineChunk)
	for stream.wait() {
		n, err := conn.Read(buf)
		if n > 0 {
			stream.spend(n)
			if h.conn.Send(link.StreamData(m.ID, buf[:n])) != nil {
				h.streams.drop(m.ID)
				return
			}
		}
		if err != nil {
			if errors.Is(err, io.EOF) {
				err = nil
			}
			if h.streams.drop(m.ID) {
				_ = h.conn.Send(link.StreamClose(m.ID, err))
			}
			return
		}
	}
}

// feedStream writes what the server sent down to the executor, in order.
func (h *Host) feedStream(id string, conn net.Conn, stream *hostStream) {
	for {
		select {
		case data := <-stream.down:
			if _, err := conn.Write(data); err != nil {
				h.failStream(id, err)
				return
			}
		case <-stream.done:
			return
		}
	}
}

func (h *Host) failStream(id string, err error) {
	if h.streams.drop(id) {
		_ = h.conn.Send(link.StreamClose(id, err))
	}
}

// add registers a stream; nil, nil when one by that id is open already.
func (r *streamRelay) add(id string) (*hostStream, error) {
	if id == "" {
		return nil, fmt.Errorf("a stream needs an id")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.streams == nil {
		r.streams = map[string]*hostStream{}
	}
	if _, open := r.streams[id]; open {
		return nil, nil
	}
	if len(r.streams) >= streamLimit {
		return nil, errStreamBusy
	}
	stream := &hostStream{down: make(chan []byte, streamQueue), done: make(chan struct{})}
	stream.credit = sync.NewCond(&stream.mu)
	r.streams[id] = stream
	return stream, nil
}

func (r *streamRelay) get(id string) *hostStream {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.streams[id]
}

// drop closes and forgets a stream, reporting whether it was still open.
func (r *streamRelay) drop(id string) bool {
	r.mu.Lock()
	stream, ok := r.streams[id]
	delete(r.streams, id)
	r.mu.Unlock()
	if !ok {
		return false
	}
	close(stream.done)
	stream.mu.Lock()
	stream.closed = true
	conn := stream.conn
	stream.mu.Unlock()
	stream.credit.Broadcast()
	if conn != nil {
		_ = conn.Close()
	}
	return true
}
