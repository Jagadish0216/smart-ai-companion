[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$TftEsPiPath = (Join-Path $env:USERPROFILE 'Documents\Arduino\libraries\TFT_eSPI')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $sourcePath = Join-Path $PSScriptRoot '..\tft_espi\User_Setup.h'
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Repository setup file was not found: $sourcePath"
    }
    $sourcePath = (Resolve-Path -LiteralPath $sourcePath).Path

    if (-not (Test-Path -LiteralPath $TftEsPiPath -PathType Container)) {
        throw "TFT_eSPI library directory was not found: $TftEsPiPath"
    }
    $libraryPath = (Resolve-Path -LiteralPath $TftEsPiPath).Path
    $targetPath = Join-Path $libraryPath 'User_Setup.h'
    if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        throw "Installed TFT_eSPI User_Setup.h was not found: $targetPath"
    }

    $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    $targetHash = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash
    if ($sourceHash -eq $targetHash) {
        Write-Host 'TFT_eSPI User_Setup.h already matches the repository setup.' -ForegroundColor Green
        Write-Host "Installed library: $libraryPath"
        exit 0
    }

    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmssfff'
    $backupPath = Join-Path $libraryPath "User_Setup.h.backup-$timestamp"
    Copy-Item -LiteralPath $targetPath -Destination $backupPath
    if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
        throw "Backup could not be verified: $backupPath"
    }

    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
    $installedHash = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash
    if ($installedHash -ne $sourceHash) {
        throw "Copied setup did not match the repository source. Backup remains at: $backupPath"
    }

    Write-Host 'TFT_eSPI setup applied successfully.' -ForegroundColor Green
    Write-Host "Source: $sourcePath"
    Write-Host "Installed: $targetPath"
    Write-Host "Backup: $backupPath"
} catch {
    Write-Error "Unable to apply TFT_eSPI setup. $($_.Exception.Message)"
    exit 1
}
