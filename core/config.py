"""配置加载:读 config.yaml + 取环境变量。单一配置入口。"""
import os
from pathlib import Path

import yaml


class Config:
    """对 config.yaml dict 的薄封装,支持 [] 与 get。"""

    def __init__(self, data: dict):
        self._d = data

    def __getitem__(self, key):
        return self._d[key]

    def get(self, key, default=None):
        return self._d.get(key, default)


def load_config(path=None) -> Config:
    """加载 config.yaml。不传 path 时默认读仓库根的 config.yaml。"""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return Config(yaml.safe_load(f))


def env(name: str) -> str:
    """取必填环境变量;缺失则显式报错(见 .env.example)。"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}(见 .env.example)")
    return value
