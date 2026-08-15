@echo off
chcp 65001 >nul
set "FLOWER_PROJECT_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$path = Join-Path $env:FLOWER_PROJECT_ROOT 'voice\choose-voice.ps1'; $source = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8); & ([ScriptBlock]::Create($source))"
pause
