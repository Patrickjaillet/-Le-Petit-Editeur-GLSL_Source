# Compile le moteur Rust en release, vérifie que le module natif est bien
# installé, puis fige l'application PySide6 en exécutable autonome via
# PyInstaller. À lancer depuis la racine du projet OU depuis packaging/ :
# le script se replace tout seul à la racine.
#
#   .\packaging\build_release.ps1
#
# Résultat : dist\PetitEditeurGLSL\PetitEditeurGLSL.exe (dossier complet,
# c'est lui que packaging\installer.iss empaquette ensuite).
#
# Prérequis : le venv .venv doit déjà exister avec PySide6 + maturin
# installés (voir docs/COMPILATION.md, étape 1), et pyinstaller doit être
# installé dedans (`.venv\Scripts\python.exe -m pip install pyinstaller`).

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "venv introuvable ($Python). Créez-le d'abord (docs/COMPILATION.md, étape 1)."
}

$FfmpegExe = Join-Path $ProjectRoot "packaging\bin\ffmpeg.exe"
if (-not (Test-Path $FfmpegExe)) {
    throw "packaging\bin\ffmpeg.exe introuvable. Ce binaire (~100 Mo) n'est pas versionné dans le dépôt : téléchargez-le d'abord (docs/COMPILATION.md, étape 3bis), sinon l'installeur serait généré sans lui et l'export vidéo serait cassé une fois installé."
}

Write-Host "== 1. Compilation du moteur Rust (release) ==" -ForegroundColor Cyan
Push-Location (Join-Path $ProjectRoot "rust_engine")
try {
    $env:VIRTUAL_ENV = (Resolve-Path (Join-Path $ProjectRoot ".venv")).Path
    $env:Path = "$((Resolve-Path (Join-Path $ProjectRoot '.venv\Scripts')).Path);$env:Path"
    & $Python -m maturin develop --release
    if ($LASTEXITCODE -ne 0) { throw "maturin develop --release a échoué (code $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

Write-Host "== 2. Vérification du module natif ==" -ForegroundColor Cyan
& $Python -c "import shadertoy_engine; print('shadertoy_engine OK ->', shadertoy_engine.__file__)"
if ($LASTEXITCODE -ne 0) { throw "import shadertoy_engine a échoué après le build" }

Write-Host "== 3. Vérification de PyInstaller ==" -ForegroundColor Cyan
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller absent du venv, installation..." -ForegroundColor Yellow
    & $Python -m pip install -r (Join-Path $ProjectRoot "packaging\requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "échec de l'installation de pyinstaller" }
}

Write-Host "== 4. Nettoyage build/dist précédents ==" -ForegroundColor Cyan
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "dist") -ErrorAction SilentlyContinue

Write-Host "== 5. Empaquetage PyInstaller (onedir) ==" -ForegroundColor Cyan
& $Python -m PyInstaller (Join-Path $ProjectRoot "packaging\petit_editeur_glsl.spec") --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a échoué (code $LASTEXITCODE)" }

$OutDir = Join-Path $ProjectRoot "dist\PetitEditeurGLSL"

Write-Host "== 6. Vérification de ffmpeg.exe dans le bundle ==" -ForegroundColor Cyan
$BundledFfmpeg = Join-Path $OutDir "ffmpeg.exe"
if (-not (Test-Path $BundledFfmpeg)) {
    throw "ffmpeg.exe absent de $OutDir malgré packaging\bin\ffmpeg.exe présent : le .spec n'a pas copié le binaire, vérifiez petit_editeur_glsl.spec."
}

Write-Host ""
Write-Host "== Build terminé : $OutDir ==" -ForegroundColor Green
Write-Host "Testez-le avec :  $OutDir\PetitEditeurGLSL.exe"
Write-Host "Puis générez l'installeur avec Inno Setup : packaging\installer.iss"
