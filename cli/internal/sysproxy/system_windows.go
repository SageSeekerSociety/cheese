//go:build windows

package sysproxy

import "golang.org/x/sys/windows/registry"

// The key the Windows proxy setting lives in: the one the Settings app writes
// and browsers read. Per user, because the connector runs as the person who
// enrolled the machine and never asks for administrator rights.
const internetSettings = `Software\Microsoft\Windows\CurrentVersion\Internet Settings`

// readSystemProxy reads the manual proxy setting. A PAC script
// (AutoConfigURL) is not read: it needs a JavaScript engine to evaluate, and
// the connector has no way to run one. A machine configured only that way is
// left to its environment, as it was before.
func readSystemProxy() (servers map[string]string, override string, enabled bool) {
	key, err := registry.OpenKey(registry.CURRENT_USER, internetSettings, registry.QUERY_VALUE)
	if err != nil {
		return nil, "", false
	}
	defer key.Close()
	if value, _, err := key.GetIntegerValue("ProxyEnable"); err == nil {
		enabled = value != 0
	}
	if !enabled {
		return nil, "", false
	}
	server, _, err := key.GetStringValue("ProxyServer")
	if err != nil || server == "" {
		return nil, "", false
	}
	// The override list is optional; without it nothing is bypassed.
	override, _, _ = key.GetStringValue("ProxyOverride")
	return parseServers(server), override, true
}
