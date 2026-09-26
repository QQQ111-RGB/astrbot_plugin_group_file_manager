"""蓝奏云文件管理器 - 部署自检脚本（不依赖 AstrBot，可直接运行）。

用途：插件在 AstrBot WebUI 里看不到时，用这个脚本快速排除
「目录结构 / 元数据 / 语法 / 依赖」这几类最常见的原因。

用法（把本文件拷到插件目录下再跑）::

    python selfcheck.py

或指定插件目录::

    python selfcheck.py D:\\AstrBot\\data\\plugins\\astrbot_plugin_group_file_manager
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN_DIR_NAME = "astrbot_plugin_group_file_manager"

# 插件运行必需的文件
REQUIRED_FILES = [
    "__init__.py",
    "main.py",
    "file_utils.py",
    "lanzou_store.py",
    "metadata.yaml",
    "_conf_schema.json",
    "pages/file-manager/index.html",
    "pages/file-manager/app.js",
    "pages/file-manager/style.css",
]

PROBLEMS: list[str] = []
WARNINGS: list[str] = []


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def bad(msg: str) -> None:
    print(f"  [问题] {msg}")
    PROBLEMS.append(msg)


def warn(msg: str) -> None:
    print(f"  [注意] {msg}")
    WARNINGS.append(msg)


def check_layout(root: Path) -> None:
    print("\n=== 1. 目录结构 ===")
    if not root.is_dir():
        bad(f"插件目录不存在: {root}")
        return

    if root.name != PLUGIN_DIR_NAME:
        warn(
            f"目录名是 {root.name!r}，不是 {PLUGIN_DIR_NAME!r}。"
            "AstrBot 按目录名注册插件，改名会导致插件名/Web API 路由与预期不一致。",
        )
    else:
        ok(f"目录名正确: {root.name}")

    # 常见错误：多套了一层目录（解压压缩包时最容易发生）
    nested = root / PLUGIN_DIR_NAME
    if nested.is_dir():
        bad(
            f"检测到嵌套目录 {PLUGIN_DIR_NAME}/ 在插件目录内。"
            "这通常是把压缩包整个解压进去导致的。"
            f"请把 {nested} 里的文件上移一层到 {root}。",
        )

    missing = [f for f in REQUIRED_FILES if not (root / f).is_file()]
    if missing:
        for f in missing:
            bad(f"缺少必需文件: {f}")
    else:
        ok(f"必需文件齐全（{len(REQUIRED_FILES)} 个）")

    if not (root / "pages" / "file-manager" / "index.html").is_file():
        bad("缺少 pages/file-manager/index.html —— WebUI 页面不会出现")

    # 告知是否存在开发自测目录（可删）
    if (root / "_devtest").is_dir():
        warn("_devtest/ 是开发自测目录，不影响运行，可以删除。")

    # 旧的 Python 字节码可能来自不同的 Python 版本，拷来拷去容易出问题
    for cache in root.rglob("__pycache__"):
        warn(f"存在字节码缓存 {cache.relative_to(root)}，建议删除后再重启（避免跨 Python 版本问题）")
        break


def check_metadata(root: Path) -> None:
    print("\n=== 2. metadata.yaml ===")
    path = root / "metadata.yaml"
    if not path.is_file():
        bad("metadata.yaml 不存在")
        return

    text = path.read_text(encoding="utf-8")
    if "\t" in text:
        bad("metadata.yaml 含 Tab 字符 —— YAML 禁止用 Tab 缩进，会导致解析失败！")

    fields: dict[str, str] = {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            bad("metadata.yaml 顶层不是键值对结构")
            return
        fields = {k: str(v) for k, v in data.items()}
        ok("YAML 语法解析通过（pyyaml）")
    except ImportError:
        # 没有 pyyaml 时做一次极简解析，只够检查必填项
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" in line and not line.startswith((" ", "-")):
                key, _, value = line.partition(":")
                # 去掉行内注释（YAML 中 " #" 之后是注释）
                value = value.split(" #", 1)[0]
                fields[key.strip()] = value.strip().strip('"\'')
        warn("本机没有 pyyaml，已用简化解析检查（AstrBot 自带 pyyaml，不影响运行）")
    except Exception as exc:  # noqa: BLE001
        bad(f"metadata.yaml 解析失败: {exc}")
        return

    for key in ("name", "author", "desc", "version"):
        if not fields.get(key):
            bad(f"metadata.yaml 缺少必填字段: {key}")
    if fields.get("name") and fields["name"] != PLUGIN_DIR_NAME:
        warn(
            f"metadata 的 name={fields['name']!r} 与目录名 {PLUGIN_DIR_NAME!r} 不一致。"
            "Web API 路由用的是 name，请保持两者一致。",
        )

    if fields.get("author") in {"your_name", ""}:
        warn("author 还是占位符 your_name，建议改成你自己的名字。")
    if "your_name" in fields.get("repo", ""):
        warn("repo 还是占位符，建议改成你自己的仓库地址。")

    ver = fields.get("astrbot_version")
    if ver:
        print(f"         astrbot_version 约束: {ver}")
        warn(
            "若你的 AstrBot 版本不满足上面这个约束，插件会被【直接拦截加载】，"
            "表现为 WebUI 里完全看不到。可用下面的命令查你的 AstrBot 版本；"
            "实在不确定就把 metadata.yaml 里这一行删掉再试。",
        )
        print("         查询版本: python -c \"import astrbot;print(astrbot.__version__)\"")
    ok(f"已读到字段: {', '.join(sorted(fields))}")


def check_python(root: Path) -> None:
    print("\n=== 3. Python 语法 / 导入 ===")
    import py_compile

    py_files = [
        "__init__.py",
        "main.py",
        "file_utils.py",
        "lanzou_store.py",
    ]
    failed = False
    for name in py_files:
        target = root / name
        if not target.is_file():
            continue
        try:
            py_compile.compile(str(target), doraise=True)
            ok(f"{name} 语法正确")
        except py_compile.PyCompileError as exc:
            failed = True
            bad(f"{name} 语法错误: {exc}")
    if failed:
        return

    print(f"         当前 Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        bad("Python < 3.10：本插件使用了 int | str 等新语法，需要 3.10 或更高")

    # lanzou-api 是可选依赖：只有开启蓝奏云链接提取（lanzou_enabled）才需要
    try:
        import lanzou.api  # noqa: F401

        ok("可选依赖 lanzou-api 已安装（蓝奏云链接提取可用）")
    except ImportError:
        warn(
            "未安装可选依赖 lanzou-api：蓝奏云链接提取（lanzou_enabled）不可用。"
            "需要时执行 pip install lanzou-api",
        )


def check_conf_schema(root: Path) -> None:
    print("\n=== 4. _conf_schema.json ===")
    path = root / "_conf_schema.json"
    if not path.is_file():
        warn("_conf_schema.json 不存在（可选，但配置项会缺失）")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ok(f"JSON 合法（{len(data)} 个配置项）")
    except Exception as exc:  # noqa: BLE001
        bad(f"_conf_schema.json 不是合法 JSON: {exc}")


def check_pages(root: Path) -> None:
    print("\n=== 5. Pages 静态资源 ===")
    page = root / "pages" / "file-manager"
    if not page.is_dir():
        bad("pages/file-manager/ 目录不存在")
        return
    html = page / "index.html"
    if not html.is_file():
        bad("pages/file-manager/index.html 不存在")
        return
    ok("pages/file-manager/index.html 存在")

    text = html.read_text(encoding="utf-8")
    for ref in ("./app.js", "./style.css"):
        if ref not in text:
            warn(f"index.html 未引用 {ref}（页面可能不完整）")
        elif not (page / ref[2:]).is_file():
            bad(f"index.html 引用了 {ref}，但文件不存在")

    # 前后端 endpoint 交叉校验：app.js 里 bridge.apiGet/apiPost 调用的 endpoint
    # 必须在 main.py 的 _register_web_apis 中注册过（前缀由后端补上）。
    js_file = page / "app.js"
    main_file = root / "main.py"
    if js_file.is_file() and main_file.is_file():
        js_text = js_file.read_text(encoding="utf-8")
        py_text = main_file.read_text(encoding="utf-8")
        used = set(re.findall(r'bridge\.api(?:Get|Post)\(\s*"([^"]+)"', js_text))
        registered = set(
            hit.lstrip("/")
            for hit in re.findall(r'f"/\{PLUGIN_NAME\}([^"]*)"', py_text)
        )
        missing = sorted(endpoint for endpoint in used if endpoint not in registered)
        if missing:
            bad(f"前端调用了后端未注册的 API：{', '.join(missing)}")
        else:
            ok(f"前后端 API endpoint 一致（{len(used)} 个）")


def main() -> int:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1]).resolve()
    else:
        root = Path(__file__).resolve().parent

    print("蓝奏云文件管理器 - 部署自检")
    print(f"插件目录: {root}")

    check_layout(root)
    check_metadata(root)
    check_python(root)
    check_conf_schema(root)
    check_pages(root)

    print("\n" + "=" * 58)
    if PROBLEMS:
        print(f"发现 {len(PROBLEMS)} 个会导致插件加载失败的问题：")
        for p in PROBLEMS:
            print(f"  * {p}")
    else:
        print("没有发现会导致加载失败的问题。")
    if WARNINGS:
        print(f"\n另外有 {len(WARNINGS)} 条提醒（不一定阻塞加载）：")
        for w in WARNINGS:
            print(f"  - {w}")

    print("\n如果这里全部通过但仍然看不到插件，请看 AstrBot 启动日志里带")
    print("astrbot_plugin_group_file_manager 或 'Failed to load' 的几行，")
    print("那是最直接的线索。")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
