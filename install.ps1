param(
    [switch]$Symlink
)

$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginName = "antigravity-fork-session"
$TargetRoot = if ($env:AGY_PLUGINS_DIR) { $env:AGY_PLUGINS_DIR } else { Join-Path $HOME ".gemini\config\plugins" }
$TargetDir = Join-Path $TargetRoot $PluginName

# Locate Python 3.9+
$PythonExe = $null
foreach ($candidate in @("python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $ver = & $cmd.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver -and ([version]$ver -ge [version]"3.9")) {
                $PythonExe = $cmd.Source
                break
            }
        } catch {}
    }
}

if (-not $PythonExe) {
    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $resolved = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null
            if ($resolved -and (Test-Path $resolved)) {
                $PythonExe = $resolved.Trim()
            }
        } catch {}
    }
}

if (-not $PythonExe) {
    Write-Error "Python 3.9+ is required but was not found in PATH. Please install Python 3.9+ first."
    exit 1
}

New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null

if (Test-Path $TargetDir) {
    Write-Host "Updating existing installation at $TargetDir ..."
    Remove-Item -Recurse -Force $TargetDir
}

if ($Symlink) {
    New-Item -ItemType SymbolicLink -Path $TargetDir -Target $RepoDir | Out-Null
    Write-Host "Installed (symlink): $TargetDir -> $RepoDir"
} else {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    foreach ($item in @("plugin.json", "README.md", "assets", "skills", "sidecars")) {
        Copy-Item -Recurse -Force (Join-Path $RepoDir $item) (Join-Path $TargetDir $item)
    }
    Get-ChildItem -Path $TargetDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

    # Configure sidecar.json with the resolved Windows Python executable
    $SidecarJsonPath = Join-Path $TargetDir "sidecars\session-manager\sidecar.json"
    $SidecarConfig = Get-Content -Raw -Encoding UTF8 $SidecarJsonPath | ConvertFrom-Json
    $SidecarConfig.command = $PythonExe
    $JsonOut = $SidecarConfig | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($SidecarJsonPath, $JsonOut + "`n", [System.Text.UTF8Encoding]::new($false))

    Write-Host "Installed: $TargetDir (Python: $PythonExe)"
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Open Antigravity Desktop -> Customizations and enable 'Antigravity-fork-session' (and its Sidecar)."
Write-Host "  2. Click '+' in the right-hand side pane and select '会话 Fork 管理'."
