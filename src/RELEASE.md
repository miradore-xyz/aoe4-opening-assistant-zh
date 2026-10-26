# 安装包与发布构建

`release_build.py`（发行构建脚本）生成固定名称的安装包、便携压缩包、更新清单及 SHA-256（文件校验值）。1.9.4起，程序包不再包含源码目录或重复的源码素材，也不生成独立源码附件；对应源码在GitHub仓库公开。需要 Windows 10 或更新系统、Python 3.11.9（运行环境）、`requirements.txt` 中的固定依赖，以及 Inno Setup 6.7.3（安装包编译工具）。固定依赖保证构建流程可重复，构建时间及压缩器记录仍可能使两次产物的二进制校验值不同。

在仓库根目录运行：

```powershell
# 安装项目声明的构建依赖。
python -m pip install -r src/requirements.txt
# 从源码构建全部发行文件，ISCC 路径替换成实际安装位置。
python src/release_build.py --iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' --output-dir release
```

已经完成程序冻结时，可传 `--dist-dir`（已构建程序目录）复用该次构建。此目录必须包含“启动中文助手.exe”和 `_internal`（程序依赖），务必确认它来自当前源码。所有输入目录都只读，构建脚本不运行安装器、不修改当前用户软件。

只从明确的运行文件、示例和许可证构建；不会遍历现有用户目录。安装包排除个人开局、设置、搜索记录、语音缓存及旧离线模型。版权、许可证、第三方署名及素材来源继续随附，运行需要的地图、字体、游戏图标各保留一份。

公开源码仓库为 https://github.com/miradore-xyz/aoe4-opening-assistant-zh 。正式发布二进制前，应在仓库同版本标签提供完整对应源码、构建脚本和所需素材，并在发行页标明获取方式；公开旧版源码不能替代新版对应源码。本地构建不推送仓库、不创建标签、不上传附件。

国内源码镜像为 https://gitee.com/code_liujiaxin/aoe4-opening-assistant-zh 。发布时两边同步同一版本源码和标签，发行附件使用相同二进制文件和校验值；备用更新清单使用Gitee自身的附件地址。

## 内置紧凑地图与字体

1.9.3起，74张官方完整代表实例只保留高程地表版，使用WebP（压缩图像格式）分块内置，地图资料共约116.5MB，其中图像约97.4MB。最大显示图像3072像素，质量90；原始实体坐标、坐标校准、场景范围及来源证明保留。图像压缩会损失纹理细节，不改变实体位置。构建脚本与压缩派生记录随源码提供。

安装包与便携版均直接带齐地图，无需另下10GB资料包。更新后优先使用内置紧凑图；只有内置资料缺失时才使用已保存的外部资料位置。内置资料正常时隐藏“地图资料”入口。完整地图属于官方生成参考实例，不能当成当前实际对局的地形或随机资源。

Sen（英文字体）与Source Han Sans SC（思源黑体简体中文）随程序内置，只登记到当前应用进程，不安装进系统字体目录。程序包中的字体、固定来源与SIL开放字体许可证均位于“_internal/assets/design/字体”。Sen不包含中文字形，中文由思源黑体承接。

## 安装与更新参数

安装器 `AoE4OpeningAssistant-Setup.exe`（固定发行文件名）的默认位置为 `%LOCALAPPDATA%\Programs\AoE4OpeningAssistant`，无需管理员权限。全新安装默认数据在 `%APPDATA%\AoE4OpeningAssistant`。向导允许选择现有便携版的“用户数据”目录，不复制、不移动、不删除该目录。安装到原便携目录时，自动沿用旁边已有的“用户数据”。

软件内更新使用参数列表，实际路径由当前程序提供：

```text
/SILENT
/NORESTART
/SUPPRESSMSGBOXES
/CURRENTUSER
/DIR=<当前程序文件夹的绝对路径>
/DATA_DIR=<当前实际资料文件夹的绝对路径>
/WAITPID=<当前程序的进程编号>
/RESTART_APP=1
```

`/SILENT`（安静安装）、`/NORESTART`（不重启系统）、`/CURRENTUSER`（当前用户安装）由 Inno Setup 提供；`/DIR` 指定当前安装位置。`/DATA_DIR`（资料目录）、`/WAITPID`（等待退出的进程编号）、`/RESTART_APP`（安装后启动）为本项目约定。

更新界面先检查并处理未保存的编辑窗口，启动安装器后正常关闭程序。安装器等待指定进程自然退出最多60秒，超时中止；不强制终止进程、不自动关闭其他程序。安装成功后只启动一次新程序，并传入同一个 `--data-dir`（资料目录）参数。不要同时传 Inno Setup 的 `/CLOSEAPPLICATIONS` 或 `/RESTARTAPPLICATIONS`，以免外部重启流程造成重复实例。

安装器将 UTF-8（不含字节序标记）的 `data-location.json` 写到程序旁：

```json
{"data_dir":"C:\\Users\\示例\\AppData\\Roaming\\AoE4OpeningAssistant"}
```

其中 `data_dir` 表示资料的绝对路径。快捷方式与安装后启动均明确传相同目录；直接双击程序时也由程序读取该约定。卸载不清除此目录，不添加用户数据清理选项。

## GitHub 自动构建

仓库的 `.github/workflows/release.yml`（GitHub 自动构建流程）固定官方构建依赖，先运行测试，再构建发行文件。手动运行只保存构建产物；推送 `v1.6.0` 一类标签时，核对标签与 `src/version.py`（程序版本）一致后发布到 GitHub Releases（发行版）。软件更新客户端读取固定安装包文件名及 `SHA256SUMS.txt`（校验值清单），无须用户打开发布页。

Inno Setup 下载自官方 GitHub，固定版本 6.7.3，SHA-256 为 `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`；流程同时校验官方数字签名。简体中文安装向导采用官方源码随附的社区翻译，原署名和许可保留在 `installer_resources`（安装器资源）内。

当前项目未配置 Windows 代码签名证书。安装包自身的下载校验由更新客户端完成，Windows 首次运行仍可能提示未知发布者；获得发布证书后，可在编译步骤配置 Inno Setup 的签名工具。
