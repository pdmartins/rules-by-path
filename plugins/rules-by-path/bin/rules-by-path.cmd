@echo off
rem rules-by-path admin CLI launcher (Windows).
rem No for-loop: %errorlevel% inside a for body expands at parse time, so an
rem `exit /b %errorlevel%` there always reports 0.
setlocal
set "RBP_SCRIPT=%~dp0..\scripts\rules-by-path-admin.py"
where py >nul 2>&1 && (
  py -3 "%RBP_SCRIPT%" %*
  exit /b
)
where python >nul 2>&1 && (
  python "%RBP_SCRIPT%" %*
  exit /b
)
where python3 >nul 2>&1 && (
  python3 "%RBP_SCRIPT%" %*
  exit /b
)
echo rules-by-path: no Python found on PATH; install Python 3.8+ 1>&2
exit /b 1
