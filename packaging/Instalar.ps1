param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\ProducaoPresotto'),
    [switch]$NoLaunch,
    [switch]$NoShortcuts,
    [switch]$Quiet
)
$ErrorActionPreference = 'Stop'
$staging = $null
try {
    $source = $PSScriptRoot
    $payload = Join-Path $source 'payload.zip'
    if (Test-Path -LiteralPath $payload) {
        $staging = Join-Path ([System.IO.Path]::GetTempPath()) ('presotto-install-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $payload -DestinationPath $staging
        $source = (Get-ChildItem -LiteralPath $staging -Directory | Select-Object -First 1).FullName
    }
    if (!(Test-Path -LiteralPath (Join-Path $source 'runtime\python.exe'))) { throw 'Pacote incompleto.' }
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
    if ([string]::Equals($InstallDir, [System.IO.Path]::GetFullPath($source), [StringComparison]::OrdinalIgnoreCase)) { throw 'Escolha outra pasta para instalar.' }
    $newVersion = [version](Get-Content -LiteralPath (Join-Path $source 'VERSION') -Raw).Trim().Split('-')[0]
    $oldVersionFile = Join-Path $InstallDir 'VERSION'
    if (Test-Path -LiteralPath $oldVersionFile) {
        $oldVersion = [version](Get-Content -LiteralPath $oldVersionFile -Raw).Trim().Split('-')[0]
        if ($oldVersion -gt $newVersion) { throw 'Uma versao mais recente ja esta instalada.' }
        & (Join-Path $InstallDir 'runtime\python.exe') (Join-Path $InstallDir 'desktop.py') stop --no-browser
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel parar a instalacao existente.' }
    }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    $database = Join-Path $InstallDir 'data\kanban.sqlite3'
    if (Test-Path -LiteralPath $database) {
        $backupDir = Join-Path $InstallDir 'data\backups'
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        $backup = Join-Path $backupDir ('before-installer-' + [guid]::NewGuid().ToString('N') + '.sqlite3')
        & (Join-Path $source 'runtime\python.exe') (Join-Path $source 'backup.py') $backup --source $database
        if ($LASTEXITCODE -ne 0) { throw 'Falha no backup; instalacao cancelada.' }
    }
    foreach ($item in Get-ChildItem -LiteralPath $source) {
        if ($item.Name -in @('data','Instalar.ps1','Instalar.cmd')) { continue }
        Copy-Item -LiteralPath $item.FullName -Destination $InstallDir -Recurse -Force
    }
    if (!(Test-Path -LiteralPath $database)) {
        New-Item -ItemType Directory -Path (Split-Path $database) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $source 'data\kanban.sqlite3') -Destination $database
    }
    $pointer = Join-Path $InstallDir 'data\updates\active.json'
    if (Test-Path -LiteralPath $pointer) { Remove-Item -LiteralPath $pointer }
    if (!$NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Producao Presotto'
        New-Item -ItemType Directory -Path $menu -Force | Out-Null
        foreach ($action in @('start','stop')) {
            $name = if ($action -eq 'start') { 'Producao Presotto' } else { 'Parar Producao Presotto' }
            $link = $shell.CreateShortcut((Join-Path $menu ($name + '.lnk')))
            $link.TargetPath = Join-Path $InstallDir 'runtime\pythonw.exe'
            $link.Arguments = '"' + (Join-Path $InstallDir 'desktop.py') + '" ' + $action
            $link.WorkingDirectory = $InstallDir
            $link.Save()
            if ($action -eq 'start') {
                Copy-Item -LiteralPath (Join-Path $menu ($name + '.lnk')) -Destination (Join-Path ([Environment]::GetFolderPath('Desktop')) ($name + '.lnk')) -Force
            }
        }
    }
    if (!$NoLaunch) {
        Start-Process -FilePath (Join-Path $InstallDir 'runtime\pythonw.exe') -ArgumentList ('"' + (Join-Path $InstallDir 'desktop.py') + '" start') -WorkingDirectory $InstallDir -WindowStyle Hidden
    }
    Write-Output ('Instalado em ' + $InstallDir)
    if (!$Quiet -and !$NoLaunch) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show('Instalacao concluida. Use o atalho Producao Presotto para abrir. Login inicial: Admin / adminpasspres.','Producao Presotto') | Out-Null
    }
} catch {
    if (!$Quiet) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'Falha na instalacao') | Out-Null
    }
    Write-Error $_
    exit 1
} finally {
    if ($staging -and (Test-Path -LiteralPath $staging)) {
        $resolved = [System.IO.Path]::GetFullPath($staging)
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and [System.IO.Path]::GetFileName($resolved).StartsWith('presotto-install-')) {
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}
