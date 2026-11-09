@echo off
chcp 65001 >nul
title Sentinelle
cd /d "%~dp0"
rem Lanceur Windows : double-cliquez sur ce fichier.
rem Options possibles : --demo  --diagnostic  --remote  --port 8765
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\lanceur.py %*
  goto fin
)
where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\lanceur.py %*
  goto fin
)
echo.
echo Python n'est pas installe sur cet ordinateur.
echo Installez Python 3.12 depuis https://www.python.org/downloads/
echo en cochant la case Add python.exe to PATH pendant l'installation,
echo ou tapez dans un terminal : winget install Python.Python.3.12
echo Puis relancez ce fichier.
:fin
echo.
pause
