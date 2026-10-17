"""Market participation research components (offline, read-only, research only). Nothing here is imported by the pipeline, the tracker, the collector, the bot, a timer or a service.

  participation   breakout_participation_v1: market-wide Donchian breakout counts per session, strictly point in time (pure)
  run             command line entry point: reads prices through the harness's read-only session, writes a CSV + manifest to a directory the caller chooses
"""
