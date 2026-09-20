package link

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// The Go half of the executor wire contract. backend/tests/fixtures/wire/*.json
// holds one committed frame per file and the backend reads the same files from
// backend/tests/contract/test_wire_frames.py — the two sides never import each
// other, so a fixture both of them are held to is the only thing that keeps
// link.Msg and the backend's device_link.py from drifting apart in silence.
//
// Each frame is asked the two questions that matter at a seam: does Msg read
// the bytes the backend writes, and does Msg write the bytes the backend
// expects to read. Nothing here starts a process or opens a socket.

const fixtureDir = "../../../backend/tests/fixtures/wire"

type fixture struct {
	name    string
	Type    string         `json:"type"`
	Origin  string         `json:"origin"`
	Why     string         `json:"why"`
	Frame   map[string]any `json:"frame"`
	Meaning map[string]any `json:"meaning"`
}

func loadFixtures(t *testing.T) []fixture {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(fixtureDir, "*.json"))
	if err != nil {
		t.Fatalf("glob %s: %v", fixtureDir, err)
	}
	if len(paths) == 0 {
		t.Fatalf("no wire fixtures under %s — the contract has nothing to hold", fixtureDir)
	}
	out := make([]fixture, 0, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read %s: %v", path, err)
		}
		var f fixture
		if err := json.Unmarshal(data, &f); err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		f.name = filepath.Base(path)
		out = append(out, f)
	}
	return out
}

// A frame read into Msg and written back out must come out the same object.
// This is what a renamed or dropped json tag breaks: the field falls out on the
// way in and is missing on the way out.
func TestFixtureFramesSurviveMsg(t *testing.T) {
	for _, f := range loadFixtures(t) {
		t.Run(f.name, func(t *testing.T) {
			raw, err := json.Marshal(f.Frame)
			if err != nil {
				t.Fatalf("re-encode fixture: %v", err)
			}
			var m Msg
			if err := json.Unmarshal(raw, &m); err != nil {
				t.Fatalf("unmarshal into Msg: %v", err)
			}
			if m.T != f.Type {
				t.Fatalf("type: got %q, fixture says %q", m.T, f.Type)
			}
			assertSameFrame(t, m, f.Frame)
		})
	}
}

// The call the backend writes, read the way host.go and executor.go read it.
func TestExecutionCallIsReadAsAMethodOnOneStateDir(t *testing.T) {
	f := only(t, "execution.call")
	var m Msg
	decodeFrame(t, f.Frame, &m)

	if want := f.Meaning["call_id"].(string); m.ID != want {
		t.Errorf("id: got %q, want %q", m.ID, want)
	}
	if want := f.Meaning["state"].(string); m.Path != want {
		t.Errorf("path: got %q, want %q", m.Path, want)
	}
	if want := int(f.Meaning["timeout_seconds"].(float64)); m.Timeout != want {
		t.Errorf("timeout: got %d, want %d", m.Timeout, want)
	}

	// Stdin goes into the executor socket verbatim (executor.go), so what the
	// runner reads is part of this contract.
	var request struct {
		Method string         `json:"method"`
		Params map[string]any `json:"params"`
	}
	if err := json.Unmarshal([]byte(m.Stdin), &request); err != nil {
		t.Fatalf("stdin is not the runner's request: %v", err)
	}
	if want := f.Meaning["method"].(string); request.Method != want {
		t.Errorf("method: got %q, want %q", request.Method, want)
	}
	if want := f.Meaning["params"].(map[string]any); !reflect.DeepEqual(request.Params, want) {
		t.Errorf("params: got %v, want %v", request.Params, want)
	}
}

// The answers the connector writes, built here exactly as executor.go builds
// them.
func TestConnectorWritesTheAnswerFramesInTheFixtures(t *testing.T) {
	for _, f := range loadFixtures(t) {
		if f.Origin != "device" {
			continue
		}
		t.Run(f.name, func(t *testing.T) {
			id := f.Meaning["call_id"].(string)
			var built Msg
			switch f.Type {
			case "execution.data":
				payload := f.Meaning["payload"].(string)
				built = Msg{
					T:    "execution.data",
					ID:   id,
					Data: base64.StdEncoding.EncodeToString([]byte(payload)),
				}
			case "execution.result":
				built = Msg{T: "execution.result", ID: id}
				if reason := f.Meaning["error"].(string); reason != "" {
					built.Error = reason
				}
			default:
				t.Fatalf("no builder for %s", f.Type)
			}
			assertSameFrame(t, built, f.Frame)
		})
	}
}

// An empty error is not an error: omitempty drops the key, and the backend
// tells "the machine failed" from "the call finished" by whether it is there.
func TestACleanResultCarriesNoErrorKey(t *testing.T) {
	data, err := json.Marshal(Msg{T: "execution.result", ID: "execution-1"})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, present := out["error"]; present {
		t.Errorf("clean result carries an error key: %s", data)
	}
}

func assertSameFrame(t *testing.T, m Msg, want map[string]any) {
	t.Helper()
	data, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("marshal Msg: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal what Msg wrote: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("frame drifted\n Msg wrote: %v\n  fixture: %v", got, want)
	}
}

func decodeFrame(t *testing.T, frame map[string]any, m *Msg) {
	t.Helper()
	raw, err := json.Marshal(frame)
	if err != nil {
		t.Fatalf("re-encode fixture: %v", err)
	}
	if err := json.Unmarshal(raw, m); err != nil {
		t.Fatalf("unmarshal into Msg: %v", err)
	}
}

func only(t *testing.T, frameType string) fixture {
	t.Helper()
	var found []fixture
	for _, f := range loadFixtures(t) {
		if f.Type == frameType {
			found = append(found, f)
		}
	}
	if len(found) != 1 {
		t.Fatalf("%s: %d fixtures, want exactly one", frameType, len(found))
	}
	return found[0]
}
