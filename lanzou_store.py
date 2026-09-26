"""蓝奏云只读后端（列目录清单 + 取分享链接）。

把你自己**蓝奏云网盘**里已有的文件，变成群友可用的**永久分享链接**
（对齐群文件机器人「存储位置：蓝奏网盘 / 有效期：永久」的效果）。
本模块**只读**：只调用列目录与取分享链接两个接口，不上传、不改、不转存。

依赖第三方库 ``lanzou-api``（``pip install lanzou-api``，
zaxtyson/LanZouCloud-API，已对 2.6.10 版本接口核对）。
采用懒加载：未安装时插件其余功能照常，只有蓝奏云功能会给出明确报错。

登录方式：**仅支持 Cookie**。浏览器登录蓝奏云网页版后，从 Cookie 中取
``ylogin`` 与 ``phpdisk_info`` 两项填入插件配置（直接粘贴即可，
% 编码会自动解码）。

> 为什么不支持账号密码：``lanzou-api`` 的 ``login()`` 已被上游标记弃用，
> 且蓝奏云控制台页面结构已变更，实测账号密码无法登录（2026-09）。
> 因此插件不再提供该登录方式，避免用户填了半天却连不上网盘。

已处理的 lanzou-api 2.6.10 与现网行为的两个偏差（2026-09 实测）：

* ``login_by_cookie`` 通过访问 ``account.php`` 判断是否登录，但该页面
  现在恒为「网盘用户登录」页（与上传控制台是两套体系），导致有效 Cookie
  也被判为失败。本模块改为注入 Cookie 后用 ``doupload.php task=5``
  （文件列表接口）返回 ``zt == 1`` 作为登录校验标准；
* ``get_dir_list`` 在账户**没有任何文件夹**时，服务端返回空响应体，
  ``resp.json()`` 会抛 ``JSONDecodeError``（``mkdir`` 内部也调它）。
  本模块会把该异常包装为「空目录列表」。
"""

from __future__ import annotations

import asyncio
import types
import urllib.parse
from pathlib import Path
from typing import Any

from astrbot.api import logger


class LanzouError(Exception):
    """蓝奏云转存失败（未安装依赖 / 登录失败 / 上传失败 / 取分享链接失败）。"""


class LanzouClient:
    """对 lanzou-api 的最小封装（只读）：登录 + 列目录 + 取分享链接。

    构造时**不做任何网络请求**；首次使用（列目录/取链接）时才登录（懒加载）。
    登录只支持 Cookie——账号密码登录已失效，故不再提供。
    """

    def __init__(
        self,
        *,
        cookie_ylogin: str = "",
        cookie_phpdisk_info: str = "",
        folder_name: str = "",
        share_pwd: str = "",
    ) -> None:
        self.cookie_ylogin = (cookie_ylogin or "").strip()
        self.cookie_phpdisk_info = (cookie_phpdisk_info or "").strip()
        # 空串表示直接读取蓝奏云根目录（不需要创建文件夹）
        self.folder_name = (folder_name or "").strip()
        self.share_pwd = (share_pwd or "").strip()
        self._disk: Any = None
        self._folder_id: int | None = None
        # lanzou-api 是同步阻塞 IO：串行化，避免并发把会话打挂
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 登录（同步，首次使用时执行）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _patch_dir_list(disk: Any) -> None:
        """包装 ``get_dir_list``：账户无文件夹时服务端返回空响应体，
        原生实现会在 ``resp.json()`` 处抛 ``JSONDecodeError``，这里兜底为空列表。"""
        original = disk.get_dir_list

        def safe_get_dir_list(self_disk, folder_id: int = -1):
            try:
                return original(folder_id)
            except ValueError:  # JSONDecodeError 是 ValueError 的子类
                from lanzou.api.models import FolderList

                return FolderList()

        disk.get_dir_list = types.MethodType(safe_get_dir_list, disk)

    def _validate_login(self, disk: Any) -> bool:
        """用文件列表接口（``doupload.php task=5``）校验登录态。

        现网行为（2026-09 实测）：未登录返回 ``{"zt":9,"info":"login not"}``，
        已登录返回 ``{"zt":1,...}``。lanzou-api 自带的 ``account.php`` 校验
        已失效（该页面恒为登录页），不能再用。
        """
        try:
            resp = disk._post(
                disk._doupload_url + "?uid=" + str(disk._uid),
                {"task": 5, "folder_id": -1, "pg": 1},
            )
            return bool(resp) and resp.json().get("zt") == 1
        except Exception:  # noqa: BLE001 - 任何异常都按未登录处理
            return False

    def _load(self):
        """懒加载 lanzou-api 并完成登录。失败抛 :class:`LanzouError`。"""
        if self._disk is not None:
            return self._disk
        try:
            from lanzou.api import LanZouCloud
        except ImportError as exc:
            raise LanzouError(
                "未安装 lanzou-api。请在 AstrBot 运行环境执行：pip install lanzou-api",
            ) from exc

        if not (self.cookie_ylogin and self.cookie_phpdisk_info):
            raise LanzouError(
                "未配置蓝奏云 Cookie：请在插件配置中填写 ylogin 与 phpdisk_info"
                "（浏览器登录蓝奏云网页版后，从 Cookie 里复制这两项的值）",
            )

        disk = LanZouCloud()
        # 用户从浏览器复制的 Cookie 值常带 % 编码（如 %3D%3D），先解码；
        # 对未编码的值 unquote 是无副作用的
        disk._uid = self.cookie_ylogin
        disk._session.cookies.update(
            {
                "ylogin": self.cookie_ylogin,
                "phpdisk_info": urllib.parse.unquote(self.cookie_phpdisk_info),
            },
        )

        self._patch_dir_list(disk)
        if not self._validate_login(disk):
            raise LanzouError(
                "蓝奏云登录失败：Cookie 可能已过期，请重新从浏览器复制"
                "（ylogin + phpdisk_info）",
            )
        self._disk = disk
        logger.info("[group-file-manager] 蓝奏云登录成功")
        return disk

    def _ensure_folder(self, disk) -> int:
        """找到（或创建）文件目录，返回文件夹 id；目录名为空时返回根目录 -1。"""
        if self._folder_id is not None:
            return self._folder_id
        if not self.folder_name:
            self._folder_id = -1
            return -1
        folder_id = disk.mkdir(-1, self.folder_name, "AstrBot 蓝奏云文件管理器")
        if folder_id < 0:
            # 已存在（或其他失败）→ 从根目录列表里找
            for item in disk.get_dir_list(-1):
                if item.name == self.folder_name:
                    folder_id = item.id
                    break
        if not folder_id or folder_id < 0:
            raise LanzouError(f"在蓝奏云创建/定位文件夹「{self.folder_name}」失败")
        self._folder_id = folder_id
        return folder_id

    # ------------------------------------------------------------------ #
    # 只读清单：列出转存目录（含子目录）下的全部文件
    # ------------------------------------------------------------------ #
    async def list_files(
        self,
        *,
        recursive: bool = True,
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        """列出转存目录下的全部文件（只读，不上传、不修改）。

        Returns:
            ``[{"fid", "name", "size", "time"}, ...]``（``size`` 是蓝奏云给的
            人类可读字符串，如 ``"35.1 M"``；``time`` 是 ``"2026-09-26"``）。
            失败抛 :class:`LanzouError`。
        """
        async with self._lock:
            return await asyncio.to_thread(self._list_files_sync, recursive, max_depth)

    def _list_files_sync(self, recursive: bool, max_depth: int) -> list[dict[str, Any]]:
        disk = self._load()
        root_id = self._ensure_folder(disk)
        out: list[dict[str, Any]] = []

        def describe(item) -> dict[str, Any]:
            return {
                "fid": int(item.id),
                "name": str(item.name),
                "size": str(getattr(item, "size", "") or ""),
                "time": str(getattr(item, "time", "") or ""),
            }

        def walk(folder_id: int, depth: int) -> None:
            for item in disk.get_file_list(folder_id):
                out.append(describe(item))
            if recursive and depth < max_depth:
                for sub in disk.get_dir_list(folder_id):
                    walk(int(sub.id), depth + 1)

        walk(root_id, 0)
        return out

    # ------------------------------------------------------------------ #
    # 按需取分享链接
    # ------------------------------------------------------------------ #
    async def share_info(self, fid: int) -> dict[str, Any]:
        """按文件 fid 取分享链接与提取码（只读）。

        Returns:
            ``{"fid", "url", "pwd", "name"}``；失败抛 :class:`LanzouError`。
        """
        async with self._lock:
            return await asyncio.to_thread(self._share_info_sync, int(fid))

    def _share_info_sync(self, fid: int) -> dict[str, Any]:
        from lanzou.api import LanZouCloud  # _load 已保证可导入

        disk = self._load()
        info = disk.get_share_info(fid)
        if info.code != LanZouCloud.SUCCESS or not info.url:
            raise LanzouError("获取蓝奏云分享链接失败")
        return {
            "fid": fid,
            "url": info.url,
            "pwd": info.pwd or "",
            "name": info.name or "",
        }

    # ------------------------------------------------------------------ #
    # 上传（备用能力：默认流程不使用，保留给需要转存的场景）
    # ------------------------------------------------------------------ #
    async def upload(self, file_path, display_name: str = "") -> dict[str, Any]:
        """上传本地文件到蓝奏云并返回分享信息。

        Returns:
            ``{"fid", "url", "pwd", "name"}``；失败抛 :class:`LanzouError`。
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._upload_sync,
                Path(file_path),
                display_name,
            )

    def _upload_sync(self, file_path: Path, display_name: str) -> dict[str, Any]:
        from lanzou.api import LanZouCloud  # _load 已保证可导入

        disk = self._load()
        folder_id = self._ensure_folder(disk)
        code = disk.upload_file(str(file_path), folder_id)
        if code != LanZouCloud.SUCCESS:
            raise LanzouError(
                f"上传到蓝奏云失败（错误码 {code}）。"
                "免费账号单文件上限约 100 MB，超限文件会回退 QQ 临时链接",
            )

        # 取回 fid：同名文件可能已存在，取 id 最大的（最新上传的那个）
        name = display_name or file_path.name
        fid = 0
        for item in disk.get_file_list(folder_id):
            if item.name == name and int(item.id) > fid:
                fid = int(item.id)
        if not fid:
            raise LanzouError("上传成功但在蓝奏云文件列表中找不到该文件")

        if self.share_pwd:
            code = disk.set_passwd(fid, self.share_pwd, is_file=True)
            if code != LanZouCloud.SUCCESS:
                logger.warning(
                    "[group-file-manager] 设置蓝奏云提取码失败（错误码 %s）",
                    code,
                )

        info = disk.get_share_info(fid)
        if info.code != LanZouCloud.SUCCESS or not info.url:
            raise LanzouError("获取蓝奏云分享链接失败")
        return {
            "fid": fid,
            "url": info.url,
            "pwd": info.pwd or "",
            "name": info.name or name,
        }
