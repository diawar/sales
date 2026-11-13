param([ValidateRange(1024,65535)][int]$Port=8173)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project .venv first. See README.md.' }
$previousDirectory=Get-Location
$previousSmtp=$env:SALES_SMTP_ENABLED
try {
    Set-Location -LiteralPath $projectRoot
    $env:SALES_SMTP_ENABLED='0'
    & $python manage.py check
    if ($LASTEXITCODE -ne 0) { throw 'Django configuration check failed.' }
    & $python manage.py migrate --check
    if ($LASTEXITCODE -ne 0) { throw 'Pending migrations. Back up the database and apply migrations first.' }
    & $python manage.py runserver "127.0.0.1:$Port" --noreload
    if ($LASTEXITCODE -ne 0) { throw 'Local server stopped with an error. Check whether the port is already in use.' }
} finally {
    $env:SALES_SMTP_ENABLED=$previousSmtp
    Set-Location -LiteralPath $previousDirectory.Path
}
