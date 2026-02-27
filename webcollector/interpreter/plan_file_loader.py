"""Load a CrawlPlan from a YAML or JSON file."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from webcollector.models.crawl_plan import CrawlPlan


def load_plan_file(path: Path) -> CrawlPlan:
    """Load a CrawlPlan from a YAML or JSON file."""
    text = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif path.suffix == ".json":
        data = json.loads(text)
    else:
        # Try YAML first, then JSON
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"Plan file must be a mapping, got {type(data).__name__}")

    return CrawlPlan(**data)
