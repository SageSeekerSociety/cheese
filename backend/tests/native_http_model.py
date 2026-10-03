"""Finite model tool continuations; never hold an HTTP response for an echo."""

import json
import time


def continue_until_echo(machine, contract, screen, target_path):
    started = time.monotonic()
    count = 0
    verified = False
    trace = machine.root / "http-model-boundaries.jsonl"

    def continuation(body):
        nonlocal count, verified
        count += 1
        assert count <= 96 and time.monotonic() - started < 60, (
            "The correction echo did not reach a bounded model tool boundary"
        )
        native = screen()
        assert native is not None
        status = native.call("ping")
        records = native.records()
        target = json.loads(target_path.read_text()) if target_path.exists() else None
        echoes = [
            entry
            for entry in records
            if entry["record"].get("type") == "user"
            and entry["record"].get("cheese", {}).get("receipt")
        ]
        exact = [
            entry
            for entry in echoes
            if target and entry["record"].get("uuid") == target["input_id"]
        ]
        with trace.open("a") as output:
            output.write(
                json.dumps(
                    {
                        "boundary": count,
                        "working": status["working"],
                        "work_id": status.get("work_id"),
                        "target": target,
                        "echoes": echoes,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if exact:
            assert len(exact) == 1
            record = exact[0]["record"]
            stamp = record["cheese"]
            assert record.get("isReplay") is True
            assert record["session_id"] == target["native_session_id"]
            assert stamp["agent_handle"] == target["recipient_handle"]
            assert stamp["receipt_work_id"] == target["work_id"]
            assert stamp["receipt_execution_work_id"] == target["work_id"]
            assert "更正" in json.dumps(body["messages"], ensure_ascii=False)
            if verified:
                assert "HTTP_CORRECTION_CONTINUED" in json.dumps(body["messages"])
                return None
            verified = True
            return {
                "name": "Bash",
                "input": {
                    "command": "printf HTTP_CORRECTION_CONTINUED",
                    "description": "continue the original answerer's correction",
                },
            }
        assert not verified, "The target echo disappeared from retained records"
        return {
            "name": "Bash",
            "input": {
                "command": "sleep 0.2; printf HTTP_BUSY_CONTINUE",
                "description": "bounded continuation before correction echo",
            },
        }

    class Actions(contract.Directives):
        def __getitem__(self, index):
            return contract.directive if index == 0 else continuation

    machine.server.state["actions"] = Actions()
    return trace
