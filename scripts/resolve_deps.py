"""依赖解析器 v3：带约束传播的 PyPI 解析，生成可 `--no-deps` 安装的锁定清单。

为什么需要它
------------
本机 `pip install -r requirements.txt` 会在 langchain / chromadb 的候选版本之间长时间回溯
（表现为长时间无输出，实测超过 10 分钟仍不推进）。本脚本改为：

    1. 读取 requirements.txt 的顶层约束（如 `chromadb>=0.5.5,<0.7`）；
    2. 查询 PyPI JSON API，按 **当前 Python 版本 + 平台** 过滤出有 wheel 的候选版本；
    3. 自底向上做约束传播（父包对子包的 `requires_dist` 版本区间会约束子包选择），
       选中「满足全部约束的最高可用版本」，保证互相兼容；
    4. 输出 requirements.lock，配合 `pip install --no-deps` 一次性安装。

v2 的问题：忽略父包约束，直接取每个包的最新版，导致
pandas 3 vs streamlit 的 `pandas<3`、protobuf 7 vs `protobuf<7`、
langgraph 需要 langchain-core 1.x 等冲突。v3 通过约束传播解决。

用法：
    python scripts/resolve_deps.py
    python scripts/resolve_deps.py --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPI = "https://pypi.org/pypi/{name}/json"

# 不安装的包：体积巨大 / 与本平台无关 / 运行时不需要
SKIP = {
    "torch",
    "tensorflow",
    "sentence-transformers",
    "onnxruntime-gpu",
    "pytest",
    "pytest-asyncio",
    "mypy",
    "ruff",
    "black",
    "sphinx",
    "twine",
    "build",          # chromadb 声明但运行时不需要
    "watchdog",       # streamlit 的可选文件监听依赖
    "dataclasses",    # py3.13 以下不需要的 backport
    "typing",         # 老 backport（3.10 包名，装不上）
    "enum34",
    "mock",
    "nose",
    "async-timeout",
    "exceptiongroup",
}

# 当前运行环境（用于环境标记判断与 wheel 兼容性判断）
PY_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"
PY_VER = f"{sys.version_info.major}.{sys.version_info.minor}"
MARKER_ENV = {
    "python_version": PY_VER,
    "python_full_version": f"{PY_VER}.{sys.version_info.micro}",
    "sys_platform": "win32" if sys.platform.startswith("win") else sys.platform,
    "platform_system": "Windows" if sys.platform.startswith("win") else sys.platform,
    "platform_machine": "AMD64",
    "os_name": "nt" if sys.platform.startswith("win") else "posix",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
}

_cache: dict = {}
_version_cache: dict = {}
failures: dict = {}
# 预发布版本识别：需要覆盖 0.50b0 / 1.0a2 / 2.0rc1 / 1.0.dev3 / 0.66b1 等写法。
# 判断方式：只看版本号最后一段，避免把普通版本里的字母误判（注意 0.50b0 的小版本是两位数字）。
_PRERELEASE_RE = re.compile(r"^\d*(a|b|rc|alpha|beta|pre|dev|post)\d*$", re.IGNORECASE)


def is_prerelease(version: str) -> bool:
    main = version.split("+")[0]
    if ".dev" in main:
        return True
    last = main.split(".")[-1]
    return bool(_PRERELEASE_RE.match(last))


def norm_name(name: str) -> str:
    """PEP 503 名称规范化：小写并把 -/_/. 统一为 -，避免同一包被当成两个包。"""
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


SKIP_KEYS = {norm_name(name) for name in SKIP}

_REQ_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(\[[^\]]*\])?\s*(.*)$")
# 注意：PyPI 的 requires_dist 里操作符后可能带空格（如 `< 2.0.0`），必须允许
_CLAUSE_RE = re.compile(r"^(==|>=|<=|>|<|~=|!=)\s*([0-9][^\s,]*)$")

# ---------------------------------------------------------------------------
# 版本处理
# ---------------------------------------------------------------------------
def ver_key(version: str) -> tuple:
    """版本号 → 可比较元组。"""
    main = version.split("+")[0]
    parts = re.split(r"[.\-_]", main)
    result = []
    for part in parts:
        if part.isdigit():
            result.append((2, int(part), ""))
            continue
        match = re.match(r"^(\d+)([A-Za-z].*)$", part)
        if match:
            result.append((2, int(match.group(1)), match.group(2)))
            continue
        pre = re.match(r"^(a|b|rc|alpha|beta|pre|dev|post)(\d*)$", part)
        if pre:
            order = {"dev": -3, "a": -2, "alpha": -2, "b": -1, "beta": -1, "rc": 0, "pre": 0, "post": 1}
            result.append((order.get(pre.group(1), -1), int(pre.group(2) or 0), part))
            continue
        if part:
            result.append((1, 0, part))
    return tuple(result)


def _pad(left: tuple, right: tuple) -> tuple:
    size = max(len(left), len(right))
    filler = (2, 0, "")
    return left + (filler,) * (size - len(left)), right + (filler,) * (size - len(right))


def matches(version: str, spec: str) -> bool:
    """判断版本是否满足约束串（支持 `==1.*` 通配符与 `~=` 兼容版本）。"""
    if not spec:
        return True
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = _CLAUSE_RE.match(clause)
        if not match:
            continue
        op, target = match.group(1), match.group(2)
        if target.endswith(".*"):
            prefix = ver_key(target[:-2])
            same = ver_key(version)[: len(prefix)] == prefix
            if (op == "==" and not same) or (op == "!=" and same):
                return False
            continue
        left, right = _pad(ver_key(version), ver_key(target))
        if op == "~=":
            # 兼容版本（PEP 440）：~=X.Y -> >=X.Y,<X+1 ；~=X.Y.Z -> >=X.Y.Z,<X.Y+1
            release = [int(x) for x in re.findall(r"\d+", target)]
            if len(release) >= 3:
                upper_parts = release[:-1]
                upper_parts[-1] += 1
            elif len(release) == 2:
                upper_parts = [release[0] + 1]
            else:
                upper_parts = [release[0] + 1] if release else [0]
            upper = ver_key(".".join(str(x) for x in upper_parts))
            ok = left >= right and ver_key(version) < _pad(ver_key(version), upper)[1]
        else:
            ok = {
                "==": left == right,
                ">=": left >= right,
                "<=": left <= right,
                ">": left > right,
                "<": left < right,
                "!=": left != right,
            }[op]
        if not ok:
            return False
    return True


# ---------------------------------------------------------------------------
# PyPI
# ---------------------------------------------------------------------------
def fetch(name: str):
    if name in _cache:
        return _cache[name]
    try:
        with urllib.request.urlopen(PYPI.format(name=name), timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:
        failures.setdefault(name, f"PyPI 查询失败: {exc}")
        payload = None
    _cache[name] = payload
    return payload


def wheel_ok(filename: str) -> bool:
    """wheel 是否适用于当前平台与 Python 版本（Windows x64 + 当前 CPython）。"""
    if filename.endswith("-none-any.whl"):
        return True
    if "win_amd64" not in filename and "win32" not in filename:
        return False
    if "abi3" in filename:
        # 稳定 ABI：cp3x-abi3 可用于更高版本解释器
        tags = re.findall(r"cp(\d)(\d+)-abi3", filename)
        return any(int(minor) <= sys.version_info.minor for _, minor in tags) or not tags
    tags = re.findall(r"cp(\d{2,3})", filename)
    if not tags:
        return True
    return PY_TAG[2:] in tags


def candidates(name: str) -> dict:
    """{version: [wheel filenames]}，仅保留本平台/本 Python 可用且有 wheel 的版本。

    预发布版本策略：只排除「比最新稳定版更新的预发布」（例如 6.0b1 之于 6.0.3），
    保留比稳定版更早的预发布（例如 opentelemetry 系列用 0.50b0 / 1.29.0 并行发版，
    以及仅以预发布形式发布的 0.66b1），否则会出现"合法旧版本被过滤掉 → 无解回退"。
    """
    payload = fetch(name)
    if not payload:
        return {}
    releases = payload.get("releases") or {}
    stables = [v for v in releases if not is_prerelease(v)]
    latest_stable = max(stables, key=ver_key) if stables else ""

    out = {}
    for version, files in releases.items():
        if not files:
            continue
        if is_prerelease(version) and latest_stable and ver_key(version) > ver_key(latest_stable):
            continue
        usable = [f["filename"] for f in files if f.get("packagetype") == "bdist_wheel" and wheel_ok(f["filename"])]
        if usable:
            out[version] = usable
    return out


def parse_requirement(raw: str):
    body, _, marker = raw.partition(";")
    match = _REQ_RE.match(body.strip())
    if not match:
        return None
    return match.group(1), (match.group(3) or "").strip(), marker.strip()


def marker_ok(marker: str) -> bool:
    if not marker:
        return True
    if "extra ==" in marker or "extra==" in marker:
        return False
    text = marker
    for key, value in MARKER_ENV.items():
        text = text.replace(key, f'"{value}"')
    try:
        return bool(eval(text, {"__builtins__": {}}, {}))  # noqa: S307 - 本地标记判断
    except Exception:
        return True


def read_constraints(path: Path) -> list:
    constraints = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_requirement(line)
        if parsed:
            constraints.append((parsed[0], parsed[1]))
    return constraints


# ---------------------------------------------------------------------------
# 约束传播解析
# ---------------------------------------------------------------------------
def fetch_version(name: str, version: str):
    """获取**指定版本**的元数据。

    关键点：PyPI 的 `info.requires_dist` 只反映该包的**最新版**依赖，
    用它去判断历史版本的依赖会得出错误结论（实测会把 langchain-openai 0.2.x
    误判为要求 langchain-core>=1.6.6）。因此这里逐版本查询
    `/pypi/{name}/{version}/json`。
    """
    key = (norm_name(name), version)
    if key in _version_cache:
        return _version_cache[key]
    try:
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
        requires = list((payload.get("info") or {}).get("requires_dist") or [])
    except Exception as exc:
        failures.setdefault(f"{name}=={version}", f"版本元数据查询失败: {exc}")
        requires = []
    _version_cache[key] = requires
    return requires


def _requires_of(payload: dict, version: str, name: str = "") -> list:
    """取指定版本的 requires_dist（优先精确版本接口，失败再退回包级元数据）。"""
    if name:
        requires = fetch_version(name, version)
        if requires:
            return requires
    return list((payload.get("info") or {}).get("requires_dist") or [])


def _deps_from_requires(requires: list) -> list:
    deps = []
    for raw in requires:
        parsed = parse_requirement(raw)
        if not parsed:
            continue
        dep, dep_spec, marker = parsed
        if not marker_ok(marker):
            continue
        deps.append((dep, dep_spec))
    return deps


def resolve(top: list, *, verbose: bool = False, budget: int = 4000) -> tuple:
    """回溯式依赖解析（带约束传播）。

    返回 ``(resolved, notes)``，resolved 为 ``{name: (display_name, version)}``。

    思路：
        * 深度优先 + 时间顺序回溯：某个包选定版本后，若其依赖导致无解，
          则撤销该决定并尝试它的下一个（更低）候选版本；
        * 优先处理约束最多的包（可选项最少，最容易先撞车）；
        * 版本依赖信息按 (包, 版本) 懒加载并缓存；先用 info.requires_dist 快速试算，
          命中后不再请求单版本元数据，从而把网络请求压到可控范围。
    """
    spec_map: dict = {}      # 包 -> 约束串列表
    assert_stack: list = []  # 已生效的依赖断言 (parent, dep, dep_spec)
    resolved: dict = {}
    notes: list = []
    steps = {"count": 0, "truncated": False}
    dead_ends: set = set()   # (包, 版本, 约束指纹) 已证明不可行，避免重复搜索

    def add_spec(name: str, spec: str) -> None:
        key = norm_name(name)
        spec_map.setdefault(key, [])
        if spec and spec not in spec_map[key]:
            spec_map[key].append(spec)

    for name, spec in top:
        add_spec(name, spec)

    def applies(key: str, version: str) -> bool:
        return all(matches(version, spec) for spec in spec_map.get(key, []))

    def ordered_unresolved() -> list:
        return sorted(
            (n for n in spec_map if n not in resolved and n not in SKIP_KEYS),
            key=lambda n: (-len(spec_map[n]), n),
        )

    def dfs() -> bool:
        if steps["count"] > budget:
            steps["truncated"] = True
            return True  # 预算耗尽，接受当前部分结果（best effort）
        pending = ordered_unresolved()
        if not pending:
            return True
        key = pending[0]
        payload = fetch(key) or {}
        all_versions = candidates(key)
        if not all_versions:
            failures.setdefault(key, f"没有适用于 Python {PY_VER} / {MARKER_ENV['sys_platform']} 的 wheel 版本")
            resolved[key] = (key, "0.0.0-UNRESOLVED")
            return dfs()
        usable = sorted((v for v in all_versions if applies(key, v)), key=ver_key, reverse=True)
        if not usable:
            usable = [max(all_versions, key=ver_key)]
            notes.append(f"{key}: 约束 {spec_map[key]} 无解，回退到 {usable[0]}")

        # 记忆化：同一包同一版本在同一组约束下失败过，就不再重试（避免指数级重复搜索）
        fingerprint = tuple(sorted(spec_map[key]))
        attempts = [v for v in usable if (key, v, fingerprint) not in dead_ends]
        if not attempts:
            resolved.pop(key, None)
            if verbose:
                print(f"  ! {key} 的候选版本均已被证明不可行")
            return False

        for version in attempts:
            if steps["count"] > budget:
                steps["truncated"] = True
                return True
            steps["count"] += 1
            resolved[key] = (key, version)
            deps = _deps_from_requires(_requires_of(payload, version, key))

            # 快速失败：新依赖与已解析包冲突时，直接换下一个候选版本
            conflict = None
            for dep, dep_spec in deps:
                dep_key = norm_name(dep)
                if dep_key in SKIP_KEYS or dep_key == key or not dep_spec:
                    continue
                existing = resolved.get(dep_key)
                if existing and existing[1] != "0.0.0-UNRESOLVED" and not matches(existing[1], dep_spec):
                    conflict = (dep, dep_spec, existing[1])
                    break
            if conflict:
                if verbose:
                    print(f"  x {key}=={version} 与 {conflict[0]}=={conflict[2]} 冲突（要求 {conflict[1]}）")
                dead_ends.add((key, version, fingerprint))
                resolved.pop(key, None)
                continue

            marker = len(assert_stack)
            for dep, dep_spec in deps:
                dep_key = norm_name(dep)
                if dep_key in SKIP_KEYS or dep_key == key:
                    # 跳过自引用（如 langchain-core 声明 `langchain-core[...]` 作为自身 extras），
                    # 否则会造成解析器无限递归。
                    continue
                assert_stack.append((key, dep, dep_spec))
                add_spec(dep, dep_spec)

            if dfs():
                return True

            # 回溯：撤销本轮加入的约束，并记录该 (包, 版本, 约束集) 为死路
            dead_ends.add((key, version, fingerprint))
            del assert_stack[marker:]
            _rebuild_spec_map()

        resolved.pop(key, None)
        if verbose:
            print(f"  ! {key} 的所有候选版本都不可行")
        return False

    def _rebuild_spec_map() -> None:
        spec_map.clear()
        for name, spec in top:
            add_spec(name, spec)
        for _parent, dep, dep_spec in assert_stack:
            add_spec(dep, dep_spec)

    ok = dfs()
    if steps["truncated"]:
        notes.append(f"解析步数达到上限 {budget}，结果为尽力而为（可能有约束回退）")
    if not ok:
        notes.append("存在无法满足的依赖约束，请检查 requirements.txt 的版本区间")
    return resolved, notes


def main() -> int:
    import _console

    _console.setup()
    parser = argparse.ArgumentParser(description="解析依赖并生成 requirements.lock")
    parser.add_argument("--out", default="requirements.lock")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    constraints = read_constraints(ROOT / "requirements.txt")
    print(f"顶层约束（{len(constraints)}）：" + ", ".join(f"{n}{s}" for n, s in constraints))
    resolved, notes = resolve(constraints, verbose=args.verbose)
    print(f"解析完成：{len(resolved)} 个包（{len(notes)} 条约束传播记录）")

    lines = [
        "# 由 scripts/resolve_deps.py 生成：经过约束传播的精确版本锁定",
        f"# 目标环境：Python {PY_VER} / {MARKER_ENV['sys_platform']}",
        "# 安装： python -m pip install --no-deps --only-binary=:all: -r requirements.lock",
        "",
    ]
    for key in sorted(resolved, key=str.lower):
        name, version = resolved[key]
        lines.append(f"{name}=={version}")
    out_path = ROOT / args.out
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"-> {out_path}")

    if failures:
        print(f"未解析 {len(failures)} 个：")
        for name, reason in sorted(failures.items()):
            print(f"  - {name}: {reason}")
    return 0 if resolved else 1


if __name__ == "__main__":
    sys.exit(main())
