<#
Open VS Code in Extension Development Host mode for quick testing.
Usage: ./run_dev_host.ps1
Requires: `code` CLI available in PATH.
#>

$extPath = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
try {
  $code = Get-Command code -ErrorAction Stop
} catch {
  Write-Host "`n`code CLI not found. Install or add VS Code to PATH.`n"
  exit 1
}

Write-Host "Opening VS Code in Extension Development Host..."
& code --extensionDevelopmentPath $extPath
