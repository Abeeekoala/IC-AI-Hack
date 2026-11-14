#!/usr/bin/env python3
"""Training files for the local MLX LoRA stand-in (README S9.5, local evaluation).

    python3 scripts/s9_5_local_lora_data.py MANIFEST CANDIDATES --out DIR --tokenizer MODEL [--max-tokens 2048] [--limit 300]
                                            [--valid 20] [--seed S]

Reads the rows the audited manifest includes (src/vaxforge/s9_5_finetune.py: training_rows checks each against its hash),
drops any whose prompt plus code is longer than --max-tokens under MODEL's chat template, keeps every licensed row and
fills the rest of --limit with synthetic rows chosen by a seeded shuffle, and writes DIR/train.jsonl and DIR/valid.jsonl as
{"prompt", "completion"} lines, which ``mlx_lm lora`` applies the chat template to. It prints how many rows of each origin
went to training: that split is part of any result and must be reported with it. Needs ``transformers`` (the MLX
environment has it). Nothing here trains or sends anything.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vaxforge import s9_5_finetune as ft  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("candidates")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tokenizer", required=True, help="the model whose chat template and tokenizer the training uses")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--valid", type=int, default=20)
    ap.add_argument("--seed", default="s9_5-local")
    args = ap.parse_args(argv)
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.tokenizer)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    candidates = ft.load_candidates(args.candidates)
    rows = ft.training_rows(candidates, manifest)
    origin = {e["id"]: e["origin"] for e in manifest["included"]}
    ids = [e["id"] for e in manifest["included"]]  # training_rows keeps the manifest's order
    fits, too_long = [], 0
    for i, row in zip(ids, rows):
        n = len(tok.apply_chat_template([{"role": "user", "content": row["prompt"]}, {"role": "assistant", "content": row["completion"]}],
                                        tokenize=True, return_dict=False))
        if n > args.max_tokens:
            too_long += 1
        else:
            fits.append((i, row, n))
    real = [x for x in fits if origin[x[0]] != "synthetic"]
    synth = [x for x in fits if origin[x[0]] == "synthetic"]
    rng = random.Random(args.seed)
    rng.shuffle(synth)
    chosen = real + synth[: max(0, args.limit - len(real))]
    rng.shuffle(chosen)
    valid, train = chosen[: args.valid], chosen[args.valid:]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, part in (("train", train), ("valid", valid)):
        (out / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for _, r, _ in part), encoding="utf-8")
    n_real = sum(1 for i, _, _ in train if origin[i] != "synthetic")
    tokens = sum(n for _, _, n in train)
    print(json.dumps({"included": len(rows), "dropped_too_long": too_long, "train": len(train), "train_real": n_real,
                      "train_synthetic": len(train) - n_real, "valid": len(valid), "train_tokens": tokens,
                      "max_tokens": args.max_tokens}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
