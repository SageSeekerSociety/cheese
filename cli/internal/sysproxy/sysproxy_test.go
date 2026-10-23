package sysproxy

import (
	"net/http"
	"net/url"
	"testing"
)

func TestParseServers(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want map[string]string
	}{
		{
			// The ordinary shape: one address, used for everything.
			name: "one address",
			raw:  "127.0.0.1:7890",
			want: map[string]string{"http": "http://127.0.0.1:7890"},
		},
		{
			name: "one per scheme",
			raw:  "http=127.0.0.1:7890;https=127.0.0.1:7891",
			want: map[string]string{
				"http":  "http://127.0.0.1:7890",
				"https": "http://127.0.0.1:7891",
			},
		},
		{
			name: "socks",
			raw:  "socks=127.0.0.1:1080",
			want: map[string]string{"socks5": "socks5://127.0.0.1:1080"},
		},
		{
			name: "spaces and a scheme the connector never speaks",
			raw:  " ftp=10.0.0.1:21 ; http = 127.0.0.1:7890 ",
			want: map[string]string{"http": "http://127.0.0.1:7890"},
		},
		{
			name: "nothing",
			raw:  " ;; ",
			want: map[string]string{},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := parseServers(c.raw)
			if len(got) != len(c.want) {
				t.Fatalf("parseServers(%q) = %v, want %v", c.raw, got, c.want)
			}
			for scheme, address := range c.want {
				if got[scheme] != address {
					t.Errorf("parseServers(%q)[%q] = %q, want %q", c.raw, scheme, got[scheme], address)
				}
			}
		})
	}
}

func TestBypassed(t *testing.T) {
	cases := []struct {
		override string
		host     string
		want     bool
	}{
		{"<local>", "intranet", true},
		{"<local>", "okcheese.com", false},
		{"*.example.com", "a.example.com", true},
		{"*.example.com", "example.com", false},
		{"example.com", "example.com", true},
		{"EXAMPLE.com", "example.COM", true},
		{"localhost;*.internal", "localhost", true},
		{"localhost;*.internal", "db.internal", true},
		{"localhost;*.internal", "okcheese.com", false},
		{"", "okcheese.com", false},
	}
	for _, c := range cases {
		if got := bypassed(c.override, c.host); got != c.want {
			t.Errorf("bypassed(%q, %q) = %v, want %v", c.override, c.host, got, c.want)
		}
	}
}

// stubSystemProxy hands systemProxyFor the settings a Windows machine would
// report, without a registry.
func stubSystemProxy(t *testing.T, servers map[string]string, override string, enabled bool) {
	t.Helper()
	previous := systemProxy
	systemProxy = func() (map[string]string, string, bool) {
		return servers, override, enabled
	}
	t.Cleanup(func() { systemProxy = previous })
}

func request(raw string) *http.Request {
	u, err := url.Parse(raw)
	if err != nil {
		panic(err)
	}
	return &http.Request{URL: u}
}

func TestSystemProxyFor(t *testing.T) {
	servers := parseServers("127.0.0.1:7890")

	t.Run("a campus network that only reaches the server through the proxy", func(t *testing.T) {
		stubSystemProxy(t, servers, "", true)
		for _, target := range []string{
			"https://okcheese.com/connector/agent",
			"wss://okcheese.com/connector/agent",
			"http://okcheese.com/connector/install.ps1",
			"ws://okcheese.com/connector/agent",
		} {
			got, err := systemProxyFor(request(target))
			if err != nil {
				t.Fatalf("systemProxyFor(%s): %v", target, err)
			}
			if got == nil || got.String() != "http://127.0.0.1:7890" {
				t.Errorf("systemProxyFor(%s) = %v, want the system proxy", target, got)
			}
		}
	})

	t.Run("a scheme with its own address", func(t *testing.T) {
		stubSystemProxy(t, parseServers("http=127.0.0.1:7890;https=127.0.0.1:7891"), "", true)
		got, _ := systemProxyFor(request("wss://okcheese.com/connector/agent"))
		if got == nil || got.String() != "http://127.0.0.1:7891" {
			t.Errorf("https request = %v, want the https entry", got)
		}
		got, _ = systemProxyFor(request("http://okcheese.com/"))
		if got == nil || got.String() != "http://127.0.0.1:7890" {
			t.Errorf("http request = %v, want the http entry", got)
		}
	})

	t.Run("turned off in the system settings", func(t *testing.T) {
		stubSystemProxy(t, servers, "", false)
		got, _ := systemProxyFor(request("https://okcheese.com/"))
		if got != nil {
			t.Errorf("disabled = %v, want a direct connection", got)
		}
	})

	t.Run("only a socks entry", func(t *testing.T) {
		stubSystemProxy(t, parseServers("socks=127.0.0.1:1080"), "", true)
		got, _ := systemProxyFor(request("https://okcheese.com/"))
		if got == nil || got.String() != "socks5://127.0.0.1:1080" {
			t.Errorf("https request = %v, want the socks entry", got)
		}
	})

	t.Run("a host the person set to go direct", func(t *testing.T) {
		stubSystemProxy(t, servers, "*.internal;<local>", true)
		got, _ := systemProxyFor(request("https://db.internal/"))
		if got != nil {
			t.Errorf("bypassed host = %v, want a direct connection", got)
		}
		got, _ = systemProxyFor(request("https://okcheese.com/"))
		if got == nil {
			t.Error("a host outside the override list should still use the proxy")
		}
	})
}

// The environment keeps the last word: a service that was given HTTP_PROXY
// must keep behaving as Go always made it, whatever the desktop is set to.
func TestEnvironmentIsInChargeWhenItSaysAnything(t *testing.T) {
	clearProxyEnvironment(t)
	if proxyConfiguredInEnvironment() {
		t.Fatal("no proxy variable is set, yet the environment claims to name one")
	}
	t.Setenv("HTTPS_PROXY", "http://10.0.0.1:8080")
	if !proxyConfiguredInEnvironment() {
		t.Fatal("HTTPS_PROXY is set, yet the environment claims to name none")
	}
}

func clearProxyEnvironment(t *testing.T) {
	t.Helper()
	for _, name := range []string{
		"HTTP_PROXY", "http_proxy",
		"HTTPS_PROXY", "https_proxy",
		"ALL_PROXY", "all_proxy",
		"NO_PROXY", "no_proxy",
	} {
		t.Setenv(name, "")
	}
}
