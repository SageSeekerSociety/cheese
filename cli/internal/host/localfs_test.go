// The glue between the wire and localfs. Value arrives untyped (the protocol is a
// flat union), so what is worth pinning here is that a payload survives the round
// trip into a typed request — including the base64 content of a write, which is the
// part that silently corrupts if the encoding is assumed rather than checked.
package host

import (
	"encoding/json"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/localfs"
)

// onTheWire renders a payload the way it actually arrives: through JSON, so that
// the numbers are float64 and the byte slices are base64 strings.
func onTheWire(t *testing.T, payload any) any {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return decoded
}

func TestDecodeValueGivesATypedOp(t *testing.T) {
	value := onTheWire(t, map[string]any{
		"kind":       "write",
		"path":       "/home/alice/MyDocs/out.txt",
		"project_id": "project-a",
		"data":       []byte("hello"),
	})
	var op localfs.Op
	if err := decodeValue(value, &op); err != nil {
		t.Fatalf("decodeValue: %v", err)
	}
	if op.Kind != localfs.OpWrite || op.ProjectID != "project-a" {
		t.Errorf("op = %+v", op)
	}
	// The content has to survive base64 intact — a truncated or re-encoded body is
	// a file silently written wrong.
	if string(op.Data) != "hello" {
		t.Errorf("data = %q, want hello", op.Data)
	}
}

func TestDecodeValueGivesATypedGrantSetPayload(t *testing.T) {
	value := onTheWire(t, map[string]any{
		"device_id":   "device-1",
		"fingerprint": "abc",
		"grants": []map[string]any{{
			"id":       "g1",
			"path":     "/home/alice/MyDocs",
			"platform": "linux",
			"mode":     "read",
			"scope":    "user",
		}},
	})
	var payload struct {
		DeviceID string          `json:"device_id"`
		Grants   []localfs.Grant `json:"grants"`
	}
	if err := decodeValue(value, &payload); err != nil {
		t.Fatalf("decodeValue: %v", err)
	}
	if payload.DeviceID != "device-1" || len(payload.Grants) != 1 {
		t.Fatalf("payload = %+v", payload)
	}
	set, err := localfs.NewGrantSet(payload.DeviceID, payload.Grants)
	if err != nil {
		t.Fatalf("NewGrantSet: %v", err)
	}
	// End to end from the wire to an actual decision: the set that arrived must
	// allow what it says and nothing else.
	allowed := localfs.Execute(set, localfs.Op{
		Kind: localfs.OpRead, Path: "/home/alice/MyDocs/grades.csv",
	})
	if !allowed.Allowed() {
		t.Errorf("the pushed grant did not allow its own directory: %s", allowed.Reason)
	}
	outside := localfs.Execute(set, localfs.Op{
		Kind: localfs.OpRead, Path: "/home/alice/Other/secret.csv",
	})
	if outside.Allowed() {
		t.Error("the pushed grant allowed a path outside it")
	}
}

func TestDecodeValueRefusesAnEmptyPayload(t *testing.T) {
	var op localfs.Op
	if err := decodeValue(nil, &op); err == nil {
		t.Fatal("an empty payload must be reported, not decoded into a zero request")
	}
}
