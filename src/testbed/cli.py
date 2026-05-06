from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from .config import Settings
from . import runner


@click.group()
def main() -> None:
    pass


@main.command()
@click.option("--split", default="lite", show_default=True)
@click.option("--num-samples", "num_samples", type=int, required=True)
@click.option("--qps", type=float, required=True)
@click.option("--seed", type=int, default=42, show_default=True)
@click.option("--router", default="kv", show_default=True,
              help="Recorded into summary.json; actual router mode is set by deploy/launch_frontend.sh")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True)
@click.option("--workspace-field", default="workspace", show_default=True,
              help="Session-create payload field that carries the workspace path")
@click.option("--jaeger-lookup-delay", default=2.0, show_default=True,
              help="Seconds to wait before querying Jaeger so spans are flushed")
def run(split, num_samples, qps, seed, router, out_dir, workspace_field, jaeger_lookup_delay):
    """Run a Poisson workload of SWE-bench samples through OpenCode."""
    settings = Settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps({
        "split": split, "num_samples": num_samples, "qps": qps, "seed": seed,
        "router": router, "model": settings.model_name,
    }, indent=2))
    asyncio.run(runner.run(
        split=split,
        num_samples=num_samples,
        qps=qps,
        seed=seed,
        out_dir=out_dir,
        settings=settings,
        workspace_field=workspace_field,
        jaeger_lookup_delay_s=jaeger_lookup_delay,
    ))
    click.echo((out_dir / "summary.json").read_text())


@main.command()
@click.argument("run_dir", type=click.Path(exists=True, path_type=Path))
def analyze(run_dir):
    """Print summary.json for a completed run."""
    click.echo((run_dir / "summary.json").read_text())


if __name__ == "__main__":
    main()
