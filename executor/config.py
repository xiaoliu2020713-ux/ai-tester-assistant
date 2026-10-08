"""被测系统（SUT）配置：地址、鉴权、超时、环境变量。

设计目标：让「生成的测试脚本」不硬编码任何环境相关内容，
全部通过 `execution/sut.yaml` + 环境变量注入，从而能在
「本地 Mock / 线上真实服务 / 联调环境」之间切换而不用改代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import config as app_config

LOGGER = app_config.get_logger("executor.config")

EXECUTION_DIR = app_config.STORAGE_DIR / "execution"
SUT_CONFIG_PATH = EXECUTION_DIR / "sut.yaml"
GENERATED_DIR = EXECUTION_DIR / "generated"


@dataclass
class SUTConfig:
    """被测系统连接配置。"""

    name: str = "ecommerce_api_test (Fake Store API)"
    # 目标环境：mock=项目自带本地 Mock 服务；live=线上真实服务；custom=自定义地址
    environment: str = "mock"
    mock_base_url: str = "http://127.0.0.1:8765"
    live_base_url: str = "https://fakestoreapi.com"
    custom_base_url: str = ""

    # 鉴权：登录接口 + 期望状态码 + 从响应中提取 token 的 JSON 路径
    auth_enabled: bool = True
    auth_method: str = "POST"
    auth_path: str = "/auth/login"
    auth_username: str = "mor_2314"
    auth_password: str = "83r5^_"
    auth_token_field: str = "token"
    auth_expect_status: int = 201
    auth_header_name: str = "Authorization"
    auth_header_template: str = "Bearer {token}"

    timeout: int = 15
    verify_ssl: bool = True

    # 本地 Mock 服务启动脚本（相对 SUT 项目根目录）。
    # 被测项目 `ecommerce_api_test` 的 Mock 支持 `--port 0`（操作系统分配随机端口），
    # 因此**绝不能硬编码端口**：runner 会读取脚本打印的 `MOCK_BASE_URL=...` 拿到真实地址。
    mock_server_script: str = "sandbox/mock_server.py"
    mock_server_python: str = ""       # 留空则使用当前解释器
    mock_server_args: List[str] = field(default_factory=lambda: ["--port", "0"])

    # 额外注入到执行环境的环境变量（键值对）
    env: Dict[str, str] = field(default_factory=dict)
    # 生成脚本的额外 pytest 参数
    pytest_args: List[str] = field(default_factory=lambda: ["-q", "--no-header", "-p", "no:cacheprovider"])

    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        if self.environment == "live":
            return self.live_base_url.rstrip("/")
        if self.environment == "custom" and self.custom_base_url:
            return self.custom_base_url.rstrip("/")
        return self.mock_base_url.rstrip("/")

    @property
    def environment_label(self) -> str:
        return {"mock": "本地 Mock 服务", "live": "线上真实服务", "custom": "自定义地址"}.get(
            self.environment, self.environment
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _yaml_load(path: Path) -> Dict[str, Any]:
    """读取 YAML（优先 PyYAML，缺失时退回 JSON 解析）。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:  # pragma: no cover
        try:
            return json.loads(text)
        except Exception:
            return {}
    except Exception as exc:
        LOGGER.warning("解析 %s 失败：%s", path, exc)
        return {}


def _yaml_dump(path: Path, payload: Dict[str, Any]) -> None:
    try:
        import yaml  # type: ignore

        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    except ImportError:  # pragma: no cover
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sut_config(path: Optional[Path] = None) -> SUTConfig:
    """读取被测系统配置（文件不存在时返回默认值，并允许环境变量覆盖）。"""
    target = path or SUT_CONFIG_PATH
    cfg = SUTConfig()
    if target.exists():
        data = _yaml_load(target)
        for key, value in data.items():
            if hasattr(cfg, key) and value is not None:
                try:
                    setattr(cfg, key, value)
                except Exception:  # pragma: no cover
                    LOGGER.warning("忽略无法应用的配置项 %s=%r", key, value)

    # 环境变量覆盖（便于 CI 使用）
    env_map = {
        "SUT_ENVIRONMENT": "environment",
        "SUT_BASE_URL": "custom_base_url",
        "SUT_MOCK_URL": "mock_base_url",
        "SUT_LIVE_URL": "live_base_url",
        "SUT_TIMEOUT": "timeout",
        "SUT_AUTH_USERNAME": "auth_username",
        "SUT_AUTH_PASSWORD": "auth_password",
    }
    for env_key, attr in env_map.items():
        raw = os.getenv(env_key)
        if raw:
            setattr(cfg, attr, int(raw) if attr == "timeout" and raw.isdigit() else raw)
    if os.getenv("SUT_BASE_URL"):
        cfg.environment = cfg.environment if cfg.environment == "live" else "custom"
    return cfg


def save_sut_config(cfg: SUTConfig, path: Optional[Path] = None) -> Path:
    """保存被测系统配置。"""
    target = path or SUT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    _yaml_dump(target, cfg.to_dict())
    return target


def ensure_execution_dirs() -> None:
    EXECUTION_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)


__all__ = [
    "SUTConfig",
    "load_sut_config",
    "save_sut_config",
    "ensure_execution_dirs",
    "SUT_CONFIG_PATH",
    "GENERATED_DIR",
    "EXECUTION_DIR",
]
