# 中文说明：在本目录创建独立构建环境，并将便携程序写入「dist/启动中文助手」。
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
    & $BuildPython -c 'from default_openings import validate_default_bundle; validate_default_bundle()'
    if ($LASTEXITCODE -ne 0) { throw '默认开局资料不完整，构建已停止。' }
    # 共用正式构建入口，避免重新打入已移除的旧地图或旧字体。
    & $BuildPython -c 'from pathlib import Path; from release_build import freeze_program; freeze_program(Path.cwd())'
    if ($LASTEXITCODE -ne 0) { throw '便携程序构建失败。' }
    $ProductRoot = Join-Path $PSScriptRoot 'dist\启动中文助手'
    Copy-Item -LiteralPath '..\LICENSE' -Destination $ProductRoot -Force
    Write-Host "已完成：$ProductRoot"
} finally {
    Pop-Location
}
