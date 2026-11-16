@echo off
rem One-click deploy of PrintQ to the remote server. Double-click this file.
rem Settings come from the project's .env (including DEPLOY_PUBLIC_PORT); extra arguments are passed through.
title Deploy PrintQ
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
if errorlevel 1 (
  echo.
  echo DEPLOY FAILED. Read the messages above; the server keeps running the previous release.
) else (
  echo.
  echo DEPLOY FINISHED.
)
pause
