@echo off
chcp 65001 >nul
set LOG=E:\novel_desk-agent\logs\clash-service-fix.log
echo ===== RAN %date% %time% ===== > "%LOG%" 2>&1
whoami /groups | findstr /C:"S-1-16-12288" >> "%LOG%" 2>&1
echo --- 1) sc config start= demand --- >> "%LOG%" 2>&1
sc config clash_verge_service start= demand >> "%LOG%" 2>&1
echo --- 2) sc start --- >> "%LOG%" 2>&1
sc start clash_verge_service >> "%LOG%" 2>&1
echo --- 3) sc query --- >> "%LOG%" 2>&1
sc query clash_verge_service >> "%LOG%" 2>&1
echo --- 4) sc qc --- >> "%LOG%" 2>&1
sc qc clash_verge_service >> "%LOG%" 2>&1
echo --- 5) DONE --- >> "%LOG%" 2>&1
type "%LOG%"
echo.
echo 日志: %LOG%
pause
