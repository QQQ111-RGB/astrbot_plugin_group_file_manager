"""蓝奏云文件管理器 - AstrBot 插件主入口（只读链接提取）。

功能概览
--------
本插件**不索引、不识别、不保存、不发送任何群文件**，只做一件事：
把你**自己蓝奏云网盘**里已有的文件，变成群友可用的**永久分享链接**。

1. **不识别群内文件**：不监听群文件上传事件、不导入群文件库、不保存任何群文件
   记录（没有索引 KV、没有任何本地缓存）。
2. **蓝奏云只读**：按 ``lanzou_cache_minutes`` 定期读取你网盘指定目录的文件清单
   （只读，不写、不改、不转存），失败时用旧缓存兜底。
3. **搜索 / 下载**：群内发「搜索 <关键词>」卡片式模糊搜索网盘文件（带数字文件ID
   与分页），发「下载 <文件ID或文件名>」取永久分享链接。
4. **WebUI 页面**：``pages/file-manager/``，列出网盘文件、搜索、一键复制链接。

为什么不索引群文件
------------------
QQ 群文件链接有有效期，而且部分协议端（如 NapCat 在某些 QQ 版本上）的文件接口
不可用。改用「你自己把文件传蓝奏云 + 插件只读提取永久链接」后：不产生副本、
不受单文件大小限制、**完全不依赖协议端的文件接口**。

API 核实说明
------------
* 群文件相关的适配器接口（``get_group_file_url`` 等）本插件**不再使用**，
  因此不依赖 NapCat packetBackend 之类的协议端能力。
* ``lanzou-api`` 与现网的三处偏差已做兼容，详见 ``lanzou_store.py`` 与 README
  的「蓝奏云链接提取」章节。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request
from astrbot.core.star.filter.command import GreedyStr

from .file_utils import human_size, normalize_name, now_ts, numeric_id
from .lanzou_store import LanzouClient, LanzouError

PLUGIN_NAME = "astrbot_plugin_group_file_manager"

_DEFAULT_CONFIG: dict[str, Any] = {
    # 群内可直接发送「搜索 <关键词>」「下载 <文件ID>」
    "quick_commands": True,
    # 卡片式搜索结果每页条数
    "search_page_size": 5,
    # ---- 蓝奏云链接提取（只读，Cookie 登录） --------------------------
    "lanzou_enabled": False,
    "lanzou_cookie_ylogin": "",
    "lanzou_cookie_phpdisk_info": "",
    "lanzou_folder": "",
    "lanzou_recursive": True,
    "lanzou_cache_minutes": 30,
}


@register(
    PLUGIN_NAME,
    "your_name",
    "蓝奏云链接提取（只读）：读取你自己蓝奏云网盘的文件清单，「搜索 <关键词>」卡片式模糊搜索，「下载 <文件ID>」给永久分享链接。不索引群文件、不下载、不转存。",
    "3.0.0",
    "https://github.com/your_name/astrbot_plugin_group_file_manager",
)
class GroupFileManagerPlugin(Star):
    """蓝奏云文件管理器插件（只读链接提取，不碰群文件）。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)

        # 插件配置（来自 _conf_schema.json）。手动实例化时可能为 None。
        self.config: dict[str, Any] = {**_DEFAULT_CONFIG, **(config or {})}

        # ---- 蓝奏云链接提取（只读） ------------------------------------
        self._lanzou: LanzouClient | None = None
        # 蓝奏云文件清单缓存：归一化文件名 -> {"fid", "name", "size", "time"}
        self._lanzou_map: dict[str, dict[str, Any]] = {}
        self._lanzou_map_at: float = 0.0
        # 已取过的分享链接缓存：fid -> {"url", "pwd", ...}
        self._lanzou_share_cache: dict[int, dict[str, Any]] = {}
        self._lanzou_refresh_lock = asyncio.Lock()

        # ---- 注册 Web API（路径必须以插件名为前缀） --------------------
        self._register_web_apis()

        logger.info(
            "[group-file-manager] 已加载（只读链接提取；蓝奏云 %s）",
            "已配置" if self._lanzou_configured() else "未配置",
        )

    # ================================================================== #
    # 生命周期
    # ================================================================== #
    async def initialize(self) -> None:
        """插件激活。只读模式无需做任何准备工作。"""

    async def terminate(self) -> None:
        """插件卸载/重载。没有后台任务需要清理。"""

    # ================================================================== #
    # 蓝奏云链接提取（可选，只读）：按文件名匹配用户网盘里已有的文件
    # ================================================================== #
    def _lanzou_configured(self) -> bool:
        """蓝奏云提取是否已启用且填了 Cookie。

        只支持 Cookie 登录：蓝奏云的账号密码登录接口已被标记弃用、且现网控制台
        页面结构变更，实测无法登录，因此插件不再提供该选项。
        """
        if not self.config.get("lanzou_enabled", False):
            return False
        return bool(
            self.config.get("lanzou_cookie_ylogin")
            and self.config.get("lanzou_cookie_phpdisk_info")
        )

    def _get_lanzou_client(self) -> LanzouClient:
        """按当前配置构造（并缓存）蓝奏云客户端。构造本身不做网络请求。"""
        if self._lanzou is None:
            self._lanzou = LanzouClient(
                cookie_ylogin=str(self.config.get("lanzou_cookie_ylogin") or ""),
                cookie_phpdisk_info=str(
                    self.config.get("lanzou_cookie_phpdisk_info") or "",
                ),
                folder_name=str(self.config.get("lanzou_folder") or ""),
            )
        return self._lanzou

    def _lanzou_cache_ttl(self) -> float:
        """蓝奏云文件清单的缓存时长（秒）。"""
        try:
            minutes = float(self.config.get("lanzou_cache_minutes") or 30)
        except (TypeError, ValueError):
            minutes = 30.0
        return max(1.0, minutes) * 60

    def _lanzou_folder_label(self) -> str:
        """当前匹配的网盘目录显示名（空目录名 = 根目录）。"""
        return str(self.config.get("lanzou_folder") or "").strip() or "根目录"

    async def _lanzou_file_map(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        """获取蓝奏云文件清单（归一化文件名 -> {fid, name, size, time}），带 TTL 缓存。

        列目录很便宜（每个文件夹一次请求），清单用于「搜索/下载」时定位文件。
        失败时返回旧缓存兜底。
        """
        if not self._lanzou_configured():
            return {}
        now = now_ts()
        if not force and self._lanzou_map and now - self._lanzou_map_at < self._lanzou_cache_ttl():
            return self._lanzou_map
        async with self._lanzou_refresh_lock:
            # 拿到锁后再检查一次，避免并发重复刷新
            if (
                not force
                and self._lanzou_map
                and now_ts() - self._lanzou_map_at < self._lanzou_cache_ttl()
            ):
                return self._lanzou_map
            try:
                files = await self._get_lanzou_client().list_files(
                    recursive=bool(self.config.get("lanzou_recursive", True)),
                )
            except LanzouError as exc:
                logger.warning("[group-file-manager] 读取蓝奏云文件清单失败: %s", exc)
                return self._lanzou_map
            new_map: dict[str, dict[str, Any]] = {}
            for item in files:
                key = normalize_name(item["name"])
                if key and key not in new_map:
                    new_map[key] = item
            self._lanzou_map = new_map
            self._lanzou_map_at = now_ts()
            if force:
                # 强制刷新说明用户可能刚更新过网盘，旧的分享链接缓存一并失效
                self._lanzou_share_cache.clear()
            logger.info(
                "[group-file-manager] 蓝奏云文件清单已刷新：%s 个文件",
                len(new_map),
            )
            return self._lanzou_map

    async def _lanzou_share(self, fid: int) -> dict[str, Any] | None:
        """按需取某个蓝奏云文件的分享链接（带缓存）。失败返回 ``None``。"""
        fid = int(fid)
        if fid in self._lanzou_share_cache:
            return self._lanzou_share_cache[fid]
        try:
            info = await self._get_lanzou_client().share_info(fid)
        except LanzouError as exc:
            logger.warning(
                "[group-file-manager] 获取蓝奏云分享链接失败 fid=%s: %s",
                fid,
                exc,
            )
            return None
        self._lanzou_share_cache[fid] = info
        return info

    @staticmethod
    def _lanzou_entry_id(fid: int) -> str:
        """网盘文件的「文件ID」：由 fid 派生，重启后稳定、不直接暴露 fid。"""
        return numeric_id(f"lz_{fid}")

    @staticmethod
    def _lanzou_search_entries(
        fmap: dict[str, dict[str, Any]],
        keyword: str,
    ) -> list[dict[str, Any]]:
        """在网盘清单里按关键词模糊搜索（归一化 + 子串）。

        关键词为空表示「列出全部」。结果按网盘上传日期倒序（``time`` 形如
        ``2026-09-26``，可直接比较）。
        """
        kw = (keyword or "").strip().lower()
        kw_norm = normalize_name(keyword)
        hits: list[dict[str, Any]] = []
        for item in fmap.values():
            name = item["name"]
            if not kw or kw in name.lower() or (kw_norm and kw_norm in normalize_name(name)):
                hits.append(item)
        hits.sort(key=lambda e: (e.get("time") or "", e["name"]), reverse=True)
        return hits

    @staticmethod
    def _lanzou_find_entry(
        fmap: dict[str, dict[str, Any]],
        file_ref: str,
    ) -> dict[str, Any] | None:
        """在网盘清单里按「派生数字ID」或文件名定位一个文件。"""
        ref = (file_ref or "").strip()
        if not ref or not fmap:
            return None
        if ref.isdigit():
            for item in fmap.values():
                if numeric_id(f"lz_{item['fid']}") == ref:
                    return item
        ref_lower = ref.lower()
        for item in fmap.values():
            if item["name"].lower() == ref_lower:
                return item
        key = normalize_name(ref)
        if key:
            for item in fmap.values():
                if normalize_name(item["name"]) == key:
                    return item
            fuzzy = [i for i in fmap.values() if key in normalize_name(i["name"])]
            if len(fuzzy) == 1:
                return fuzzy[0]
        return None

    @staticmethod
    def _share_fields(share: dict[str, Any]) -> dict[str, Any]:
        """把分享信息里**真实存在**的可选字段拼进响应。

        蓝奏云只提供「分享链接 + 提取码」（lanzou-api 的 ``ShareInfo`` 也只有
        ``code / name / url / desc / pwd``）。短链接、口令是网盘类机器人对
        **百度网盘**那套的输出，蓝奏云并没有——所以这里只在 ``share`` 确实
        带了对应键时才输出，绝不拿长链接去冒充短链接。
        """
        out: dict[str, Any] = {}
        short_url = str(share.get("short_url") or "").strip()
        if short_url and short_url != (share.get("url") or ""):
            out["short_url"] = short_url
        command = str(share.get("command") or "").strip()
        if command:
            out["command"] = command
        return out

    # ================================================================== #
    # 指令：/file（别名 /群文件 /gf）
    # ================================================================== #
    @filter.command("file", alias={"群文件", "gf"})
    async def file_command(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr):
        """蓝奏云文件管理指令。

        用法::

            /file list [页码]          列出蓝奏云网盘里的全部文件
            /file search <关键词> [页码] 按文件名模糊搜索网盘文件
            /file download <ID或名称>  获取文件详情与永久分享链接
            /file lzrefresh           刷新蓝奏云文件清单（管理员）
            /file help                显示帮助

        .. important::
           参数必须通过签名接收，**不能**去解析 ``event.message_str``。

           AstrBot 的 ``CommandFilter.filter()`` 只用剥掉命令名后的文本
           （``message_str[len(full_cmd):]``）去做参数解析，**并不会**修改
           ``event.message_str`` 本身——所以 ``/file list`` 触发的 handler 里
           ``event.message_str`` 仍然是 ``"file list"``。若自行 split，会把
           ``file`` 当成子指令。

        .. important::
           ``GreedyStr``（吃掉其余全部文本）要生效，**默认值必须写 ``GreedyStr``
           这个类本身**（不是 ``GreedyStr()``、不是 ``""``、也不是 ``None``）。

           ``CommandFilter.validate_and_convert_params`` 的判断顺序是::

               is_greedy = param_type_or_default_val is GreedyStr   # 同一性判断
               if is_greedy:
                   result[param_name] = " ".join(remaining_params)   # 贪婪：全部剩余
                   break
               ...
               if param_type_or_default_val is None:                 # 只取一个 token
               elif isinstance(param_type_or_default_val, str):      # 只取一个 token

           而 ``init_handler_md`` 在参数**有默认值**时，放进 ``handler_params`` 的是
           **默认值**，不是类型注解::

               if v.default == inspect.Parameter.empty:
                   self.handler_params[k] = v.annotation   # 用注解
               else:
                   self.handler_params[k] = v.default      # 用默认值

           所以各种写法的结局：

           ==================  ==========================  ============================
           默认值写法            ``is GreedyStr``           结果
           ==================  ==========================  ============================
           （不写默认值）         True（注解就是 GreedyStr）  贪婪生效 ✅
           ``= GreedyStr``       True（类对象同一性成立）     贪婪生效 ✅
           ``= GreedyStr()``     False（是实例）              命中 isinstance(str) → 只取一个词
           ``= ""``              False（是实例）              同上
           ``= None``            False                        命中 is None 分支 → 只取一个词
           ==================  ==========================  ============================

           实测教训：``file list``（单个词）在错误写法下也能工作，
           只有 ``file download 文件名`` 这种多词参数才会暴露问题。
        """
        group_id = event.get_group_id()
        # 取参数：优先用框架注入的值，其次读 parsed_params 兜底
        raw_args = str(args or "").strip()
        if not raw_args:
            parsed = event.get_extra("parsed_params") or {}
            if isinstance(parsed, dict) and parsed.get("args"):
                raw_args = str(parsed["args"]).strip()
        parts = raw_args.split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action in {"", "help", "帮助"}:
            yield event.plain_result(self._help_text())
            return

        if not group_id:
            yield event.plain_result("❌ 该指令只能在群聊中使用。")
            return

        if action in {"list", "ls", "列表"}:
            page = max(1, int(rest)) if rest.isdigit() else 1
            async for result in self._cmd_search(event, "", page):
                yield result
            return

        if action in {"search", "find", "搜索"}:
            if not rest:
                yield event.plain_result("❌ 用法：/file search <关键词> [页码]")
                return
            keyword, page = self._split_page(rest)
            async for result in self._cmd_search(event, keyword, page):
                yield result
            return

        if action in {"download", "dl", "下载"}:
            if not rest:
                yield event.plain_result("❌ 用法：/file download <文件ID或文件名>")
                return
            async for result in self._cmd_download(event, rest):
                yield result
            return

        if action in {"lzrefresh", "蓝奏刷新"}:
            if not event.is_admin():
                yield event.plain_result("❌ 只有管理员可以刷新蓝奏云清单。")
                return
            async for result in self._cmd_lzrefresh(event):
                yield result
            return

        yield event.plain_result(
            f"❌ 未知的子指令：{action}\n\n{self._help_text()}",
        )

    async def _cmd_lzrefresh(self, event: AstrMessageEvent):
        """``/file lzrefresh``：强制刷新蓝奏云文件清单（管理员）。"""
        if not self._lanzou_configured():
            yield event.plain_result(
                "❌ 未启用蓝奏云：请在插件配置中打开 lanzou_enabled 并填写登录信息。",
            )
            return
        fmap = await self._lanzou_file_map(force=True)
        if fmap:
            yield event.plain_result(
                f"✅ 蓝奏云文件清单已刷新：「{self._lanzou_folder_label()}」"
                f"共 {len(fmap)} 个文件。",
            )
        else:
            yield event.plain_result(
                "⚠️ 刷新失败或目录为空：请检查蓝奏云登录信息是否有效，"
                "以及目录名是否填写正确（详见运行日志）。",
            )

    def _help_text(self) -> str:
        """指令帮助文本。"""
        return (
            "📁 蓝奏云文件管理器（只读链接提取）\n"
            "━━━━━━━━━━━━━━━\n"
            "/file list [页码]        列出蓝奏云网盘里的全部文件\n"
            "/file search <关键词> [页码]  按文件名模糊搜索网盘文件\n"
            "/file download <ID或名称> 获取文件详情与永久分享链接\n"
            "/file lzrefresh          刷新蓝奏云文件清单（管理员）\n"
            "/file help               显示本帮助\n"
            "━━━━━━━━━━━━━━━\n"
            "群内直接发送「搜索 <关键词>」「下载 <文件ID>」也可以使用\n"
            "本插件不索引群文件、不下载、不转存任何文件。"
        )

    @staticmethod
    def _split_page(text: str) -> tuple[str, int]:
        """从「关键词 [页码]」中拆出页码。

        末尾是一个独立的纯数字词时视为页码（``搜索 晴天 2`` → ``("晴天", 2)``）；
        否则整段都视为关键词（``搜索 报告2024`` → ``("报告2024", 1)``）。
        """
        parts = (text or "").rsplit(maxsplit=1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0], max(1, int(parts[1]))
        return (text or "").strip(), 1

    async def _cmd_search(self, event: AstrMessageEvent, keyword: str, page: int = 1):
        """卡片式模糊搜索蓝奏云网盘文件，带数字文件 ID 与分页。

        ``keyword`` 为空表示「列出全部」（``/file list``）。
        """
        if not self._lanzou_configured():
            yield event.plain_result(
                "❌ 未启用蓝奏云：请在插件配置中打开 lanzou_enabled 并填写 Cookie"
                "（ylogin + phpdisk_info）。",
            )
            return

        entries = self._lanzou_search_entries(await self._lanzou_file_map(), keyword)
        if not entries:
            if keyword:
                yield event.plain_result(f"🔍 蓝奏云网盘里没有找到包含「{keyword}」的文件。")
            else:
                yield event.plain_result(
                    f"📂 蓝奏云网盘的「{self._lanzou_folder_label()}」里还没有文件。\n"
                    "把文件上传到该目录后，管理员可执行 /file lzrefresh 立即刷新。",
                )
            return

        page_size = max(1, int(self.config.get("search_page_size") or 5))
        total = len(entries)
        total_pages = max(1, -(-total // page_size))  # 向上取整
        page = min(max(1, page), total_pages)
        start = (page - 1) * page_size
        chunk = entries[start : start + page_size]

        lines = [
            f"🔍 搜索「{keyword}」" if keyword else "📂 蓝奏云网盘文件",
            "━━━━━━━━━━━━━━━",
        ]
        for index, entry in enumerate(chunk, start=start + 1):
            lines.append(
                f"{index}.{entry['name']}\n"
                f"文件ID：{self._lanzou_entry_id(entry['fid'])}\n"
                f"文件大小：{entry.get('size') or '未知'}",
            )
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"共 {'搜索到 ' if keyword else ''}{total} 个文件")
        if total_pages > 1:
            footer = f"当前页码 {page} / {total_pages}"
            if page < total_pages:
                prefix = f"搜索 {keyword}" if keyword else "文件列表"
                footer += f"（发送「{prefix} {page + 1}」查看下一页）"
            lines.append(footer)
        else:
            lines.append("当前页码 1 / 1")
        lines.append("发送「下载 文件ID」获取永久分享链接")
        yield event.plain_result("\n".join(lines))

    async def _cmd_download(self, event: AstrMessageEvent, file_ref: str):
        """``下载 <文件ID或文件名>``：返回网盘文件详情卡片（永久分享链接）。"""
        if not self._lanzou_configured():
            yield event.plain_result(
                "❌ 未启用蓝奏云：请在插件配置中打开 lanzou_enabled 并填写 Cookie"
                "（ylogin + phpdisk_info）。",
            )
            return

        entry = self._lanzou_find_entry(await self._lanzou_file_map(), file_ref)
        if entry is None:
            # 可能刚上传，强制刷新一次再试
            entry = self._lanzou_find_entry(
                await self._lanzou_file_map(force=True),
                file_ref,
            )
        if entry is None:
            yield event.plain_result(
                f"❌ 蓝奏云网盘里没有找到「{file_ref}」。\n"
                "可用「搜索 <关键词>」查找文件ID，或提供更精确的文件名。",
            )
            return

        share = await self._lanzou_share(entry["fid"])
        if not share:
            yield event.plain_result(
                f"❌ 获取「{entry['name']}」的分享链接失败，请稍后再试"
                "（详见运行日志，通常是登录信息过期）。",
            )
            return
        yield event.plain_result(
            self._lanzou_detail_card(
                entry["name"],
                entry.get("size") or "未知",
                self._lanzou_entry_id(entry["fid"]),
                share,
            ),
        )

    @staticmethod
    def _lanzou_detail_card(
        name: str,
        size_str: str,
        file_id: str,
        share: dict[str, Any],
    ) -> str:
        """网盘文件详情卡片。

        只展示蓝奏云**真实存在**的字段。lanzou-api 的 ``ShareInfo`` 只有
        ``code / name / url / desc / pwd`` —— 蓝奏云没有「短链接」，也没有
        百度网盘那套「口令」，所以默认不输出这两行；只有当 ``share`` 里
        确实带了 ``short_url`` / ``command`` 且与主链接不同时才追加，
        避免用同一个长链接冒充短链接。
        """
        url = share.get("url") or ""
        pwd = share.get("pwd") or ""
        lines = [
            "📦 文件详情",
            "━━━━━━━━━━━━━━━",
            f"文件名：{name}",
            f"文件ID：{file_id}",
            f"文件大小：{size_str}",
            "存储位置：蓝奏网盘",
            f"链接：{url}",
            f"提取码：{pwd or '无'}",
        ]
        short_url = (share.get("short_url") or "").strip()
        if short_url and short_url != url:
            lines.append(f"短链接：{short_url}")
        command = (share.get("command") or "").strip()
        if command:
            lines.append(f"口令：{command}")
        lines.append("有效期：永久")
        lines.append("━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    # ================================================================== #
    # 快捷指令：搜索 / 下载（无需 /file 前缀）
    # ================================================================== #
    @filter.command("搜索")
    async def quick_search_command(
        self,
        event: AstrMessageEvent,
        keyword: GreedyStr = GreedyStr,
    ):
        """群内直接发送「搜索 <关键词> [页码]」，效果同 /file search。"""
        if not self.config.get("quick_commands", True):
            return
        group_id = event.get_group_id()
        if not group_id:
            return  # 私聊里静默忽略，避免打扰
        keyword = str(keyword or "").strip()
        if not keyword:
            yield event.plain_result("❌ 用法：搜索 <关键词> [页码]")
            return
        keyword, page = self._split_page(keyword)
        async for result in self._cmd_search(event, keyword, page):
            yield result

    @filter.command("下载")
    async def quick_download_command(
        self,
        event: AstrMessageEvent,
        file_ref: GreedyStr = GreedyStr,
    ):
        """群内直接发送「下载 <文件ID或文件名>」，效果同 /file download。"""
        if not self.config.get("quick_commands", True):
            return
        group_id = event.get_group_id()
        if not group_id:
            return
        file_ref = str(file_ref or "").strip()
        if not file_ref:
            yield event.plain_result("❌ 用法：下载 <文件ID或文件名>")
            return
        async for result in self._cmd_download(event, file_ref):
            yield result

    # ================================================================== #
    # Web API（供 pages/file-manager 调用）
    # ================================================================== #
    def _register_web_apis(self) -> None:
        """注册 WebUI 管理页面使用的后端 API。

        路由格式为 ``/{PLUGIN_NAME}/xxx``；Page 端通过
        ``bridge.apiGet("files")`` / ``bridge.apiPost("files/refresh")`` 调用。
        """
        routes: list[tuple[str, Any, list[str], str]] = [
            (f"/{PLUGIN_NAME}/overview", self.api_overview, ["GET"], "网盘总览"),
            (f"/{PLUGIN_NAME}/files", self.api_files, ["GET"], "网盘文件列表"),
            (
                f"/{PLUGIN_NAME}/files/link",
                self.api_file_link,
                ["GET"],
                "获取文件分享链接",
            ),
            (
                f"/{PLUGIN_NAME}/files/refresh",
                self.api_refresh_files,
                ["POST"],
                "强制刷新网盘文件清单",
            ),
        ]
        for route, handler, methods, desc in routes:
            try:
                self.context.register_web_api(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[group-file-manager] 注册 Web API %s 失败: %s",
                    route,
                    exc,
                )

    async def api_overview(self):
        """``GET /{PLUGIN_NAME}/overview``：网盘总览，供页面顶部卡片展示。"""
        try:
            configured = self._lanzou_configured()
            fmap = await self._lanzou_file_map() if configured else {}
            try:
                cache_minutes = int(float(self.config.get("lanzou_cache_minutes") or 30))
            except (TypeError, ValueError):
                cache_minutes = 30
            return json_response(
                {
                    "configured": configured,
                    "enabled": bool(self.config.get("lanzou_enabled", False)),
                    "folder": self._lanzou_folder_label(),
                    "recursive": bool(self.config.get("lanzou_recursive", True)),
                    "files": len(fmap),
                    "cache_minutes": cache_minutes,
                    "cached_at": int(self._lanzou_map_at) if self._lanzou_map_at else 0,
                    "search_page_size": max(
                        1,
                        int(self.config.get("search_page_size") or 5),
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[group-file-manager] overview 失败: %s", exc, exc_info=True)
            return error_response(f"获取网盘总览失败: {exc}", status_code=500)

    async def api_files(self):
        """``GET /{PLUGIN_NAME}/files``：网盘文件列表，支持按名称模糊搜索。

        query 参数：
            keyword: 文件名模糊匹配；留空表示全部
            limit:   最多返回条数（默认 500）
        """
        try:
            keyword = (request.query.get("keyword") or "").strip()
            limit = request.query.get("limit", 500, type=int) or 500
            limit = max(1, min(int(limit), 5000))

            if not self._lanzou_configured():
                return json_response(
                    {
                        "configured": False,
                        "total": 0,
                        "returned": 0,
                        "items": [],
                    },
                )

            entries = self._lanzou_search_entries(
                await self._lanzou_file_map(),
                keyword,
            )
            items = [
                {
                    "id": self._lanzou_entry_id(entry["fid"]),
                    "name": entry["name"],
                    "size_text": entry.get("size") or "未知",
                    "time_text": entry.get("time") or "",
                    "storage": "lanzou",
                }
                for entry in entries[:limit]
            ]
            return json_response(
                {
                    "configured": True,
                    "folder": self._lanzou_folder_label(),
                    "total": len(entries),
                    "returned": len(items),
                    "items": items,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[group-file-manager] files 失败: %s", exc, exc_info=True)
            return error_response(f"获取文件列表失败: {exc}", status_code=500)

    async def api_file_link(self):
        """``GET /{PLUGIN_NAME}/files/link``：取单个网盘文件的分享链接。

        query：``file_id``（数字短 ID / 文件名）
        """
        try:
            file_id = (request.query.get("file_id") or "").strip()
            if not file_id:
                return error_response("缺少 file_id", status_code=400)
            if not self._lanzou_configured():
                return error_response(
                    "未启用蓝奏云：请在插件配置中打开 lanzou_enabled 并填写登录信息",
                    status_code=409,
                )

            entry = self._lanzou_find_entry(await self._lanzou_file_map(), file_id)
            if entry is None:
                entry = self._lanzou_find_entry(
                    await self._lanzou_file_map(force=True),
                    file_id,
                )
            if entry is None:
                return error_response(
                    "蓝奏云网盘里没有找到该文件（可先点「刷新网盘清单」再试）",
                    status_code=404,
                )

            share = await self._lanzou_share(entry["fid"])
            if not share:
                return error_response(
                    "获取分享链接失败，请稍后再试（通常是蓝奏云登录信息过期）",
                    status_code=409,
                )
            return json_response(
                {
                    "name": entry["name"],
                    "file_id": self._lanzou_entry_id(entry["fid"]),
                    "size_text": entry.get("size") or "未知",
                    "storage": "lanzou",
                    "link": share.get("url") or "",
                    "pwd": share.get("pwd") or "",
                    "permanent": True,
                    **self._share_fields(share),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[group-file-manager] files/link 失败: %s", exc, exc_info=True)
            return error_response(f"获取链接失败: {exc}", status_code=500)

    async def api_refresh_files(self):
        """``POST /{PLUGIN_NAME}/files/refresh``：强制刷新网盘文件清单。"""
        try:
            if not self._lanzou_configured():
                return error_response(
                    "未启用蓝奏云：请在插件配置中打开 lanzou_enabled 并填写登录信息",
                    status_code=409,
                )
            fmap = await self._lanzou_file_map(force=True)
            if not fmap:
                return error_response(
                    "刷新失败或目录为空：请检查登录信息与目录名（详见运行日志）",
                    status_code=502,
                )
            return json_response(
                {
                    "files": len(fmap),
                    "folder": self._lanzou_folder_label(),
                    "cached_at": int(self._lanzou_map_at),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[group-file-manager] files/refresh 失败: %s", exc, exc_info=True)
            return error_response(f"刷新失败: {exc}", status_code=500)
