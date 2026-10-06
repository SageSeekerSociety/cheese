package devenv

import "os"

// foreignPython names the variables through which a Python installed by the
// machine's owner reaches every Python started under them. Once the
// connector's own Python is first on PATH, they point that Python at another
// version's standard library: on a Windows PC with Anaconda, PYTHONPATH put
// Anaconda's DLLs ahead of the runtime's own, `import ssl` loaded Anaconda's
// _ssl.pyd and failed, and the executor install could not open https.
var foreignPython = []string{"PYTHONPATH", "PYTHONHOME"}

// isolatePython drops foreignPython from this process's environment, which
// every command the connector runs inherits.
func isolatePython() {
	for _, name := range foreignPython {
		os.Unsetenv(name)
	}
}
