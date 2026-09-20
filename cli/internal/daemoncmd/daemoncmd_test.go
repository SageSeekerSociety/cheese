package daemoncmd

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"testing"
)

// After an uninstall the machine is the machine its owner lent us, with nothing
// of ours on it. That is what `cheese uninstall` promises in words, and it used
// to mean only the connector's own config and binary: everything the backend
// had written over the link — session homes, worktrees, the shared package
// store, the executor and its helpers — stayed behind under the footprint root,
// on a machine that no longer had anything installed that could find it.
func TestUninstallLeavesNothingBehind(t *testing.T) {
	home := t.TempDir()
	configDir := filepath.Join(t.TempDir(), "cheese")

	// What a machine that has run rooms actually holds, spelled as the launcher
	// writes it rather than as a single directory, so the assertion below is
	// about the whole footprint and not about one path we remembered.
	written := []string{
		filepath.Join(home, ".cheese", "home", "project", "room", ".claude", "settings.json"),
		filepath.Join(home, ".cheese", "work", "project", "room", "README.md"),
		filepath.Join(home, ".cheese", "store", "project", "uv", "package"),
		filepath.Join(home, ".cheese", "launch", "room.sh"),
		filepath.Join(home, ".cheese", "executor-releases", "abc", "runtime.py"),
		filepath.Join(home, ".cheese", "cheese-tunnel.token"),
		filepath.Join(configDir, "config.json"),
	}
	for _, path := range written {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// Something of the owner's, next to ours: an uninstall that took this too
	// would pass every assertion below and be a far worse bug than the one this
	// test is here for.
	theirs := filepath.Join(home, "notes.txt")
	if err := os.WriteFile(theirs, []byte("mine"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := removeFootprint(configDir, home); err != nil {
		t.Fatalf("removeFootprint: %v", err)
	}

	entries, err := os.ReadDir(home)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.Name() != filepath.Base(theirs) {
			t.Errorf("uninstall left %s behind in the home directory", entry.Name())
		}
	}
	if _, err := os.Stat(configDir); !os.IsNotExist(err) {
		t.Errorf("uninstall left the config directory behind: %v", err)
	}
	if _, err := os.Stat(theirs); err != nil {
		t.Errorf("uninstall removed something that was not ours: %v", err)
	}
}

// The test above proves removeFootprint clears a machine; this one proves that
// `cheese uninstall` is what calls it, and calls it on the path it always takes.
//
// It reads the source rather than running the command, because running it is not
// something a test can be allowed to do. uninstallCmd stops and uninstalls the
// connector's service through kardianos, which resolves the launchd plist from
// user.Current() and not from $HOME — no temporary home redirects it, so on any
// machine that has the connector installed the run would uninstall it for real.
// The same call kills the per-user tmux server (a stable socket, shared with
// whatever sessions are live) and then deletes its own executable. What is left
// to check without any of that is the wiring — and the wiring is the whole of
// what could rot: a removal that exists, is tested, and is called from nowhere
// leaves every machine exactly as it was before. Delete the call, or move it
// inside a branch that is not always taken, and this goes red.
func TestUninstallRemovesTheFootprintOnEveryRun(t *testing.T) {
	file, err := parser.ParseFile(token.NewFileSet(), "daemoncmd.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}

	var body *ast.BlockStmt
	ast.Inspect(file, func(node ast.Node) bool {
		function, ok := node.(*ast.FuncDecl)
		if !ok || function.Name.Name != "uninstallCmd" {
			return true
		}
		ast.Inspect(function, func(node ast.Node) bool {
			field, ok := node.(*ast.KeyValueExpr)
			if !ok {
				return true
			}
			key, isName := field.Key.(*ast.Ident)
			value, isFunction := field.Value.(*ast.FuncLit)
			if isName && isFunction && key.Name == "RunE" {
				body = value.Body
			}
			return true
		})
		return false
	})
	if body == nil {
		t.Fatal("uninstallCmd no longer has a RunE to read")
	}

	removes := func(node ast.Node) bool {
		found := false
		ast.Inspect(node, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			if name, ok := call.Fun.(*ast.Ident); ok && name.Name == "removeFootprint" {
				found = true
			}
			return !found
		})
		return found
	}

	for _, statement := range body.List {
		// Only what the statement itself evaluates. An `if` contributes its init
		// and its condition and never the block it guards, so a call that moved
		// into a branch stops counting as one every run makes.
		var evaluated []ast.Node
		switch statement := statement.(type) {
		case *ast.IfStmt:
			if statement.Init != nil {
				evaluated = append(evaluated, statement.Init)
			}
			evaluated = append(evaluated, statement.Cond)
		case *ast.ExprStmt:
			evaluated = append(evaluated, statement.X)
		case *ast.AssignStmt:
			for _, value := range statement.Rhs {
				evaluated = append(evaluated, value)
			}
		}
		for _, node := range evaluated {
			if removes(node) {
				return
			}
		}
	}
	t.Error("`cheese uninstall` no longer removes the footprint root on every run")
}
