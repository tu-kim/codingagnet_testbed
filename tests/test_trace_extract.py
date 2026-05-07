from testbed.trace import extract_assistant_turns, extract_user_messages


def _payload():
    return {
        "messages": [
            {
                "info": {
                    "id": "u1",
                    "role": "user",
                    "time": {"created": 0.5},
                    "agent": "build",
                    "model": {"providerID": "local", "modelID": "Qwen/Qwen3-32B"},
                    "system": "You are a coding agent.",
                    "tools": {"bash": True, "edit": True, "webfetch": False},
                },
                "parts": [{"type": "text", "text": "Fix the bug in foo.py"}],
            },
            {
                "info": {
                    "id": "m1",
                    "role": "assistant",
                    "time": {"created": 1.0, "completed": 2.5},
                    "modelID": "Qwen/Qwen3-32B",
                    "providerID": "local",
                    "mode": "build",
                    "cost": 0.0123,
                    "finish": "tool_use",
                    "tokens": {
                        "input": 10, "output": 20, "reasoning": 5,
                        "cache": {"read": 100, "write": 50},
                    },
                },
                "parts": [
                    {"type": "text", "text": "Looking at the file..."},
                    {"type": "reasoning", "text": "I should run ls first."},
                    {
                        "type": "tool",
                        "tool": "bash",
                        "callID": "c1",
                        "state": {
                            "status": "completed",
                            "title": "ls -la",
                            "input": {"command": "ls -la", "description": "list files"},
                            "output": "a.py\nb.py",
                            "time": {"start": 1.1, "end": 1.4},
                            "metadata": {"exit_code": 0},
                        },
                    },
                    {
                        "type": "step-finish",
                        "reason": "tool_calls",
                        "cost": 0.005,
                        "tokens": {
                            "input": 8, "output": 12, "reasoning": 5,
                            "cache": {"read": 100, "write": 50},
                        },
                    },
                    {"type": "patch", "hash": "abc", "files": ["foo.py", "bar.py"]},
                    {"type": "retry", "attempt": 1, "error": {"name": "ApiError"}},
                ],
            },
        ]
    }


def test_assistant_turn_captures_full_metadata():
    turns = extract_assistant_turns(_payload())
    assert len(turns) == 1
    t = turns[0]
    assert t.model_id == "Qwen/Qwen3-32B"
    assert t.provider_id == "local"
    assert t.mode == "build"
    assert t.cost == 0.0123
    assert t.finish == "tool_use"
    assert t.tokens["cache"]["read"] == 100
    assert "Looking at the file..." in t.text
    assert t.reasoning_text == "I should run ls first."
    assert t.retries == 1
    assert t.patched_files == ["foo.py", "bar.py"]
    assert len(t.steps) == 1
    assert t.steps[0].reason == "tool_calls"
    assert t.steps[0].cost == 0.005


def test_tool_call_captures_command_and_title():
    turns = extract_assistant_turns(_payload())
    tc = turns[0].tool_calls[0]
    assert tc.tool == "bash"
    assert tc.call_id == "c1"
    assert tc.title == "ls -la"
    assert tc.status == "completed"
    assert tc.started_at == 1.1 and tc.completed_at == 1.4
    assert tc.input == {"command": "ls -la", "description": "list files"}
    assert tc.output == "a.py\nb.py"
    assert tc.metadata == {"exit_code": 0}


def test_user_message_captures_system_and_tools():
    users = extract_user_messages(_payload())
    assert len(users) == 1
    u = users[0]
    assert u.message_id == "u1"
    assert u.created_at == 0.5
    assert u.agent == "build"
    assert u.model_id == "Qwen/Qwen3-32B"
    assert u.provider_id == "local"
    assert u.system == "You are a coding agent."
    assert u.tools == {"bash": True, "edit": True, "webfetch": False}
    assert u.text == "Fix the bug in foo.py"


def test_handles_message_list_directly():
    msgs = [{"info": {"role": "assistant", "time": {}}, "parts": []}]
    assert len(extract_assistant_turns(msgs)) == 1


def test_ignores_unknown_shapes():
    assert extract_assistant_turns(None) == []
    assert extract_assistant_turns(123) == []
    assert extract_user_messages(None) == []
