@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 开发模式：run.bat --reload（或 -r）改代码自动重载；默认不开，保留一键重启/关闭
set "RELOAD_FLAG="
if /i "%~1"=="--reload" set "RELOAD_FLAG=--reload"
if /i "%~1"=="-r" set "RELOAD_FLAG=--reload"

rem 国内镜像源（清华 TUNA），可用环境变量 PIP_INDEX_URL 覆盖
if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

rem ---- 便携 Python 3.12：解压到项目内 .python，不依赖系统已装环境、不写注册表 ----
rem 固定 3.12：winsdk 只有 cp312 及以下的预编译包，3.13+ 会卡在源码编译
set "PYEXE=%~dp0.python\python.exe"
if not exist "%PYEXE%" (
  echo [0/3] 下载便携 Python 3.12.8（约 14MB，Azure 中国区 NuGet 镜像）...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $pkg=Join-Path $env:TEMP 'python.3.12.8.zip'; $tmp=Join-Path $env:TEMP 'py312x'; Invoke-WebRequest -UseBasicParsing -Uri 'https://nuget.azure.cn/v3-flatcontainer/python/3.12.8/python.3.12.8.nupkg' -OutFile $pkg; $h=(Get-FileHash -LiteralPath $pkg -Algorithm SHA256).Hash; if ($h -ne '406856be971d957e0bee7a5cefe20a5ec78d70a495e9e33cd0e53d31faec049d') { Write-Error ('便携 Python 校验不匹配: ' + $h); exit 1 }; if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }; Expand-Archive -LiteralPath $pkg -DestinationPath $tmp -Force; if (Test-Path '.python') { Remove-Item '.python' -Recurse -Force }; Move-Item (Join-Path $tmp 'tools') '.python'; Remove-Item $tmp -Recurse -Force; Remove-Item $pkg -Force" || goto :error
  if not exist "%PYEXE%" goto :error
)
"%PYEXE%" --version || goto :error

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys;raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo 现有 .venv 不是 Python 3.12，winsdk 等依赖会安装失败。
    echo 请删除 .venv 目录后重新运行 run.bat。
    pause
    goto :eof
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 正在创建虚拟环境并安装依赖...
  echo [1/3] 创建虚拟环境 .venv ...
  "%PYEXE%" -m venv .venv || goto :error
  echo [1/3] 升级 pip ...
  ".venv\Scripts\python.exe" -m pip --isolated install --upgrade pip -i %PIP_INDEX_URL% || goto :error
  echo [1/3] 安装核心依赖 ...
  ".venv\Scripts\python.exe" -m pip --isolated install -r requirements.txt -i %PIP_INDEX_URL% || goto :error
  rem 可选能力各自一组：装不上只关掉对应功能，不能拖垮整个启动流程
  for %%G in (weixin4 moments pet) do call :trygroup %%G
)

rem 桌宠素材与余额挂件来自上游仓库；公开包不内嵌这些素材，需要本机自行拉取
echo [2/3] 初始化上游素材子模块（没有 Git 或网络不通会跳过，相关功能给出可读报错）...
where git >nul 2>nul && (git submodule update --init --recursive) || echo       已跳过：未找到 git 命令

echo [3/3] 启动 Deep肥鱼 控制台 http://127.0.0.1:8765
rem 后台轮询首页：服务真正可用（HTTP 200）后再弹出浏览器，避免打开时连不上或白屏
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 180; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/' -TimeoutSec 2; if ($r.StatusCode -eq 200) { Start-Process 'http://127.0.0.1:8765'; break } } catch { }; Start-Sleep -Seconds 1 }"
".venv\Scripts\python.exe" -m app.main %RELOAD_FLAG%
goto :eof

:trygroup
".venv\Scripts\python.exe" -m pip --isolated install -r requirements-%1.txt -i %PIP_INDEX_URL% 1>nul 2>nul
if errorlevel 1 echo       可选组 %1 未安装：对应功能不会启用，不影响启动
goto :eof

:error
echo.
echo 启动失败：请检查网络（Python/依赖下载）后重试。
pause
