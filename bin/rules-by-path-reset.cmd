@echo off
rem rules-by-path SessionStart reset launcher (Windows). Always exits 0.
setlocal
set "RBP_SCRIPT=%~dp0..\hooks\rules-by-path.py"
where py >nul 2>&1 && ( py -3 "%RBP_SCRIPT%" --reset-session & exit /b 0 )
where python >nul 2>&1 && ( python "%RBP_SCRIPT%" --reset-session & exit /b 0 )
where python3 >nul 2>&1 && ( python3 "%RBP_SCRIPT%" --reset-session & exit /b 0 )
exit /b 0
