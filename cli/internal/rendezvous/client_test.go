package rendezvous

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeSession stands in for the rendezvous server a real Claude Code binds. It
// speaks the same shape: one connection at a time, newline-delimited JSON, auth
// by a frame carrying `role`, and refusals as `auth-rejected`/`reply-rejected`.
type fakeSession struct {
	path  string
	token string

	ln net.Listener

	mu       sync.Mutex
	received []Frame

	// rejectReplies makes the session refuse every reply, the way an un-authed
	// connection is refused.
	rejectReplies bool
}

func newFakeSession(t *testing.T, token string) *fakeSession {
	t.Helper()
	// Socket paths are capped near 104 bytes on macOS; t.TempDir() under
	// /var/folders/... plus a name can exceed it, which fails as a confusing
	// "invalid argument" at bind. Keep the name short and the dir shallow.
	dir, err := os.MkdirTemp("", "rv")
	if err != nil {
		t.Fatalf("tempdir: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })

	s := &fakeSession{path: filepath.Join(dir, "s.sock"), token: token}
	return s
}

func (s *fakeSession) start(t *testing.T) {
	t.Helper()
	ln, err := net.Listen("unix", s.path)
	if err != nil {
		t.Fatalf("listen %s: %v", s.path, err)
	}
	s.ln = ln
	t.Cleanup(func() { ln.Close() })
	go s.accept()
}

func (s *fakeSession) accept() {
	for {
		conn, err := s.ln.Accept()
		if err != nil {
			return
		}
		go s.serve(conn)
	}
}

func (s *fakeSession) serve(conn net.Conn) {
	defer conn.Close()
	sc := bufio.NewScanner(conn)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		var f Frame
		if err := json.Unmarshal(sc.Bytes(), &f); err != nil {
			continue
		}
		s.mu.Lock()
		s.received = append(s.received, f)
		reject := s.rejectReplies
		s.mu.Unlock()

		if f.Role != "" {
			if f.Auth != s.token {
				fmt.Fprintf(conn, "%s\n", mustJSON(Frame{Type: TypeAuthRejected}))
			}
			continue
		}
		if f.Type == TypeReply && reject {
			fmt.Fprintf(conn, "%s\n", mustJSON(Frame{Type: TypeReplyRejected}))
		}
	}
}

func (s *fakeSession) frames() []Frame {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Frame, len(s.received))
	copy(out, s.received)
	return out
}

func (s *fakeSession) replies() []string {
	var out []string
	for _, f := range s.frames() {
		if f.Type == TypeReply {
			out = append(out, f.Text)
		}
	}
	return out
}

func mustJSON(f Frame) string {
	b, err := json.Marshal(f)
	if err != nil {
		panic(err)
	}
	return string(b)
}

func dialTest(t *testing.T, s *fakeSession, token string) *Client {
	t.Helper()
	c, err := Dial(context.Background(), s.path, token, Options{
		WaitForSocket: 3 * time.Second,
		Logf:          func(f string, a ...any) { t.Logf("client: "+f, a...) },
	})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { c.Close() })
	return c
}

func TestReplyReachesSession(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")

	if err := c.Reply("hello"); err != nil {
		t.Fatalf("reply: %v", err)
	}
	got := s.replies()
	if len(got) != 1 || got[0] != "hello" {
		t.Fatalf("session received %#v, want one %q", got, "hello")
	}
	frames := s.frames()
	if frames[0].Role == "" || frames[0].Auth != "tok" {
		t.Fatalf("first frame was not the handshake: %#v", frames[0])
	}
}

// The delivery failure that started all this was a CJK prompt in a narrow pane.
// Nothing here can care about pane width, but the payload must survive byte for
// byte — including the newlines that a bracketed paste used to have to protect.
func TestChinesePromptSurvivesVerbatim(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")

	prompt := "【平台】以下是平台自动发出的指令，不是任何人手打的话：\n" +
		"1. 先发开场白：一两句复述你理解的任务\n" +
		"2. 把活文档改写成你自己的状态摘要（目标/约束/下一步）\n" +
		"引号 \" 反斜杠 \\ 制表 \t 都要原样通过"
	if err := c.Reply(prompt); err != nil {
		t.Fatalf("reply: %v", err)
	}
	got := s.replies()
	if len(got) != 1 {
		t.Fatalf("want 1 reply, got %d", len(got))
	}
	if got[0] != prompt {
		t.Fatalf("payload mutated in transit:\n got %q\nwant %q", got[0], prompt)
	}
}

// tmux send-keys had to chunk anything long or the command was rejected
// outright (upstream T-058). A socket has no such limit; prove it at a size
// well past the old 4096-byte chunk.
func TestLongPromptArrivesWhole(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")

	prompt := strings.Repeat("测试内容 abcdefghij ", 3000) // ~60KB
	if err := c.Reply(prompt); err != nil {
		t.Fatalf("reply: %v", err)
	}
	got := s.replies()
	if len(got) != 1 || got[0] != prompt {
		t.Fatalf("long prompt did not arrive intact (got %d replies, %d bytes)",
			len(got), len(got[0]))
	}
}

func TestWrongTokenIsRefusedAtDial(t *testing.T) {
	s := newFakeSession(t, "right")
	s.start(t)

	_, err := Dial(context.Background(), s.path, "wrong", Options{WaitForSocket: 2 * time.Second})
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("dial with a bad token returned %v, want ErrRejected", err)
	}
}

func TestRejectedReplyIsReportedNotSwallowed(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.rejectReplies = true
	s.start(t)
	c := dialTest(t, s, "tok")

	err := c.Reply("anything")
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("Reply returned %v, want ErrRejected", err)
	}
}

// A refusal for an earlier frame must not be charged to the next Reply — that
// would report a delivered prompt as failed and trigger a duplicate re-send.
func TestStaleRejectionDoesNotPoisonNextReply(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")

	c.rejects <- TypeReplyRejected // a refusal nobody was waiting for
	if err := c.Reply("fresh"); err != nil {
		t.Fatalf("Reply inherited a stale rejection: %v", err)
	}
}

func TestDialWaitsForALateSocket(t *testing.T) {
	s := newFakeSession(t, "tok")
	go func() {
		time.Sleep(400 * time.Millisecond) // claude is still booting
		s.start(t)
	}()

	start := time.Now()
	c, err := Dial(context.Background(), s.path, "tok", Options{WaitForSocket: 5 * time.Second})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer c.Close()
	if time.Since(start) < 300*time.Millisecond {
		t.Fatalf("dial returned before the socket could exist")
	}
	if err := c.Reply("after boot"); err != nil {
		t.Fatalf("reply after late bind: %v", err)
	}
}

func TestDialGivesUpWithAClearError(t *testing.T) {
	dir, err := os.MkdirTemp("", "rv")
	if err != nil {
		t.Fatalf("tempdir: %v", err)
	}
	defer os.RemoveAll(dir)

	_, err = Dial(context.Background(), filepath.Join(dir, "never.sock"), "tok",
		Options{WaitForSocket: 300 * time.Millisecond})
	if err == nil {
		t.Fatal("dial against a socket that never appears must fail")
	}
	if !strings.Contains(err.Error(), "did not appear") {
		t.Fatalf("error should say the socket never appeared, got: %v", err)
	}
}

func TestReplyAfterCloseIsAnError(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")
	c.Close()

	if err := c.Reply("too late"); !errors.Is(err, ErrClosed) {
		t.Fatalf("Reply after Close returned %v, want ErrClosed", err)
	}
}

// Concurrent writers must never interleave halves of two JSON objects into one
// line — the session would drop both and neither prompt would ever arrive.
func TestConcurrentRepliesStayWellFormed(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")

	// Kept small on purpose: Reply is serialized and each one costs a reject
	// window, so this is n × 400ms of wall clock. A dozen is plenty to catch an
	// interleave — the bug it guards against corrupts the FIRST overlap.
	const n = 12
	var wg sync.WaitGroup
	for i := range n {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			// Each payload is long enough that an interleave would be visible.
			_ = c.Reply(fmt.Sprintf("%03d:%s", i, strings.Repeat("字", 500)))
		}(i)
	}
	wg.Wait()
	time.Sleep(200 * time.Millisecond)

	got := s.replies()
	if len(got) != n {
		t.Fatalf("session decoded %d replies, want %d (a short count means frames interleaved)", len(got), n)
	}
	seen := map[string]bool{}
	for _, r := range got {
		if len(r) < 4 {
			t.Fatalf("truncated reply: %q", r)
		}
		seen[r[:3]] = true
	}
	if len(seen) != n {
		t.Fatalf("got %d distinct prompts, want %d", len(seen), n)
	}
}

func TestHeartbeatKeepsClientAlive(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)
	c := dialTest(t, s, "tok")

	if !c.Alive() {
		t.Fatal("a freshly dialled client should be alive")
	}
	c.Close()
	if c.Alive() {
		t.Fatal("a closed client must not report alive")
	}
}

// Every frame the session pushes must reach the host, which is how the platform
// learns about state changes and shutdowns without polling.
func TestOutboundFramesReachTheCallback(t *testing.T) {
	s := newFakeSession(t, "tok")
	s.start(t)

	var mu sync.Mutex
	var got []string
	c, err := Dial(context.Background(), s.path, "tok", Options{
		WaitForSocket: 2 * time.Second,
		OnFrame: func(f Frame) {
			mu.Lock()
			got = append(got, f.Type)
			mu.Unlock()
		},
	})
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer c.Close()

	// Push frames from the session side by opening a second connection is not
	// possible (single-client server), so drive the fake's writer directly.
	s.mu.Lock()
	s.rejectReplies = true
	s.mu.Unlock()
	_ = c.Reply("provoke a rejection")

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := len(got)
		mu.Unlock()
		if n > 0 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(got) == 0 || got[0] != TypeReplyRejected {
		t.Fatalf("OnFrame saw %#v, want a %s first", got, TypeReplyRejected)
	}
}
