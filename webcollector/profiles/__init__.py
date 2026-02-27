"""Site profile system for adaptive crawling.

Profiles describe *how* a type of site works (navigation, rendering, content
patterns) — independent of *what* to collect (that's the CrawlPlan).  They
compound over time: the LLM generates new profiles when the crawler stumbles
on an unknown site type, and well-tested profiles can graduate to bundled
adapters.
"""
