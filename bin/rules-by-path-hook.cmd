@echo off
rem rules-by-path PreToolUse hook launcher (Windows).
rem Always exits 0: a hook must never block a tool call, and a non-zero exit on
rem every Read would make the plugin unusable instead of merely inactive.
setlocal
set "RBP_SCRIPT=%~dp0..\hooks\rules-by-path.py"
where py >nul 2>&1 && (
  py -3 "%RBP_SCRIPT%" %*
  exit /b 0
)
where python >nul 2>&1 && (
  python "%RBP_SCRIPT%" %*
  exit /b 0
)
where python3 >nul 2>&1 && (
  python3 "%RBP_SCRIPT%" %*
  exit /b 0
)
echo rules-by-path: no Python on PATH; rules are not being injected 1>&2
exit /b 0
