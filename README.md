# 帝国开局助手

面向《帝国时代 IV》玩家的 Windows 中文开局工具，基于 CraftySalamander 的 RTS_Overlay 修改，采用 GNU GPL 第三版。

## 安装与更新

首次安装：[下载安装包](https://github.com/miradore-xyz/aoe4-opening-assistant-zh/releases/latest/download/AoE4OpeningAssistant-Setup.exe)。

安装后直接打开“帝国开局助手”。程序启动时在后台检查 GitHub 正式发行版；发现更高版本后，主页顶部会出现“下载更新”。
点击下载，在进度完成后选择“安装并重启”，无需跳转发布页。下载文件通过大小和 SHA256（文件内容摘要）校验后才可安装。

GitHub 是主更新源。国内备用更新源正在配置，尚未承诺所有国内网络可直连；配置入口位于 `src/update_config.json` 的 `mirrors`（备用源）列表。

## 功能

- 导入、编辑和导出中文开局，按分秒组织时间点与资源村民分配。
- 置顶悬浮窗、计时、自动切换与全局快捷键；悬浮正文可调至 6 磅。
- 按文明选择建筑、地标和科技；科技按研究建筑分组。
- 晓晓、云希、晓伊免登录在线朗读与本地语音缓存，也可使用系统声音。
- 通过 AoE4 World 公开接口搜索玩家、查看最近战绩和按模式统计，搜索记录可删除。

软件不读取游戏内存、不自动操作游戏。战绩表来自公开记录，不包含完整的玩家操作时间线。

## 资料保存

便携版默认使用程序旁的“用户数据”；首次安装默认使用个人应用资料目录。
从应用内安装更新时会沿用当前资料目录，保留开局、设置和搜索记录。卸载程序不删除玩家资料。
本仓库与发行包不包含作者的个人开局、查询历史、战绩缓存或语音缓存。

## 从源码运行

需要 Windows 和 Python（运行环境）3.11。

```powershell
# 建立独立运行环境并安装依赖。
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r src\requirements.txt
# 运行中文助手。
.venv\Scripts\python.exe src\app.py
```

## 构建发行版

`src/release_build.py` 负责生成程序、安装包和更新清单。安装器采用 Inno Setup（Windows 安装包编译工具）。
打包时附带完整对应源码、GPL 许可证与第三方许可，不包含用户数据。
更新通道只使用正式发行版；安装包名固定为 `AoE4OpeningAssistant-Setup.exe`，更新清单为 `update.json`。

## 上游与素材

- 原作：[CraftySalamander/RTS_Overlay](https://github.com/CraftySalamander/RTS_Overlay)，采用提交 `aaee02c95e47e4aadb7c3269fd07fb12b5871620`。
- 文明、建筑、科技目录来自 [AoE4 World Data](https://github.com/aoe4world/data)，固定资料版本及完整来源见 `src/文明资料`。
- 图标和游戏名称归各自权利人所有；本项目为非官方工具，没有官方背书。
- 蓝金织锦背景为生成的原创装饰；当前界面主要采用黑灰配色。

完整许可见 [LICENSE](LICENSE) 和“许可与依赖”。新增源码的英文标识附有中文说明；上游原始文件保留原貌与署名。
