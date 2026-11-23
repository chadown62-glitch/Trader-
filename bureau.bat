@echo off
chcp 65001 >nul
title Sentinelle
cd /d "%~dp0"
rem Application de bureau Windows : double-cliquez sur ce fichier ou sur le raccourci « Sentinelle » du Bureau.
rem La fenêtre Sentinelle s'ouvre ; la fermer arrête Sentinelle. Option possible : --demo (marché simulé).
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\lanceur.py --bureau %*
  goto fin
)
where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\lanceur.py --bureau %*
  goto fin
)
echo.
echo Python n'est pas installe sur cet ordinateur.
echo Installez Python 3.12 depuis https://www.python.org/downloads/
echo en cochant la case Add python.exe to PATH pendant l'installation,
echo ou tapez dans un terminal : winget install Python.Python.3.12
echo Puis relancez ce fichier.
pause
exit /b 1
:fin
if errorlevel 1 pause
