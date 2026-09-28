$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

azd env get-values | ForEach-Object {
    if ($_ -match '^([^=]+)="(.*)"$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

if (-not (Test-Path '.venv')) {
    python -m venv .venv
}
$venvPython = if ($IsWindows) { '.venv\Scripts\python.exe' } else { '.venv/bin/python' }

& $venvPython -m pip install --disable-pip-version-check --quiet -r requirements.txt
& $venvPython scripts/deploy_agent.py deploy
