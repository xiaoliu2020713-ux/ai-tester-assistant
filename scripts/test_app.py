"""界面冒烟测试：用 Streamlit 官方 AppTest 无头执行 app.py。

作用：在没有人打开浏览器的情况下，真实跑一遍 Streamlit 脚本，
捕获语法/API 误用/会话状态错误等只有运行时才会暴露的问题。

运行：
    python scripts/test_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

from streamlit.testing.v1 import AppTest  # noqa: E402

FAILURES: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(_console.safe(("✅ " if condition else "❌ ") + name + (f" — {detail}" if detail else "")))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    print("=" * 70)
    print("Streamlit 界面无头测试（AppTest）")
    print("=" * 70)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()

    check("脚本执行无异常", not at.exception, str([e.value for e in at.exception][:1]))
    if at.exception:
        for exc in at.exception:
            print("  异常：", exc.value)

    titles = " ".join(m.value for m in at.markdown)
    check("显示 AI 测试员角色", "AI 测试员" in titles, "页面标题含「AI 测试员」")
    check("模型配置区存在", len(at.sidebar.text_input) >= 3, f"侧边栏输入框 {len(at.sidebar.text_input)} 个")
    check("知识域单选存在", len(at.sidebar.radio) >= 2, f"侧边栏 radio {len(at.sidebar.radio)} 个")
    check("聊天输入框存在", len(at.chat_input) >= 1)
    check("文档粘贴框存在", len(at.text_area) >= 1, f"text_area {len(at.text_area)} 个")
    check("上传组件存在", len(at.get("file_uploader")) >= 1)
    check("快捷指令按钮存在", len(at.button) >= 5, f"按钮 {len(at.button)} 个")

    # 交互：切换知识域到电商平台，再切换到 DeepSeek，确认不报错
    try:
        domain_radio = at.sidebar.radio[0]
        domain_radio.set_value("电商平台").run()
        check("切换知识域无异常", not at.exception)
    except Exception as exc:  # pragma: no cover
        check("切换知识域无异常", False, f"{type(exc).__name__}: {exc}")

    try:
        provider_radio = at.sidebar.radio[1]
        provider_radio.set_value("DeepSeek 官方 API").run()
        check("切换模型提供方无异常", not at.exception)
    except Exception as exc:  # pragma: no cover
        check("切换模型提供方无异常", False, f"{type(exc).__name__}: {exc}")

    # 交互：粘贴 API 文档并点「直接让 AI 分析」（只检查不崩溃；模型不可用时会展示错误提示）
    try:
        at.text_area[0].set_value("POST /api/orders\n请求体：skuId(必填)\n").run()
        analyze = [b for b in at.button if "直接让 AI 分析" in (b.label or "")]
        if analyze:
            analyze[0].click().run()
            check("点击「直接让 AI 分析」无异常", not at.exception, "模型不可用时应显示错误提示而非崩溃")
        else:
            check("点击「直接让 AI 分析」无异常", False, "未找到按钮")
    except Exception as exc:  # pragma: no cover
        check("点击「直接让 AI 分析」无异常", False, f"{type(exc).__name__}: {exc}")

    # 交互：阶段二页签（结构化用例 → 生成 pytest 脚本）
    try:
        source_radio = at.radio(key="exec_source")
        source_radio.set_value("内置演示用例").run()
        check("切换到内置演示用例无异常", not at.exception, "免模型即可体验阶段二全流程")
    except Exception as exc:  # pragma: no cover
        check("切换到内置演示用例无异常", False, f"{type(exc).__name__}: {exc}")

    try:
        demo_buttons = [b for b in at.button if "载入演示用例" in (b.label or "")]
        if demo_buttons:
            demo_buttons[0].click().run()
            check("一键载入演示用例无异常", not at.exception, "演示用例灌入会话")
        else:
            check("一键载入演示用例无异常", False, "未找到按钮")
    except Exception as exc:  # pragma: no cover
        check("一键载入演示用例无异常", False, f"{type(exc).__name__}: {exc}")

    try:
        gen_buttons = [b for b in at.button if "生成 pytest 脚本" in (b.label or "")]
        if gen_buttons:
            gen_buttons[0].click().run()
            check("生成 pytest 脚本无异常", not at.exception)
            codes = " ".join(str(c.value) for c in at.code if isinstance(c.value, str))
            check("界面展示了生成产物清单", "conftest.py" in codes or "support_cases.json" in codes,
                  "生成文件列表已渲染")
        else:
            check("生成 pytest 脚本无异常", False, "未找到按钮")
    except Exception as exc:  # pragma: no cover
        check("生成 pytest 脚本无异常", False, f"{type(exc).__name__}: {exc}")

    run_buttons = [b for b in at.button if "运行测试" in (b.label or "")]
    check("存在「运行测试」按钮", bool(run_buttons), f"按钮 {len(run_buttons)} 个")

    print()
    if FAILURES:
        print(f"❌ 界面测试失败 {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("✅ 界面无头测试全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
