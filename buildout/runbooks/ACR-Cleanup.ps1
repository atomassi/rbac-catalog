<#
.SYNOPSIS
    Prunes Azure Container Registry repositories.

.DESCRIPTION
    For every repository in the configured ACR:
      1. Deletes untagged manifests (orphans left by re-pushes).
      2. Keeps only the latest N tagged manifests, deletes the rest.

    Inputs (Automation variables, populated by modules/automation.bicep):
      - AcrName     : ACR name (without .azurecr.io).
      - AcrKeepLastN: how many tags to keep per repository.

    Required permissions on the Automation Account managed identity:
      AcrDelete (c2f4ef07-c644-48eb-af81-4b1b4947fb11) on the ACR resource.
#>

$ErrorActionPreference = 'Stop'
Disable-AzContextAutosave -Scope Process | Out-Null
Connect-AzAccount -Identity | Out-Null

$acrName   = Get-AutomationVariable -Name 'AcrName'
$keepLastN = [int](Get-AutomationVariable -Name 'AcrKeepLastN')
Write-Output "ACR: $acrName | keep last: $keepLastN tags"

$token   = (Get-AzAccessToken -ResourceUrl "https://$acrName.azurecr.io").Token
$headers = @{ Authorization = "Bearer $token" }
$base    = "https://$acrName.azurecr.io"

$repos = (Invoke-RestMethod -Uri "$base/v2/_catalog" -Headers $headers).repositories
foreach ($repo in $repos) {
    Write-Output "`n=== $repo ==="
    $manifests = (Invoke-RestMethod -Uri "$base/acr/v1/$repo/_manifests?orderby=time_desc" -Headers $headers).manifests

    # 1) Delete untagged manifests
    $manifests | Where-Object { -not $_.tags } | ForEach-Object {
        Write-Output "  delete (untagged) $($_.digest)"
        Invoke-RestMethod -Method Delete -Uri "$base/v2/$repo/manifests/$($_.digest)" -Headers $headers | Out-Null
    }
    # 2) Keep only the newest N tagged manifests
    $manifests | Where-Object { $_.tags } | Select-Object -Skip $keepLastN | ForEach-Object {
        Write-Output "  delete (old) $($_.digest) [$($_.tags -join ',')]"
        Invoke-RestMethod -Method Delete -Uri "$base/v2/$repo/manifests/$($_.digest)" -Headers $headers | Out-Null
    }
}

Write-Output "`nDone."
