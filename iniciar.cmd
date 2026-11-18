@echo off
setlocal
cd /d "%~dp0"
node --env-file-if-exists=.env bot.mjs
if errorlevel 1 pause
