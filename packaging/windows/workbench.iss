#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={code:ApplicationId}
UsePreviousLanguage=no
AppName=GPT Image Workbench
AppVersion={#AppVersion}
AppPublisher=deep-blue-zero
AppPublisherURL=https://github.com/deep-blue-zero/gpt-image-workbench
AppSupportURL=https://github.com/deep-blue-zero/gpt-image-workbench/issues
AppUpdatesURL=https://github.com/deep-blue-zero/gpt-image-workbench/releases/latest
DefaultDirName={localappdata}\Programs\GPT Image Workbench
DefaultGroupName=GPT Image Workbench
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=gpt-image-workbench-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\GPTImageWorkbench.exe
AppMutex=Local\GPTImageWorkbench
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
SetupLogging=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GPT Image Workbench"; Filename: "{app}\GPTImageWorkbench.exe"
Name: "{autodesktop}\GPT Image Workbench"; Filename: "{app}\GPTImageWorkbench.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GPTImageWorkbench.exe"; Description: "Open GPT Image Workbench"; Flags: nowait postinstall skipifsilent

[Code]
function ApplicationId(Param: String): String;
begin
  Result := 'GPTImageWorkbench-338C4D94-51F2-4CDE-89FD-6F46B517F2AC';
  { Isolated installer testing never replaces a user's installed application registration. }
  if ExpandConstant('{param:SMOKETEST|0}') = '1' then
    Result := Result + '-SmokeTest';
end;

{ User libraries and Credential Manager entries are never registered as installed files.
  Upgrades and uninstall therefore preserve them. }
