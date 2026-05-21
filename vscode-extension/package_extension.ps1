<#
Simple helper to package the extension into a .vsix using `vsce`.
If `vsce` is not installed, install it with `npm install -g vsce`.
#>

try {
  $vsce = Get-Command vsce -ErrorAction Stop
} catch {
  Write-Host "`n`vsce` not found. Install with: npm install -g vsce`n"
  exit 1
}

$cwd = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
Push-Location $cwd
try {
  Write-Host "Packaging extension..."
  vsce package
  Write-Host "Done. A .vsix file will be created in this folder. Install with: code --install-extension <file>.vsix"
} finally {
  Pop-Location
}
