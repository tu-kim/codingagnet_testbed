from testbed.poisson import arrival_offsets


def test_arrival_offsets_monotonic_and_count():
    offs = arrival_offsets(100, qps=2.0, seed=1)
    assert len(offs) == 100
    assert all(b > a for a, b in zip(offs, offs[1:]))


def test_arrival_offsets_mean_close_to_inverse_qps():
    n, qps = 5000, 4.0
    offs = arrival_offsets(n, qps=qps, seed=7)
    inter = [b - a for a, b in zip([0.0, *offs[:-1]], offs)]
    mean = sum(inter) / len(inter)
    expected = 1.0 / qps
    # Loose bound; large-N should be within ±10%.
    assert 0.9 * expected < mean < 1.1 * expected


def test_arrival_offsets_seed_repeatable():
    a = arrival_offsets(50, qps=1.5, seed=42)
    b = arrival_offsets(50, qps=1.5, seed=42)
    assert a == b


def test_arrival_offsets_rejects_zero_qps():
    import pytest

    with pytest.raises(ValueError):
        arrival_offsets(10, qps=0.0, seed=1)
