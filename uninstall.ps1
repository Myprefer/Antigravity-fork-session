$ErrorActionPreference = "Stop"

$PluginName = "antigravity-fork-session"
$TargetRoot = if ($env:AGY_PLUGINS_DIR) { $env:AGY_PLUGINS_DIR } else { Join-Path $HOME ".gemini\config\plugins" }
$TargetDir = Join-Path $TargetRoot $PluginName

if (Test-Path $TargetDir) {
    Remove-Item -Recurse -Force $TargetDir
    Write-Host "Removed: $TargetDir"
} else {
    Write-Host "Plugin not found at $TargetDir"
}
