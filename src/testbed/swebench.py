from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DATASET_NAME = {
    "lite": "princeton-nlp/SWE-bench_Lite",
    "verified": "princeton-nlp/SWE-bench_Verified",
    "full": "princeton-nlp/SWE-bench",
}


@dataclass
class Sample:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    version: str = ""


def load_samples(split: str, num_samples: int) -> list[Sample]:
    from datasets import load_dataset  # heavy import; lazy

    if split not in DATASET_NAME:
        raise ValueError(f"unknown split {split!r}; choose from {list(DATASET_NAME)}")
    ds = load_dataset(DATASET_NAME[split], split="test")
    out: list[Sample] = []
    for i in range(num_samples):
        row = ds[i % len(ds)]
        out.append(
            Sample(
                instance_id=row["instance_id"],
                repo=row["repo"],
                base_commit=row["base_commit"],
                problem_statement=row["problem_statement"],
                hints_text=row.get("hints_text", "") or "",
                version=row.get("version", "") or "",
            )
        )
    return out


def render_prompt(sample: Sample) -> str:
    parts = [
        f"You are working on the GitHub repository `{sample.repo}` "
        f"at commit `{sample.base_commit}`.",
        "",
        "## Problem statement",
        sample.problem_statement.strip(),
    ]
    if sample.hints_text.strip():
        parts += ["", "## Hints", sample.hints_text.strip()]
    parts += [
        "",
        "Investigate the repository and produce a patch that resolves the issue. "
        "Use available tools to read and edit files.",
    ]
    return "\n".join(parts)


def chunk(seq: Iterable[Sample], n: int) -> list[list[Sample]]:
    seq = list(seq)
    return [seq[i : i + n] for i in range(0, len(seq), n)]
