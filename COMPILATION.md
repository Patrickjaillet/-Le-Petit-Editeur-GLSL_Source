# Compilation et lancement

Ce document explique comment compiler le moteur Rust, vendoriser Monaco
Editor, et lancer l'application depuis une machine neuve.

## Prerequis

- **Rust** (edition 2021) avec `cargo` — https://rustup.rs
- **Python** >= 3.9
- **Node.js / npm** (uniquement pour recuperer Monaco Editor, pas necessaire a l'execution)
- Une carte graphique / driver supportant Vulkan, Metal ou DirectX 12 (utilise par `wgpu`)

## 1. Environnement virtuel Python

Depuis la racine du projet :

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` installe `PySide6` (UI) et `maturin` (build du module Rust).

## 2. Compiler le moteur Rust (module `shadertoy_engine`)

Le moteur Rust est compile en extension Python native via `maturin`, et
installe directement dans le venv du projet.

```powershell
cd rust_engine
$env:VIRTUAL_ENV = (Resolve-Path ..\.venv).Path
$env:Path = "$((Resolve-Path ..\.venv\Scripts).Path);$env:Path"
..\.venv\Scripts\python.exe -m maturin develop --release
cd ..
```

`maturin develop` compile le crate (`cargo build --release` sous le capot)
et installe le wheel resultant dans le venv en mode editable. A chaque
modification d'un fichier `.rs`, il faut relancer cette commande pour que
`import shadertoy_engine` reflete le nouveau code.

Verification rapide :

```powershell
.venv\Scripts\python.exe -c "import shadertoy_engine; print(shadertoy_engine)"
```

### Erreur "could not overwrite ... in use"

Si l'application (ou un script Python) a deja importe `shadertoy_engine`,
`maturin develop` ne peut pas remplacer le fichier charge. Fermez tout
processus Python qui utilise le module puis relancez la commande. Si un
dossier `~hadertoy_engine` reste dans
`.venv\Lib\site-packages\`, supprimez-le manuellement avant de reessayer.

## 3. Vendoriser Monaco Editor

Le module `monaco-editor` npm est copie tel quel dans
`python_ui/assets/monaco/vs` (aucune dependance npm au runtime). Utilisez
la version **0.52.2** : les versions plus recentes ont change la structure
de build de `min/vs` et cassent le chargement des web workers.

```powershell
mkdir _monaco_tmp
cd _monaco_tmp
npm init -y
npm install monaco-editor@0.52.2
cd ..
Remove-Item -Recurse -Force python_ui\assets\monaco\vs -ErrorAction SilentlyContinue
Copy-Item -Recurse _monaco_tmp\node_modules\monaco-editor\min\vs python_ui\assets\monaco\vs
Remove-Item -Recurse -Force _monaco_tmp
```

Ce dossier (`python_ui/assets/monaco/vs`, ~9 Mo) doit ensuite etre versionne
ou re-genere sur chaque poste ; il n'est pas telecharge automatiquement au
lancement de l'application.

## 3bis. Recuperer ffmpeg.exe (export video)

L'export video (`video_export.py`, menu *Fichier -> Exporter une video...*)
encode la sequence de frames capturee avec un `ffmpeg.exe` autonome
(build statique "essentials", licence LGPL — voir
`packaging\bin\ffmpeg-LICENSE.txt`) attendu a l'emplacement
`packaging\bin\ffmpeg.exe`. Ce binaire (~100 Mo) n'est pas versionne dans
le depot ; recuperez-le une fois par poste de developpement :

```powershell
Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile _ffmpeg.zip
Expand-Archive _ffmpeg.zip -DestinationPath _ffmpeg_tmp
mkdir packaging\bin -ErrorAction SilentlyContinue
Copy-Item (Get-ChildItem _ffmpeg_tmp -Recurse -Filter ffmpeg.exe)[0].FullName packaging\bin\ffmpeg.exe
Copy-Item (Get-ChildItem _ffmpeg_tmp -Recurse -Filter LICENSE)[0].FullName packaging\bin\ffmpeg-LICENSE.txt
Remove-Item -Recurse -Force _ffmpeg_tmp, _ffmpeg.zip
```

`video_export.resolve_ffmpeg_path()` va chercher ce fichier a deux
endroits selon le mode d'execution :

- **En developpement** (`python run.py`) : directement dans
  `packaging\bin\ffmpeg.exe`.
- **En build PyInstaller** (`PetitEditeurGLSL.exe`) : a la racine du
  dossier `dist\PetitEditeurGLSL\`, a cote de l'exe — `petit_editeur_glsl.spec`
  (etape 5.1) copie automatiquement `packaging\bin\ffmpeg.exe` a cet
  endroit, il n'y a rien a faire manuellement pour le build release une
  fois ce fichier present dans `packaging\bin\`.

Sans ce fichier, l'export video affiche un message d'erreur explicite
(chemin attendu + renvoi vers cette section) plutot que d'echouer
silencieusement.

## 4. Lancer l'application

```powershell
.venv\Scripts\python.exe run.py
```

`run.py` ajoute `python_ui/` au `sys.path` puis demarre `python_ui/main.py`.
Au demarrage, l'application lance aussi un petit serveur HTTP local
(`python_ui/local_server.py`, `127.0.0.1:<port aleatoire>`) qui sert le
dossier `python_ui/assets/` a l'editeur Monaco embarque : Chromium (et donc
QtWebEngine) refuse de charger des Web Workers depuis une page `file://`,
d'ou la necessite de servir ces fichiers en HTTP meme en local.

## Recompilation en developpement

- Modification d'un fichier Python (`python_ui/`) : relancer simplement
  `python run.py`, aucune recompilation necessaire.
- Modification d'un fichier Rust (`rust_engine/src/*.rs`) : refaire l'etape 2
  (`maturin develop --release`) avant de relancer l'application.
- `cargo check` (execute depuis `rust_engine/`) permet de valider rapidement
  le code Rust sans reconstruire le wheel complet.

## 5. Compiler une version release (exe autonome) et l'installeur Windows

Cette section produit un `.exe` distribuable ne necessitant ni Python ni
Rust sur la machine cible, puis un installeur Windows a partir de ce build.
Tous les fichiers correspondants vivent dans `packaging/`.

### 5.1. Build autonome (PyInstaller)

```powershell
.venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
.\packaging\build_release.ps1 ou powershell -ExecutionPolicy Bypass -File .\packaging\build_release.ps1
```

`build_release.ps1` enchaine : compilation Rust en release (etape 2
ci-dessus), verification que `shadertoy_engine` s'importe, installation de
PyInstaller si absent, puis `pyinstaller packaging\petit_editeur_glsl.spec`.
Resultat : `dist\PetitEditeurGLSL\PetitEditeurGLSL.exe` + un dossier
`_internal\` (module natif, PySide6/QtWebEngine, assets Monaco vendorises).
C'est un mode **onedir** (pas onefile) : QtWebEngine lance un sous-processus
(`QtWebEngineProcess.exe`) qui doit retrouver ses ressources a cote de
l'executable, ce qu'un onefile complique inutilement pour une appli deja
distribuee via installeur.

Le spec appelle `collect_all()` uniquement sur les 6 sous-modules PySide6
reellement importes par le code (`QtCore`, `QtGui`, `QtWidgets`,
`QtMultimedia`, `QtWebChannel`, `QtWebEngineWidgets`) plutot que sur tout
`PySide6` : chacun a son propre hook PyInstaller qui embarque ce dont il
a besoin, notamment celui de `QtWebEngineWidgets` qui recupere a lui seul
`QtWebEngineProcess.exe`, `icudtl.dat`, `*.pak` et les traductions
necessaires. Les grandes familles Qt non utilisees (3D, capteurs/IoT,
base de donnees, QML/Quick, PDF, outils de design...) sont en plus
exclues explicitement (`excludes=PYSIDE6_SUBMODULES_EXCLUDED`). Le
dossier de sortie reste volumineux (moteur de rendu web complet) mais
sensiblement plus leger qu'avec un `collect_all("PySide6")` global.

Le spec filtre en plus les locales Chromium embarquees par
`QtWebEngineCore` (`qtwebengine_locales/*.pak`, une cinquantaine de
langues par defaut) pour ne garder que celles correspondant aux 12
langues de `lngs/` (+ `en-US`, repli Chromium). **Ce filtrage n'a pas pu
etre teste dans cet environnement** (pas de PySide6/Windows disponible) :
verifiez avant release que le menu contextuel de l'editeur Monaco
fonctionne correctement dans chacune des 12 langues et qu'aucun
avertissement "locale introuvable" n'apparait au demarrage. En cas de
probleme, supprimer le bloc "Locales QtWebEngine (Chromium)" du spec
revient au comportement precedent (toutes les locales embarquees).

Testez le build avant de generer l'installeur :

```powershell
dist\PetitEditeurGLSL\PetitEditeurGLSL.exe
```

Icone : `packaging\icon.ico` est deja versionne et repris automatiquement
par l'exe (section 5.1) et l'installeur (section 5.2). Genere par
`packaging\make_icon.py` (Pillow, aucun asset externe) ; pour la
regenerer/modifier :

```powershell
.venv\Scripts\python.exe -m pip install pillow
.venv\Scripts\python.exe packaging\make_icon.py
```

### 5.2. Installeur (Inno Setup 6/7)

Prerequis : [Inno Setup](https://jrsoftware.org/isinfo.php) installe sur la
machine qui genere l'installeur (pas necessaire sur les postes cibles).

```powershell
& "C:\Program Files\Inno Setup 7\ISCC.exe" packaging\installer.iss
```

(ou ouvrez `packaging\installer.iss` dans le compilateur Inno Setup et
lancez *Build*). Le script empaquette directement le dossier produit par
`build_release.ps1` (`dist\PetitEditeurGLSL\`) — il faut donc avoir refait
l'etape 5.1 avant, et le script echoue explicitement si ce dossier
n'existe pas encore plutot que de generer un installeur vide.

Sortie : `packaging\output\PetitEditeurGLSL-Setup-<version>.exe`. Installe
dans `Program Files`, cree un raccourci menu Demarrer (+ Bureau optionnel),
et un desinstalleur standard. 64 bits uniquement (le module natif wgpu est
compile en abi3 64 bits).

Pour changer le nom/version affiches dans l'installeur, editer les
`#define AppVersion`/`AppName` en tete de `packaging\installer.iss` (a
tenir synchronise avec `version` dans `rust_engine\Cargo.toml`).

## Depannage

- **Fenetre noire / editeur vide** : verifiez que `python_ui/assets/monaco/vs`
  contient bien `loader.js` et `editor/editor.main.js` (voir etape 3).
- **`ImportError: shadertoy_engine introuvable`** : le module natif n'a pas
  ete compile — refaites l'etape 2.
- **`aucun adaptateur graphique wgpu disponible`** : mettez a jour les
  drivers graphiques ; `wgpu` a besoin d'un backend Vulkan/Metal/DX12
  fonctionnel.
- **Build PyInstaller : fenetre blanche/vide au lancement de l'exe** :
  l'un des dossiers `_internal\assets` ou `_internal\PySide6\translations`
  manque — relancez `packaging\build_release.ps1` depuis zero (il nettoie
  `build\`/`dist\` avant de reconstruire) plutot que d'appeler `pyinstaller`
  a la main sur un `build\` deja present.
- **`ISCC.exe` introuvable** : Inno Setup n'est installe que sur la machine
  qui genere l'installeur, jamais requis a l'execution — installez-le
  depuis jrsoftware.org si vous devez generer l'installeur vous-meme.
- **`AttributeError: 'builtins.Engine' object has no attribute 'set_xxx'`
  (une methode qui existe pourtant dans `rust_engine/src/lib.rs`)** : le
  module natif installe dans le venv est perime — `maturin develop
  --release` n'a pas reellement recompile/remplace le `.pyd`. Symptome
  caracteristique : la commande se termine en **moins d'une seconde**
  (`Finished release profile [optimized] target(s) in 0.5xs`) alors qu'un
  vrai build avec `wgpu` prend normalement 1 a 5 minutes. Cause la plus
  frequente : un `.pyd` verrouille par un process Python encore ouvert
  (une precedente instance de `run.py`, un terminal Python, VSCode/Pylance
  qui a importe le module...) empeche cargo/maturin de remplacer le
  fichier, sans forcement lever d'erreur visible.

  Procedure de recompilation "a froid" pour en etre sur :

  1. Fermer **tous** les processus Python/l'application (verifier avec
     `Get-Process python* -ErrorAction SilentlyContinue`, tuer avec
     `Stop-Process -Id <pid> -Force` si besoin).
  2. Vider le cache de build et desinstaller le paquet existant :
     ```powershell
     Remove-Item -Recurse -Force rust_engine\target
     .venv\Scripts\python.exe -m pip uninstall shadertoy_engine -y
     dir .venv\Lib\site-packages\ | findstr /i shadertoy
     ```
     (le dernier `dir` ne doit plus rien lister)
  3. Recompiler :
     ```powershell
     cd rust_engine
     $env:VIRTUAL_ENV = (Resolve-Path ..\.venv).Path
     $env:Path = "$((Resolve-Path ..\.venv\Scripts).Path);$env:Path"
     ..\.venv\Scripts\python.exe -m maturin develop --release
     cd ..
     ```
     Cette fois la compilation doit prendre nettement plus d'une seconde.
  4. Verifier que la nouvelle methode est bien presente avant de relancer
     l'appli :
     ```powershell
     .venv\Scripts\python.exe -c "import shadertoy_engine; e = shadertoy_engine.Engine(64,64); print(hasattr(e, 'set_ichannel_procedural'))"
     ```
     (adapter le nom de methode teste au besoin) — doit afficher `True`.
