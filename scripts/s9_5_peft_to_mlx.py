#!/usr/bin/env python3
"""Convert a PEFT LoRA adapter (Adaption's AutoScientist checkpoint) to an mlx_lm adapter (README S9.5, running the models).

    python3 scripts/s9_5_peft_to_mlx.py PEFT_DIR OUT_DIR

PEFT_DIR holds adapter_model.safetensors, adapter_config.json and config.json (for text_config.num_hidden_layers; or pass --num-layers).
OUT_DIR must not exist or must be empty; it receives adapters.safetensors and adapter_config.json, which
``mlx_lm.server --model <mlx build of the base> --adapter-path OUT_DIR`` loads. Only the two JSON/safetensors files are read,
as data; nothing from the archive is executed. Needs ``mlx`` (the mlx_lm environment).

The mapping, for a Qwen3.5 (hybrid linear/full attention) text model:

    base_model.model.model.language_model.layers.N.<mlp|self_attn>.X_proj.lora_A.weight  (r, in)
 -> language_model.model.layers.N.<mlp|self_attn>.X_proj.lora_a                          (in, r)   = A.T
    ...lora_B.weight                                                                      (out, r)
 -> ...lora_b                                                                             (r, out)  = B.T
    scale = lora_alpha / r  (mlx_lm applies scale * (x @ lora_a) @ lora_b, as PEFT does with alpha / r)

Nothing is skipped silently: a tensor that does not match the pattern, an A without its B, a rank other than r, a
PEFT feature the conversion would drop (DoRA, rsLoRA, per-module ranks or alphas, LoRA bias, modules_to_save) and layer
ids outside the model all stop the run with the list of what did not map. mlx_lm's own loader then checks every converted
name and shape against the model it is applied to. The weights are float32 as stored; the base the adapter is applied to
may be quantised, which is not what the adapter was trained against.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PEFT_KEY = re.compile(r"base_model\.model\.model\.language_model\.layers\.(\d+)\.((?:mlp|self_attn)\.\w+_proj)\.lora_([AB])\.weight\Z")


class ConversionError(Exception):
    pass


def convert(tensors, peft_config, num_layers):
    """tensors: name -> array (anything with .T and .shape); num_layers: the base model's layer count (mlx_lm applies LoRA to
    its last num_layers layers, so this must be all of them). Returns (mlx tensors, mlx adapter_config, mapped-module counts)."""
    cfg = peft_config
    problems = [f"{k}={cfg[k]!r}" for k in ("use_dora", "use_rslora", "lora_bias") if cfg.get(k)]
    problems += [f"{k}={cfg[k]!r}" for k in ("rank_pattern", "alpha_pattern", "modules_to_save") if cfg.get(k)]
    if cfg.get("peft_type", "LORA") != "LORA":
        problems.append(f"peft_type={cfg['peft_type']!r}")
    if problems:
        raise ConversionError("PEFT features this conversion would drop: " + ", ".join(problems))
    rank, alpha = int(cfg["r"]), float(cfg["lora_alpha"])

    out, unmapped, layers, modules = {}, [], set(), Counter()
    for name, w in tensors.items():
        m = PEFT_KEY.match(name)
        if not m:
            unmapped.append(name)
            continue
        layer, module, ab = int(m.group(1)), m.group(2), m.group(3)
        layers.add(layer)
        if ab == "A":
            if w.shape[0] != rank:
                raise ConversionError(f"{name}: rank {w.shape[0]}, adapter_config says r={rank}")
            out[f"language_model.model.layers.{layer}.{module}.lora_a"] = w.T
            modules[module] += 1
        else:
            if w.shape[1] != rank:
                raise ConversionError(f"{name}: rank {w.shape[1]}, adapter_config says r={rank}")
            out[f"language_model.model.layers.{layer}.{module}.lora_b"] = w.T
    if unmapped:
        raise ConversionError(f"{len(unmapped)} tensors did not map, for example: {sorted(unmapped)[:5]}")
    a = {k[:-1] for k in out if k.endswith("lora_a")}
    b = {k[:-1] for k in out if k.endswith("lora_b")}
    if a != b:
        raise ConversionError(f"lora_A and lora_B do not pair up: {sorted(a ^ b)[:5]}")
    if not out:
        raise ConversionError("no tensors in the adapter")
    n = num_layers
    if not n:
        raise ConversionError("the model's layer count is unknown: pass --num-layers or put config.json beside the adapter")
    if max(layers) >= n:
        raise ConversionError(f"layer {max(layers)} is outside a {n}-layer model")
    mlx_config = {
        "fine_tune_type": "lora",
        "num_layers": n,
        "lora_parameters": {"rank": rank, "scale": alpha / rank, "dropout": 0.0, "keys": sorted(modules)},
    }
    return out, mlx_config, dict(sorted(modules.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert a PEFT LoRA adapter to an mlx_lm adapter")
    ap.add_argument("peft_dir")
    ap.add_argument("out_dir", help="new or empty directory")
    ap.add_argument("--num-layers", type=int, help="layers in the base model (default: text_config.num_hidden_layers of PEFT_DIR/config.json)")
    args = ap.parse_args(argv)
    src, dst = Path(args.peft_dir), Path(args.out_dir)
    try:
        import mlx.core as mx
    except ImportError:
        print("s9_5_peft_to_mlx: needs mlx (run it in the mlx_lm environment)", file=sys.stderr)
        return 1
    try:
        if dst.exists() and any(dst.iterdir()):
            raise ConversionError(f"{dst} is not empty")
        cfg = json.loads((src / "adapter_config.json").read_text(encoding="utf-8"))
        layers = args.num_layers
        model_cfg = src / "config.json"
        if layers is None and model_cfg.is_file():
            c = json.loads(model_cfg.read_text(encoding="utf-8"))
            layers = (c.get("text_config") or c).get("num_hidden_layers")
        tensors = mx.load(str(src / "adapter_model.safetensors"))
        out, mlx_config, modules = convert(tensors, cfg, layers)
    except (ConversionError, OSError, ValueError, KeyError) as e:
        print(f"s9_5_peft_to_mlx: {e}", file=sys.stderr)
        return 1
    dst.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(dst / "adapters.safetensors"), {k: mx.array(v).astype(mx.float32) for k, v in out.items()})
    (dst / "adapter_config.json").write_text(json.dumps(mlx_config, indent=2) + "\n", encoding="utf-8")
    print(f"{len(out)} tensors, scale {mlx_config['lora_parameters']['scale']}, {mlx_config['num_layers']} layers; modules mapped:")
    for module, count in modules.items():
        print(f"  {module}: {count} layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
