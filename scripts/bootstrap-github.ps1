#Requires -Version 7.0

<#
.SYNOPSIS
One-time bootstrap: creates the GitHub OIDC deployment identity and configures
per-stage GitHub Environments (dev, test, prod), each targeting its own resource group.
#>
[CmdletBinding()]
param(
    [string]$GitHubOwner,
    [string]$GitHubRepository,
    [string]$GitHubSubjectPrefix,
    [string[]]$GitHubEnvironments = @('dev', 'test', 'prod'),
    [string]$EnvironmentPrefix = 'hosted-agents',
    [string]$Location = 'swedencentral',
    [string]$SubscriptionId,
    [string]$IdentityResourceGroupName = 'rg-github-identities',
    [string]$IdentityName = 'github-foundry-hosted-agent',
    [switch]$SkipGitHubConfiguration
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

if (-not (Get-Command 'az' -ErrorAction SilentlyContinue)) {
    throw "Required command 'az' was not found."
}
$needsGh = (-not $GitHubOwner) -or (-not $GitHubRepository) -or (-not $GitHubSubjectPrefix) -or (-not $SkipGitHubConfiguration)
if ($needsGh -and -not (Get-Command 'gh' -ErrorAction SilentlyContinue)) {
    throw "Required command 'gh' was not found. Pass -GitHubOwner, -GitHubRepository, -GitHubSubjectPrefix, and -SkipGitHubConfiguration to run without it."
}

if (-not $GitHubOwner -or -not $GitHubRepository) {
    $repositoryParts = (gh repo view --json nameWithOwner --jq '.nameWithOwner') -split '/', 2
    if ($repositoryParts.Count -ne 2) {
        throw 'Could not determine the GitHub repository from the current clone.'
    }
    if (-not $GitHubOwner) { $GitHubOwner = $repositoryParts[0] }
    if (-not $GitHubRepository) { $GitHubRepository = $repositoryParts[1] }
}
$repository = "$GitHubOwner/$GitHubRepository"

if (-not $GitHubSubjectPrefix) {
    gh auth status | Out-Null
    $oidcConfiguration = gh api "repos/$repository/actions/oidc/customization/sub" | ConvertFrom-Json
    $GitHubSubjectPrefix = $oidcConfiguration.sub_claim_prefix
    if (-not $GitHubSubjectPrefix) {
        $GitHubSubjectPrefix = "repo:$repository"
    }
}

$accountArguments = @('account', 'show', '--output', 'json')
if ($SubscriptionId) {
    $accountArguments += @('--subscription', $SubscriptionId)
}
$account = az @accountArguments | ConvertFrom-Json
if (-not $account.id) {
    throw 'Azure CLI is not authenticated. Run az login first.'
}
$SubscriptionId = $account.id

$parameterFile = Join-Path ([System.IO.Path]::GetTempPath()) "hosted-agent-bootstrap-$([guid]::NewGuid()).json"
$parameters = @{
    '$schema'      = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters     = @{
        location                  = @{ value = $Location }
        identityResourceGroupName = @{ value = $IdentityResourceGroupName }
        identityName              = @{ value = $IdentityName }
        githubSubjectPrefix       = @{ value = $GitHubSubjectPrefix }
        githubEnvironments        = @{ value = $GitHubEnvironments }
    }
}

try {
    $parameters | ConvertTo-Json -Depth 5 | Set-Content -Path $parameterFile -Encoding utf8
    $deployment = az deployment sub create `
        --subscription $SubscriptionId `
        --name 'github-hosted-agent-identity' `
        --location $Location `
        --template-file (Join-Path $PSScriptRoot '../infra/bootstrap/bootstrap.bicep') `
        --parameters "@$parameterFile" `
        --output json | ConvertFrom-Json
}
finally {
    Remove-Item $parameterFile -ErrorAction SilentlyContinue
}

$outputs = $deployment.properties.outputs
if (-not $outputs.AZURE_CLIENT_ID.value -or -not $outputs.AZURE_PRINCIPAL_ID.value) {
    throw 'The Azure bootstrap deployment did not return identity outputs.'
}
$shared = [ordered]@{
    AZURE_CLIENT_ID       = $outputs.AZURE_CLIENT_ID.value
    AZURE_PRINCIPAL_ID    = $outputs.AZURE_PRINCIPAL_ID.value
    AZURE_TENANT_ID       = $outputs.AZURE_TENANT_ID.value
    AZURE_SUBSCRIPTION_ID = $outputs.AZURE_SUBSCRIPTION_ID.value
    AZURE_LOCATION        = $outputs.AZURE_LOCATION.value
}

$summary = foreach ($githubEnvironment in $GitHubEnvironments) {
    $values = [ordered]@{}
    foreach ($entry in $shared.GetEnumerator()) { $values[$entry.Key] = $entry.Value }
    $values.AZURE_ENV_NAME = "$EnvironmentPrefix-$githubEnvironment"
    if (-not $SkipGitHubConfiguration) {
        gh api --method PUT "repos/$repository/environments/$githubEnvironment" | Out-Null
        foreach ($entry in $values.GetEnumerator()) {
            gh variable set $entry.Key --env $githubEnvironment --repo $repository --body $entry.Value
        }
        Write-Host "Configured GitHub environment '$githubEnvironment' in $repository."
    }
    [pscustomobject]@{
        Environment   = $githubEnvironment
        AzdEnv        = $values.AZURE_ENV_NAME
        ResourceGroup = "rg-$($values.AZURE_ENV_NAME)"
    }
}

$shared.GetEnumerator() | Format-Table -AutoSize
$summary | Format-Table -AutoSize
