@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Met Sentinelle à jour sans toucher à vos données (dossier data).
if not exist .git goto zip
where git >nul 2>nul
if not %errorlevel%==0 goto zip
echo Mise a jour en cours...
git pull --ff-only
if errorlevel 1 (
  echo La mise a jour automatique a echoue : vos fichiers ont peut-etre ete modifies.
  goto fin
)
echo Mise a jour terminee. Les composants seront mis a jour au prochain lancement.
goto fin
:zip
echo Sentinelle a ete installe depuis un ZIP (sans Git).
echo Pour mettre a jour : telechargez le nouveau ZIP, puis copiez-y votre dossier data.
echo Astuce : installez Git (winget install Git.Git) pour des mises a jour en un clic.
:fin
echo.
pause
