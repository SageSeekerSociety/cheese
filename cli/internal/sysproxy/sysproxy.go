// Package sysproxy points the connector's outbound requests at the proxy the
// person has already configured on this computer.
//
// Go's http.ProxyFromEnvironment — what http.DefaultTransport and gorilla's
// DefaultDialer both use — reads only HTTP_PROXY / HTTPS_PROXY / NO_PROXY from
// the process environment. On Windows that is not where the setting lives: a
// proxy client (Clash, v2rayN and the like) writes it under
// HKCU\…\Internet Settings, where browsers and other programs that look there
// find it. The connector is a command-line program, so it dialled straight
// out, and on a network that only reaches the server through that proxy — a
// campus network, typically — the browser worked while the machine stayed
// offline, retrying silently.
//
// So the environment stays in charge where it says anything (a shell or
// service given HTTP_PROXY keeps behaving exactly as Go always made it), and
// only when it says nothing does the system's own setting apply. That answer
// is read per request rather than once at startup: a person switches proxy
// modes while the connector keeps running, and the next reconnect should
// follow the switch.
package sysproxy

import (
	"net"
	"net/http"
	"net/url"
	"os"
	"path"
	"strings"

	"github.com/gorilla/websocket"
)

// systemProxy answers with the proxy this computer is configured to use: the
// server entries by scheme, the override list, and whether it is switched on.
// A package variable so the decision below can be tested without a registry.
var systemProxy = readSystemProxy

// Install lays the system proxy under the connector's own network: every
// request http.DefaultTransport makes (the runtime it fetches, its
// self-update, the server's API) and the dialer the control link uses.
//
// It does nothing when the environment names a proxy at all. That setting is
// deliberate — someone put it in the service's environment or the shell's —
// and Go already reads it; replacing it would make the connector behave
// differently from the command the person was told to run.
func Install() {
	if proxyConfiguredInEnvironment() {
		return
	}
	if transport, ok := http.DefaultTransport.(*http.Transport); ok {
		transport.Proxy = systemProxyFor
	}
	websocket.DefaultDialer.Proxy = systemProxyFor
}

// proxyConfiguredInEnvironment reports whether any of the variables Go's own
// proxy lookup reads — HTTP_PROXY, HTTPS_PROXY, NO_PROXY, in either case — is
// set. NO_PROXY counts: a machine that names what must go direct has thought
// about proxies, and quietly routing it through the system's would be the
// opposite of what it asked for. ALL_PROXY is deliberately absent: Go ignores
// it, so counting it would both misstate what decides the answer and leave a
// machine whose only variable is ALL_PROXY still dialling past its proxy.
func proxyConfiguredInEnvironment() bool {
	for _, name := range []string{
		"HTTP_PROXY", "http_proxy",
		"HTTPS_PROXY", "https_proxy",
		"NO_PROXY", "no_proxy",
	} {
		if os.Getenv(name) != "" {
			return true
		}
	}
	return false
}

// systemProxyFor is the http.Proxy function Install gives the transport and
// the dialer. It answers nil, nil — "connect directly" — whenever the system
// names no proxy, the host is in the override list, or nothing is configured
// for the request's scheme.
func systemProxyFor(req *http.Request) (*url.URL, error) {
	if req == nil || req.URL == nil {
		return nil, nil
	}
	servers, override, enabled := systemProxy()
	if !enabled {
		return nil, nil
	}
	host := req.URL.Hostname()
	if host == "" || isLoopback(host) || bypassed(override, host) {
		return nil, nil
	}
	scheme := req.URL.Scheme
	switch scheme {
	case "ws":
		scheme = "http"
	case "wss":
		scheme = "https"
	}
	address := servers[scheme]
	if address == "" {
		// Windows keeps one entry under "http" for both schemes unless the
		// person set them apart, which is the ordinary shape.
		address = servers["http"]
	}
	if address == "" {
		// A machine configured with only a socks entry still expects the proxy
		// to carry its web traffic.
		address = servers["socks5"]
	}
	if address == "" {
		return nil, nil
	}
	return url.Parse(address)
}

// parseServers reads the ProxyServer value: either one `host:port` for
// everything, or `scheme=host:port` entries separated by semicolons. Each
// answer comes back as a full URL, ready for url.URL.
//
// Both `http=` and `https=` entries become `http://` URLs: the name says which
// requests the entry is for, not that the connector speaks TLS to the proxy
// itself — an https:// proxy URL would make the transport open a TLS session
// to it, which is not what any of these clients mean.
func parseServers(raw string) map[string]string {
	servers := map[string]string{}
	for _, part := range strings.Split(raw, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		scheme, address := "http", part
		if name, value, ok := strings.Cut(part, "="); ok {
			scheme = strings.ToLower(strings.TrimSpace(name))
			address = strings.TrimSpace(value)
		}
		if address == "" {
			continue
		}
		switch scheme {
		case "http", "https":
			servers[scheme] = "http://" + address
		case "socks", "socks5":
			servers["socks5"] = "socks5://" + address
		default:
			// ftp and anything else the connector never speaks.
			continue
		}
	}
	return servers
}

// isLoopback is the bypass Go's own environment lookup applies without being
// asked: a request to this machine never goes through a proxy, however the
// desktop is configured. Without it, `cheese api` against its default
// http://localhost:8080 base would leave for the proxy on a Windows machine
// that has one, and a local dev server would answer nothing.
func isLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// bypassed reports whether the override list — the ProxyOverride value, the
// box Windows shows as "bypass the proxy for these" — covers host. `<local>`
// means names without a dot; anything else is a shell pattern, so
// `*.example.com` covers its subdomains and `example.com` only itself.
func bypassed(override, host string) bool {
	host = strings.ToLower(host)
	for _, entry := range strings.Split(override, ";") {
		entry = strings.ToLower(strings.TrimSpace(entry))
		if entry == "" {
			continue
		}
		if entry == "<local>" {
			if !strings.Contains(host, ".") {
				return true
			}
			continue
		}
		entry = strings.TrimPrefix(entry, ".")
		if ok, _ := path.Match(entry, host); ok {
			return true
		}
	}
	return false
}
