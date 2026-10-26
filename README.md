# AoE4 HELP（帝国开局助手）

面向《帝国时代 IV》玩家的 Windows 中文开局与复盘工具。当前版本为 **1.9.5 · 王兰花版本**。基于 CraftySalamander 的 RTS_Overlay（即时战略悬浮工具）修改，沿用 GNU GPL 第三版（通用公共许可证）。

## 安装与更新

首次安装请下载 [GitHub 最新安装包](https://github.com/miradore-xyz/aoe4-opening-assistant-zh/releases/latest/download/AoE4OpeningAssistant-Setup.exe)，国内可查看 [Gitee 发行页](https://gitee.com/miradore/aoe4-opening-assistant-zh/releases)。便携版位于对应发行页。

软件会后台检查正式版本；发现新版后可查看更新内容，下载完成后选择“安装并重启”。下载文件通过大小和 SHA-256（文件内容校验值）核对后安装。更新沿用当前资料目录；安装与卸载均不删除玩家开局或设置。本版大安装包由GitHub提供；Gitee提供源码与发行说明，普通附件100MB上限无法承载本版安装包。源码镜像的同步状态以同版本标签为准，连接速度取决于网络环境。

从1.9.4起，安装包和便携版不再附带源码目录或重复素材。**完整对应源码、构建脚本、许可证与所需运行素材均在本仓库同版本标签中公开**；国内源码镜像为 [Gitee](https://gitee.com/miradore/aoe4-opening-assistant-zh)，版本同步状态以镜像标签为准。发行附件为安装包、便携版、更新清单和校验表。

1.9.5主要调整查询历史弹层圆角、查询区间距和搜索记录删除图标，隐藏多余成功提示；“获取开局”入口、开局库操作与下拉箭头统一图标。内置地图继续使用高程参考版。

## 功能与范围

- 导入、编辑和导出开局，按时间安排村民、建筑、科技与文字提示；内置23文明中文练习提要，保留作者与原稿链接。
- 可调整大小的置顶悬浮提示、计时、自动推进和语音朗读；全局快捷键默认留空，由玩家自行配置。
- 基于 AoE4 World（公开战绩站）查询玩家、段位、近期战绩和全部模式统计；查询记录可删除。
- 从公开战绩直接进入软件内对局分析，也可打开本地游戏回放，查看已解析操作、时间线与操作坐标。
- 内置74张官方生成的完整高程参考地图，使用紧凑分块图像，地图资料约116.5MB；无需另下10GB资料包。
- 黑灰界面统一背景 `#0F0F11`、面板 `#161619`；通用操作采用 Lucide（开源线条图标）及其配色派生素材，英文字体 Sen，中文由思源黑体承接。

完整地图是固定的官方生成**参考实例**，不是当前对局随机地形、资源与单位的完整还原。回放中的操作坐标与参考地图分开显示，不能据此推断未提供的位置。软件不读取游戏内存、不自动操作游戏；公开回放是否可下载取决于官方数据是否可用。个人页与在线开局库仍为开发中占位。

中文练习提要来源与23文明列表见 [默认开局来源说明](src/默认开局/来源说明.md)。原稿赛季和收藏量不能替代当前游戏版本的实战验证；不编造原稿未提供的时间和人数。

## 资料保存

便携版默认使用程序旁的“用户数据”；全新安装默认使用个人应用资料目录。安装向导可选择已有资料目录，软件更新会继续使用该目录。开局、设置、查询记录及缓存独立于运行程序保存。

本仓库与发行包不包含作者个人开局、查询历史、账号、私人回放或语音缓存。内置默认开局、参考地图与公开素材来源表属于分发资料。

## 从源码运行

需要 Windows 10 或更新系统与 Python（运行环境）3.11。

```powershell
# 建立独立运行环境并安装固定依赖。
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r src\requirements.txt
# 运行中文助手。
.venv\Scripts\python.exe src\app.py
```

## 构建发行版

`src/release_build.py`（发行构建脚本）生成安装包、便携版、更新清单及校验表；使用 Inno Setup（Windows 安装器编译工具），完整参数见 [构建说明](src/RELEASE.md)。

```powershell
# 在系统Windows插件验证随附字体及真实字形。
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s src -p 'test_app_fonts.py'
# 其余检查由离屏分组装载原始字体字节后执行，仍运行全部其余测试。
python .github/scripts/run_offscreen_checks.py
# 按本机编译器路径构建。
python src/release_build.py --iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' --output-dir release
```

自动构建核对标签与程序版本，字体检查由Windows插件独立执行，其余检查使用已登记真实字体的离屏后端，兼容英语系统的路径编码差异；全部检查通过后才打包。更新通道只使用正式发行版；安装包固定名为 `AoE4OpeningAssistant-Setup.exe`，更新清单为 `update.json`，文件校验表为 `SHA256SUMS.txt`。同标签发行附件不覆盖。

## 上游与素材

- 原作：[CraftySalamander/RTS_Overlay](https://github.com/CraftySalamander/RTS_Overlay)，固定提交 `aaee02c95e47e4aadb7c3269fd07fb12b5871620`。
- 文明、建筑、科技目录：[AoE4 World Data（游戏资料）](https://github.com/aoe4world/data)，固定版本与来源见 `src/文明资料`。
- 默认开局：[AoE4 Guides（开局指南）](https://aoe4guides.com/builds)，作者和具体链接保存在各开局及来源表中。
- [Lucide（开源线条图标）](https://lucide.dev/)：固定官方提交、原始SVG、ISC及相关MIT声明见 `src/界面素材/图标/Lucide`。
- [Sen](https://github.com/philatype/Sen) 与 [思源黑体](https://github.com/adobe-fonts/source-han-sans)：固定来源和开放字体许可见 `src/界面素材/字体`。Sen不含中文字形，中文使用思源黑体。
- 游戏图像和名称归各自权利人所有；本项目为非官方工具，无官方背书。

完整许可见 [LICENSE](LICENSE)、[许可与依赖](许可与依赖) 及各素材目录的来源与许可文件。新增源码的英文标识附中文说明；上游文件保留原貌与署名。
