"""Regenerate docs/config.stream_b.yaml from the built-in defaults:  python scripts/stream_b_make_config_snippet.py"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HEADER = """# ---- Stream B (S3-S7, "prediction core") ----------------------------------------------------------------------
# Paste at the end of config.yaml. Generated from src/vaxforge/prediction/config.py:DEFAULTS (a test keeps the two in sync), so
# every value is also the built-in default: deleting a key changes nothing.
# Shared with Stream A and therefore NOT repeated here: thresholds, epitopes, structure, reproducibility (identical keys),
# paths.cache_dir, network.offline, pipeline.top_k and tools.docker / tools.blast_image (mapped automatically).
# `refdata:` and `tools:` below only ADD keys - if config.yaml already has a `tools:` section, merge these keys into it.
"""
SHARED = {"refdata": ("cache_dir", "offline"), "tools": ("docker",)}


def snippet_dict() -> dict:
    from vaxforge.prediction.config import DEFAULTS
    out = {k: copy.deepcopy(DEFAULTS[k]) for k in ("refdata", "tools", "s3", "s4", "s5", "s6", "s7")}
    for section, keys in SHARED.items():
        for k in keys:
            out[section].pop(k, None)
    return out


def render() -> str:
    import yaml
    parts = []
    for name, value in snippet_dict().items():
        style = False if name == "tools" else None            # tools: block style so its keys can be merged line by line
        parts.append(yaml.safe_dump({name: value}, sort_keys=False, default_flow_style=style, width=100000, allow_unicode=True))
    return HEADER + "".join(parts)


if __name__ == "__main__":
    (ROOT / "docs" / "config.stream_b.yaml").write_text(render())
    print("wrote docs/config.stream_b.yaml")
