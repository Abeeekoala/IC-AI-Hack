"""Shared helpers for stream B (S3-S7, "prediction core").

Only the five stage modules (``s3_rank`` ... ``s7_structure``) are public entry points; everything
here is internal plumbing owned by stream B so it cannot collide with stream A's scaffolding.
"""
