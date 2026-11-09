@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Démo hors-ligne : marché simulé, aucune connexion nécessaire.
call lancer.bat --demo --data data-demo
