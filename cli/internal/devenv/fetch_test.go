//go:build !windows

package devenv

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// A server that writes half an artifact and then holds the connection open
// without sending anything more — a stalled transfer, which is what a link cut
// mid-download looks like from here. The second request is the whole artifact.
func stallingServer(t *testing.T, archive []byte) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var asked atomic.Int32
	release := make(chan struct{})
	// Registered in this order so LIFO runs them the other way round: the
	// handler parked on release must be let go before srv.Close, which waits
	// for every outstanding request to finish.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		n := asked.Add(1)
		w.Header().Set("X-Checksum-SHA256", digest(archive))
		w.WriteHeader(http.StatusOK)
		if n == 1 {
			_, _ = w.Write(archive[:len(archive)/2])
			w.(http.Flusher).Flush()
			<-release
			return
		}
		_, _ = w.Write(archive)
	}))
	t.Cleanup(srv.Close)
	t.Cleanup(func() { close(release) })
	return srv, &asked
}

// A fetch that stops arriving is given up and tried again, rather than holding
// the machine's enrolment for ever — the failure a person watching cannot tell
// apart from a slow download.
func TestAStalledFetchIsGivenUpAndTriedAgain(t *testing.T) {
	home := machine(t, false)
	archive := pythonArchive(t, map[string]string{"python/bin/python3": placedPython})
	srv, asked := stallingServer(t, archive)

	saved := stallAfter
	stallAfter = 300 * time.Millisecond
	defer func() { stallAfter = saved }()

	if err := Ensure(context.Background(), srv.URL+"/connector", io.Discard); err != nil {
		t.Fatalf("a fetch that stalled once was not retried: %v", err)
	}
	if got := asked.Load(); got < 2 {
		t.Fatalf("the server was asked %d times, want a second attempt after the stall", got)
	}
	if _, err := os.Stat(filepath.Join(home, ".cheese", "runtime", "python", "bin", "python3")); err != nil {
		t.Fatalf("the runtime was not placed after the retry: %v", err)
	}
}

// An attempt that stalled leaves nothing of itself behind: the next attempt, or
// the next start of the connector, must not find a half archive to unpack.
func TestAStalledAttemptLeavesNoArchiveBehind(t *testing.T) {
	home := machine(t, false)
	archive := pythonArchive(t, map[string]string{"python/bin/python3": placedPython})
	srv, _ := stallingServer(t, archive)

	saved := stallAfter
	stallAfter = 300 * time.Millisecond
	defer func() { stallAfter = saved }()

	if err := Ensure(context.Background(), srv.URL+"/connector", io.Discard); err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	entries, err := os.ReadDir(filepath.Join(home, ".cheese", "runtime"))
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), "python-") {
			t.Errorf("a temp archive was left behind: %s", entry.Name())
		}
	}
}

// A download that keeps arriving, however slowly, is not cut off: the term is
// silence, not elapsed time.
func TestASlowFetchIsNotCutOff(t *testing.T) {
	home := machine(t, false)
	archive := pythonArchive(t, map[string]string{"python/bin/python3": placedPython})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("X-Checksum-SHA256", digest(archive))
		for _, b := range archive {
			_, _ = w.Write([]byte{b})
			w.(http.Flusher).Flush()
			time.Sleep(time.Millisecond)
		}
	}))
	t.Cleanup(srv.Close)

	saved := stallAfter
	stallAfter = 50 * time.Millisecond
	defer func() { stallAfter = saved }()

	began := time.Now()
	if err := Ensure(context.Background(), srv.URL+"/connector", io.Discard); err != nil {
		t.Fatalf("a slow fetch was given up on: %v", err)
	}
	if took := time.Since(began); took <= stallAfter*2 {
		// If the whole archive had arrived within a couple of stall terms this
		// test would pass without the watchdog ever being asked the question.
		t.Skipf("the archive arrived too fast (%s) for this to mean anything", took)
	}
	if _, err := os.Stat(filepath.Join(home, ".cheese", "runtime", "python", "bin", "python3")); err != nil {
		t.Fatalf("the slow download was not placed: %v", err)
	}
}
