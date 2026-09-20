package host

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
	"github.com/SageSeekerSociety/cheese/cli/internal/rendezvous"
	"github.com/gorilla/websocket"
)

func TestFirstAndCachedPromptsAreEachDeliveredOnce(t *testing.T) {
	dir, err := os.MkdirTemp("", "rv-host-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	path := filepath.Join(dir, "socket")
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	frames := make(chan rendezvous.Frame, 8)
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		conn.SetReadDeadline(time.Now().Add(5 * time.Second))
		scanner := bufio.NewScanner(conn)
		for scanner.Scan() {
			var frame rendezvous.Frame
			if json.Unmarshal(scanner.Bytes(), &frame) == nil {
				frames <- frame
			}
		}
	}()
	tokenFile := filepath.Join(dir, "token")
	if err := os.WriteFile(tokenFile, []byte("tok"), 0o600); err != nil {
		t.Fatal(err)
	}
	screen := &sess{rvPath: path, rvTokenFile: tokenFile}
	t.Cleanup(func() {
		screen.rvMu.Lock()
		defer screen.rvMu.Unlock()
		if screen.rv != nil {
			screen.rv.Close()
		}
	})
	server := hostOnAFakeServer(t, map[string]*sess{"s1": screen})
	for _, prompt := range []string{"first", "second"} {
		server.send(t, link.Msg{T: "rpc.call", Sid: "s1", ID: prompt,
			Name: "prompt", Args: []any{prompt}})
		if result := server.awaitResult(t, prompt); result.Error != "" {
			t.Fatal(result.Error)
		}
	}
	for i, text := range []string{"", "first", "second"} {
		select {
		case frame := <-frames:
			if frame.Text != text || (i == 0 && frame.Auth != "tok") {
				t.Fatalf("unexpected frame %d: %+v", i, frame)
			}
		case <-time.After(time.Second):
			t.Fatalf("missing frame %d", i)
		}
	}
	select {
	case frame := <-frames:
		t.Fatalf("duplicate delivery: %+v", frame)
	default:
	}
}

func TestWriteScreenFileIsAtomicAndConfinedToUploads(t *testing.T) {
	work := t.TempDir()
	raw := []byte("\x89PNG\r\n\x1a\nimage")
	encoded := base64.StdEncoding.EncodeToString(raw)

	if err := writeScreenFile(work, "uploads/img-a.png", encoded); err != nil {
		t.Fatalf("writeScreenFile: %v", err)
	}
	got, err := os.ReadFile(filepath.Join(work, "uploads", "img-a.png"))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(raw) {
		t.Fatalf("got %q, want %q", got, raw)
	}
	for _, bad := range []string{"../escape.png", "/absolute.png", "src/main.go"} {
		if err := writeScreenFile(work, bad, encoded); err == nil {
			t.Fatalf("unsafe path %q was accepted", bad)
		}
	}
}

// A call the screen cannot serve must come back as an error, never as silence.
// The server awaits an rpc.result for every prompt it sends; a dropped call
// leaves the turn hanging on a timeout that says nothing about what went wrong,
// which is the failure this delivery path was built to end.
func TestAnUnservableCallIsAnsweredWithAnError(t *testing.T) {
	cases := []struct {
		name    string
		call    string
		screen  *sess
		wantHas string
	}{
		{
			name:    "a call this build does not know",
			call:    "snapshot",
			screen:  &sess{rvPath: "/tmp/x.sock"},
			wantHas: `unknown call "snapshot"`,
		},
		{
			name:    "a prompt for a screen with no socket",
			call:    "prompt",
			screen:  &sess{},
			wantHas: envRvSock,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server := hostOnAFakeServer(t, map[string]*sess{"s1": tc.screen})
			server.send(t, link.Msg{T: "rpc.call", Sid: "s1", ID: "c1",
				Name: tc.call, Args: []any{"hello"}})

			got := server.awaitResult(t, "c1")
			if got.Error == "" {
				t.Fatalf("%s was answered as a success: %+v", tc.call, got)
			}
			if !strings.Contains(got.Error, tc.wantHas) {
				t.Fatalf("the error should name %q, got: %s", tc.wantHas, got.Error)
			}
		})
	}
}

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

func (f *fakeServer) awaitResult(t *testing.T, id string) link.Msg {
	t.Helper()
	for {
		select {
		case m := <-f.in:
			if m.T == "rpc.result" && m.ID == id {
				return m
			}
		case <-time.After(10 * time.Second):
			t.Fatalf("no rpc.result for %q", id)
		}
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
	}
	go func() { _ = h.conn.Run(ctx, h.onMsg) }()
	return f
}

func TestReadRvTokenTrimsAndReturns(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "tok")
	// The launcher writes with `od | tr`, which can leave a trailing newline;
	// a token compared byte-for-byte on the other side must not carry it.
	if err := os.WriteFile(p, []byte("  deadbeef\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := readRvToken(p)
	if err != nil {
		t.Fatalf("readRvToken: %v", err)
	}
	if got != "deadbeef" {
		t.Fatalf("got %q, want %q", got, "deadbeef")
	}
}

func TestReadRvTokenWaitsForALateFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "tok")
	go func() {
		time.Sleep(300 * time.Millisecond) // the launcher is still booting
		_ = os.WriteFile(p, []byte("late-token"), 0o600)
	}()

	old := rvTokenWait
	rvTokenWait = 5 * time.Second
	defer func() { rvTokenWait = old }()

	got, err := readRvToken(p)
	if err != nil {
		t.Fatalf("readRvToken: %v", err)
	}
	if got != "late-token" {
		t.Fatalf("got %q", got)
	}
}

func TestReadRvTokenFailsLoudly(t *testing.T) {
	old := rvTokenWait
	rvTokenWait = 300 * time.Millisecond
	defer func() { rvTokenWait = old }()

	if _, err := readRvToken(""); err == nil {
		t.Fatal("an unconfigured token file must be an error, not an empty token")
	} else if !strings.Contains(err.Error(), envRvTokenFile) {
		t.Fatalf("the error should name the missing env var, got: %v", err)
	}

	_, err := readRvToken(filepath.Join(t.TempDir(), "never"))
	if err == nil {
		t.Fatal("a token file that never appears must be an error")
	}
	if !strings.Contains(err.Error(), "never appeared") {
		t.Fatalf("unclear error: %v", err)
	}
}

// The token file is written on the launcher's way to exec'ing claude, and the
// socket is bound by the claude it then execs — so the token necessarily appears
// before the socket, and its wait window must be at least as long as the socket's.
// It was a sixth of it (20s vs 120s), so a first prompt for a cold screen gave up
// on the token long before it would have given up on the socket, and a new topic
// whose workspace was still coming up died at ~20s every time. This guards the
// ordering, not a specific number: raise the socket window and this fails until
// the token window follows.
func TestTheTokenWindowIsNeverShorterThanTheSocketWindow(t *testing.T) {
	if rvTokenWait < rvDialWindow {
		t.Fatalf("token wait %v is shorter than the socket wait %v: a cold "+
			"screen's first prompt will abandon the token before the socket",
			rvTokenWait, rvDialWindow)
	}
}

// An empty file is not a token: treating it as one would send an empty auth
// frame and the session would refuse every prompt with no obvious cause.
func TestReadRvTokenRejectsAnEmptyFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "tok")
	if err := os.WriteFile(p, []byte("   \n"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := rvTokenWait
	rvTokenWait = 300 * time.Millisecond
	defer func() { rvTokenWait = old }()

	if _, err := readRvToken(p); err == nil {
		t.Fatal("a whitespace-only token file must not pass as a token")
	}
}
