from testbed.swebench import Sample, render_prompt


def test_render_prompt_includes_repo_and_commit():
    s = Sample(
        instance_id="x__y-1",
        repo="x/y",
        base_commit="deadbeef",
        problem_statement="Bug X happens.",
        hints_text="",
    )
    out = render_prompt(s)
    assert "x/y" in out
    assert "deadbeef" in out
    assert "Bug X happens." in out
    assert "Hints" not in out  # empty hints suppressed


def test_render_prompt_includes_hints_when_present():
    s = Sample(
        instance_id="x__y-2",
        repo="x/y",
        base_commit="c0ffee",
        problem_statement="P",
        hints_text="Try Z.",
    )
    out = render_prompt(s)
    assert "Hints" in out
    assert "Try Z." in out
