"""蓝奏云文件管理器插件包。

AstrBot 以包的形式加载 ``main.py``，此文件用于让 ``file_utils`` /
``lanzou_store`` 的相对导入生效，同时便于本地自测。
"""

from . import file_utils, lanzou_store  # noqa: F401

__all__ = ["file_utils", "lanzou_store"]
