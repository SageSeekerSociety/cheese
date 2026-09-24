package host

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
	"github.com/gorilla/websocket"
)

// fakeServer is the control channel's other end: it accepts the host's dial-out
// websocket and lets a test push frames down it and read what comes back.
type fakeServer struct {
	mu sync.Mutex
	ws *websocket.Conn
	in chan link.Msg
}

func (f *fakeServer) send(t *testing.T, m link.Msg) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		f.mu.Lock()
		ws := f.ws
		f.mu.Unlock()
		if ws != nil {
			if err := ws.WriteJSON(m); err != nil {
				t.Fatalf("send %s: %v", m.T, err)
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("the host never connected")
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func hostOnAFakeServer(t *testing.T, sessions map[string]*sess) *fakeServer {
	t.Helper()
	f := &fakeServer{in: make(chan link.Msg, 16)}
	up := websocket.Upgrader{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		f.mu.Lock()
		f.ws = ws
		f.mu.Unlock()
		for {
			var m link.Msg
			if err := ws.ReadJSON(&m); err != nil {
				return
			}
			select {
			case f.in <- m:
			default:
			}
		}
	}))
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	h := &Host{
		conn:     link.New("ws"+strings.TrimPrefix(srv.URL, "http"), "", "", ""),
		ctx:      ctx,
		sessions: sessions,
		execs:    map[string]context.CancelFunc{},
	}
	go func() { _ = h.conn.Run(ctx, h.onMsg) }()
	return f
}
