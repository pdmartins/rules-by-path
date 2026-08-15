@echo off
rem rules-by-path PreToolUse hook launcher (Windows). Always exits 0.
setlocal
set "RBP_DIR=%~dp0.."
for %%P in (py python python3) do (
  where %%P >nul 2>&1 && (
    if /i "%%P"=="py" ( py -3 "%RBP_DIR%\hooks\rules-by-path.py" %* ) else ( %%P "%RBP_DIR%\hooks\rules-by-path.py" %* )
    exit /b 0
  )
)
echo rules-by-path: no Python on PATH; rules are not being injected 1>&2
exit /b 0
