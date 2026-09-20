package link

import "encoding/base64"

// The two frames the connector writes back for one execution.call.
//
// They live here beside Msg, not inline at the call site in
// cli/internal/host/executor.go, because nothing at this seam imports anything
// across it: the only thing holding the Go writer and the backend reader
// together is backend/tests/fixtures/wire/execution-{data,result}.json, and a
// fixture can only hold code that actually builds the frame. Built inline, the
// fixture check in frames_test.go would be a second hand-written copy of the
// construction, agreeing with the fixture while the shipped one drifts.

// ExecutionData carries one chunk of the executor's answer: base64 of the raw
// bytes read off the executor socket.
func ExecutionData(id string, payload []byte) Msg {
	return Msg{
		T:    "execution.data",
		ID:   id,
		Data: base64.StdEncoding.EncodeToString(payload),
	}
}

// ExecutionResult ends one call. A nil err leaves Error empty and omitempty
// drops the key, which is how the backend tells "the machine failed" from "the
// call finished".
func ExecutionResult(id string, err error) Msg {
	m := Msg{T: "execution.result", ID: id}
	if err != nil {
		m.Error = err.Error()
	}
	return m
}
