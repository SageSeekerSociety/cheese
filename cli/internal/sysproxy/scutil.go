package sysproxy

import (
	"os/exec"
	"strings"
)

// scutilProxy asks the `scutil` at command for this Mac's proxy and reads its
// answer. It sits outside the darwin build with the parser below, because a
// test tagged darwin would never run in CI — and the invocation is half of what
// this reader has to get right: a command that is missing, or that fails,
// means "no proxy", which is the direct connection the connector had before.
//
// command is passed in rather than fixed here so a test can hand it a stand-in;
// the darwin file names the real one.
func scutilProxy(command string) (servers map[string]string, override string, enabled bool) {
	out, err := exec.Command(command, "--proxy").Output()
	if err != nil {
		return nil, "", false
	}
	return parseScutilProxy(string(out))
}

// parseScutilProxy reads the dictionary `scutil --proxy` prints: the proxy
// SystemConfiguration holds for the network this Mac is on, which is the one
// the Network pane's Proxies tab writes and browsers honour.
//
// It sits outside the darwin build on purpose. The file that runs the command
// is darwin-only, so a test tagged the same way would never run in CI — and the
// parsing is where the mistakes are: the key names, the nested bypass list, and
// which of the enabled proxies answers for which scheme.
//
// The shape is one key per line, with the bypass list nested:
//
//	<dictionary> {
//	  ExceptionsList : <array> {
//	    0 : *.local
//	    1 : 169.254/16
//	  }
//	  ExcludeSimpleHostnames : 0
//	  HTTPEnable : 1
//	  HTTPPort : 7890
//	  HTTPProxy : 127.0.0.1
//	  HTTPSEnable : 1
//	  HTTPSPort : 7890
//	  HTTPSProxy : 127.0.0.1
//	  SOCKSEnable : 0
//	}
//
// A PAC script (ProxyAutoConfigEnable, ProxyAutoConfigURLString) is not read,
// for the reason the Windows reader skips AutoConfigURL: evaluating it needs a
// JavaScript engine, and the connector has none.
func parseScutilProxy(out string) (servers map[string]string, override string, enabled bool) {
	values := map[string]string{}
	var exceptions []string
	inExceptions := false
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if inExceptions {
			if line == "}" {
				inExceptions = false
				continue
			}
			// One entry per line, indexed: `0 : *.local`. The index is not
			// kept — the bypass list is an order-free set.
			if _, entry, ok := strings.Cut(line, ":"); ok {
				if entry = unquoted(strings.TrimSpace(entry)); entry != "" {
					exceptions = append(exceptions, entry)
				}
			}
			continue
		}
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if key == "ExceptionsList" {
			if value == "<array> {" {
				inExceptions = true
			}
			continue
		}
		values[key] = unquoted(value)
	}

	// Each enabled proxy becomes one `scheme=host:port` entry, which is the
	// shape parseServers already reads and the one systemProxyFor selects from.
	// A scheme left out here falls back the way it does on Windows: both web
	// schemes reach the http entry, and a socks-only machine still expects its
	// web traffic to go through the proxy.
	var entries []string
	entry := func(scheme, host, port string) {
		if host == "" {
			return
		}
		if port != "" {
			host += ":" + port
		}
		entries = append(entries, scheme+"="+host)
	}
	if values["HTTPEnable"] == "1" {
		entry("http", values["HTTPProxy"], values["HTTPPort"])
	}
	if values["HTTPSEnable"] == "1" {
		entry("https", values["HTTPSProxy"], values["HTTPSPort"])
	}
	if values["SOCKSEnable"] == "1" {
		entry("socks", values["SOCKSProxy"], values["SOCKSPort"])
	}
	if len(entries) == 0 {
		return nil, "", false
	}
	// The bypass list is what the pane shows as "Bypass proxy settings for
	// these hosts". ExcludeSimpleHostnames is its separate checkbox for names
	// without a dot, which is what `<local>` means to the matcher.
	if values["ExcludeSimpleHostnames"] == "1" {
		exceptions = append(exceptions, "<local>")
	}
	return parseServers(strings.Join(entries, ";")), strings.Join(exceptions, ";"), true
}

// unquoted strips the quotes scutil adds around a value it would otherwise
// print ambiguously. An address like 127.0.0.1 prints bare; this is for the
// ones that do not.
func unquoted(s string) string {
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		return s[1 : len(s)-1]
	}
	return s
}
