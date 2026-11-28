package daemoncmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/config"
)

// A subscription added with `cheesehost claude login --server … --ticket …` is
// usable only while this machine's connector runs, and on a server that login
// is made over ssh. systemd tears a user's services down with their last
// session unless the user lingers, so the ticketed login has to leave linger
// on, and a machine that refuses it still gets its login (with a warning)
// rather than losing the subscription it just authorised.
//
// loginctl here is a stand-in that keeps one user's linger setting in a file,
// as logind keeps it in /var/lib/systemd/linger: what is asserted is the
// setting it ends with, not how it was asked.
func TestTicketedModelLoginKeepsTheConnectorAfterLogout(t *testing.T) {
	for _, refused := range []bool{false, true} {
		name := map[bool]string{false: "linger allowed", true: "linger refused"}[refused]
		t.Run(name, func(t *testing.T) {
			t.Setenv("HOME", t.TempDir())
			t.Setenv("XDG_CONFIG_HOME", t.TempDir())
			bin := t.TempDir()
			lingers := filepath.Join(bin, "linger")
			enable := `touch "` + lingers + `"`
			if refused {
				enable = `echo "Access denied" >&2; exit 1`
			}
			script := "#!/bin/sh\ncase \"$1\" in\n" +
				"enable-linger) " + enable + " ;;\n" +
				"show-user) if [ -e \"" + lingers + "\" ]; then echo Linger=yes; else echo Linger=no; fi ;;\n" +
				"esac\n"
			if err := os.WriteFile(filepath.Join(bin, "loginctl"), []byte(script), 0o755); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))

			original := startConnector
			startConnector = func(string, string) error { return nil }
			t.Cleanup(func() { startConnector = original })

			var mu sync.Mutex
			var requests []string
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				requests = append(requests, r.URL.Path)
				mu.Unlock()
				switch r.URL.Path {
				case "/model-login/register":
					json.NewEncoder(w).Encode(registeredModelLogin{LoginID: "login", Token: "token", DeviceID: "computer", CredentialHost: true})
				case "/model-login/refresh":
					json.NewEncoder(w).Encode(map[string]bool{"refreshed": true})
				default:
					http.NotFound(w, r)
				}
			}))
			defer server.Close()

			path, err := config.ProfilePath("research")
			if err != nil {
				t.Fatal(err)
			}
			if _, err := registerModelLogin(&path, server.URL, "team-authorization"); err != nil {
				t.Fatal(err)
			}
			if err := connectModelLogin(path); err != nil {
				t.Fatalf("the login failed: %v", err)
			}
			_, err = os.Stat(lingers)
			if !refused && err != nil {
				t.Fatal("the login left linger off: the connector stops when the ssh session that made it closes")
			}
			mu.Lock()
			defer mu.Unlock()
			if len(requests) != 2 || requests[1] != "/model-login/refresh" {
				t.Fatalf("the login did not finish with the server: requests = %v", requests)
			}
		})
	}
}
