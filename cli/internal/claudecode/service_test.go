package claudecode

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

var glm = ModelService{BaseURL: "https://open.bigmodel.cn/api/anthropic", Token: "secret", Model: "glm-4.6"}

func TestAModelServiceIsKeptForTheSessionsAndOnlyItsOwnerCanReadIt(t *testing.T) {
	dir := t.TempDir()
	if err := SaveModelService(dir, glm); err != nil {
		t.Fatal(err)
	}
	got, err := LoadModelService(dir)
	if err != nil || got == nil || *got != glm {
		t.Fatalf("loaded %+v, %v", got, err)
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(filepath.Join(dir, serviceFile))
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm()&0o077 != 0 {
			t.Fatalf("others can read the key: %v", info.Mode().Perm())
		}
	}
}

func TestAServiceThatCannotBeCalledIsNotSaved(t *testing.T) {
	for name, s := range map[string]ModelService{
		"no scheme":  {BaseURL: "open.bigmodel.cn", Token: "k", Model: "glm-4.6"},
		"no key":     {BaseURL: glm.BaseURL, Model: "glm-4.6"},
		"no model":   {BaseURL: glm.BaseURL, Token: "k"},
		"two models": {BaseURL: glm.BaseURL, Token: "k", Model: "glm-4.6 glm-4.5"},
	} {
		dir := t.TempDir()
		if err := SaveModelService(dir, s); err == nil {
			t.Errorf("%s: saved", name)
		}
		if got, _ := LoadModelService(dir); got != nil {
			t.Errorf("%s: a service is in use: %+v", name, got)
		}
	}
}

func TestForgettingTheServiceGoesBackToTheLogin(t *testing.T) {
	dir := t.TempDir()
	if err := SaveModelService(dir, glm); err != nil {
		t.Fatal(err)
	}
	if err := ForgetModelService(dir); err != nil {
		t.Fatal(err)
	}
	if got, err := LoadModelService(dir); err != nil || got != nil {
		t.Fatalf("after forgetting: %+v, %v", got, err)
	}
	if err := ForgetModelService(dir); err != nil {
		t.Fatalf("forgetting twice: %v", err)
	}
}
