<#
.SYNOPSIS
  Deploys the LOCAL working tree of PrintQ to the remote server over SSH (PM2 app "printq").
.DESCRIPTION
  Connection details are read at runtime from FreeRemote.cmd, app settings from the local .env,
  and the code is packed from this folder and uploaded. GitHub is not involved. See deploy\DEPLOY.md.
.PARAMETER PublicPort
  Public port that your router/provider forwards to the server port. Used for
  DJANGO_CSRF_TRUSTED_ORIGINS and the final URL.
#>
param(
    [string]$RemoteCmd = "$env:USERPROFILE\SSH Remote\FreeRemote.cmd",
    [int]$Port = 8010,
    [int]$PublicPort = 0,
    [switch]$SkipChecks,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RemoteBase = "/root/services/printq"

function Fail($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

function Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- 1. parse FreeRemote.cmd
Step "Reading connection details"
if (-not (Test-Path -LiteralPath $RemoteCmd)) { Fail "Remote command file not found: $RemoteCmd" }
$remoteDir = Split-Path -Parent (Resolve-Path -LiteralPath $RemoteCmd).Path
$line = Get-Content -LiteralPath $RemoteCmd | Where-Object { $_ -match "\bssh\b" } | Select-Object -First 1
if (-not $line) { Fail "No ssh line found in $RemoteCmd" }

$sshPort = 22
if ($line -match "-p\s+(\d+)") { $sshPort = [int]$Matches[1] }
if ($line -notmatch '-i\s+"?([^"\s]+)"?') { Fail "Could not find -i <key> in $RemoteCmd" }
$keyRaw = $Matches[1]
if ($line -notmatch "([^\s`"]+)@([^\s`"]+)") { Fail "Could not find user@host in $RemoteCmd" }
$sshUser = $Matches[1]
$sshHost = $Matches[2]

if ([System.IO.Path]::IsPathRooted($keyRaw)) { $keyPath = $keyRaw } else { $keyPath = Join-Path $remoteDir $keyRaw }
if (-not (Test-Path -LiteralPath $keyPath)) { Fail "SSH key not found: $keyPath" }
$keyPath = (Resolve-Path -LiteralPath $keyPath).Path
Write-Host "Target: $sshUser@$sshHost (ssh port $sshPort), key file found."

# ---------------------------------------------------------------- build remote env from local .env
Step "Preparing production settings from local .env"
$localEnv = Join-Path $RepoRoot ".env"
if (-not (Test-Path -LiteralPath $localEnv)) {
    Fail "No .env found at $localEnv. Create it (copy .env.example) and set DATABASE_URL first."
}

$dropKeys = @("TEST_STUDENT_EMAIL", "TEST_STUDENT_PASSWORD", "PRIVATE_MEDIA_ROOT", "DB_SSL_REQUIRE", "DJANGO_SECRET_KEY")
$deployDomain = ""
$envMap = [ordered]@{}
foreach ($raw in (Get-Content -LiteralPath $localEnv)) {
    $t = $raw.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    if ($t -match "^export\s+") { $t = $t -replace "^export\s+", "" }
    $idx = $t.IndexOf("=")
    if ($idx -lt 1) { continue }
    $k = $t.Substring(0, $idx).Trim()
    $v = $t.Substring($idx + 1)
    # DEPLOY_PUBLIC_PORT is a deploy setting for this script only (enables one-click deploys); never sent.
    if ($k -eq "DEPLOY_PUBLIC_PORT") {
        $parsed = 0
        if ($PublicPort -eq 0 -and [int]::TryParse($v.Trim().Trim('"'), [ref]$parsed) -and $parsed -gt 0) {
            $PublicPort = $parsed
            Write-Host "Using public port $PublicPort from .env (DEPLOY_PUBLIC_PORT)"
        }
        continue
    }
    # DEPLOY_DOMAIN: the public HTTPS domain (e.g. behind Cloudflare). Deploy-only; never sent as-is.
    if ($k -eq "DEPLOY_DOMAIN") {
        $d = $v.Trim().Trim('"').ToLower() -replace "^https?://", "" -replace "/.*$", ""
        if ($d -ne "") { $deployDomain = $d; Write-Host "Using domain $deployDomain from .env (DEPLOY_DOMAIN)" }
        continue
    }
    if ($dropKeys -contains $k) { continue }
    $envMap[$k] = $v
}

function Merge-List($existing, [string[]]$extra) {
    $out = New-Object System.Collections.ArrayList
    $items = @()
    if ($existing) { $items += ($existing -split ",") }
    $items += $extra
    foreach ($i in $items) {
        $x = $i.Trim()
        if ($x -ne "" -and -not ($out -contains $x)) { [void]$out.Add($x) }
    }
    return ($out -join ",")
}

$envMap["DJANGO_DEBUG"] = "0"
$localHosts = $null
if ($envMap.Contains("DJANGO_ALLOWED_HOSTS")) { $localHosts = $envMap["DJANGO_ALLOWED_HOSTS"] }
$envMap["DJANGO_ALLOWED_HOSTS"] = Merge-List $localHosts @($sshHost, "localhost", "127.0.0.1")
if ($PublicPort -gt 0) {
    $localCsrf = $null
    if ($envMap.Contains("DJANGO_CSRF_TRUSTED_ORIGINS")) { $localCsrf = $envMap["DJANGO_CSRF_TRUSTED_ORIGINS"] }
    $envMap["DJANGO_CSRF_TRUSTED_ORIGINS"] = Merge-List $localCsrf @("http://${sshHost}:$PublicPort")
}
if ($deployDomain -ne "") {
    # Accept the domain as a host, trust its HTTPS origin for logins/forms, and let Django read the
    # X-Forwarded-Proto header from the TLS proxy (Cloudflare) so it knows requests were HTTPS.
    $envMap["DJANGO_ALLOWED_HOSTS"] = Merge-List $envMap["DJANGO_ALLOWED_HOSTS"] @($deployDomain)
    $existingCsrf = $null
    if ($envMap.Contains("DJANGO_CSRF_TRUSTED_ORIGINS")) { $existingCsrf = $envMap["DJANGO_CSRF_TRUSTED_ORIGINS"] }
    $envMap["DJANGO_CSRF_TRUSTED_ORIGINS"] = Merge-List $existingCsrf @("https://$deployDomain")
    $envMap["DJANGO_BEHIND_TLS_PROXY"] = "1"
}

$dbUrl = ""
if ($envMap.Contains("DATABASE_URL")) { $dbUrl = $envMap["DATABASE_URL"].Trim() }
if ($dbUrl -eq "") {
    Fail "Set DATABASE_URL (Supabase session pooler URL) in your local .env first. Production refuses SQLite."
}
Write-Host ("Settings to send (values hidden): " + (($envMap.Keys | ForEach-Object { "$_=***" }) -join ", "))

# ---------------------------------------------------------------- 2. local gate
Push-Location $RepoRoot
try {
    if ($SkipChecks) {
        Write-Host ""
        Write-Host "!!! WARNING: -SkipChecks given. Skipping check, migration and test gates. !!!" -ForegroundColor Yellow
    } else {
        Step "Gate: Django checks and tests"
        python manage.py check
        if ($LASTEXITCODE -ne 0) { Fail "manage.py check failed." }
        python manage.py makemigrations --check --dry-run
        if ($LASTEXITCODE -ne 0) { Fail "Model changes without a migration." }
        python manage.py test
        if ($LASTEXITCODE -ne 0) { Fail "Tests failed." }
    }

    # ------------------------------------------------------------ package the working tree
    Step "Packaging the local working tree"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git is required (it decides which files are packed via .gitignore)." }
    git rev-parse --is-inside-work-tree 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "This folder is not a git repository. git and .gitignore decide what gets uploaded." }
    $tarExe = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path -LiteralPath $tarExe)) { Fail "Windows tar.exe not found at $tarExe." }

    $label = Get-Date -Format "yyyyMMdd-HHmmss"
    $short = (git rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $short) { $label += "-" + $short.Trim() }
    $dirtyState = git status --porcelain
    if ($dirtyState) { $label += "-dirty" }
    Write-Host "Release label: $label"

    $files = git ls-files --cached --others --exclude-standard | Where-Object { $_ -and ($_ -notlike ".github/*") }
    $files = $files | Where-Object { Test-Path -LiteralPath (Join-Path $RepoRoot $_) -PathType Leaf } | Sort-Object -Unique
    $listFile = [System.IO.Path]::GetTempFileName()
    $tarball = [System.IO.Path]::GetTempFileName() + ".tar.gz"
    try {
        [System.IO.File]::WriteAllText($listFile, (($files -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
        & $tarExe -czf $tarball -T $listFile
        if ($LASTEXITCODE -ne 0) { Fail "tar.exe failed to create the archive." }
        $sizeKb = [math]::Round((Get-Item -LiteralPath $tarball).Length / 1KB, 1)
        Write-Host "Archive: $(@($files).Count) files, $sizeKb KB"
        if ($DryRun -and $env:PRINTQ_DEPLOY_LIST_ARCHIVE -eq "1") { & $tarExe -tzf $tarball }

        # ---------------------------------------------------- ssh/scp helpers and upload
        $sshOpts = @("-i", $keyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20")
        $sshArgs = @("-p", "$sshPort") + $sshOpts
        $scpArgs = @("-P", "$sshPort") + $sshOpts
        $target = "$sshUser@$sshHost"

        function Run-Remote([string]$cmd) {
            if ($DryRun) { Write-Host "[dry-run] ssh -p $sshPort -i <key> $target `"$cmd`""; return 0 }
            & ssh.exe @sshArgs $target $cmd | Out-Host
            return $LASTEXITCODE
        }
        function Copy-ToRemote([string]$local, [string]$remote) {
            if ($DryRun) { Write-Host "[dry-run] scp -P $sshPort -i <key> $local ${target}:$remote"; return 0 }
            & scp.exe @scpArgs $local "${target}:$remote" | Out-Host
            return $LASTEXITCODE
        }

        Step "Preparing server directories"
        $rc = Run-Remote "mkdir -p $RemoteBase/shared $RemoteBase/incoming"
        if ($rc -ne 0) { Fail "Could not reach the server (ssh exit $rc)." }

        Step "Uploading release archive"
        $rc = Copy-ToRemote $tarball "$RemoteBase/incoming/$label.tar.gz"
        if ($rc -ne 0) { Fail "Uploading the archive failed (scp exit $rc)." }

        Step "Uploading settings"
        $tmp = [System.IO.Path]::GetTempFileName()
        try {
            $content = (($envMap.Keys | ForEach-Object { "$_=$($envMap[$_])" }) -join "`n") + "`n"
            [System.IO.File]::WriteAllText($tmp, $content, (New-Object System.Text.UTF8Encoding($false)))
            if ($DryRun) {
                Write-Host "[dry-run] would write these keys to the temp file:"
                $envMap.Keys | ForEach-Object { Write-Host "    $_=***" }
            }
            $rc = Copy-ToRemote $tmp "$RemoteBase/shared/.env.incoming"
            if ($rc -ne 0) { Fail "Uploading settings failed (scp exit $rc)." }
        } finally {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
        }

        Step "Uploading deploy script"
        $rc = Copy-ToRemote (Join-Path $PSScriptRoot "remote_deploy.sh") "$RemoteBase/remote_deploy.sh"
        if ($rc -ne 0) { Fail "Uploading remote_deploy.sh failed (scp exit $rc)." }

        Step "Running deploy on the server"
        $remoteRun = "sed -i 's/\r$//' $RemoteBase/remote_deploy.sh && bash $RemoteBase/remote_deploy.sh --release $label --port $Port"
        $rc = Run-Remote $remoteRun
        if ($rc -ne 0) { Fail "Remote deploy failed (exit $rc). See the output above." }
    } finally {
        if (Test-Path -LiteralPath $listFile) { Remove-Item -LiteralPath $listFile -Force }
        if (Test-Path -LiteralPath $tarball) { Remove-Item -LiteralPath $tarball -Force }
        $bare = $tarball -replace "\.tar\.gz$", ""
        if (Test-Path -LiteralPath $bare) { Remove-Item -LiteralPath $bare -Force }
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------- result
Write-Host ""
if ($DryRun) {
    Write-Host "Dry run finished. Nothing was sent to the server." -ForegroundColor Yellow
    exit 0
}
if ($deployDomain -ne "") {
    Write-Host "Deployed release $label -> https://$deployDomain" -ForegroundColor Green
    if ($PublicPort -gt 0) { Write-Host "    (also reachable directly at http://${sshHost}:$PublicPort)" }
} elseif ($PublicPort -gt 0) {
    Write-Host "Deployed release $label -> http://${sshHost}:$PublicPort" -ForegroundColor Green
} else {
    Write-Host "Deployed release $label -> http://${sshHost}:$Port (forward a public port to $Port, then re-run with -PublicPort <n>)" -ForegroundColor Green
}
exit 0
