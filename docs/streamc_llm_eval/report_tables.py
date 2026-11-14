"""Print the markdown tables that docs/streamc_llm_eval.md quotes, straight from results.json and calls.jsonl.
Offline: no model call, no key. Usage: python docs/streamc_llm_eval/report_tables.py"""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
res = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
rows = res["attempts"]
calls = [json.loads(x) for x in (HERE / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
ends = {(e["model"], e["mode"], e["session"]): e for e in calls if e.get("event") == "call_end"}
SHORT = {"claude-sonnet-5-5": "Sonnet", "claude-haiku-5-5": "Haiku"}
MODELS = list(SHORT)


def detail(r: dict) -> str:
    if r["outcome"] == "PASS":
        w = len(r["warning_findings"])
        return "PASS" + (f" ({w} pipette_range warnings)" if w else "")
    if r["outcome"] == "REFUSED":
        return f"REFUSED ({r['refusal_kind']})"
    if r["sim_ok"] is False:
        return f"FAIL: {r['sim_error_kind']}"
    rc = {k: v for k, v in r["rule_counts"].items() if v}
    return "FAIL: " + ", ".join(f"{k} x{v}" for k, v in rc.items())


def per_session(mode: str) -> None:
    sess = []
    for r in rows:
        if r["mode"] == mode and r["session"] not in sess:
            sess.append(r["session"])
    print(f"\n### per-session, {mode} mode\n")
    print("| session | S8 step family | " + " | ".join(SHORT[m] for m in MODELS) + " |")
    print("|---|---|" + "---|" * len(MODELS))
    for s in sess:
        cells, fam = [], ""
        for m in MODELS:
            r = next((x for x in rows if x["mode"] == mode and x["model"] == m and x["session"] == s), None)
            cells.append(detail(r) if r else "not run")
            fam = fam or (r["family"] if r else "")
        print(f"| {s} | {fam} | " + " | ".join(cells) + " |")


def refusals() -> None:
    print("\n### refusals\n")
    print("| mode | model | session | stop_reason | detector kind | input tok | output tok | latency s | content blocks | text chars |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["outcome"] != "REFUSED":
            continue
        e = ends[(r["model"], r["mode"], r["session"])]
        print(f"| {r['mode']} | {SHORT[r['model']]} | {r['session']} | {e['stop_reason']} | {r['refusal_kind']} | {e['input_tokens']} | "
              f"{e['output_tokens']} | {e['latency_s']} | {'+'.join(e['block_types']) or 'none'} | {e['reply_chars']} |")


def accounting() -> None:
    starts = [e for e in calls if e["event"] == "call_start"]
    print("\n### accounting\n")
    print(f"call_start events: {len(starts)}; call_end ok: {sum(1 for e in calls if e['event'] == 'call_end' and e.get('ok'))}; "
          f"call_end not ok: {sum(1 for e in calls if e['event'] == 'call_end' and not e.get('ok'))}; "
          f"started without an end: {len(starts) - sum(1 for e in calls if e['event'] == 'call_end')}; "
          f"re-issued: {sum(1 for e in starts if e.get('reissue_of'))}; replays: {sum(1 for e in calls if e['event'] == 'replay')}; "
          f"sdk_retry events: {sum(1 for e in calls if e['event'] == 'sdk_retry')}; blocked: {sum(1 for e in calls if e['event'] == 'blocked')}")
    print("distinct system prompts:", sorted({e["system_sha256"][:12] for e in starts}), "| max_tokens:", sorted({e["max_tokens"] for e in starts}))
    print("first call", min(e["ts"] for e in starts), "| last end", max(e["ts"] for e in calls if e["event"] == "call_end"))
    tok = defaultdict(lambda: [0, 0, 0, []])
    for e in calls:
        if e["event"] == "call_end" and e.get("ok"):
            t = tok[(SHORT[e["model"]], e["mode"])]
            t[0] += 1
            t[1] += e["input_tokens"]
            t[2] += e["output_tokens"]
            t[3].append(e["latency_s"])
    print("\n| model | mode | calls with a reply | input tokens | output tokens | median latency s | max output tokens in one call |")
    print("|---|---|---|---|---|---|---|")
    for (m, mo), (n, i, o, lat) in sorted(tok.items()):
        mx = max(e["output_tokens"] for e in calls if e["event"] == "call_end" and e.get("ok") and SHORT[e["model"]] == m and e["mode"] == mo)
        print(f"| {m} | {mo} | {n} | {i} | {o} | {statistics.median(lat):.1f} | {mx} |")
    print("\nstop reasons:", dict(Counter(e["stop_reason"] for e in calls if e["event"] == "call_end" and e.get("ok"))))
    print("block types:", dict(Counter("+".join(e["block_types"]) or "none" for e in calls if e["event"] == "call_end" and e.get("ok"))))


def headline() -> None:
    print("\n### headline\n")
    for h in res["headline"]:
        k, n = h["PASS"], h["n"]
        att = n - h["REFUSED"] - h["ERROR"]
        print(f"{h['mode']:5} {SHORT[h['model']]:7} n={n} PASS={k} FAIL={h['FAIL']} REFUSED={h['REFUSED']} ERROR={h['ERROR']} UNVERIFIED={h['UNVERIFIED']} "
              f"pass@1={k}/{n} (Wilson {h['wilson95']}) ; among non-refused: {k}/{att}")


if __name__ == "__main__":
    headline()
    per_session("spec")
    per_session("free")
    refusals()
    accounting()
