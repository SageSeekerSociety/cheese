//go:build darwin

package sysproxy

// scutilPath is where the command lives on macOS. An absolute path, not PATH:
// the connector is launched by launchd, whose PATH is bare, and by the desktop
// app, which supplies its own.
const scutilPath = "/usr/sbin/scutil"

// readSystemProxy reads the proxy SystemConfiguration holds for the network
// this Mac is on — the setting the Network pane's Proxies tab writes, and the
// one the browser that opened this enrollment is already using.
//
// It is not where curl looks. libcurl calls SCDynamicStoreCopyProxies only so
// that IPv4 literals become IPv6 ones, and says in lib/macos.c that it "does
// not use any value this function might return". So on a Mac whose only way out
// is the proxy — a campus network, typically — the page loaded and the
// connector dialled straight into a wall, which is the failure the Windows
// reader beside this one was added for.
//
// Read per request rather than once at startup, as the Windows reader is: this
// setting follows the active network service, so joining a different network
// changes it and the next reconnect should follow.
func readSystemProxy() (servers map[string]string, override string, enabled bool) {
	return scutilProxy(scutilPath)
}
