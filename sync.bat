@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  「无语子点点」公众号文章归档 —— 一键增量同步到 GitHub
rem  前置条件：微信 PC 客户端 (Weixin.exe) 已启动并登录
rem  用法：双击本文件即可；或 sync.bat --no-push 只本地构建
rem ============================================================

set "PY=C:\Users\zhico\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "HERE=%~dp0"

if not exist "%PY%" (
  echo [ERROR] 找不到 Python 解释器: %PY%
  pause
  exit /b 1
)

cd /d "%HERE%"

echo [%date% %time%] 开始同步 ...
"%PY%" "%HERE%tools\sync.py" %*

set RC=%ERRORLEVEL%
echo.
echo [%date% %time%] 同步结束，返回码 = %RC%
pause
exit /b %RC%
