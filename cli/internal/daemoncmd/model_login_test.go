package daemoncmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/config"
)

func TestModelLoginDoesNotEnrollADevice(t *testing.T) {
	for _, existing := range []bool{false, true} {
		t.Run(map[bool]string{false: "without enrollment", true: "existing profile"}[existing], func(t *testing.T) {
			t.Setenv("HOME", t.TempDir())
			t.Setenv("USERPROFILE", t.TempDir())
			t.Setenv("XDG_CONFIG_HOME", t.TempDir())
			var requests []string
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, r.URL.Path)
				var body map[string]string
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["ticket"] != "team-authorization" {
					t.Errorf("ticket = %q", body["ticket"])
				}
				wantToken := ""
				if existing {
					wantToken = "current-profile"
				}
				if body["transport_token"] != wantToken {
					t.Errorf("transport = %q", body["transport_token"])
				}
				json.NewEncoder(w).Encode(registeredModelLogin{LoginID: "new-independent-login", Token: "current-profile", DeviceID: "computer", CredentialHost: !existing})
			}))
			defer server.Close()
			path, _ := config.ProfilePath("research")
			if existing {
				if err := config.Save(path, &config.Config{Base: server.URL, Token: "current-profile", DeviceID: "computer"}); err != nil {
					t.Fatal(err)
				}
			}
			original := path
			id, err := registerModelLogin(&path, server.URL, "team-authorization")
			if err != nil {
				t.Fatal(err)
			}
			if path != original || id != "new-independent-login" {
				t.Fatalf("profile %s login %s", path, id)
			}
			if len(requests) != 1 || requests[0] != "/model-login/register" {
				t.Fatalf("requests = %v", requests)
			}
			cfg, err := config.Load(path)
			if err != nil || cfg.Base != server.URL || cfg.DeviceID != "computer" {
				t.Fatalf("saved config = %+v, %v", cfg, err)
			}
		})
	}
}
