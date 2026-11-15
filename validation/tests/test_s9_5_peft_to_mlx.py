"""Tests for scripts/s9_5_peft_to_mlx.py (the pure mapping; reading and writing the safetensors files needs mlx)."""
import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("s9_5_peft_to_mlx", ROOT / "scripts" / "s9_5_peft_to_mlx.py")
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)

PEFT = "base_model.model.model.language_model.layers.{}.{}.lora_{}.weight"
CFG = {"r": 4, "lora_alpha": 8, "peft_type": "LORA", "use_dora": False, "use_rslora": False, "lora_bias": False,
       "rank_pattern": {}, "alpha_pattern": {}, "modules_to_save": None}


def tensors(*modules, layers=(0, 3)):
    t = {}
    for layer in layers:
        for module in modules:
            t[PEFT.format(layer, module, "A")] = np.arange(4 * 6, dtype="float32").reshape(4, 6)   # (r, in)
            t[PEFT.format(layer, module, "B")] = np.arange(5 * 4, dtype="float32").reshape(5, 4)   # (out, r)
    return t


def test_names_transposes_scale_and_keys():
    t = tensors("mlp.gate_proj", "self_attn.q_proj")
    out, cfg, modules = conv.convert(t, CFG, 4)
    a = out["language_model.model.layers.3.self_attn.q_proj.lora_a"]
    b = out["language_model.model.layers.3.self_attn.q_proj.lora_b"]
    assert a.shape == (6, 4) and b.shape == (4, 5)                                         # (in, r) and (r, out)
    assert (a == t[PEFT.format(3, "self_attn.q_proj", "A")].T).all() and (b == t[PEFT.format(3, "self_attn.q_proj", "B")].T).all()
    assert len(out) == 8 and modules == {"mlp.gate_proj": 2, "self_attn.q_proj": 2}
    assert cfg == {"fine_tune_type": "lora", "num_layers": 4,
                   "lora_parameters": {"rank": 4, "scale": 2.0, "dropout": 0.0, "keys": ["mlp.gate_proj", "self_attn.q_proj"]}}


@pytest.mark.parametrize("name", [
    "base_model.model.model.language_model.layers.0.linear_attn.in_proj_qkv.lora_A.weight",   # a module the model has but the pattern does not
    "base_model.model.model.visual.blocks.0.attn.qkv.lora_A.weight",
    "base_model.model.model.language_model.layers.0.mlp.gate_proj.lora_embedding_A",
])
def test_an_unknown_tensor_stops_the_run_and_is_named(name):
    t = tensors("mlp.up_proj")
    t[name] = np.zeros((4, 6), dtype="float32")
    with pytest.raises(conv.ConversionError, match="did not map"):
        conv.convert(t, CFG, 4)


def test_a_missing_b_a_wrong_rank_and_a_layer_outside_the_model_stop_the_run():
    t = tensors("mlp.up_proj")
    del t[PEFT.format(3, "mlp.up_proj", "B")]
    with pytest.raises(conv.ConversionError, match="pair up"):
        conv.convert(t, CFG, 4)
    with pytest.raises(conv.ConversionError, match="rank"):
        conv.convert(tensors("mlp.up_proj"), {**CFG, "r": 8}, 4)
    with pytest.raises(conv.ConversionError, match="outside"):
        conv.convert(tensors("mlp.up_proj"), CFG, 3)
    with pytest.raises(conv.ConversionError, match="layer count"):
        conv.convert(tensors("mlp.up_proj"), CFG, None)


@pytest.mark.parametrize("change", [{"use_dora": True}, {"use_rslora": True}, {"lora_bias": True},
                                    {"rank_pattern": {"q_proj": 8}}, {"modules_to_save": ["lm_head"]}, {"peft_type": "IA3"}])
def test_a_peft_feature_the_conversion_would_drop_stops_the_run(change):
    with pytest.raises(conv.ConversionError, match="drop"):
        conv.convert(tensors("mlp.up_proj"), {**CFG, **change}, 4)
