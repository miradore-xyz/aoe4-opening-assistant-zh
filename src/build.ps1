# 中文说明：在本目录创建独立构建环境，并将便携程序写入「构建产物」。
# 参数 PythonPath（运行环境路径）可指定已安装的 Python 3.11。
param([string]$PythonPath = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $PythonPath -m venv .build-env
    if ($LASTEXITCODE -ne 0) { throw '创建构建环境失败。' }
    $BuildPython = Join-Path $PSScriptRoot '.build-env\Scripts\python.exe'
    & $BuildPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '安装构建依赖失败。' }
    & $BuildPython -m PyInstaller --noconfirm --clean --windowed --onedir --noupx --name '启动中文助手' --paths 'upstream/python' --icon '界面素材/龙序.ico' --add-data 'upstream/docs/assets;assets' --add-data '高清图标;assets/hd' --add-data '界面素材;assets/design' --add-data '文明旗帜;assets/flags' --add-data '文明资料;assets/catalog' --add-data 'update_config.json;assets' --add-data 'qt_zh_CN.qm;.' --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes --collect-all edge_tts --collect-data certifi --exclude-module sherpa_onnx --exclude-module sherpa_onnx_core --exclude-module numpy --distpath '构建产物' --workpath '构建缓存' app.py
    if ($LASTEXITCODE -ne 0) { throw '便携程序构建失败。' }
    $ProductRoot = Join-Path $PSScriptRoot '构建产物\启动中文助手'
    Copy-Item -LiteralPath '..\示例开局' -Destination (Join-Path $ProductRoot '示例开局') -Recurse -Force
    Copy-Item -LiteralPath '..\LICENSE' -Destination $ProductRoot -Force
    Write-Host "已完成：$ProductRoot"
} finally {
    Pop-Location
}
