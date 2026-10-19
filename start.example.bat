@echo off
REM StuLink 启动示例（复制为 start.bat 后按本机情况修改；start.bat 已被 .gitignore 忽略）
REM 不在这里写任何密钥/口令；请用环境变量或 .env 提供。
setlocal

if "%STULINK_BIND%"=="" set STULINK_BIND=0.0.0.0
if "%STULINK_PORT%"=="" set STULINK_PORT=5000
if "%STULINK_HTTPS%"=="" set STULINK_HTTPS=0

echo [StuLink] bind=%STULINK_BIND% port=%STULINK_PORT% https=%STULINK_HTTPS%
py -3.12 run.py

endlocal
