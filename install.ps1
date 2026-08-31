# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$UvInstallUrl = "https://astral.sh/uv/install.ps1"
$MarketplaceSource = "oceanbase/powercontext"
$RuntimeProfile = $null
$MarketplaceRef = $env:POWERCONTEXT_INSTALL_REF
$Hosts = [System.Collections.Generic.List[string]]::new()
$NoHosts = $false
$AssumeYes = $false

function Show-Help {
    @"
Install PowerContext and selected Agent integrations.

Usage:
  install.ps1 [--profile local|seekdb] [--host codex|claude-code]... [--no-hosts] [--ref REF] [--yes]

Options:
  --profile PROFILE  Runtime profile. Choose local or seekdb.
  --host HOST        Agent host integration. Repeatable.
  --no-hosts         Install only the Runtime.
  --ref REF          Override the PowerContext marketplace Git ref.
  --yes              Apply the plan without confirmation.
  -h, --help         Show this help.
"@
}

function Read-Arguments {
    param([string[]]$Arguments)

    for ($Index = 0; $Index -lt $Arguments.Count; $Index++) {
        $Argument = $Arguments[$Index]
        switch -Regex ($Argument) {
            '^--profile$' {
                $Index++
                if ($Index -ge $Arguments.Count) { throw "--profile requires a value" }
                $script:RuntimeProfile = $Arguments[$Index]
            }
            '^--profile=' { $script:RuntimeProfile = $Argument.Substring("--profile=".Length) }
            '^--host$' {
                $Index++
                if ($Index -ge $Arguments.Count) { throw "--host requires a value" }
                $script:Hosts.Add($Arguments[$Index])
            }
            '^--host=' { $script:Hosts.Add($Argument.Substring("--host=".Length)) }
            '^--no-hosts$' { $script:NoHosts = $true }
            '^--ref$' {
                $Index++
                if ($Index -ge $Arguments.Count) { throw "--ref requires a value" }
                $script:MarketplaceRef = $Arguments[$Index]
            }
            '^--ref=' { $script:MarketplaceRef = $Argument.Substring("--ref=".Length) }
            '^--yes$' { $script:AssumeYes = $true }
            '^(-h|--help)$' {
                Show-Help
                exit 0
            }
            default { throw "unknown argument: $Argument" }
        }
    }
}

function Select-Plan {
    if ([string]::IsNullOrWhiteSpace($script:RuntimeProfile)) {
        if ([Console]::IsInputRedirected) { throw "--profile is required without an interactive terminal" }
        $script:RuntimeProfile = Read-Host "Runtime profile [local/seekdb] (local)"
        if ([string]::IsNullOrWhiteSpace($script:RuntimeProfile)) { $script:RuntimeProfile = "local" }
    }
    if ($script:RuntimeProfile -notin @("local", "seekdb")) {
        throw "unsupported profile: $script:RuntimeProfile"
    }
    if ($script:NoHosts -and $script:Hosts.Count -gt 0) { throw "--no-hosts cannot be combined with --host" }

    if (-not $script:NoHosts -and $script:Hosts.Count -eq 0) {
        if ([Console]::IsInputRedirected) { throw "--host or --no-hosts is required without an interactive terminal" }
        $Answer = Read-Host "Hosts [codex, claude-code, none]"
        if ($Answer -eq "none") {
            $script:NoHosts = $true
        }
        else {
            foreach ($HostName in $Answer.Split(',')) {
                if (-not [string]::IsNullOrWhiteSpace($HostName)) { $script:Hosts.Add($HostName.Trim()) }
            }
        }
    }
    foreach ($HostName in $script:Hosts) {
        if ($HostName -notin @("codex", "claude-code")) { throw "unsupported host: $HostName" }
    }

    if ([string]::IsNullOrWhiteSpace($script:MarketplaceRef)) {
        $script:MarketplaceRef = "master"
    }
}

function Confirm-Plan {
    Write-Host "==> Installation plan"
    Write-Host "Runtime profile: $script:RuntimeProfile"
    Write-Host "Runtime source: repository ref $script:MarketplaceRef"
    Write-Host "Marketplace ref: $script:MarketplaceRef"
    Write-Host "Hosts: $(if ($script:NoHosts) { 'none' } else { $script:Hosts -join ', ' })"
    if (-not $script:AssumeYes) {
        if ([Console]::IsInputRedirected) { throw "confirmation requires an interactive terminal; pass --yes" }
        $Answer = Read-Host "Proceed? [y/N]"
        if ($Answer -notin @("y", "Y", "yes")) { throw "installation cancelled" }
    }
}

function Test-HostPrerequisites {
    foreach ($HostName in $script:Hosts) {
        switch ($HostName) {
            "codex" {
                if ($null -eq (Get-Command codex -ErrorAction SilentlyContinue)) {
                    throw "Codex CLI is not installed or is not on PATH"
                }
            }
            "claude-code" {
                if ($null -eq (Get-Command claude -ErrorAction SilentlyContinue)) {
                    throw "Claude Code CLI is not installed or is not on PATH"
                }
            }
        }
    }
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $Output = & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit status $LASTEXITCODE" }
    return $Output
}

function Resolve-Uv {
    $Command = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $Command) {
        Write-Host "==> Using uv at $($Command.Source)"
        return $Command.Source
    }

    Write-Host "==> Installing uv"
    Invoke-RestMethod $UvInstallUrl | Invoke-Expression
    foreach ($Candidate in @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $HOME ".cargo\bin\uv.exe")
    )) {
        if (Test-Path $Candidate) { return $Candidate }
    }
    throw "uv was installed, but uv.exe was not found"
}

function Install-Runtime {
    $LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
    $InstallRoot = if ($env:POWERCONTEXT_INSTALL_ROOT) {
        $env:POWERCONTEXT_INSTALL_ROOT
    }
    else {
        Join-Path $LocalAppData "PowerContext\distribution"
    }
    $ExecutableDirectory = if ($env:POWERCONTEXT_EXECUTABLE_DIR) {
        $env:POWERCONTEXT_EXECUTABLE_DIR
    }
    else {
        Join-Path $InstallRoot "bin"
    }
    $Venv = Join-Path $InstallRoot "venv"
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    $VenvPowerContext = Join-Path $Venv "Scripts\powercontext.exe"
    $PublicPowerContext = Join-Path $ExecutableDirectory "powercontext.exe"
    $Extras = if ($script:RuntimeProfile -eq "seekdb") { "cli,server,seekdb" } else { "cli,server" }
    $Requirement = if ($env:POWERCONTEXT_INSTALL_PACKAGE) {
        $env:POWERCONTEXT_INSTALL_PACKAGE
    }
    else {
        "powercontext[$Extras] @ git+https://github.com/oceanbase/powercontext.git@$($script:MarketplaceRef)"
    }

    Write-Host "==> Installing PowerContext Runtime"
    New-Item -ItemType Directory -Force -Path $InstallRoot, $ExecutableDirectory | Out-Null
    Invoke-Native $script:UvPath @("venv", "--python", "3.11", "--allow-existing", $Venv) | Out-Null
    Invoke-Native $script:UvPath @("pip", "install", "--python", $VenvPython, "--upgrade", $Requirement) | Out-Null
    if (-not (Test-Path $VenvPowerContext)) { throw "PowerContext was installed without an executable" }
    Remove-Item -Force $PublicPowerContext -ErrorAction SilentlyContinue
    New-Item -ItemType HardLink -Path $PublicPowerContext -Target $VenvPowerContext | Out-Null
    Invoke-Native $VenvPowerContext @("--version") | Out-Null

    $script:RuntimePython = $VenvPython
    $script:PublicExecutable = $PublicPowerContext
}

function Test-JsonExpression {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][scriptblock]$Predicate
    )
    return & $Predicate (($Value -join "`n") | ConvertFrom-Json)
}

function Install-Codex {
    Write-Host "==> Installing Codex integration"
    Invoke-Native "codex" @(
        "plugin", "marketplace", "add", $MarketplaceSource, "--ref", $script:MarketplaceRef, "--json"
    ) | Out-Null
    Invoke-Native "codex" @("plugin", "add", "powercontext@powercontext", "--json") | Out-Null
    $Plugins = Invoke-Native "codex" @("plugin", "list", "--json")
    $Installed = Test-JsonExpression $Plugins {
        param($Data)
        @($Data.installed | Where-Object { $_.name -eq "powercontext" -and $_.installed -and $_.enabled }).Count -gt 0
    }
    if (-not $Installed) { throw "Codex did not report an enabled PowerContext plugin" }
}

function Install-ClaudeCode {
    Write-Host "==> Installing Claude Code integration"
    $Marketplaces = Invoke-Native "claude" @("plugin", "marketplace", "list", "--json")
    $Exists = Test-JsonExpression $Marketplaces {
        param($Data)
        @($Data | Where-Object { $_.name -eq "powercontext" }).Count -gt 0
    }
    if (-not $Exists) {
        Invoke-Native "claude" @(
            "plugin", "marketplace", "add", "${MarketplaceSource}@$($script:MarketplaceRef)", "--scope", "user"
        ) | Out-Null
    }
    Invoke-Native "claude" @("plugin", "install", "powercontext@powercontext", "--scope", "user") | Out-Null
    $Plugins = Invoke-Native "claude" @("plugin", "list", "--json")
    $Installed = Test-JsonExpression $Plugins {
        param($Data)
        @($Data | Where-Object { $_.id -eq "powercontext@powercontext" -and $_.enabled }).Count -gt 0
    }
    if (-not $Installed) { throw "Claude Code did not report an enabled PowerContext plugin" }
}

Read-Arguments $args
Select-Plan
Test-HostPrerequisites
Confirm-Plan
$UvPath = Resolve-Uv
Install-Runtime
foreach ($HostName in $Hosts) {
    switch ($HostName) {
        "codex" { Install-Codex }
        "claude-code" { Install-ClaudeCode }
    }
}

Write-Host ""
Write-Host "PowerContext installation complete."
Write-Host "Runtime: $PublicExecutable"
Write-Host "Next: run 'powercontext config init', then 'powercontext server run'."
