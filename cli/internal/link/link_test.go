package link

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// The backend starts its silence clock only after it has heard a heartbeat
// (`DeviceHub.silence_allowed`). A machine that slept right after dialling in,
// before the first 15s tick, was therefore never timed out: calls to it waited
// for the socket to close. The connector says it heartbeats as soon as the link
// is up, not at the first tick.
func TestRunSaysItHeartbeatsRightAfterHello(t *testing.T) {
	frames := make(chan string, 4)
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				return
			}
			var m Msg
			if json.Unmarshal(data, &m) == nil {
				frames <- m.T
			}
		}
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	conn := New(strings.Replace(server.URL, "http", "ws", 1), "token", "", "")
	go func() { _ = conn.Run(ctx, func(Msg) {}) }()

	for _, want := range []string{"hello", "heartbeat"} {
		select {
		case got := <-frames:
			if got != want {
				t.Fatalf("frame order: got %q where %q was due", got, want)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("no %q frame within 2s", want)
		}
	}
}
