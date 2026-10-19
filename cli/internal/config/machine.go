package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

// Machine settings belong to the physical computer, shared by all profiles.
type Machine struct {
	PreventSleep bool `json:"prevent_sleep"`
}

func MachinePath() string { return filepath.Join(Dir(), "machine.json") }

func LoadMachine(path string) (Machine, error) {
	m := Machine{PreventSleep: true}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return m, nil
	}
	if err != nil {
		return m, err
	}
	err = json.Unmarshal(b, &m)
	return m, err
}

func SaveMachine(path string, m Machine) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	b, err := json.Marshal(m)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".machine-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err := f.Write(b); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}
