; Script Inno Setup (6/7) pour Petit Editeur GLSL.
;
; Prerequis : avoir d'abord genere le build autonome avec
; packaging\build_release.ps1 (produit dist\PetitEditeurGLSL\*.exe + ses
; dependances). Ce script se contente d'empaqueter ce dossier deja pret.
;
; Compilation : ouvrir ce fichier dans Inno Setup Compiler (ISCC.exe) et
; lancer "Build", ou en ligne de commande depuis la racine du projet :
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; Sortie : packaging\output\PetitEditeurGLSL-Setup-<version>.exe

#define AppName "Petit Editeur GLSL"
#define AppVersion "0.1.18"
#define AppPublisher "Petit Editeur GLSL"
#define AppExeName "PetitEditeurGLSL.exe"
; Dossier produit par packaging\build_release.ps1 (PyInstaller, mode onedir)
#define BuildDir "..\dist\PetitEditeurGLSL"
#define IconFile "icon.ico"

[Setup]
AppId={{6F2C6E5D-6C7A-4B7A-9E7B-3B6B6E6E1A02}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Le module natif est compile en abi3 64 bits (wgpu) : installeur 64 bits uniquement.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=PetitEditeurGLSL-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}
; Genere par packaging\build_release.ps1 : le script echoue proprement si
; ce dossier n'existe pas encore, plutot que de produire un installeur vide.
#if !DirExists(BuildDir)
  #error "dist\PetitEditeurGLSL introuvable : lancez d'abord packaging\build_release.ps1"
#endif
#if FileExists(IconFile)
SetupIconFile={#IconFile}
#endif

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copie tout le dossier de build (exe, DLLs, PySide6/QtWebEngine, module
; natif shadertoy_engine, assets Monaco) tel quel, en preservant la
; structure de sous-dossiers.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; QtWebEngine ecrit un profil/cache a cote de l'exe au premier lancement ;
; le supprimer evite de laisser des fichiers orphelins apres desinstallation.
Type: filesandordirs; Name: "{app}"
