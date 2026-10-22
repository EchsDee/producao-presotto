param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\ProducaoPresotto')
)
$ErrorActionPreference = 'Stop'
try {
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
    $requirementsPath = Join-Path $InstallDir 'requirements.txt'
    $versionPath = Join-Path $InstallDir 'VERSION'
    if (!(Test-Path -LiteralPath $requirementsPath) -or !(Test-Path -LiteralPath $versionPath)) {
        throw 'Instalacao nao encontrada. Informe a pasta do aplicativo usando -InstallDir.'
    }
    $version = [System.IO.File]::ReadAllText($versionPath).Trim()
    if ($version -notin @('1.0.1', '1.0.2')) { throw 'Este reparo e exclusivo para a atualizacao 1.0.1 para 1.0.2.' }
    $expectedBytes = [Convert]::FromBase64String('Rmxhc2s+PTMuMS4wLDwzLjIKYXJnb24yLWNmZmk+PTI1LjEuMCw8MjYKd2FpdHJlc3M+PTMuMC4yLDw0ClBpbGxvdz49MTIsPDEzCg==')
    $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $expected = $utf8.GetString($expectedBytes)
    $bytes = [System.IO.File]::ReadAllBytes($requirementsPath)
    $current = $utf8.GetString($bytes).TrimStart([char]0xFEFF).Replace("`r`n", "`n").Replace("`r", "`n").TrimEnd([char]10)
    if ($current -cne $expected.TrimEnd([char]10)) {
        throw 'As dependencias desta instalacao sao diferentes. Nenhum arquivo foi alterado. Utilize o instalador oficial 1.0.2 na mesma pasta para preservar o banco existente.'
    }
    if ([Convert]::ToBase64String($bytes) -eq [Convert]::ToBase64String($expectedBytes)) {
        Write-Output 'O arquivo de dependencias ja corresponde ao pacote 1.0.2. Nenhum reparo necessario nesta pasta.'
        exit 0
    }
    $backup = $requirementsPath + '.before-update-' + [guid]::NewGuid().ToString('N') + '.bak'
    $temporary = $requirementsPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [System.IO.File]::WriteAllBytes($temporary, $expectedBytes)
    [System.IO.File]::Replace($temporary, $requirementsPath, $backup)
    Write-Output 'Formatacao corrigida. Tente baixar a atualizacao novamente. Banco e credenciais preservados.'
    Write-Output ('Backup: ' + $backup)
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
