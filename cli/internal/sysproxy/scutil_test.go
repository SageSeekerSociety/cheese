package sysproxy

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The dictionary `scutil --proxy` prints on a Mac whose only way out is the
// proxy — the campus-network case this reader exists for. Copied from the
// command's own output, including the nested bypass array.
const scutilThroughAProxy = `<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
  }
  ExcludeSimpleHostnames : 0
  FTPPassive : 1
  HTTPEnable : 1
  HTTPPort : 7890
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 7890
  HTTPSProxy : 127.0.0.1
  ProxyAutoConfigEnable : 0
  ProxyAutoDiscoveryEnable : 0
  SOCKSEnable : 0
}`

// proxyFor answers the question the connector actually asks: for this Mac's
// settings, does a request to host go through the proxy, and which one. Every
// case below is judged by that, not by the shape of the parse.
func proxyFor(t *testing.T, settings string, rawURL string) string {
	t.Helper()
	servers, override, enabled := parseScutilProxy(settings)
	stubSystemProxy(t, servers, override, enabled)
	got, err := systemProxyFor(request(rawURL))
	if err != nil {
		t.Fatalf("systemProxyFor(%s): %v", rawURL, err)
	}
	if got == nil {
		return ""
	}
	return got.String()
}

func TestAMacWhoseOnlyWayOutIsTheProxySendsTheConnectorThroughIt(t *testing.T) {
	for _, target := range []string{
		"https://okcheese.com/connector/latest/darwin-arm64/cheesehost",
		"wss://okcheese.com/connector/agent",
		"http://okcheese.com/connector/install.sh",
	} {
		if got := proxyFor(t, scutilThroughAProxy, target); got != "http://127.0.0.1:7890" {
			t.Errorf("a request to %s = %q, want the proxy from System Settings", target, got)
		}
	}
}

// "Bypass proxy settings for these hosts" is part of the setting, not a detail:
// a host the person listed there must not be forced through the proxy.
func TestTheBypassListGoesDirect(t *testing.T) {
	if got := proxyFor(t, scutilThroughAProxy, "https://printer.local/"); got != "" {
		t.Errorf("a host in ExceptionsList = %q, want a direct connection", got)
	}
	if got := proxyFor(t, scutilThroughAProxy, "https://okcheese.com/"); got == "" {
		t.Error("a host outside ExceptionsList should still use the proxy")
	}
}

// The checkbox beside the bypass list, which the matcher knows as `<local>`.
func TestExcludeSimpleHostnamesIsHonoured(t *testing.T) {
	settings := `<dictionary> {
  ExcludeSimpleHostnames : 1
  HTTPEnable : 1
  HTTPPort : 7890
  HTTPProxy : 127.0.0.1
}`
	if got := proxyFor(t, settings, "https://intranet/"); got != "" {
		t.Errorf("a name without a dot = %q, want a direct connection", got)
	}
	if got := proxyFor(t, settings, "https://okcheese.com/"); got == "" {
		t.Error("a dotted name should still use the proxy")
	}
}

// A Mac with the proxy switched off — or with none set — dials directly, as it
// did before this reader existed.
func TestNoEnabledProxyMeansADirectConnection(t *testing.T) {
	for name, settings := range map[string]string{
		"every proxy off": `<dictionary> {
  HTTPEnable : 0
  HTTPSEnable : 0
  SOCKSEnable : 0
  HTTPProxy : 127.0.0.1
  HTTPPort : 7890
}`,
		"nothing set": `<dictionary> {
}`,
		"enabled but no address": `<dictionary> {
  HTTPEnable : 1
  HTTPPort : 7890
}`,
	} {
		if got := proxyFor(t, settings, "https://okcheese.com/"); got != "" {
			t.Errorf("%s = %q, want a direct connection", name, got)
		}
	}
}

// A PAC script needs a JavaScript engine to evaluate and the connector has
// none, as on Windows: such a Mac is left to its environment, exactly as it
// was before this reader existed.
func TestAPacOnlyMachineIsLeftToItsEnvironment(t *testing.T) {
	settings := `<dictionary> {
  ProxyAutoConfigEnable : 1
  ProxyAutoConfigURLString : http://wpad/wpad.dat
}`
	if got := proxyFor(t, settings, "https://okcheese.com/"); got != "" {
		t.Errorf("a PAC-only machine = %q, want a direct connection", got)
	}
}

// Only the schemes the person enabled are used, and the fallbacks match the
// Windows reader's: both web schemes reach the http entry, and a socks-only
// machine still expects its web traffic to go through the proxy.
func TestTheEnabledSchemePicksTheProxyAsOnWindows(t *testing.T) {
	oneEntryForBothSchemes := `<dictionary> {
  HTTPEnable : 1
  HTTPPort : 7890
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 0
  HTTPSProxy : 10.0.0.9
  HTTPSPort : 9999
}`
	if got := proxyFor(t, oneEntryForBothSchemes, "https://okcheese.com/"); got != "http://127.0.0.1:7890" {
		t.Errorf("https with only the http entry enabled = %q, want the http entry", got)
	}

	socksOnly := `<dictionary> {
  HTTPEnable : 0
  SOCKSEnable : 1
  SOCKSPort : 1080
  SOCKSProxy : 127.0.0.1
}`
	if got := proxyFor(t, socksOnly, "https://okcheese.com/"); got != "socks5://127.0.0.1:1080" {
		t.Errorf("socks-only = %q, want the socks entry", got)
	}
}

// A request to this machine never goes through a proxy, whatever the desktop
// is set to — the same rule the Windows reader follows.
func TestARequestToThisMachineStaysDirect(t *testing.T) {
	for _, target := range []string{
		"http://localhost:8080/api",
		"http://127.0.0.1:8080/api",
		"http://[::1]:8080/api",
	} {
		if got := proxyFor(t, scutilThroughAProxy, target); got != "" {
			t.Errorf("a request to %s = %q, want a direct connection", target, got)
		}
	}
}

// command writes a stand-in `scutil` to a temp directory and answers its path.
func command(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "scutil")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// The reader runs `scutil --proxy` and reads what it prints. This is the half
// of the fix a parser test cannot reach: the Windows reader shipped without it,
// so removing the wiring would have left every test green.
func TestTheReaderRunsScutilAndReadsItsAnswer(t *testing.T) {
	// Answers only the flag it is asked for, so `install` or no argument at all
	// gets nothing — which is what a wrong invocation would produce.
	path := command(t, `[ "$1" = "--proxy" ] || exit 1
echo `+shQuote(scutilThroughAProxy))

	servers, override, enabled := scutilProxy(path)
	if !enabled {
		t.Fatal("scutil reported a proxy, yet the reader found none")
	}
	if servers["http"] != "http://127.0.0.1:7890" || servers["https"] != "http://127.0.0.1:7890" {
		t.Errorf("servers = %v, want the addresses scutil printed", servers)
	}
	// The bypass list is a set, so ask what it does rather than what it reads:
	// the hosts scutil listed stay out, and one nobody listed still goes
	// through. Comparing the string would fail a reader that kept the same
	// entries in another order, which is not a defect.
	if !bypassed(override, "printer.local") {
		t.Errorf("printer.local is in ExceptionsList, yet %q does not bypass it", override)
	}
	if bypassed(override, "okcheese.com") {
		t.Errorf("okcheese.com is not in ExceptionsList, yet %q bypasses it", override)
	}
}

// Answering nothing is not an error: a Mac with no proxy set dials directly,
// which is the behaviour that must survive this change.
func TestACommandThatAnswersNothingMeansNoProxy(t *testing.T) {
	if _, _, enabled := scutilProxy(command(t, "exit 0")); enabled {
		t.Error("scutil printed nothing, yet the reader reported a proxy")
	}
}

// A command that cannot be run — missing, or failing — means "no proxy" rather
// than an error the connector's network path would have to handle. The failing
// stand-in still prints a whole dictionary, so this would report a proxy if the
// failure were not checked: an answer from a command that failed is not one.
func TestACommandThatFailsMeansNoProxy(t *testing.T) {
	if _, _, enabled := scutilProxy(filepath.Join(t.TempDir(), "absent")); enabled {
		t.Error("the command does not exist, yet the reader reported a proxy")
	}
	failing := command(t, `echo `+shQuote(scutilThroughAProxy)+`
exit 1`)
	if _, _, enabled := scutilProxy(failing); enabled {
		t.Error("the command failed, yet the reader took its output as a proxy")
	}
}

// shQuote wraps s in single quotes for the shell, so the fixture's braces and
// newlines survive being written into a script.
func shQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
