"""落盘 + 执行 + 结果解析。

对外两个核心函数：

    generate_suite(cases, cfg, domain, domain_name, out_dir=None) -> GenerationResult
    run_pytest(target_dir, cfg, *, with_mock=False, extra_args=())      -> ExecutionResult

`with_mock=True` 时会先启动被测项目自带的 Mock 服务
（`ecommerce_api_test/sandbox/mock_server.py`，支持 `--port 0` 随机端口），
从它输出的 `MOCK_BASE_URL=...` 拿到真实地址后写入 `support_cases.json`，
再运行 pytest，最后关闭 Mock —— 这样生成的脚本永远不需要硬编码端口。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config as app_config
from executor import renderer
from executor.config import GENERATED_DIR, SUTConfig, ensure_execution_dirs
from executor.schema import TestCase

LOGGER = app_config.get_logger("executor.runner")

# 被测项目根目录（可用环境变量覆盖，默认取工作目录下的 ecommerce_api_test）
SUT_PROJECT_DIR = Path(os.getenv("SUT_PROJECT_DIR", str(app_config.BASE_DIR.parent / "ecommerce_api_test")))

_MOCK_URL_RE = re.compile(r"(?:MOCK_BASE_URL=|https?://)(\S*?127\.0\.0\.1:\d+|https?://\S+)")
_MOCK_URL_EXTRACT_RE = re.compile(r"(https?://[\w.\-]+:\d+)")
_PYTEST_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")


@dataclass
class GenerationResult:
    """生成结果。"""

    out_dir: Path
    files: List[str] = field(default_factory=list)
    total: int = 0
    auto: int = 0
    manual: int = 0
    apis: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"生成 {self.total} 条用例（可自动断言 {self.auto} / 待人工确认 {self.manual}），"
            f"覆盖 {self.apis} 个接口，产出 {len(self.files)} 个文件"
        )


@dataclass
class ExecutionResult:
    """执行结果。"""

    returncode: int = -1
    stdout: str = ""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0
    xpassed: int = 0
    duration_s: float = 0.0
    base_url: str = ""
    mock_url: str = ""
    failed_cases: List[str] = field(default_factory=list)
    diagnostic: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.errors == 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped + self.xfailed + self.xpassed

    @property
    def summary(self) -> str:
        parts = [f"通过 {self.passed}", f"失败 {self.failed}"]
        if self.errors:
            parts.append(f"错误 {self.errors}")
        if self.xfailed:
            parts.append(f"待人工确认 {self.xfailed}")
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        return "，".join(parts) + f"（共 {self.total} 条，{self.duration_s:.1f}s）"


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def generate_suite(
    cases: Sequence[TestCase],
    cfg: SUTConfig,
    *,
    domain: str = "",
    domain_name: str = "",
    out_dir: Optional[Path] = None,
) -> GenerationResult:
    """把用例渲染成 pytest 脚本并落盘。"""
    ensure_execution_dirs()
    target = Path(out_dir) if out_dir else GENERATED_DIR
    target.mkdir(parents=True, exist_ok=True)

    files = renderer.build_suite(cases, cfg, domain=domain, domain_name=domain_name)
    written: List[str] = []
    for name, content in files.items():
        path = target / name
        path.write_text(content, encoding="utf-8")
        written.append(name)

    manual = sum(1 for c in cases if c.needs_manual_review)
    result = GenerationResult(
        out_dir=target,
        files=sorted(written),
        total=len(cases),
        auto=len(cases) - manual,
        manual=manual,
        apis=len({(c.method, c.path) for c in cases}),
    )
    if manual:
        result.notes.append(f"{manual} 条用例含无法机器判定的断言，已生成 xfail 用例供人工确认")
    LOGGER.info("生成测试套件：%s -> %s", result.summary, target)
    return result


# ---------------------------------------------------------------------------
# Mock 服务生命周期
# ---------------------------------------------------------------------------
class MockServerHandle:
    """被测项目自带 Mock 服务的托管句柄（读 `MOCK_BASE_URL=` 获取真实地址）。"""

    def __init__(self, process: subprocess.Popen, base_url: str, stdout_lines: List[str]) -> None:
        self.process = process
        self.base_url = base_url
        self.stdout_lines = stdout_lines

    def stop(self) -> None:
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:  # pragma: no cover
                pass


def start_mock_server(cfg: SUTConfig, *, wait_seconds: int = 20) -> Tuple[Optional[MockServerHandle], str]:
    """启动 SUT 项目的 Mock 服务，返回 `(handle, message)`。

    Mock 脚本支持 `--port 0`，会打印 `MOCK_BASE_URL=http://127.0.0.1:<随机端口>`；
    我们解析该行拿到真实地址，从而避免硬编码端口。
    """
    script = Path(cfg.mock_server_script)
    if not script.is_absolute():
        script = SUT_PROJECT_DIR / cfg.mock_server_script
    if not script.exists():
        return None, f"未找到 Mock 服务脚本：{script}"

    python = cfg.mock_server_python or sys.executable
    command = [python, "-u", str(script), *[str(a) for a in (cfg.mock_server_args or ["--port", "0"])]]
    LOGGER.info("启动 Mock 服务：%s", " ".join(command))
    env = dict(os.environ)
    # 关键：让子进程行缓冲，否则管道模式下 `已启动：http://...` 会一直留在缓冲区里读不到
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        process = subprocess.Popen(
            command,
            cwd=str(script.parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except Exception as exc:
        return None, f"启动 Mock 服务失败：{type(exc).__name__}: {exc}"

    lines: List[str] = []
    base_url = ""
    deadline = time.time() + wait_seconds

    def reader() -> None:
        nonlocal base_url
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip())
            if not base_url:
                match = _MOCK_URL_EXTRACT_RE.search(line)
                if match:
                    base_url = match.group(1).strip().rstrip("/")

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    while time.time() < deadline and not base_url:
        if process.poll() is not None:
            break
        time.sleep(0.2)

    if not base_url:
        detail = "\n".join(lines[-5:]) or "（无输出）"
        try:
            process.kill()
        except Exception:  # pragma: no cover
            pass
        return None, f"Mock 服务未在 {wait_seconds}s 内就绪。输出：{detail}"

    return MockServerHandle(process, base_url, lines), f"Mock 服务已启动：{base_url}"


def _patch_settings(target_dir: Path, cfg: SUTConfig, base_url: Optional[str] = None) -> Optional[dict]:
    """把本次执行生效的连接配置写入 support_cases.json，返回原配置用于恢复。

    必须这样做：生成时的配置与执行时的目标环境可能不同（例如先生成再切到 live），
    若不覆盖，脚本会仍然打旧地址。
    """
    path = target_dir / "support_cases.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    previous = json.loads(json.dumps(payload.get("settings") or {}))
    settings = payload.setdefault("settings", {})
    settings["base_url"] = base_url or cfg.base_url
    settings["environment"] = cfg.environment
    settings["environment_label"] = cfg.environment_label
    settings["timeout"] = cfg.timeout
    settings["verify_ssl"] = cfg.verify_ssl
    settings["variables"] = dict(cfg.env or {})
    settings["auth"] = {
        "enabled": bool(cfg.auth_enabled),
        "method": cfg.auth_method,
        "path": cfg.auth_path,
        "username": cfg.auth_username,
        "password": cfg.auth_password,
        "token_field": cfg.auth_token_field,
        "expect_status": cfg.auth_expect_status,
        "header_name": cfg.auth_header_name,
        "header_template": cfg.auth_header_template,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return previous


def _restore_settings(target_dir: Path, previous: Optional[dict]) -> None:
    if not previous:
        return
    path = target_dir / "support_cases.json"
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["settings"] = previous
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _parse_counts(output: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value, label in _PYTEST_COUNT_RE.findall(output or ""):
        key = {
            "passed": "passed",
            "failed": "failed",
            "error": "errors",
            "errors": "errors",
            "skipped": "skipped",
            "xfailed": "xfailed",
            "xpassed": "xpassed",
        }.get(label, label)
        counts[key] = max(counts.get(key, 0), int(value))
    return counts


def _collect_failed_cases(output: str) -> List[str]:
    names: List[str] = []
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAILED ", "ERROR ")):
            names.append(stripped.split(" ", 1)[1].split(" - ")[0])
        elif stripped.startswith("_____") and "test_" in stripped:
            name = stripped.strip("_ ").strip()
            if name and name not in names:
                names.append(name)
    return names[:40]


def run_pytest(
    target_dir: Optional[Path] = None,
    cfg: Optional[SUTConfig] = None,
    *,
    with_mock: bool = False,
    extra_args: Iterable[str] = (),
    timeout: int = 900,
    run_defects: bool = False,
    on_line=None,
) -> ExecutionResult:
    """运行生成好的 pytest 套件。

    - `with_mock=True`：自动起停被测项目自带 Mock 服务，并把随机端口写进用例配置；
    - `run_defects=True`：同时运行默认跳过的「负向契约」用例（用于演示缺陷发现）；
    - `on_line`：可选回调，逐行接收 pytest 输出（界面可实时展示）。
    """
    cfg = cfg or SUTConfig()
    target = Path(target_dir) if target_dir else GENERATED_DIR
    if not (target / "support_cases.json").exists():
        return ExecutionResult(diagnostic=f"目录中没有 support_cases.json：{target}，请先生成脚本")

    handle: Optional[MockServerHandle] = None
    message = ""
    previous_settings: Optional[dict] = None
    base_url = cfg.base_url

    if with_mock:
        handle, message = start_mock_server(cfg)
        if handle is None:
            return ExecutionResult(diagnostic=message or "Mock 服务启动失败")
        base_url = handle.base_url

    previous_settings = _patch_settings(target, cfg, base_url)

    command = [sys.executable, "-m", "pytest", str(target), *cfg.pytest_args, *[str(a) for a in extra_args]]
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (cfg.env or {}).items()})
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if run_defects:
        env["RUN_DEFECT_CASES"] = "1"

    started = time.time()
    lines: List[str] = []
    result = ExecutionResult(base_url=base_url, mock_url=(handle.base_url if handle else ""))
    try:
        process = subprocess.Popen(
            command,
            cwd=str(target),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip())
            if on_line:
                try:
                    on_line(line.rstrip())
                except Exception:  # pragma: no cover
                    pass
        result.returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result.returncode = -1
        result.diagnostic = f"执行超时（>{timeout}s），已终止"
        try:
            process.kill()  # type: ignore[possibly-undefined]
        except Exception:  # pragma: no cover
            pass
    except Exception as exc:
        result.returncode = -2
        result.diagnostic = f"执行失败：{type(exc).__name__}: {exc}"
    finally:
        if previous_settings is not None:
            _restore_settings(target, previous_settings)
        if handle is not None:
            handle.stop()

    result.duration_s = time.time() - started
    result.stdout = "\n".join(lines)
    counts = _parse_counts(result.stdout)
    result.passed = counts.get("passed", 0)
    result.failed = counts.get("failed", 0)
    result.errors = counts.get("errors", 0)
    result.skipped = counts.get("skipped", 0)
    result.xfailed = counts.get("xfailed", 0)
    result.xpassed = counts.get("xpassed", 0)
    result.failed_cases = _collect_failed_cases(result.stdout)

    if not result.diagnostic:
        if "No module named pytest" in result.stdout:
            result.diagnostic = "当前环境未安装 pytest，请先执行：pip install pytest requests"
        elif result.total == 0:
            result.diagnostic = "未收集到任何用例，请检查生成的脚本与用例数据"
        else:
            result.diagnostic = "执行完成"
    LOGGER.info("执行结果：%s（%s）", result.summary, result.diagnostic)
    return result


__all__ = [
    "generate_suite",
    "run_pytest",
    "start_mock_server",
    "GenerationResult",
    "ExecutionResult",
    "SUT_PROJECT_DIR",
]
