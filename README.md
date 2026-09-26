# astrbot_plugin_group_file_manager · 蓝奏云文件管理器

AstrBot 的**蓝奏云链接提取**插件（只读），带专属 WebUI 管理页面。

一句话说明它做什么：**把你蓝奏云网盘里已有的文件，变成群友可用的永久分享链接。**

**v3.0.0 起：彻底不碰群文件。** 插件
**不索引群文件、不识别群内发送/上传的文件、不导入群文件库、不下载、不转存**。
只有一条只读链路：

```
你自己把文件上传到蓝奏云
        │
        ▼
插件只读读取网盘文件清单（只读，不写、不改、不转存）
        │
        ▼
群内「搜索 <关键词>」卡片式模糊搜索 ──▶「下载 <文件ID>」给永久分享链接（含提取码）
        │
        ▼
WebUI 页面：搜索网盘文件、一键复制链接、刷新网盘清单
```

- **不产生副本**：文件只有你上传的那一份，网盘不会越堆越乱；
- **不受群文件大小/有效期限制**：给的是蓝奏云分享页链接（永久有效）；
- **不依赖协议端的文件接口**：整个流程不需要 `get_group_file_url`，
  也不需要 NapCat packetBackend 之类的协议端能力；
- **零磁盘占用**：没有任何本地缓存目录，清单只保存在内存里（带 TTL）。

> ⚠️ 这是一个**只读**插件：它只调用蓝奏云的「列目录」与「取分享链接」两个只读接口，
> **不会**上传、转存、删除、重命名你网盘里的任何东西。

---

## 一、目录结构

```
astrbot_plugin_group_file_manager/
├─ main.py                  # 插件入口：/file 指令 + 快捷指令 + Web API 注册
├─ file_utils.py            # 纯工具函数（human_size / numeric_id / normalize_name / now_ts）
├─ lanzou_store.py          # 蓝奏云后端（只读：列目录清单 + 取分享链接）
├─ __init__.py
├─ metadata.yaml            # 插件元数据
├─ _conf_schema.json        # 插件配置项
├─ requirements.txt         # lanzou-api（仅开启蓝奏云链接提取时需要）
├─ selfcheck.py             # 部署自检脚本（无需 AstrBot）
├─ pages/
│  └─ file-manager/         # 插件 Pages（WebUI 管理页面）
│     ├─ index.html
│     ├─ app.js
│     └─ style.css
└─ _devtest/                # 开发期自测（AstrBot 桩 + 测试），可安全删除
   ├─ test_plugin.py
   ├─ verify_lanzou.py      # 用真实凭据验证蓝奏云登录/列目录/取链接
   └─ astrbot_stubs/
```

**没有运行时数据目录**：插件不写插件 KV，也不写本地缓存。文件清单常驻内存
（`归一化文件名 → {fid, name, size, time}`），按 `lanzou_cache_minutes` 过期重取。

---

## 二、安装

1. 把整个 `astrbot_plugin_group_file_manager/` 目录放进 AstrBot 的 `data/plugins/`。
2. 在 WebUI「插件管理」中重载插件（**新增 Pages 目录后必须重载插件**才能看到页面）。
3. **启用蓝奏云链接提取**：在 AstrBot 运行环境执行 `pip install lanzou-api`，
   然后在插件配置中打开 `lanzou_enabled` 并填写登录信息
   （见「五、蓝奏云链接提取」）。

查看页面：WebUI → 插件 → 本插件卡片 → 插件详情页 → 打开「蓝奏云文件管理器」Page。

> **从 v2.x 升级**：v2.x 的群文件索引 KV 不再被读取（也不会被清理，可自行忽略）。
> 群文件索引 / 历史导入 / 发送到群 / 重命名 / 删除等能力在 v3.0.0 已**整体移除**，
> 配置项里的 `enabled` / `max_list_items` / `allow_delete_by_non_admin` /
> `import_enabled` / `lanzou_search` 也一并删除；
> 蓝奏云**账号密码登录**（`lanzou_username` / `lanzou_password`）同样已移除，
> 现在只支持 Cookie。
> 如果你还需要这些能力，请停留在 v2.4（仓库里保留了 `*.bak-v2.4.0` 备份）。

---

## 三、指令

### 快捷指令（无需 `/file` 前缀，可在配置 `quick_commands` 中关闭）

群内直接发送：

```
搜索 <关键词> [页码]      卡片式模糊搜索你的蓝奏云网盘文件
下载 <文件ID或文件名>     获取该文件的永久分享链接与提取码
```

**示例**

```
搜索 晴天
→ 🔍 搜索「晴天」
   ━━━━━━━━━━━━━━━
   1.朗朗 晴天.zip
   文件ID：320409845
   文件大小：35.1 M
   2.朗朗晴天.mp3
   文件ID：741203998
   文件大小：8.4 M
   ━━━━━━━━━━━━━━━
   共 搜索到 2 个文件
   当前页码 1 / 1
   发送「下载 文件ID」获取永久分享链接

下载 320409845
→ 📦 文件详情
   ━━━━━━━━━━━━━━━
   文件名：朗朗 晴天.zip
   文件ID：320409845
   文件大小：35.1 M
   存储位置：蓝奏网盘
   链接：https://wwx.lanzou.com/xxxxxxxx
   提取码：ab11
   有效期：永久
   ━━━━━━━━━━━━━━━
```

> **卡片里只有蓝奏云真实存在的字段。** 蓝奏云只提供「分享链接 + 提取码」，
> **没有**「短链接」，也**没有**百度网盘那套「口令」——所以卡片不会出现这两行。
> 只有当你网盘返回的分享信息里**确实**带了对应字段时才会追加显示。

### `/file` 指令

```
/file list [页码]          列出蓝奏云网盘里的全部文件
/file search <关键词> [页码]  按文件名模糊搜索网盘文件
/file download <ID或名称>  获取文件详情与永久分享链接
/file lzrefresh           刷新蓝奏云文件清单（管理员）
/file help                显示帮助
```

别名：`/群文件`、`/gf`。

**匹配规则（很宽容）**：`下载` 支持三种引用方式——

1. **数字文件ID**（卡片里给的，由 `fid` 派生、重启后稳定）；
2. **精确文件名**；
3. **归一化文件名 / 唯一子串**——会剥掉空格、全角空格、下划线、连字符、
   Windows 非法字符再匹配，因此「朗朗 晴天.zip」也能命中「朗朗晴天.zip」。

「下载」未命中时会**自动强制刷新一次清单再试**（你可能刚把文件传上去）。

---

## 四、配置（`_conf_schema.json`）

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `quick_commands` | bool | `true` | 是否启用群内快捷指令「搜索 / 下载」 |
| `search_page_size` | int | `5` | 卡片式搜索结果每页条数 |
| `lanzou_enabled` | bool | `false` | 是否启用蓝奏云链接提取（只读） |
| `lanzou_cookie_ylogin` | string | `""` | 蓝奏云 Cookie：`ylogin` |
| `lanzou_cookie_phpdisk_info` | string | `""` | 蓝奏云 Cookie：`phpdisk_info`（`%` 编码会自动解码） |
| `lanzou_folder` | string | `""` | 读取的网盘目录名；**留空 = 根目录（推荐）** |
| `lanzou_recursive` | bool | `true` | 是否包含子目录（最深 3 层） |
| `lanzou_cache_minutes` | int | `30` | 文件清单缓存分钟数 |

---

## 五、蓝奏云链接提取（只读）

QQ 群文件链接有有效期，而网盘分享页链接是**永久**的。本插件的做法是：
**你自己把文件上传到蓝奏云网盘**（手动或用你自己的上传工具），
插件只负责**读取你的网盘**、按文件名匹配、把永久分享链接发给群友。
插件**不会上传 / 转存 / 修改**你网盘里的任何东西，也不会产生重复副本。

### 工作流程

1. 你把文件传到蓝奏云（`lanzou_folder` 留空 = 根目录，推荐；填目录名则只在该目录
   及其子目录中匹配，「下载」也支持按文件名在当前范围内定位）；
2. 插件每隔 `lanzou_cache_minutes` 分钟（默认 30）读取一次该范围的文件清单
   （只读，每个文件夹一次请求，很便宜），并把结果**归一化后**放进内存缓存；
3. 群成员发「搜索 \<关键词\>」→ 在缓存清单里做子串 / 归一化模糊匹配，
   结果按网盘上传日期倒序，按 `search_page_size` 分页；
4. 群成员发「下载 \<文件ID或文件名\>」→ 在清单里定位（数字 ID → 精确名 →
   归一化名 → 唯一子串），然后**实时**调用分享接口取出链接与提取码；
5. WebUI 页面同样列出网盘文件，每行有「获取链接」按钮，一键复制链接 / 提取码。

等不及缓存周期？管理员发 `/file lzrefresh`，或在 WebUI 点「刷新网盘清单」即可强制刷新
（强制刷新会同时让已取过的分享链接缓存失效）。

### 与「转存到网盘」方案的区别 / 优势

* **不产生副本**：文件只有你上传的那一份，网盘不会越堆越乱；
* **不受文件大小限制**：不经过机器人中转，多大都行；
* **不依赖协议端文件接口**：整个流程只跟蓝奏云通信。

### 登录方式（仅 Cookie）

蓝奏云**只支持 Cookie 登录**：

1. 浏览器登录蓝奏云网页版；
2. F12 → 应用/存储 → Cookie；
3. 把 `ylogin` 与 `phpdisk_info` 两项的值分别填进
   `lanzou_cookie_ylogin` / `lanzou_cookie_phpdisk_info`
   （值里带 `%3D` 之类的编码可直接粘贴，插件会自动解码）。

> ⚠️ **为什么不支持账号密码**：`lanzou-api` 的 `login()` 已被上游标记弃用，
> 且蓝奏云控制台页面结构已变更，2026-09 实测账号密码无法登录。
> 因此插件**不提供**该登录方式（配置项里也没有），避免白填一场。

### 依赖与限制

* 依赖第三方库 [`lanzou-api`](https://github.com/zaxtyson/LanZouCloud-API)
  （`pip install lanzou-api`，已按 2.6.10 接口核对）。采用**懒加载**：
  未安装时插件会在日志中给出明确报错，不会影响 AstrBot 其余功能。
* 已对 lanzou-api 与现网的两处偏差做了兼容（2026-09 实测）：
  ① 原 `login_by_cookie` 依赖的 `account.php` 校验页已失效 → 改为手动注入 Cookie
  并用文件列表接口（`doupload.php task=5`，返回 `zt==1`）校验登录；
  ② 账户无文件夹时目录列表接口返回空响应导致的 `JSONDecodeError` 已兜底。
  另外，账号密码登录路径已失效 → 插件直接不提供该选项（见「登录方式」）。
* 卡片里的「提取码」就是你分享文件时自己设置的提取码，没有则显示「无」
  （表示这份分享不需要密码）。
* 蓝奏云官方对非 VIP 账号的**分享直链**有访问限制，但**分享页面链接永久有效**，
  卡片给出的正是分享页面链接。
* 网盘里同名的多个文件取清单中的第一个。
* **单文件 > 100 MB**（免费账号）无法通过蓝奏云分享——这是蓝奏云的限制，不是插件的。

---

## 六、Web API（供 Pages 调用）

注册前缀为插件名 `/{PLUGIN_NAME}/`，Page 端通过 bridge 调用时**不带前缀**。

| 路由 | 方法 | Page 端 endpoint | 说明 |
| --- | --- | --- | --- |
| `/{PLUGIN_NAME}/overview` | GET | `overview` | 网盘总览（是否已配置、文件数、匹配目录、缓存时间） |
| `/{PLUGIN_NAME}/files` | GET | `files` | 网盘文件列表，支持 `keyword`（模糊搜索）/ `limit`（默认 500） |
| `/{PLUGIN_NAME}/files/link` | GET | `files/link` | 取单个网盘文件的分享链接，参数 `file_id`（数字ID / 文件名） |
| `/{PLUGIN_NAME}/files/refresh` | POST | `files/refresh` | 强制刷新网盘文件清单 |

响应约定：业务成功直接 `json_response({...})`；失败用
`error_response("原因", status_code=...)`，前端 bridge 会把后者 reject 成 `Error`。

### `files/link` 返回字段

```json
{
  "name": "朗朗 晴天.zip",
  "file_id": "320409845",
  "size_text": "35.1 M",
  "storage": "lanzou",
  "link": "https://wwx.lanzou.com/xxxxxxxx",
  "pwd": "ab11",
  "permanent": true
}
```

| 字段 | 说明 |
| --- | --- |
| `file_id` | 卡片里显示的「文件ID」（由 `fid` 派生，稳定、不直接暴露真实 fid） |
| `size_text` | 蓝奏云给的人类可读大小（如 `35.1 M`） |
| `storage` | 固定为 `lanzou`（本插件只有网盘文件） |
| `pwd` | 提取码；没有则为 `""`（前端显示「无」） |
| `permanent` | 固定为 `true`（分享页链接永久有效） |
| `short_url` | **可选**。仅当分享信息里确实带了这个字段、且与 `link` 不同时才出现 |
| `command` | **可选**。仅当分享信息里确实带了口令时才出现（蓝奏云通常没有） |

> ⚠️ 没有 `short_link` 字段。早期版本把长链接同时塞进「短链接」「口令」两行，
> 属于伪造数据——蓝奏云并不提供这两样，现已移除。

失败码：缺 `file_id` → 400；未启用蓝奏云 → 409；清单里找不到该文件 → 404。

### WebUI 页面

* 顶部统计：**网盘文件数 / 匹配目录 / 清单缓存时间 / 状态**；
* 工具条：**搜索框**（防抖模糊搜索）、**查询**、**刷新网盘清单**；
* 列表列：**文件名（含文件ID）/ 大小 / 上传时间 / 状态 / 操作**——
  状态固定为「永久链接」徽标，操作只有「**获取链接**」；
* 「获取链接」打开对话框，字段为 **链接 / 提取码 / 有效期（永久）**，
  每行可一键**复制**，无需手动选中；
  （对话框同样只在 `short_url` / `command` 真实存在时才多显示「短链接」「口令」行）

> 页面上**没有任何群文件相关内容**：没有群筛选、没有群 / 上传者 / 存储位置列、
> 没有发送 / 重命名 / 删除 / 导入，因为 v3.0.0 的插件根本不索引群文件。

---

## 七、API 核实清单（重要）

以下每一项都在 **AstrBot `master` 分支源码 / 官方文档 / lanzou-api 源码**中核对过。

### 指令与参数注入

| # | 结论 | 依据 |
| --- | --- | --- |
| 1 | **`event.message_str` 在 handler 里仍是完整原文**（如 `"file list"`）：`CommandFilter` 只用剥掉命令名后的文本做参数解析，**不修改** `message_str`。因此**绝不能**自己 `split(event.message_str)` 取子指令 | `astrbot/core/star/filter/command.py::CommandFilter.filter` |
| 2 | 正确取参方式：参数写进 handler 签名，由管线 `call_handler(event, handler, **params)` 注入；全部剩余文本用 `GreedyStr` 标记 | `command.py`、`pipeline/process_stage/method/star_request.py` |
| 3 | **`GreedyStr` 要生效，默认值必须写类本身 `= GreedyStr`**（不是 `GreedyStr()`、`""` 或 `None`）。`validate_and_convert_params` 用**同一性判断** `param_type_or_default_val is GreedyStr`；而有默认值时放进 `handler_params` 的是**默认值**。写 `""`/`GreedyStr()` 是 `str` 实例 → 命中 `isinstance(str)` → 只取一个词 | `command.py`、`init_handler_md` |
| 4 | 单测教训：`file list`（单个词）在错误写法下也能工作，**只有 `file download 带空格 文件名` 这种多词参数才会暴露问题**——所以自测专门覆盖了多词文件名 | 本插件 `_devtest/test_plugin.py` |

### 插件 / Pages / 配置

| # | 结论 | 依据 |
| --- | --- | --- |
| 5 | `@register(name, author, desc, version, repo)` 来自 `astrbot.api.star`；`metadata.yaml` 优先级更高 | `astrbot/api/star/__init__.py`、官方文档 |
| 6 | `_conf_schema.json` 存在时，AstrBot 会把配置以第二个参数注入 `__init__(self, context, config)` | 官方文档「插件配置」 |
| 7 | Pages 加载约定：只扫描 `pages/<page_name>/index.html`；前端用 `window.AstrBotPluginPage`；后端用 `context.register_web_api(route, handler, methods, desc)`；`astrbot.api.web` 提供 `request` / `json_response` / `error_response` | 官方文档「插件 Pages」+ `astrbot/api/web.py` |
| 8 | bridge 返回值规则：`{status:"ok",data}` → resolve `data`；普通 JSON → 完整对象；`{status:"error"}` 或 HTTP 失败 → reject | 官方文档「插件 Pages · 请求和返回值规则」 |
| 9 | Pages 静态资源用**相对路径**引用（`./app.js` / `./style.css`）；AstrBot 会重写相对路径并追加 `asset_token`，**不要手工拼接** `/api/plugin/page/content/...` | 同 #7 |
| 10 | Pages iframe 沙箱为 `allow-scripts allow-forms allow-downloads`，不能读 Dashboard cookie/LocalStorage | 同 #7 |

### 蓝奏云（lanzou-api 2.6.10）

| # | 结论 | 依据 |
| --- | --- | --- |
| 11 | `get_share_info(fid)` 返回的 `ShareInfo` **只有** `code / name / url / desc / pwd`——**没有短链接、没有口令** | `lanzou/api/models.py` |
| 12 | `login_by_cookie` 依赖的 `account.php` 校验页已失效（恒为登录页）→ 改用 `doupload.php task=5` 返回 `zt==1` 校验登录 | 2026-09 实测 |
| 13 | 账户无文件夹时 `get_dir_list` 会因空响应体抛 `JSONDecodeError`（`ValueError` 子类）→ 已包装为空列表 | 2026-09 实测 + `lanzou/api/apis.py` |
| 14 | 账号密码登录（`LanZouCloud.login`）已被上游标记弃用且现网失败 → 插件**不再提供**该登录方式，只支持 Cookie | 2026-09 实测 |

---

## 八、设计与边界处理

- **只读**：只调用 `list_files`（列目录）与 `share_info`（取分享链接）两个只读接口；
  `upload()` 虽然保留在 `lanzou_store.py` 里作为备用能力，但**默认流程从不调用**。
- **零持久化**：文件清单只存在内存（`_lanzou_map`），TTL 由 `lanzou_cache_minutes`
  控制；分享链接也只在内存里缓存（`_lanzou_share_cache`），强制刷新时清空。
- **并发安全**：清单刷新用 `asyncio.Lock` 串行化，并在拿到锁后二次检查 TTL，
  避免并发重复请求。
- **读不到不崩**：列目录失败时**回退旧缓存**并把告警写进日志，
  清空缓存时直接返回空表——绝不因网盘抖动而报错刷屏。
- **匹配归一化**：`normalize_name()` 会剥掉空格 / 全角空格 / 下划线 / 连字符 /
  Windows 非法字符再做比较，因此文件名的常见写法差异不影响命中。
- **稳定且不暴露 fid 的「文件ID」**：`numeric_id(f"lz_{fid}")`（md5 前 8 位取模 10^9），
  纯数字、方便群友输入，重启后不变。
- **诚实输出**：卡片与接口只输出蓝奏云真实返回的字段，绝不拿长链接冒充短链接。

---

## 九、开发自测

仓库内 `_devtest/` 提供了一套 AstrBot 桩（`astrbot.api.event` / `message_components` /
`star` / `web` + 假蓝奏云客户端），可在**没有安装 AstrBot / 没有真实网盘**的情况下
跑通插件全部业务逻辑：

```bash
cd astrbot_plugin_group_file_manager
python _devtest/test_plugin.py
```

覆盖 **145 项断言**：群文件层已彻底移除（旧模块文件不存在、没有上传事件处理器、
只注册 3 个指令、配置项已清理）、注册信息与版本号、纯函数、
蓝奏云配置判定、清单 TTL 缓存 / 强制刷新 / 失败回退、分享链接按需取与缓存、
模糊搜索与定位、`/file list|search|download|lzrefresh|help`、
快捷指令「搜索 / 下载」（含开关与私聊静默）、分页解析、
详情卡片**不出现「短链接」「口令」**（以及真带这些字段时才出现）、
全部 Web API（含 400 / 404 / 409 分支）、前端资源与前后端 endpoint 契约。
测试通过后删除 `_devtest/` 即可，它不参与插件运行。

想用**真实凭据**验证蓝奏云是否配置正确（登录 → 列目录 → 取分享链接）：

```bash
# Linux/macOS
LZ_YLOGIN=xxx LZ_PHPDISK=yyy python _devtest/verify_lanzou.py
# Windows (cmd)
set LZ_YLOGIN=xxx && set LZ_PHPDISK=yyy && python _devtest/verify_lanzou.py
```

另有部署自检脚本（同样无需 AstrBot）：

```bash
python selfcheck.py
```

除目录结构 / `metadata.yaml` / 语法 / 依赖外，它还会做一项**前后端契约校验**：
把 `pages/file-manager/app.js` 里所有 `bridge.apiGet/apiPost` 的 endpoint
与 `main.py` 中注册的路由比对，端点对不上会直接报错——避免「前端调了、
后端没注册」这类只能靠手点才发现的问题。

---

## 十、已知限制

1. 只支持**蓝奏云**。其他网盘（百度 / 阿里 / 夸克等）没有做适配。
2. 依赖 `lanzou-api`，需要 `pip install lanzou-api`；且蓝奏云的
   `account.php` 校验页与目录列表接口都曾发生过变更（见「五、依赖与限制」），
   未来可能还需跟进。**只能用 Cookie 登录**，Cookie 属于长期凭据但也会过期，
   过期后需重新从浏览器复制。
3. **免费账号单文件上限约 100 MB**，超限文件无法通过蓝奏云分享。
4. WebUI 页面只读展示网盘文件，**不提供上传入口**——文件需要你自己传到蓝奏云。
5. 群内快捷指令「搜索 / 下载」只在**群聊**里生效，私聊会被静默忽略
   （避免打扰）；`/file help` 私聊可用。
6. 原生 AstrBot 之外的二次封装（如某些桌面壳）若改变了 Pages 的注入方式，
   WebUI 页面可能打不开；此时可只用群内指令。
