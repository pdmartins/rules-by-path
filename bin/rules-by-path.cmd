@echo off
rem rules-by-path admin CLI launcher (Windows).
setlocal
set "RBP_DIR=%~dp0.."
for %%P in (py python python3) do (
  where %%P >nul 2>&1 && (
    if /i "%%P"=="py" ( py -3 "%RBP_DIR%\scripts\rules-by-path-admin.py" %* ) else ( %%P "%RBP_DIR%\scripts\rules-by-path-admin.py" %* )
    exit /b %errorlevel%
  )
)
echo rules-by-path: no Python found on PATH; install Python 3.8+ 1>&2
exit /b 1
