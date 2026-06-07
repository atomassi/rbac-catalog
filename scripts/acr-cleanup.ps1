<#
.SYNOPSIS
    Removes old container images from Azure Container Registry.

.DESCRIPTION
    Azure Automation Runbook that cleans up old image versions from ACR,
    keeping only the N most recent versions. The 'latest' tag is never deleted.

    DEPLOYMENT
    ----------
    Automation Account: rbac-catalog-automation
    Resource Group:     builtinroles
    Runtime:            PowerShell 7.2

    Deploy via CLI:
        az automation runbook replace-content \
            --automation-account-name rbac-catalog-automation \
            --resource-group builtinroles \
            --name ACR-Cleanup \
            --content @scripts/acr-cleanup.ps1

        az automation runbook publish \
            --automation-account-name rbac-catalog-automation \
            --resource-group builtinroles \
            --name ACR-Cleanup

    SCHEDULE
    --------
    Runs daily via Azure Automation Schedule (Daily-3AM-UTC).

    PREREQUISITES
    -------------
    - System-Assigned Managed Identity enabled on Automation Account
    - Managed Identity has "AcrDelete" role on the Container Registry

.PARAMETER RegistryName
    Azure Container Registry name.

.PARAMETER RepositoryName
    Repository/image name to clean.

.PARAMETER KeepCount
    Number of versions to keep (excluding 'latest'). Must be at least 1.

.EXAMPLE
    .\acr-cleanup.ps1
    Runs with defaults: azurerbacregistry/rbaccatalog, keeps 4 versions.

.EXAMPLE
    .\acr-cleanup.ps1 -KeepCount 10
    Keeps the 10 most recent versions instead of 4.

.NOTES
    Author:   Andrea Tomassilli
    Requires: Az.Accounts, Az.ContainerRegistry
#>

#Requires -Modules Az.Accounts, Az.ContainerRegistry

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RegistryName = 'azurerbacregistry',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RepositoryName = 'rbaccatalog',

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$KeepCount = 4
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

#region Authentication
Write-Output 'Authenticating with Managed Identity...'

try {
    $null = Connect-AzAccount -Identity
    Write-Output 'Authenticated successfully'
}
catch {
    Write-Error "Authentication failed: $($_.Exception.Message)"
    throw
}
#endregion

#region Fetch Manifests
Write-Output "`nFetching manifests from $RegistryName/$RepositoryName..."

$manifests = Get-AzContainerRegistryManifest `
    -RegistryName $RegistryName `
    -RepositoryName $RepositoryName

if (-not $manifests) {
    Write-Output 'No manifests found. Nothing to clean.'
    return
}

# Filter: has tags, excludes 'latest', sorted newest first
$versionedManifests = $manifests |
    Where-Object { $_.Tags -and ($_.Tags -notcontains 'latest') } |
    Sort-Object -Property CreatedTime -Descending

$totalCount = @($versionedManifests).Count
Write-Output "Found $totalCount versioned manifests (excluding 'latest')"

if ($totalCount -le $KeepCount) {
    Write-Output "Only $totalCount exist. Keeping all (threshold: $KeepCount)."
    return
}
#endregion

#region Delete Old Manifests
$manifestsToDelete = $versionedManifests | Select-Object -Skip $KeepCount
$deleteCount = @($manifestsToDelete).Count

Write-Output "`nDeleting $deleteCount oldest manifests (keeping $KeepCount newest):`n"

$results = [System.Collections.Generic.List[PSCustomObject]]::new()

foreach ($manifest in $manifestsToDelete) {
    $tagDisplay = ($manifest.Tags -join ', ')
    $digestShort = $manifest.Digest.Substring(0, 19)

    $result = [PSCustomObject]@{
        Tags    = $tagDisplay
        Digest  = $digestShort
        Created = $manifest.CreatedTime
        Status  = 'Pending'
    }

    try {
        Write-Output "  Deleting: $tagDisplay ($digestShort...)"

        Remove-AzContainerRegistryManifest `
            -RegistryName $RegistryName `
            -RepositoryName $RepositoryName `
            -Manifest $manifest.Digest

        $result.Status = 'Deleted'
        Write-Output '      OK'
    }
    catch {
        $result.Status = "Failed: $($_.Exception.Message)"
        Write-Warning "      FAILED: $($_.Exception.Message)"
    }

    $results.Add($result)
}
#endregion

#region Summary
$deleted = @($results | Where-Object { $_.Status -eq 'Deleted' }).Count
$failed = @($results | Where-Object { $_.Status -ne 'Deleted' }).Count

Write-Output @"

================================================================
  ACR CLEANUP SUMMARY
================================================================
  Registry:     $RegistryName
  Repository:   $RepositoryName
  Threshold:    Keep $KeepCount newest
  Deleted:      $deleted
  Failed:       $failed
================================================================
"@

if ($failed -gt 0) {
    Write-Warning 'Some deletions failed. Review output above.'
    exit 1
}

Write-Output 'Cleanup complete!'
#endregion
