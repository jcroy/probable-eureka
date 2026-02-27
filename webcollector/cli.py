"""CLI entry point for webcollector."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
import structlog

from webcollector.config import load_config

logger = structlog.get_logger(__name__)


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to config YAML file.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default=None,
    help="Override log level.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, log_level: str | None) -> None:
    """webcollector — prompt-driven web data collection."""
    ctx.ensure_object(dict)
    config = load_config(config_path=config_path)
    if log_level:
        config.logging.level = log_level.upper()
    ctx.obj["config"] = config

    # Set up structured logging
    from webcollector.logging import setup_logging

    setup_logging(config.logging)


@cli.command()
@click.argument("prompt", required=False)
@click.option(
    "--plan-file",
    type=click.Path(exists=True, path_type=Path),
    help="Path to a YAML/JSON crawl plan file (skips LLM interpretation).",
)
@click.option("--max-pages", type=int, default=None, help="Override max pages to crawl.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["jsonl", "csv"]),
    default=None,
)
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

    # Load the crawl plan
    if plan_file:
        from webcollector.interpreter.plan_file_loader import load_plan_file

        plan = load_plan_file(plan_file)
        click.echo(
            f"Plan loaded: {len(plan.seed_urls)} seed URLs, "
            f"depth={plan.max_depth}, domains={plan.target_domains}"
        )
    elif prompt:
        click.echo(f"Prompt: {prompt}")
        click.echo("LLM interpretation not yet implemented. Use --plan-file for MVP.")
        sys.exit(1)
    else:
        sys.exit(1)

    # Show plan summary and ask for approval
    if not auto_approve:
        click.echo("\n--- Crawl Plan ---")
        click.echo(f"  Intent: {plan.intent_summary}")
        click.echo(f"  Seed URLs: {plan.seed_urls}")
        click.echo(f"  Domains: {plan.target_domains}")
        click.echo(f"  Max depth: {plan.max_depth}, Max pages: {plan.max_pages}")
        if plan.url_patterns:
            click.echo(f"  URL patterns: {plan.url_patterns}")
        if plan.document_types:
            click.echo(f"  Document types: {plan.document_types}")
        click.echo("")

        if not click.confirm("Proceed with this crawl plan?"):
            click.echo("Aborted.")
            sys.exit(0)

    # Run the orchestrator
    from webcollector.orchestrator import RunOrchestrator

    orchestrator = RunOrchestrator(
        config=config,
        plan=plan,
        prompt=prompt or f"plan-file:{plan_file}",
    )

    click.echo(f"\nStarting crawl run: {orchestrator.run_id}")
    result = asyncio.run(orchestrator.execute())

    click.echo("\n--- Run Complete ---")
    click.echo(f"  Run ID:     {result.run_id}")
    click.echo(f"  Pages:      {result.pages_crawled}")
    click.echo(f"  Documents:  {result.documents_stored}")
    click.echo(f"  Duplicates: {result.duplicates_found}")
    click.echo(f"  Files:      {result.files_downloaded}")
    click.echo(f"  Errors:     {result.errors}")


@cli.command("list-runs")
@click.pass_context
def list_runs(ctx: click.Context) -> None:
    """List all crawl runs."""
    config = ctx.obj["config"]

    from webcollector.storage.database import Database

    async def _list():
        db = Database(config.storage.db_path)
        await db.init()
        try:
            runs = await db.list_crawl_runs()
            return runs
        finally:
            await db.close()

    runs = asyncio.run(_list())

    if not runs:
        click.echo("No runs found.")
        return

    click.echo(f"{'ID':<38} {'Status':<12} {'Docs':<6} {'Started'}")
    click.echo("-" * 80)
    for run in runs:
        started = run.get("started_at", "")
        if hasattr(started, "strftime"):
            started = started.strftime("%Y-%m-%d %H:%M")
        click.echo(
            f"{run['id']:<38} {run['status']:<12} "
            f"{run.get('total_documents_stored', 0):<6} {started}"
        )


@cli.command()
@click.argument("run_id")
@click.pass_context
def report(ctx: click.Context, run_id: str) -> None:
    """Show report for a crawl run."""
    config = ctx.obj["config"]

    from webcollector.storage.database import Database

    async def _report():
        db = Database(config.storage.db_path)
        await db.init()
        try:
            run = await db.get_crawl_run(run_id)
            doc_count = 0
            if run:
                doc_count = await db.count_documents(run_id)
            return run, doc_count
        finally:
            await db.close()

    run, doc_count = asyncio.run(_report())

    if not run:
        click.echo(f"Run {run_id} not found.")
        sys.exit(1)

    click.echo(f"\n--- Run Report: {run_id} ---")
    click.echo(f"  Status:     {run['status']}")
    click.echo(f"  Prompt:     {run.get('prompt', '')[:100]}")
    click.echo(f"  Started:    {run.get('started_at', 'N/A')}")
    click.echo(f"  Finished:   {run.get('finished_at', 'N/A')}")
    click.echo(f"  Pages:      {run.get('total_urls_fetched', 0)}")
    click.echo(f"  Documents:  {run.get('total_documents_stored', 0)}")
    click.echo(f"  Duplicates: {run.get('total_duplicates_found', 0)}")
    click.echo(f"  Errors:     {run.get('total_errors', 0)}")
    click.echo(f"  Bytes:      {run.get('total_bytes_downloaded', 0)}")
    click.echo(f"  DB docs:    {doc_count}")


@cli.command()
@click.argument("run_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["jsonl", "csv"]),
    default="jsonl",
)
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None)
@click.pass_context
def export(
    ctx: click.Context,
    run_id: str,
    output_format: str,
    output_path: Path | None,
) -> None:
    """Export results from a crawl run."""
    config = ctx.obj["config"]

    from webcollector.storage.database import Database

    async def _export():
        db = Database(config.storage.db_path)
        await db.init()
        try:
            docs = await db.list_documents(run_id, limit=10000)
            return docs
        finally:
            await db.close()

    docs = asyncio.run(_export())

    if not docs:
        click.echo(f"No documents found for run {run_id}.")
        sys.exit(1)

    def _serialize_doc(doc: dict) -> dict:
        record = {}
        for k, v in doc.items():
            record[k] = v.isoformat() if hasattr(v, "isoformat") else v
        return record

    def _write_output(out) -> None:
        if output_format == "jsonl":
            for doc in docs:
                out.write(json.dumps(_serialize_doc(doc), ensure_ascii=False) + "\n")
        elif output_format == "csv":
            import csv

            if docs:
                writer = csv.DictWriter(out, fieldnames=list(docs[0].keys()))
                writer.writeheader()
                for doc in docs:
                    writer.writerow(_serialize_doc(doc))

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            _write_output(f)
        click.echo(f"Exported {len(docs)} documents to {output_path}")
    else:
        _write_output(sys.stdout)
