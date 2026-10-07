package auth

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// A machine re-approves only when the server says its token names nothing; a
// server it cannot reach, or one that answers otherwise, leaves it as it was.
func TestOnlyTheServersRefusalForgetsAToken(t *testing.T) {
	known := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/connector/device/me" || r.Header.Get("X-Cheese-Session") != "kept" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Write([]byte(`{"device_id":"d1"}`))
	}))
	defer known.Close()
	failing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer failing.Close()
	gone := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	unreachable := gone.URL
	gone.Close()

	ctx := context.Background()
	if Forgotten(ctx, known.URL+"/connector/", "kept") {
		t.Fatal("a token the server still knows was forgotten")
	}
	if !Forgotten(ctx, known.URL+"/connector", "unbound") {
		t.Fatal("a token the server refused was kept")
	}
	if Forgotten(ctx, failing.URL+"/connector", "kept") {
		t.Fatal("a server error was taken for a refusal")
	}
	if Forgotten(ctx, unreachable+"/connector", "kept") {
		t.Fatal("an unreachable server was taken for a refusal")
	}
}
