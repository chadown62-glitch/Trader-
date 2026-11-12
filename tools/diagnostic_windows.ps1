<#
.SYNOPSIS
  Diagnostic Windows 11 de TRADER AI : installation propre dans un dossier temporaire,
  diagnostic complet, tests rapides. Exécution finie ; rien n'est laissé en marche.

.DESCRIPTION
  - copie le code (sans data, .venv, .git) dans %TEMP%\trader-ai-diagnostic-<date> ;
  - crée un environnement Python 3.12 neuf et y installe les dépendances ;
  - lance tools/diagnose_windows.py : SQLite et portefeuille de 30 euros fictifs dans un dossier
    temporaire, démarrage / arrêt / redémarrage du panel de démonstration, intégrité des
    modèles gelés, conflit de port, lecture SEULE de votre dossier data, prix publics ;
  - exécute les tests rapides rejouables (pytest) ;
  - écrit le rapport JSON et le journal dans le dossier rapports\ de TRADER AI ;
  - supprime le dossier temporaire (sauf -Conserver).

  Votre dossier data, votre .venv et vos réglages ne sont jamais modifiés. Aucune clé,
  aucun compte, aucun ordre réel. Ce script n'a PAS été exécuté sur un vrai Windows 11 par
  ses auteurs : son premier passage sur votre PC est la première validation Windows.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\diagnostic_windows.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\diagnostic_windows.ps1 -SansReseau -SansTests
#>
param(
  [switch]$SansReseau,   # ne pas interroger les prix publics (Binance, lecture seule)
  [switch]$SansTests,    # ne pas lancer les tests rapides
  [switch]$Conserver     # garder le dossier temporaire pour inspection
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$Racine = Split-Path -Parent $PSScriptRoot
$Horodatage = Get-Date -Format 'yyyyMMdd-HHmmss'
$Travail = Join-Path $env:TEMP "trader-ai-diagnostic-$Horodatage"
$Rapports = Join-Path $Racine 'rapports'
New-Item -ItemType Directory -Force -Path $Travail, $Rapports | Out-Null
$Journal = Join-Path $Rapports "diagnostic-windows-$Horodatage.log"
$Rapport = Join-Path $Rapports "diagnostic-$Horodatage.json"
$Resultat = @{ installation = 'non lancé'; diagnostic = $null; tests = 'non lancés' }

Start-Transcript -Path $Journal | Out-Null
try {
  Write-Host "== TRADER AI : diagnostic Windows ($Horodatage) =="
  $os = Get-CimInstance Win32_OperatingSystem
  Write-Host "Système : $($os.Caption) $($os.Version) (build $($os.BuildNumber))"
  if ([int]$os.BuildNumber -lt 22000) { Write-Warning "Ce PC n'est pas sous Windows 11 (build inférieur à 22000) : le rapport ne validera pas Windows 11." }

  # 1. Python 3.12 (lanceur py de python.org)
  if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Lanceur Python 'py' introuvable. Installez Python 3.12 : winget install Python.Python.3.12"
  }
  & py -3.12 --version
  if ($LASTEXITCODE -ne 0) { throw "Python 3.12 introuvable. Installez-le : winget install Python.Python.3.12" }

  # 2. Copie du code seulement (vos données ne sont pas copiées)
  $Code = Join-Path $Travail 'code'
  robocopy $Racine $Code /E /XD data data-demo .venv .git rapports __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "Copie du code impossible (robocopy, code $LASTEXITCODE)." }

  # 3. Environnement Python neuf, dans le dossier temporaire
  $Venv = Join-Path $Travail 'venv'
  & py -3.12 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { throw "Création de l'environnement Python impossible." }
  $Python = Join-Path $Venv 'Scripts\python.exe'
  Write-Host "Installation des dépendances (quelques minutes)..."
  & $Python -m pip install --disable-pip-version-check -q -r (Join-Path $Code 'requirements.txt') pytest
  if ($LASTEXITCODE -ne 0) { throw "Installation des dépendances échouée : vérifiez la connexion internet." }
  $Resultat.installation = 'réussie'

  # 4. Diagnostic : votre dossier data est seulement lu
  $Arguments = @('-m', 'tools.diagnose_windows', '--donnees', (Join-Path $Racine 'data'), '--rapport', $Rapport)
  if (-not $SansReseau) { $Arguments += '--reseau' }
  Push-Location $Code
  try { & $Python @Arguments; $Resultat.diagnostic = $LASTEXITCODE } finally { Pop-Location }

  # 5. Tests rapides rejouables (sans réseau requis, données artificielles)
  if (-not $SansTests) {
    Push-Location $Code
    try {
      & $Python -m pytest -q -o addopts='' -p no:cacheprovider tests/test_preflight.py tests/test_diagnostic.py tests/test_lockdown.py tests/test_portfolio.py tests/test_api.py
      $Resultat.tests = if ($LASTEXITCODE -eq 0) { 'réussis' } else { "échecs (code $LASTEXITCODE)" }
    } finally { Pop-Location }
  }
}
catch {
  Write-Host "ERREUR : $($_.Exception.Message)" -ForegroundColor Red
  $Resultat.erreur = $_.Exception.Message
}
finally {
  Write-Host ""
  Write-Host "== Résumé =="
  Write-Host "Installation propre : $($Resultat.installation)"
  Write-Host ("Diagnostic : " + $(if ($null -eq $Resultat.diagnostic) { 'non lancé' } elseif ($Resultat.diagnostic -eq 0) { 'installation fonctionnelle' } else { "problèmes (code $($Resultat.diagnostic))" }))
  Write-Host "Tests rapides : $($Resultat.tests)"
  if (Test-Path $Rapport) { Write-Host "Rapport : $Rapport (affiché aussi dans le panel, page Préparation)" }
  Write-Host "Journal : $Journal"
  Stop-Transcript | Out-Null
  if (-not $Conserver) { Remove-Item -Recurse -Force $Travail -ErrorAction SilentlyContinue }
  else { Write-Host "Dossier temporaire conservé : $Travail" }
}
