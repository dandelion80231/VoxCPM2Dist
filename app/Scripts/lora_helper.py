"""LoRA 权重加载辅助（VoxCPM2 / voxcpm 2.0.3）。

训练脚本会把 LoRA 权重保存到 step_XXXXXXX/ 目录，内含：
  - lora_weights.safetensors  (或 lora_weights.ckpt)
  - lora_config.json          (含 base_model + lora_config)

本模块负责：从 lora_config.json 重建与训练时「完全一致」的 LoRAConfig
（尤其是 r / alpha），再交给 VoxCPM 加载。

⚠️ 为什么必须显式传 lora_config：
   voxcpm 的 core.py 在「只给 lora_weights_path、不给 lora_config」时，
   会自动创建一个默认 LoRAConfig(r=8, alpha=16)。而训练常用 r=32，
   权重矩阵形状是 rank-32，默认 rank-8 的 LoRA 层与之形状不匹配，
   load_lora_weights 会因 key 形状不一致而大量 skipped / 加载失败。
   因此推理端务必用训练保存的 lora_config.json 重建 config。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple


def load_lora_config(lora_dir: str) -> Tuple[object, str]:
    """读取训练产出的 LoRA 目录，返回 (LoRAConfig 实例, 权重文件路径)。

    Args:
        lora_dir: 训练产出的 step_XXXXXXX 目录（含 lora_weights.* 与 lora_config.json）。
                  也可直接传 lora_weights.safetensors / .ckpt 文件。

    Returns:
        (lora_config, weights_path)

    Raises:
        FileNotFoundError: 目录/权重/配置缺失时。
        ValueError: lora_config.json 解析失败。
    """
    try:
        from voxcpm.model.voxcpm2 import LoRAConfig  # VoxCPM2
    except Exception:
        from voxcpm.model.voxcpm import LoRAConfig    # 兼容 VoxCPM1.x

    d = Path(lora_dir)
    if d.is_file():
        # 直接给了权重文件
        weights_path = d
        cfg_file = d.parent / "lora_config.json"
    else:
        cfg_file = d / "lora_config.json"
        weights_path = None
        for name in ("lora_weights.safetensors", "lora_weights.ckpt"):
            wp = d / name
            if wp.exists():
                weights_path = wp
                break

    if weights_path is None or not Path(weights_path).exists():
        raise FileNotFoundError(f"未找到 LoRA 权重文件（期望 lora_weights.safetensors/.ckpt）：{lora_dir}")

    if not cfg_file.exists():
        raise FileNotFoundError(f"未找到 lora_config.json：{cfg_file}（无法重建与训练一致的 LoRAConfig）")

    info = json.loads(Path(cfg_file).read_text(encoding="utf-8"))
    lora_cfg_dict = info.get("lora_config", {})
    if not isinstance(lora_cfg_dict, dict):
        raise ValueError(f"lora_config.json 中 lora_config 字段异常：{lora_cfg_dict!r}")

    # 只保留当前 LoRAConfig 支持的字段，避免版本差异导致报错
    fields = set(LoRAConfig.model_fields.keys())
    filtered = {k: v for k, v in lora_cfg_dict.items() if k in fields}
    lora_cfg = LoRAConfig(**filtered)
    return lora_cfg, str(weights_path)


def resolve_lora(model_init_kwargs: dict, lora_weights_path: Optional[str]):
    """根据配置里的 lora_weights_path 解析并回填 model 初始化参数。

    Args:
        model_init_kwargs: 将传给 VoxCPM(...) / VoxCPM.from_pretrained(...) 的参数字典
                           （会被原地修改，增加 lora_config / lora_weights_path）。
        lora_weights_path: 训练产出目录或权重文件路径；空/None/不存在则不动 kwargs。

    Returns:
        (model_init_kwargs, info) 二元组：
        - model_init_kwargs：回填后的参数字典（失败时保持不变）。
        - info：结构化状态字典，含 ok(bool)/reason(str 失败原因)/r/alpha/
          enable_lm/enable_dit/enable_proj/weights_path，供上层如实向用户反馈
          （避免「路径错了也谎称已挂载」的静默失败）。
    """
    info = {
        "ok": False,
        "reason": "",
        "r": None,
        "alpha": None,
        "enable_lm": None,
        "enable_dit": None,
        "enable_proj": None,
        "weights_path": None,
    }
    if not lora_weights_path:
        info["reason"] = "未配置 LoRA 权重路径"
        return model_init_kwargs, info
    p = Path(lora_weights_path.strip())
    if not p.exists():
        reason = f"路径不存在：{lora_weights_path}"
        print(f"[LoRA] {reason}，跳过加载")
        info["reason"] = reason
        return model_init_kwargs, info
    try:
        lora_cfg, weights_path = load_lora_config(str(p))
    except Exception as e:
        reason = f"加载失败：{e}"
        print(f"[LoRA] {reason}，将不使用 LoRA")
        info["reason"] = reason
        return model_init_kwargs, info
    model_init_kwargs["lora_config"] = lora_cfg
    model_init_kwargs["lora_weights_path"] = weights_path
    info.update(
        ok=True,
        reason="",
        r=lora_cfg.r,
        alpha=lora_cfg.alpha,
        enable_lm=lora_cfg.enable_lm,
        enable_dit=lora_cfg.enable_dit,
        enable_proj=lora_cfg.enable_proj,
        weights_path=weights_path,
    )
    print(f"[LoRA] 已解析：{weights_path} (r={lora_cfg.r}, alpha={lora_cfg.alpha}, "
          f"lm={lora_cfg.enable_lm}, dit={lora_cfg.enable_dit}, proj={lora_cfg.enable_proj})")
    return model_init_kwargs, info
