package devenv

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// pinPythonPath writes the ._pth beside the runtime's Python DLL, so that
// Python builds sys.path from it alone. Without one, the embeddable Python
// also reads HKCU\Software\Python\PythonCore\<version>\PythonPath, where an
// Anaconda install registers its own Lib and DLLs: on two dev PCs that put
// Anaconda's _ssl.pyd ahead of the runtime's, `import ssl` failed, and every
// executor install died on "unknown url type: https". The file also lists
// Lib\site-packages and enables `import site`, so what pip installs into the
// runtime is still found.
//
// It runs on every start, not only when the runtime is unpacked: a runtime
// already on disk is never unpacked again while its download is current.
func pinPythonPath(dir string) error {
	dlls, _ := filepath.Glob(filepath.Join(dir, "python3*.dll"))
	var dll string
	for _, candidate := range dlls {
		// python3.dll is the stable-ABI shim; the versioned one names the ._pth.
		if !strings.EqualFold(filepath.Base(candidate), "python3.dll") {
			dll = candidate
		}
	}
	if dll == "" {
		return fmt.Errorf("no versioned python DLL in %s", dir)
	}
	stem := strings.TrimSuffix(filepath.Base(dll), filepath.Ext(dll))
	want := strings.Join([]string{
		stem + ".zip",
		".",
		"Lib",
		`Lib\site-packages`,
		"import site",
		"",
	}, "\r\n")
	pth := filepath.Join(dir, stem+"._pth")
	if have, err := os.ReadFile(pth); err == nil && string(have) == want {
		return nil
	}
	return os.WriteFile(pth, []byte(want), 0o600)
}
