from testbed.trace import extract_assistant_turns


def test_extracts_text_and_tool_parts():
    payload = {
        "messages": [
            {
                "info": {
                    "id": "m1",
                    "role": "assistant",
                    "time": {"created": 1.0, "completed": 2.5},
                    "tokens": {"input": 10, "output": 20},
                },
                "parts": [
                    {"type": "text", "text": "Looking at the file..."},
                    {
                        "type": "tool",
                        "tool": "bash",
                        "callID": "c1",
                        "state": {
                            "status": "completed",
                            "started": 1.1,
                            "completed": 1.4,
                            "input": {"cmd": "ls"},
                            "output": "a.py",
                        },
                    },
                ],
            },
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": "hi"}]},
        ]
    }
    turns = extract_assistant_turns(payload)
    assert len(turns) == 1
    t = turns[0]
    assert t.message_id == "m1"
    assert t.created_at == 1.0 and t.completed_at == 2.5
    assert t.tokens == {"input": 10, "output": 20}
    assert "Looking at the file..." in t.text
    assert len(t.tool_calls) == 1
    tc = t.tool_calls[0]
    assert tc.tool == "bash" and tc.call_id == "c1"
    assert tc.started_at == 1.1 and tc.completed_at == 1.4


def test_handles_message_list_directly():
    msgs = [{"info": {"role": "assistant", "time": {}}, "parts": []}]
    assert len(extract_assistant_turns(msgs)) == 1


def test_ignores_unknown_shapes():
    assert extract_assistant_turns(None) == []
    assert extract_assistant_turns(123) == []
