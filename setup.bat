@echo off
title nanoMuse One-Click Setup
cls
echo ==========================================================
echo                 nanoMuse One-Click Setup                 
echo ==========================================================
echo An open-source personal AI agent for every device you own.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0install.ps1'"
pause
