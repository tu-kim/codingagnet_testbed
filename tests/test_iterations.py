from testbed.iterations import build_iteration_summary, build_iteration_transcript


def _payload():
    return [
        {
            "info": {
                "id": "u1",
                "role": "user",
                "time": {"created": 100.0},
                "system": "You are a coding agent.",
                "tools": {"bash": True, "edit": True},
                "model": {"providerID": "local", "modelID": "Qwen3-32B"},
            },
            "parts": [{"type": "text", "text": "Fix bug X"}],
        },
        {
            "info": {
                "id": "m1",
                "role": "assistant",
                "sessionID": "ses_1",
                "time": {"created": 101.0, "completed": 110.0},
            },
            "parts": [
                # iteration 1
                {"type": "step-start", "id": "ss1"},
                {"type": "reasoning", "text": "I should run ls.",
                 "time": {"start": 101.1, "end": 101.5}},
                {"type": "text", "text": "Running ls.",
                 "time": {"start": 101.5, "end": 101.8}},
                {"type": "tool", "tool": "bash", "callID": "c1",
                 "state": {"status": "completed",
                           "input": {"command": "ls", "description": "list"},
                           "output": "a.py b.py",
                           "time": {"start": 101.8, "end": 102.2}}},
                {"type": "step-finish", "id": "sf1", "reason": "tool_calls",
                 "cost": 0.005,
                 "tokens": {"input": 100, "output": 20, "reasoning": 10,
                            "cache": {"read": 50, "write": 30}}},
                # iteration 2
                {"type": "step-start", "id": "ss2"},
                {"type": "text", "text": "Patching foo.py.",
                 "time": {"start": 102.5, "end": 103.0}},
                {"type": "tool", "tool": "edit", "callID": "c2",
                 "state": {"status": "completed",
                           "input": {"path": "foo.py", "patch": "..."},
                           "output": "ok",
                           "time": {"start": 103.0, "end": 103.4}}},
                {"type": "step-finish", "id": "sf2", "reason": "stop",
                 "cost": 0.004,
                 "tokens": {"input": 150, "output": 12, "reasoning": 0,
                            "cache": {"read": 100, "write": 0}}},
            ],
        },
    ]


def test_summary_has_two_iterations_with_tokens_and_durations():
    out = build_iteration_summary(_payload(), {"instance_id": "x__y-1"})
    assert out["sample"]["instance_id"] == "x__y-1"
    assert out["session_id"] == "ses_1"
    assert len(out["iterations"]) == 2

    it1 = out["iterations"][0]
    assert it1["iteration_index"] == 0
    assert it1["request_id"] == "ss1"
    assert it1["session_id"] == "ses_1"
    assert it1["input"]["token_count"] == 100
    # First step sees just system + user.
    assert it1["input"]["roles"] == ["system", "user"]
    # total_latency_ms is now at iteration level (= completed - started).
    assert abs(it1["total_latency_ms"] - (102.2 - 101.1)) < 1e-9
    assert it1["output"]["cache_tokens"] == {"read": 50, "write": 30}
    items = it1["output"]["items"]
    # one text entry + one tool entry
    assert items[0]["type"] == "text"
    assert items[0]["output_tokens"] == 20
    assert "reasoning_tokens" not in items[0]
    # llm duration is text+reasoning range
    assert abs(items[0]["llm_duration_ms"] - (101.8 - 101.1)) < 1e-9
    assert items[1]["type"] == "tool" and items[1]["tool"] == "bash"
    assert abs(items[1]["tool_duration_ms"] - 0.4) < 1e-9


def test_summary_input_roles_grow_over_iterations():
    out = build_iteration_summary(_payload())
    # step 2 sees system,user + assistant + tool (one tool from step 1)
    assert out["iterations"][1]["input"]["roles"] == [
        "system", "user", "assistant", "tool"
    ]


def test_transcript_emits_input_messages_and_output_items():
    out = build_iteration_transcript(_payload(), {"instance_id": "x__y-1"})
    assert out["sample"]["instance_id"] == "x__y-1"
    its = out["iterations"]
    assert len(its) == 2

    # Iteration 1 input has system + user only
    in1 = its[0]["input"]["messages"]
    assert in1[0] == {"role": "system", "text": "You are a coding agent."}
    assert in1[1] == {"role": "user", "text": "Fix bug X"}

    # Iteration 1 output: reason → text → tool
    items = its[0]["output"]["items"]
    assert items[0] == {"type": "reason", "text": "I should run ls."}
    assert items[1] == {"type": "text", "text": "Running ls."}
    assert items[2]["type"] == "tool"
    assert items[2]["tool"] == "bash"
    assert items[2]["input"] == {"command": "ls", "description": "list"}
    assert items[2]["output"] == "a.py b.py"

    # Iteration 2 input grows by assistant emission + tool result
    in2 = its[1]["input"]["messages"]
    assert {"role": "assistant", "text": "Running ls."} in in2
    assert {"role": "tool", "text": "a.py b.py"} in in2
