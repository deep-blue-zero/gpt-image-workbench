param([Parameter(Mandatory=$true)][string]$ToolsDir)
$ErrorActionPreference = 'Stop'
$toolsPath = [IO.Path]::GetFullPath($ToolsDir)
New-Item -ItemType Directory -Force -Path $toolsPath | Out-Null
$compiler = Join-Path $toolsPath 'inno\ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $download = Join-Path $toolsPath 'innosetup-7.1.1-x64.exe'
    Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe' -OutFile $download
    $expected = '8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0'
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        throw 'Inno Setup download hash mismatch.'
    }
    $target = Join-Path $toolsPath 'inno'
    $process = Start-Process -FilePath $download -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS',('/DIR="' + $target + '"')) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $compiler)) { throw 'Inno Setup installation failed.' }
}
Write-Output $compiler
