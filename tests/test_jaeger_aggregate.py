from testbed.jaeger import aggregate_worker_timing


def _trace(spans):
    return {
        "spans": [{"processID": pid, "duration": dur} for pid, dur in spans],
        "processes": {
            "p1": {"serviceName": "vllm-prefill-p0"},
            "p2": {"serviceName": "vllm-prefill-p1"},
            "d1": {"serviceName": "vllm-decode-d0"},
            "f1": {"serviceName": "dynamo-frontend"},
        },
    }


def test_aggregate_sums_per_role():
    t = _trace([("p1", 1000), ("p2", 500), ("d1", 2000), ("f1", 9999)])
    out = aggregate_worker_timing(t)
    assert out.prefill_us == 1500
    assert out.decode_us == 2000
    assert out.prefill_spans == 2
    assert out.decode_spans == 1


def test_aggregate_handles_missing_processes():
    t = {"spans": [{"processID": "missing", "duration": 10}], "processes": {}}
    out = aggregate_worker_timing(t)
    assert out.prefill_us == 0 and out.decode_us == 0


def test_aggregate_empty_trace():
    out = aggregate_worker_timing({})
    assert out.prefill_us == 0 and out.decode_us == 0
