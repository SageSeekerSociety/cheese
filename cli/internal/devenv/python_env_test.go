package devenv

import (
	"os"
	"testing"
)

func TestTheOwnersPythonDoesNotReachTheRuntime(t *testing.T) {
	t.Setenv("PYTHONPATH", `D:\anaconda3\Lib;D:\anaconda3\DLLs`)
	t.Setenv("PYTHONHOME", `D:\anaconda3`)
	t.Setenv("PYTHONUTF8", "1")

	isolatePython()

	for _, name := range []string{"PYTHONPATH", "PYTHONHOME"} {
		if value, set := os.LookupEnv(name); set {
			t.Errorf("%s is still %q", name, value)
		}
	}
	if os.Getenv("PYTHONUTF8") != "1" {
		t.Errorf("PYTHONUTF8 was dropped; only the variables that move the standard library go")
	}
}
