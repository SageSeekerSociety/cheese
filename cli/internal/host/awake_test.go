package host

import (
	"context"
	"fmt"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestSleepWorkReadIsDeviceAuthenticated(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/connector/device/running-sessions" || r.Header.Get("Authorization") != "Bearer device-token" {
			w.WriteHeader(401)
			return
		}
		_, _ = w.Write([]byte(`{"sessions":[{},{}]}`))
	}))
	defer srv.Close()
	n, err := runningSessions(context.Background(), srv.Client(), srv.URL+"/connector", "device-token")
	if err != nil || n != 2 {
		t.Fatalf("work count: %d %v", n, err)
	}
	if _, err := runningSessions(context.Background(), srv.Client(), srv.URL+"/connector", "wrong"); err == nil {
		t.Fatal("unauthorized read accepted")
	}
}

func TestConnectorTracksWorkAndReloadsMachinePreference(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("AppData", t.TempDir())
	var count atomic.Int32
	count.Store(1)
	read := make(chan struct{}, 64)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer token" {
			w.WriteHeader(401)
			return
		}
		sessions := ""
		for n := int32(0); n < count.Load(); n++ {
			if n > 0 {
				sessions += ","
			}
			sessions += "{}"
		}
		fmt.Fprintf(w, `{"sessions":[%s]}`, sessions)
		select {
		case read <- struct{}{}:
		default:
		}
	}))
	defer srv.Close()
	acquired, released := make(chan struct{}, 16), make(chan struct{}, 16)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	h := &Host{base: srv.URL + "/connector", token: "token"}
	go func() {
		defer close(done)
		h.preventSleep(ctx, func() (func(), error) { acquired <- struct{}{}; return func() { released <- struct{}{} }, nil }, time.Millisecond)
	}()
	defer func() { cancel(); <-done }()
	wait := func(ch <-chan struct{}) {
		t.Helper()
		select {
		case <-ch:
		case <-time.After(time.Second):
			t.Fatal("sleep lifecycle did not advance")
		}
	}
	wait(acquired)
	count.Store(2)
	for i := 0; i < 3; i++ {
		wait(read)
	}
	select {
	case <-released:
		t.Fatal("released with work remaining")
	default:
	}
	select {
	case <-acquired:
		t.Fatal("acquired twice with work remaining")
	default:
	}
	count.Store(0)
	wait(released)
	count.Store(1)
	wait(acquired)
	if err := config.SaveMachine(config.MachinePath(), config.Machine{PreventSleep: false}); err != nil {
		t.Fatal(err)
	}
	wait(released)
	count.Store(3)
	select {
	case <-acquired:
		t.Fatal("acquired while disabled")
	case <-time.After(10 * time.Millisecond):
	}
}
