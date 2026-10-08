"""阶段二执行模块：把「AI 测试员」产出的结构化用例，转换为可运行的 pytest 脚本并执行。

模块划分
--------
- `executor.schema`     用例数据模型（JSON 契约）与容错解析
- `executor.loader`     从文件/会话读取用例（JSON / YAML）
- `executor.support`    被测系统配置（base_url / 环境变量 / 占位符）/ 断言与变量提取工具
- `executor.renderer`   把用例渲染成 pytest 源码（含 conftest / pytest.ini）
- `executor.runner`     落盘 + 运行 + 结果解析（供界面与 CLI 调用）
"""

from .config import SUTConfig, load_sut_config, save_sut_config  # noqa: F401
from .schema import TestCase, TestStep, load_cases, parse_payload  # noqa: F401
from .runner import ExecutionResult, GenerationResult, generate_suite, run_pytest  # noqa: F401

__all__ = [
    "SUTConfig",
    "load_sut_config",
    "save_sut_config",
    "TestCase",
    "TestStep",
    "load_cases",
    "parse_payload",
    "generate_suite",
    "run_pytest",
    "GenerationResult",
    "ExecutionResult",
]
