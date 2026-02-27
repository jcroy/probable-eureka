"""CLI entry point for webcollector."""

from __future__ import annotations

from pathlib import Path

import click

from webcollector.config import load_config


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to config YAML file.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None) -> None:
    """webcollector — prompt-driven web data collection."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path=config_path)


@cli.command()
@click.argument("prompt", required=False)
@click.option(
    "--plan-file",
    type=click.Path(exists=True, path_type=Path),
    help="Path to a YAML/JSON crawl plan file (skips LLM interpretation).",
)
@click.option("--max-pages", type=int, default=None, help="Override max pages to crawl.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.option("--format", "output_format", type=click.Choice(["jsonl", "csv"]), default=None)
@click.option("--auto-approve", is_flag=True, help="Skip crawl plan approval prompt.")
@click.pass_context
def collect(
    ctx: click.Context,
    prompt: str | None,
    plan_file: Path | None,
    max_pages: int | None,
    output_dir: Path | None,
    output_format: str | None,
    auto_approve: bool,
) -> None:
    """Collect web data from a prompt or crawl plan file."""
    if not prompt and not plan_file:
        raise click.UsageError("Provide either a PROMPT or --plan-file.")

    config = ctx.obj["config"]

    if max_pages is not None:
        config.crawl.max_pages = max_pages

    # TODO: Wire up crawl orchestration (Task #5)
    pages = config.crawl.max_pages
    concurrency = config.crawl.max_concurrency
    click.echo(f"Config loaded: {pages} max pages, {concurrency} concurrency")
    if plan_file:
        from webcollector.interpreter.plan_file_loader import load_plan_file

        plan = load_plan_file(plan_file)
        click.echo(f"Plan loaded: {len(plan.seed_urls)} seed URLs, depth={plan.max_depth}")
    elif prompt:
        click.echo(f"Prompt: {prompt}")
        click.echo("LLM interpretation not yet implemented. Use --plan-file for MVP.")


@cli.command("list-runs")
@click.pass_context
def list_runs(ctx: click.Context) -> None:
    """List all crawl runs."""
    # TODO: Query DB for runs (Task #4/#5)
    click.echo("No runs found. (Storage not yet connected.)")


@cli.command()
@click.argument("run_id")
@click.pass_context
def report(ctx: click.Context, run_id: str) -> None:
    """Show report for a crawl run."""
    # TODO: Load and display run report (Task #5)
    click.echo(f"Report for run {run_id}: not yet implemented.")


@cli.command()
@click.argument("run_id")
@click.option("--format", "output_format", type=click.Choice(["jsonl", "csv"]), default="jsonl")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
@click.pass_context
def export(
    ctx: click.Context,
    run_id: str,
    output_format: str,
    output_path: Path | None,
) -> None:
    """Export results from a crawl run."""
    # TODO: Export from DB (Task #4/#5)
    click.echo(f"Export run {run_id} as {output_format}: not yet implemented.")
