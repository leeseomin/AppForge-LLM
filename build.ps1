Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:Mode = "serve"
$script:NoOpen = $false
$script:WebProcess = $null
$script:NpmBin = $null
$script:BunBin = $null

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[build.ps1] $Message"
}

function Stop-WithError {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw $Message
}

function Show-Usage {
    Write-Host @"
Usage: .\build.ps1 [--smoke] [--check] [--no-open] [--help]

Prepare and launch the local AppForge-LLM v7 AI app builder web UI.

Options:
  --smoke     Start the web app, probe /api/health and /, then stop it.
  --check     Run the Windows sandbox doctor plus Python, frontend, and Bun test gates.
  --no-open   Launch without opening the default browser.
  -h, --help  Show this help.

Double-click build.bat for the normal one-click Windows launch.
"@
}

function Test-Truthy {
    param([AllowNull()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    return @("1", "true", "yes", "on") -contains $Value.ToLowerInvariant()
}

function Get-EnvironmentValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Find-Application {
    param([Parameter(Mandatory = $true)][string[]]$Names)
    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $command) {
            return $command.Source
        }
    }
    return $null
}

function Set-LauncherMode {
    param([Parameter(Mandatory = $true)][string]$NextMode)
    if ($script:Mode -ne "serve" -and $script:Mode -ne $NextMode) {
        Stop-WithError "Choose only one of --smoke or --check."
    }
    $script:Mode = $NextMode
}

function Convert-ToPositiveInteger {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    $number = 0
    if (-not [int]::TryParse($Value, [ref]$number) -or $number -lt 1) {
        Stop-WithError "$Name must be a positive integer; got '$Value'."
    }
    return $number
}

function Convert-ToNonnegativeInteger {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    $number = 0
    if (-not [int]::TryParse($Value, [ref]$number) -or $number -lt 0) {
        Stop-WithError "$Name must be a non-negative integer; got '$Value'."
    }
    return $number
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "$FailureMessage (exit code $LASTEXITCODE)."
    }
}

function Install-PythonWithPip {
    if (-not (Test-Path -LiteralPath $script:PythonBin -PathType Leaf)) {
        $pyLauncher = Find-Application @("py.exe", "py")
        $python = Find-Application @("python.exe", "python3.exe", "python")
        $versionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        $created = $false

        if ($null -ne $pyLauncher) {
            & $pyLauncher -3.11 -c $versionCheck *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Creating .venv with the Python 3.11 launcher."
                & $pyLauncher -3.11 -m venv $script:VenvDir
                if ($LASTEXITCODE -ne 0) {
                    Stop-WithError "py -3.11 -m venv .venv failed."
                }
                $created = $true
            }
        }

        if (-not $created -and $null -ne $python) {
            & $python -c $versionCheck *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Creating .venv with Python."
                & $python -m venv $script:VenvDir
                if ($LASTEXITCODE -ne 0) {
                    Stop-WithError "python -m venv .venv failed."
                }
                $created = $true
            }
        }

        if (-not $created) {
            Stop-WithError "Python 3.11 or newer was not found. Install Python and enable 'Add python.exe to PATH'."
        }
    }

    & $script:PythonBin -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Bootstrapping pip in .venv."
        Invoke-CheckedCommand $script:PythonBin @("-m", "ensurepip", "--upgrade") "python -m ensurepip failed"
    }

    & $script:PythonBin -c "import setuptools, wheel" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Log "Installing Python dependencies without build isolation."
        Invoke-CheckedCommand $script:PythonBin @("-m", "pip", "install", "--no-build-isolation", "-e", ".[dev]") "Python dependency installation failed"
    }
    else {
        Write-Log "Installing Python dependencies."
        Invoke-CheckedCommand $script:PythonBin @("-m", "pip", "install", "-e", ".[dev]") "Python dependency installation failed"
    }
}

function Initialize-PythonEnvironment {
    $skipInstall = Test-Truthy ([Environment]::GetEnvironmentVariable("APPFORGE_SKIP_INSTALL"))
    if ($skipInstall -and $script:Mode -ne "check") {
        Write-Log "Skipping Python dependency sync because APPFORGE_SKIP_INSTALL is set."
    }
    else {
        if ($skipInstall -and $script:Mode -eq "check") {
            Write-Log "Ignoring APPFORGE_SKIP_INSTALL because --check is a full test gate."
        }
        $synced = $false
        $uv = Find-Application @("uv.exe", "uv")
        if ($null -ne $uv) {
            & $uv sync --help *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Syncing Python dependencies with uv.lock."
                & $uv sync --extra dev --frozen
                if ($LASTEXITCODE -eq 0) {
                    $synced = $true
                }
                else {
                    Write-Log "uv sync failed; falling back to pip."
                }
            }
        }
        if (-not $synced) {
            Install-PythonWithPip
        }
    }

    if (-not (Test-Path -LiteralPath $script:AppForgeBin -PathType Leaf)) {
        Stop-WithError ".venv\Scripts\appforge.exe is missing. Re-run without APPFORGE_SKIP_INSTALL."
    }
}

function Test-WindowsSandboxRuntime {
    if ($script:Mode -ne "check") {
        return
    }
    $probeRoot = Join-Path $script:RuntimePath "windows-sandbox-check"
    $probeHome = Join-Path $probeRoot "home"
    New-Item -ItemType Directory -Force -Path $probeRoot, $probeHome | Out-Null
    Write-Log "Verifying the Windows AppContainer and Job Object execution layer."
    & $script:PythonBin -m appforge.tooling.windows_sandbox `
        --doctor `
        --workspace $probeRoot `
        --sandbox-home $probeHome `
        --network=none
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "Windows sandbox verification failed. See docs\\WINDOWS_11.md for troubleshooting."
    }
}

function Initialize-Frontend {
    $webIndex = Join-Path $script:RootDir "appforge\resources\web\index.html"
    $skipFrontend = Test-Truthy ([Environment]::GetEnvironmentVariable("APPFORGE_SKIP_FRONTEND_BUILD"))
    if ($skipFrontend -and $script:Mode -ne "check") {
        Write-Log "Skipping frontend build because APPFORGE_SKIP_FRONTEND_BUILD is set."
        if (-not (Test-Path -LiteralPath $webIndex -PathType Leaf)) {
            Stop-WithError "Packaged web assets are missing; run npm --prefix frontend run build."
        }
        return
    }
    if ($skipFrontend -and $script:Mode -eq "check") {
        Write-Log "Ignoring APPFORGE_SKIP_FRONTEND_BUILD because --check is a full test gate."
    }

    $script:NpmBin = Find-Application @("npm.cmd", "npm.exe", "npm")
    if ($null -eq $script:NpmBin) {
        Stop-WithError "Node.js/npm was not found. Install Node.js LTS and try again."
    }

    $nodeModules = Join-Path $script:RootDir "frontend\node_modules"
    if ($script:Mode -eq "check" -or -not (Test-Path -LiteralPath $nodeModules -PathType Container)) {
        Write-Log "Installing frontend dependencies from package-lock.json."
        Invoke-CheckedCommand $script:NpmBin @("--prefix", "frontend", "ci") "npm ci failed"
    }

    if ($script:Mode -eq "check") {
        Write-Log "Frontend dependencies are ready; verification and production build run inside --check."
        return
    }

    Write-Log "Building packaged frontend assets."
    Invoke-CheckedCommand $script:NpmBin @("--prefix", "frontend", "run", "build") "Frontend build failed"
    if (-not (Test-Path -LiteralPath $webIndex -PathType Leaf)) {
        Stop-WithError "Frontend build did not produce appforge\resources\web\index.html."
    }
}

function Get-LoopbackAddress {
    param([Parameter(Mandatory = $true)][string]$HostName)
    switch ($HostName.ToLowerInvariant()) {
        "127.0.0.1" { return [Net.IPAddress]::Loopback }
        "localhost" { return [Net.IPAddress]::Loopback }
        "::1" { return [Net.IPAddress]::IPv6Loopback }
        default { Stop-WithError "APPFORGE_WEB_HOST must be 127.0.0.1, localhost, or ::1." }
    }
}

function Test-PortAvailable {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $listener = $null
    try {
        $address = Get-LoopbackAddress $HostName
        $listener = [Net.Sockets.TcpListener]::new($address, $Port)
        $listener.Server.ExclusiveAddressUse = $true
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function Get-BridgeReservedPort {
    [Uri]$bridgeUri = $null
    if (-not [Uri]::TryCreate($script:BridgeUrl, [UriKind]::Absolute, [ref]$bridgeUri)) {
        return $null
    }
    $loopbacks = @("127.0.0.1", "localhost", "::1")
    $bridgeHost = $bridgeUri.Host.ToLowerInvariant()
    $webHost = $script:WebHost.ToLowerInvariant()
    if ($bridgeHost -eq $webHost -or ($loopbacks -contains $bridgeHost -and $loopbacks -contains $webHost)) {
        return $bridgeUri.Port
    }
    return $null
}

function Update-WebUrl {
    $urlHost = $script:WebHost
    if ($urlHost.Contains(":") -and -not $urlHost.StartsWith("[")) {
        $urlHost = "[$urlHost]"
    }
    $script:WebUrl = "http://${urlHost}:$($script:WebPort)"
}

function Select-WebPort {
    $endPort = [Math]::Min(65535, $script:RequestedWebPort + $script:PortFallbackLimit)
    $bridgePort = Get-BridgeReservedPort
    $requestedReason = ""

    for ($port = $script:RequestedWebPort; $port -le $endPort; $port++) {
        if ($null -ne $bridgePort -and $port -eq $bridgePort) {
            if ($port -eq $script:RequestedWebPort) {
                $requestedReason = "reserved for llm_bridge"
            }
            continue
        }
        if (Test-PortAvailable $script:WebHost $port) {
            if ($port -ne $script:RequestedWebPort) {
                if ([string]::IsNullOrEmpty($requestedReason)) {
                    $requestedReason = "already in use"
                }
                Write-Log "Web port $($script:RequestedWebPort) is $requestedReason; using $port instead."
            }
            $script:WebPort = $port
            Update-WebUrl
            return
        }
        if ($port -eq $script:RequestedWebPort) {
            $requestedReason = "already in use"
        }
    }

    Stop-WithError "No available web port found from $($script:RequestedWebPort) through $endPort."
}

function Test-BridgeRequested {
    $driver = (Get-EnvironmentValue "APPFORGE_DRIVER" "llm-bridge-agent").ToLowerInvariant().Replace("_", "-")
    $driverRequestsBridge = @("llm-bridge", "llm-bridge-agent", "auto") -contains $driver
    $startupRequested = Test-Truthy ([Environment]::GetEnvironmentVariable("APPFORGE_START_LLM_BRIDGE"))
    return $driverRequestsBridge -or $startupRequested
}

function Select-ManagedBridgePort {
    if (-not (Test-BridgeRequested)) {
        return
    }
    if (Test-Truthy ([Environment]::GetEnvironmentVariable("APPFORGE_SKIP_LLM_BRIDGE"))) {
        return
    }
    if ($script:BridgeUrlExplicit) {
        return
    }

    $requestedPort = 8788
    $endPort = [Math]::Min(65535, $requestedPort + $script:BridgePortFallbackLimit)
    for ($port = $requestedPort; $port -le $endPort; $port++) {
        if (Test-PortAvailable "127.0.0.1" $port) {
            if ($port -ne $requestedPort) {
                Write-Log "LLM bridge port $requestedPort is already in use; using $port instead."
            }
            $script:BridgeUrl = "http://127.0.0.1:${port}"
            $env:APPFORGE_LLM_BRIDGE_URL = $script:BridgeUrl
            return
        }
    }

    Stop-WithError "No available managed LLM bridge port found from $requestedPort through $endPort. Set APPFORGE_LLM_BRIDGE_URL to an available loopback URL."
}

function Initialize-BridgeDependencies {
    if ($null -eq $script:BunBin) {
        $script:BunBin = Find-Application @("bun.exe", "bun")
    }
    if ($null -eq $script:BunBin) {
        Stop-WithError "Bun was not found. Install Bun and ensure bun.exe is available on PATH."
    }

    $bridgeDir = Join-Path $script:RootDir "llm_bridge"
    $bridgeModules = Join-Path $bridgeDir "node_modules"
    if ($script:Mode -eq "check" -or -not (Test-Path -LiteralPath $bridgeModules -PathType Container)) {
        Write-Log "Installing llm_bridge dependencies from bun.lock."
        Push-Location $bridgeDir
        try {
            Invoke-CheckedCommand $script:BunBin @("install", "--frozen-lockfile") "llm_bridge dependency installation failed"
        }
        finally {
            Pop-Location
        }
    }
    if (-not (Test-Path -LiteralPath $bridgeModules -PathType Container)) {
        Stop-WithError "Bun did not produce llm_bridge\node_modules."
    }
}

function Initialize-BridgeRuntime {
    if (-not (Test-BridgeRequested)) {
        return
    }
    if (Test-Truthy ([Environment]::GetEnvironmentVariable("APPFORGE_SKIP_LLM_BRIDGE"))) {
        Write-Log "Skipping managed llm_bridge startup because APPFORGE_SKIP_LLM_BRIDGE is set."
        return
    }
    Initialize-BridgeDependencies
    Write-Log "Secure llm_bridge startup will be handled by the AppForge web process."
}

function Invoke-CheckSuite {
    Write-Log "Compiling Python sources."
    Invoke-CheckedCommand $script:PythonBin @(
        "-m", "compileall", "-q", "appforge", "tests", "app-code-merge.py"
    ) "Python source compilation failed"

    Write-Log "Running the complete Python test suite."
    Invoke-CheckedCommand $script:PythonBin @("-m", "pytest", "-q") "Python tests failed"

    if ($null -eq $script:NpmBin) {
        $script:NpmBin = Find-Application @("npm.cmd", "npm.exe", "npm")
    }
    if ($null -eq $script:NpmBin) {
        Stop-WithError "Node.js/npm was not found before frontend verification."
    }
    Write-Log "Running frontend localization tests, explicit typecheck, and production build."
    Invoke-CheckedCommand $script:NpmBin @("--prefix", "frontend", "run", "test:i18n") "Frontend tests failed"
    Invoke-CheckedCommand $script:NpmBin @("--prefix", "frontend", "run", "typecheck") "Frontend typecheck failed"
    Invoke-CheckedCommand $script:NpmBin @("--prefix", "frontend", "run", "build") "Frontend build failed"
    $webIndex = Join-Path $script:RootDir "appforge\resources\web\index.html"
    if (-not (Test-Path -LiteralPath $webIndex -PathType Leaf)) {
        Stop-WithError "Frontend build did not produce appforge\resources\web\index.html."
    }

    Initialize-BridgeDependencies
    $bridgeDir = Join-Path $script:RootDir "llm_bridge"
    Push-Location $bridgeDir
    try {
        Write-Log "Running llm_bridge typecheck and tests."
        Invoke-CheckedCommand $script:BunBin @("run", "typecheck") "llm_bridge typecheck failed"
        Invoke-CheckedCommand $script:BunBin @("test") "llm_bridge tests failed"
    }
    finally {
        Pop-Location
    }

    Write-Log "Full Windows check passed: sandbox, Python, frontend, and llm_bridge gates are green."
}

function Get-WebArguments {
    param([bool]$ForceNoOpen)
    $arguments = @(
        "web",
        "--host", $script:WebHost,
        "--port", [string]$script:WebPort,
        "--log-level", $script:LogLevel
    )
    if ($script:NoOpen -or $ForceNoOpen) {
        $arguments += "--no-open-browser"
    }
    return $arguments
}

function Write-WebLogs {
    foreach ($path in @($script:WebLog, $script:WebErrorLog)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Log "Last web server log lines from $path`:"
            Get-Content -LiteralPath $path -Tail 40
        }
    }
}

function Wait-ForWebEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Url
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($script:SmokeTimeout)
    while ([DateTime]::UtcNow -lt $deadline) {
        $script:WebProcess.Refresh()
        if ($script:WebProcess.HasExited) {
            Write-Log "Web server exited before $Label became ready."
            Write-WebLogs
            return $false
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Log "$Label is ready: $Url"
                return $true
            }
        }
        catch {
            # The server may still be starting.
        }
        Start-Sleep -Seconds 1
    }
    Write-Log "Timed out after $($script:SmokeTimeout)s waiting for $Label at $Url."
    Write-WebLogs
    return $false
}

function Stop-WebProcess {
    if ($null -eq $script:WebProcess) {
        return
    }
    $script:WebProcess.Refresh()
    if (-not $script:WebProcess.HasExited) {
        Write-Log "Stopping web server pid $($script:WebProcess.Id)."
        Stop-Process -Id $script:WebProcess.Id -ErrorAction SilentlyContinue
        [void]$script:WebProcess.WaitForExit(5000)
    }
}

function Invoke-SmokeCheck {
    New-Item -ItemType Directory -Force -Path $script:RuntimePath | Out-Null
    $arguments = Get-WebArguments $true
    Write-Log "Starting AppForge web smoke server at $($script:WebUrl)."
    $startParameters = @{
        FilePath = $script:AppForgeBin
        ArgumentList = $arguments
        PassThru = $true
        NoNewWindow = $true
        RedirectStandardOutput = $script:WebLog
        RedirectStandardError = $script:WebErrorLog
    }
    $script:WebProcess = Start-Process @startParameters

    if (-not (Wait-ForWebEndpoint "Health endpoint" "$($script:WebUrl)/api/health")) {
        Stop-WithError "Health endpoint smoke check failed."
    }
    if (-not (Wait-ForWebEndpoint "Web UI" "$($script:WebUrl)/")) {
        Stop-WithError "Web UI smoke check failed."
    }
    Write-Log "Smoke check passed for $($script:WebUrl)."
}

function Invoke-ForegroundWeb {
    $arguments = Get-WebArguments $false
    if ($script:NoOpen) {
        Write-Log "Launching AppForge web UI at $($script:WebUrl) without opening a browser."
    }
    else {
        Write-Log "Launching AppForge web UI at $($script:WebUrl) and opening the default browser."
    }
    & $script:AppForgeBin @arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "AppForge web exited with code $LASTEXITCODE."
    }
}

function Invoke-Main {
    foreach ($argument in $args) {
        switch ($argument) {
            "--smoke" { Set-LauncherMode "smoke" }
            "--check" { Set-LauncherMode "check" }
            "--no-open" { $script:NoOpen = $true }
            "-h" { Show-Usage; return }
            "--help" { Show-Usage; return }
            default { Show-Usage; Stop-WithError "Unknown argument: $argument" }
        }
    }

    if (Test-Truthy ([Environment]::GetEnvironmentVariable("APPFORGE_NO_OPEN"))) {
        $script:NoOpen = $true
    }
    if ($script:Mode -eq "smoke") {
        $script:NoOpen = $true
    }

    $script:WebHost = Get-EnvironmentValue "APPFORGE_WEB_HOST" "127.0.0.1"
    [void](Get-LoopbackAddress $script:WebHost)
    $script:RequestedWebPort = Convert-ToPositiveInteger "APPFORGE_WEB_PORT" (Get-EnvironmentValue "APPFORGE_WEB_PORT" "8787")
    if ($script:RequestedWebPort -gt 65535) {
        Stop-WithError "APPFORGE_WEB_PORT must be between 1 and 65535."
    }
    $script:WebPort = $script:RequestedWebPort
    $script:PortFallbackLimit = Convert-ToNonnegativeInteger "APPFORGE_WEB_PORT_FALLBACK_LIMIT" (Get-EnvironmentValue "APPFORGE_WEB_PORT_FALLBACK_LIMIT" "20")
    $script:BridgePortFallbackLimit = Convert-ToNonnegativeInteger "APPFORGE_LLM_BRIDGE_PORT_FALLBACK_LIMIT" (Get-EnvironmentValue "APPFORGE_LLM_BRIDGE_PORT_FALLBACK_LIMIT" "20")
    $script:SmokeTimeout = Convert-ToPositiveInteger "APPFORGE_SMOKE_TIMEOUT" (Get-EnvironmentValue "APPFORGE_SMOKE_TIMEOUT" "30")
    [void](Convert-ToPositiveInteger "APPFORGE_BRIDGE_TIMEOUT" (Get-EnvironmentValue "APPFORGE_BRIDGE_TIMEOUT" "15"))
    $script:LogLevel = Get-EnvironmentValue "APPFORGE_LOG_LEVEL" "info"
    $bridgeUrlSetting = [Environment]::GetEnvironmentVariable("APPFORGE_LLM_BRIDGE_URL")
    $script:BridgeUrlExplicit = -not [string]::IsNullOrWhiteSpace($bridgeUrlSetting)
    $script:BridgeUrl = if ($script:BridgeUrlExplicit) { $bridgeUrlSetting } else { "http://127.0.0.1:8788" }
    $env:APPFORGE_LLM_BRIDGE_URL = $script:BridgeUrl

    $runtimeSetting = Get-EnvironmentValue "APPFORGE_DATA_DIR" ".appforge-web"
    if ([IO.Path]::IsPathRooted($runtimeSetting)) {
        $script:RuntimePath = $runtimeSetting
    }
    else {
        $script:RuntimePath = Join-Path $script:RootDir $runtimeSetting
    }
    $script:WebLog = Join-Path $script:RuntimePath "web-smoke.log"
    $script:WebErrorLog = Join-Path $script:RuntimePath "web-smoke.error.log"
    $script:VenvDir = Join-Path $script:RootDir ".venv"
    $script:PythonBin = Join-Path $script:RootDir ".venv\Scripts\python.exe"
    $script:AppForgeBin = Join-Path $script:RootDir ".venv\Scripts\appforge.exe"
    Update-WebUrl

    Push-Location $script:RootDir
    try {
        Initialize-PythonEnvironment
        Test-WindowsSandboxRuntime
        Initialize-Frontend
        if ($script:Mode -eq "check") {
            Invoke-CheckSuite
            return
        }

        Select-ManagedBridgePort
        Select-WebPort
        Initialize-BridgeRuntime

        switch ($script:Mode) {
            "smoke" { Invoke-SmokeCheck }
            "serve" { Invoke-ForegroundWeb }
            default { Stop-WithError "Unknown launcher mode: $($script:Mode)" }
        }
    }
    finally {
        Stop-WebProcess
        Pop-Location
    }
}

$exitCode = 0
try {
    Invoke-Main @args
}
catch {
    Write-Host "[build.ps1] error: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
exit $exitCode
