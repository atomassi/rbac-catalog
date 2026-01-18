<#
.SYNOPSIS
    Auto-shutdown PPE deployment slot when scheduled duration expires.

.DESCRIPTION
    Azure Automation Runbook that checks if the PPE slot has passed its scheduled
    shutdown time (PPE_SHUTDOWN_AT app setting as Unix timestamp) and stops it.

    Documentation: https://learn.microsoft.com/en-us/azure/automation/overview

    DEPLOYMENT
    ----------
    Automation Account: rbac-catalog-automation
    Resource Group:     builtinroles
    Runtime:            PowerShell 7.2

    Deploy via CLI:
        az automation runbook replace-content \
            --automation-account-name rbac-catalog-automation \
            --resource-group builtinroles \
            --name PPE-Auto-Shutdown \
            --content @scripts/ppe-auto-shutdown.ps1

        az automation runbook publish \
            --automation-account-name rbac-catalog-automation \
            --resource-group builtinroles \
            --name PPE-Auto-Shutdown

    SCHEDULE
    --------
    Runs hourly via Azure Automation Schedule.
    Max delay after scheduled shutdown: 59 minutes.

    PREREQUISITES
    -------------
    - System-Assigned Managed Identity enabled on Automation Account
    - Managed Identity has "Website Contributor" role on the App Service
    - Az.Accounts and Az.Websites modules imported

    WORKFLOW
    --------
    1. deploy-ppe.yml sets PPE_SHUTDOWN_AT (Unix timestamp) on deployment
    2. This runbook runs hourly and checks if timestamp has passed
    3. If expired: stops slot and clears the setting

.PARAMETER AppName
    Azure App Service name. Default: azurerbac-builtinroles

.PARAMETER ResourceGroupName
    Resource Group name. Default: builtinroles

.PARAMETER SlotName
    Deployment slot name. Default: ppe

.NOTES
    Requires: Az.Accounts, Az.Websites
    Identity: System-assigned Managed Identity with Website Contributor role
#>

param(
    [string]$AppName = "azurerbac-builtinroles",
    [string]$ResourceGroupName = "builtinroles",
    [string]$SlotName = "ppe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

#region Helper Functions

function Convert-UnixToDateTime([long]$Timestamp) {
    # Returns DateTime in UTC
    [DateTimeOffset]::FromUnixTimeSeconds($Timestamp).UtcDateTime
}

function Get-CurrentUnixTimestamp {
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}

function Format-TimeRemaining([int]$Seconds) {
    $hours = [math]::Floor($Seconds / 3600)
    $minutes = [math]::Floor(($Seconds % 3600) / 60)
    if ($hours -gt 0) { return "$hours h $minutes min" }
    return "$minutes min"
}

#endregion

#region Main Script

# Connect using Managed Identity
try {
    Connect-AzAccount -Identity | Out-Null
    Write-Output "Connected via Managed Identity"
}
catch {
    throw "Failed to connect to Azure: $_"
}

# Get slot state
try {
    $slot = Get-AzWebAppSlot -ResourceGroupName $ResourceGroupName -Name $AppName -Slot $SlotName
}
catch {
    Write-Output "PPE slot not found: $_"
    return
}

if ($slot.State -ne "Running") {
    Write-Output "PPE slot is $($slot.State)"
    return
}

# Get shutdown timestamp from app settings
$shutdownAt = $slot.SiteConfig.AppSettings |
    Where-Object Name -eq "PPE_SHUTDOWN_AT" |
    Select-Object -ExpandProperty Value

# No shutdown configured - stop to prevent runaway costs
if (-not $shutdownAt) {
    Write-Output "PPE running without PPE_SHUTDOWN_AT - stopping to prevent costs"
    Stop-AzWebAppSlot -ResourceGroupName $ResourceGroupName -Name $AppName -Slot $SlotName
    Write-Output "Stopped PPE slot"
    return
}

# Validate timestamp format - stop slot if invalid to prevent costs
if ($shutdownAt -notmatch '^\d+$') {
    Write-Output "Invalid PPE_SHUTDOWN_AT value '$shutdownAt' - stopping to prevent costs"
    Stop-AzWebAppSlot -ResourceGroupName $ResourceGroupName -Name $AppName -Slot $SlotName
    Write-Output "Stopped PPE slot due to invalid timestamp"
    return
}

$shutdownAtInt = [long]$shutdownAt
$now = Get-CurrentUnixTimestamp
$scheduledTime = (Convert-UnixToDateTime $shutdownAtInt).ToString("yyyy-MM-dd HH:mm:ss 'UTC'")

# Check if shutdown time has passed
if ($now -ge $shutdownAtInt) {
    Write-Output "Shutdown time reached: $scheduledTime"

    # Stop the slot
    Stop-AzWebAppSlot -ResourceGroupName $ResourceGroupName -Name $AppName -Slot $SlotName

    # Clear the shutdown setting
    $newSettings = @{}
    $slot.SiteConfig.AppSettings |
        Where-Object Name -ne "PPE_SHUTDOWN_AT" |
        ForEach-Object { $newSettings[$_.Name] = $_.Value }

    Set-AzWebAppSlot -ResourceGroupName $ResourceGroupName -Name $AppName -Slot $SlotName -AppSettings $newSettings | Out-Null

    Write-Output "Stopped PPE slot and cleared PPE_SHUTDOWN_AT"
}
else {
    $remaining = $shutdownAtInt - $now
    Write-Output "PPE running | Shutdown: $scheduledTime | Remaining: $(Format-TimeRemaining $remaining)"
}

#endregion
