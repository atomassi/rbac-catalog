<#
.SYNOPSIS
    Stops (or starts) the App Service `ppe` deployment slot.

.DESCRIPTION
    Scheduled by the Automation Account to save cost outside business hours.
    Pair with a second schedule that invokes the script with -Action Start
    in the morning.

    Inputs (Automation variables):
      - ResourceGroupName : RG holding the App Service.
      - AppServiceName    : the parent App Service name.

    Required permissions on the Automation Account managed identity:
      Website Contributor (de139f84-1756-47ae-9be6-808fbbe84772) on the App Service.
#>

param(
    [ValidateSet('Stop','Start')]
    [string]$Action = 'Stop',

    [string]$SlotName = 'ppe'
)

$ErrorActionPreference = 'Stop'
Disable-AzContextAutosave -Scope Process | Out-Null
Connect-AzAccount -Identity | Out-Null

$rg  = Get-AutomationVariable -Name 'ResourceGroupName'
$app = Get-AutomationVariable -Name 'AppServiceName'

Write-Output "$Action slot '$SlotName' on $app ($rg)"

if ($Action -eq 'Stop') {
    Stop-AzWebAppSlot -ResourceGroupName $rg -Name $app -Slot $SlotName
} else {
    Start-AzWebAppSlot -ResourceGroupName $rg -Name $app -Slot $SlotName
}

Write-Output "$Action complete."
