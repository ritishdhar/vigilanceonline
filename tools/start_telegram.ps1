<#
Secure local Telegram setup and dashboard launcher (Windows PowerShell 5.1 / PowerShell 7).
Bot credentials are encrypted with the current Windows user's DPAPI key by Export-Clixml.
No tokens are placed in command-line arguments, source files, or user-wide environment variables.
#>
[CmdletBinding()]
param(
    [switch]$ConfigureOnly,
    [switch]$Reconfigure,
    [switch]$ShowOwnerToken,
    [switch]$Synthetic,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$Source = '',
    [string]$HostAddress = '127.0.0.1',
    [string]$PublicOrigin = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$secretPath = Join-Path $projectRoot 'evidence\telegram_credentials.clixml'

function Read-PlainSecret([Security.SecureString]$Value) {
    return [System.Net.NetworkCredential]::new('', $Value).Password
}

function Invoke-TelegramCheck([string]$Token, [string]$Method) {
    $previousProtocol = [Net.ServicePointManager]::SecurityProtocol
    try {
        # Windows PowerShell 5.1 can otherwise negotiate obsolete TLS defaults.
        [Net.ServicePointManager]::SecurityProtocol = $previousProtocol -bor [Net.SecurityProtocolType]::Tls12
        $reply = Invoke-RestMethod -Uri ('https://api.telegram.org/bot' + $Token + '/' + $Method) -Method Post -TimeoutSec 15
        if (-not $reply.ok) { throw 'Telegram rejected the request.' }
        return $reply.result
    }
    catch {
        # Never print the original exception: its request URL can contain the bot token.
        throw [System.InvalidOperationException]::new('Telegram connection failed. Check your BotFather token and internet connection, then run with -Reconfigure if needed.')
    }
    finally { [Net.ServicePointManager]::SecurityProtocol = $previousProtocol }
}

if ($env:OS -ne 'Windows_NT') { throw 'This encrypted-credential launcher requires Windows.' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Project Python environment is missing. Follow SETUP.md before running this launcher.' }

$credentials = $null
if (-not $Reconfigure -and (Test-Path -LiteralPath $secretPath)) {
    try { $credentials = Import-Clixml -LiteralPath $secretPath }
    catch { throw 'Saved credentials could not be opened for this Windows account. Run again with -Reconfigure.' }
    if ($credentials.BotToken -isnot [Security.SecureString] -or $credentials.OwnerToken -isnot [Security.SecureString]) {
        throw 'Saved credentials are invalid. Run again with -Reconfigure.'
    }
}

if ($ShowOwnerToken) {
    if ($null -eq $credentials) { throw 'Run this launcher once to configure your Telegram bot first.' }
    Write-Host 'Owner sign-in token (private; enter only in your local dashboard):' -ForegroundColor Yellow
    Write-Host (Read-PlainSecret $credentials.OwnerToken)
    return
}

$newCredentials = $false
if ($null -eq $credentials) {
    Write-Host 'RakshaNetra - Telegram enrollment setup' -ForegroundColor Cyan
    Write-Host 'Create a bot in Telegram with @BotFather using /newbot, then enter its token below.'
    $botSecret = if (-not $Reconfigure -and $env:VIGILANCE_TELEGRAM_BOT_TOKEN) {
        ConvertTo-SecureString $env:VIGILANCE_TELEGRAM_BOT_TOKEN -AsPlainText -Force
    } else { Read-Host 'BotFather token (hidden)' -AsSecureString }
    $adminPlain = $env:VIGILANCE_TELEGRAM_ADMIN_TOKEN
    if ($Reconfigure -or [string]::IsNullOrWhiteSpace($adminPlain)) {
        $randomBytes = New-Object byte[] 32
        $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
        $adminPlain = [Convert]::ToBase64String($randomBytes)
    }
    if ($adminPlain.Length -lt 32) { throw 'The existing owner token must contain at least 32 characters.' }
    $credentials = [pscustomobject]@{
        BotToken = $botSecret
        OwnerToken = (ConvertTo-SecureString $adminPlain -AsPlainText -Force)
    }
    $adminPlain = $null
    $newCredentials = $true
}

$botPlain = Read-PlainSecret $credentials.BotToken
if ($botPlain -notmatch '^\d+:[A-Za-z0-9_-]{20,}$') { throw 'This does not look like a BotFather token. Run again with -Reconfigure.' }
& $pythonPath -c 'import qrcode; import PIL' 2>$null
if ($LASTEXITCODE -ne 0) { throw 'QR dependencies are missing. Install the project requirements using the project virtual environment.' }
$bot = Invoke-TelegramCheck $botPlain 'getMe'
$webhook = Invoke-TelegramCheck $botPlain 'getWebhookInfo'
if ($webhook.url) {
    throw 'This bot has an active webhook. Use a dedicated bot or remove the webhook in its existing service before enabling long polling. No webhook was changed.'
}

if ($newCredentials) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $secretPath) -Force | Out-Null
    $credentials | Export-Clixml -LiteralPath $secretPath
    Write-Host 'Credentials saved encrypted for this Windows account under evidence/.' -ForegroundColor Green
    Write-Host 'Owner sign-in token (private; enter it on Alert recipients):' -ForegroundColor Yellow
    Write-Host (Read-PlainSecret $credentials.OwnerToken)
}
Write-Host ('Telegram bot verified: @' + $bot.username) -ForegroundColor Green
Write-Host 'Use this bot with only one running RakshaNetra enrollment process.'
if ($ConfigureOnly) { $botPlain = $null; return }

$existingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existingListener) { throw "Port $Port is already in use. Stop your existing dashboard first, or use -Port 8001." }
$arguments = @((Join-Path $projectRoot 'ui.py'), '--config', (Join-Path $projectRoot 'config\default.json'), '--port', [string]$Port, '--host', $HostAddress)
if ($Synthetic) { $arguments += '--synthetic' }
elseif (-not [string]::IsNullOrWhiteSpace($Source)) { $arguments += @('--source', $Source) }
if (-not [string]::IsNullOrWhiteSpace($PublicOrigin)) { $arguments += @('--public-origin', $PublicOrigin) }

$previousBot = $env:VIGILANCE_TELEGRAM_BOT_TOKEN
$previousAdmin = $env:VIGILANCE_TELEGRAM_ADMIN_TOKEN
$applicationExitCode = 0
try {
    $env:VIGILANCE_TELEGRAM_BOT_TOKEN = $botPlain
    $env:VIGILANCE_TELEGRAM_ADMIN_TOKEN = Read-PlainSecret $credentials.OwnerToken
    $botPlain = $null
    Push-Location -LiteralPath $projectRoot
    try {
        Write-Host "Open http://127.0.0.1:$Port/#recipients and sign in with your owner token."
        & $pythonPath @arguments
        $applicationExitCode = $LASTEXITCODE
    } finally { Pop-Location }
} finally {
    $env:VIGILANCE_TELEGRAM_BOT_TOKEN = $previousBot
    $env:VIGILANCE_TELEGRAM_ADMIN_TOKEN = $previousAdmin
}
exit $applicationExitCode
