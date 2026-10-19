package config

import (
	"path/filepath"
	"testing"
)

func TestMachineSleepPreferenceDefaultsOnAndPersistsOff(t *testing.T) {
	path := filepath.Join(t.TempDir(), "machine.json")
	m, err := LoadMachine(path)
	if err != nil || !m.PreventSleep {
		t.Fatalf("default: %+v %v", m, err)
	}
	if err := SaveMachine(path, Machine{PreventSleep: false}); err != nil {
		t.Fatal(err)
	}
	m, err = LoadMachine(path)
	if err != nil || m.PreventSleep {
		t.Fatalf("saved: %+v %v", m, err)
	}
}
