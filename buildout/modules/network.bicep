// ============================================================================
// Module: VNet (10.0.0.0/16) + subnets + Ollama NSG
// ============================================================================
// Subnets:
//   - appservice-subnet (10.0.2.0/24): delegated to Microsoft.Web/serverFarms
//     for App Service outbound VNet integration.
//   - ollama-subnet     (10.0.1.0/24): hosts the Ollama VM. Protected by the
//     Ollama NSG below — SSH from operator IP + Ollama HTTP from the
//     App Service subnet, deny everything else.
// ============================================================================

metadata description = 'VNet 10.0.0.0/16 with appservice and ollama subnets and an NSG that locks down the Ollama VM.'

@description('VNet name.')
param vnetName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('Public IP allowed to SSH the Ollama VM. Empty = no SSH rule is created.')
param adminIpAddress string

var appCidr    = '10.0.2.0/24'
var ollamaCidr = '10.0.1.0/24'

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: 'ollama-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: concat(
      empty(adminIpAddress) ? [] : [ {
        name: 'AllowSSHFromAdmin'
        properties: {
          priority: 100, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: adminIpAddress, sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '22'
        }
      } ],
      [
        {
          name: 'AllowOllamaFromAppService'
          properties: {
            priority: 200, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
            sourceAddressPrefix: appCidr, sourcePortRange: '*'
            destinationAddressPrefix: '*', destinationPortRange: '11434'
          }
        }
        {
          name: 'DenyAllOtherInbound'
          properties: {
            priority: 4000, direction: 'Inbound', access: 'Deny', protocol: '*'
            sourceAddressPrefix: '*', sourcePortRange: '*'
            destinationAddressPrefix: '*', destinationPortRange: '*'
          }
        }
      ]
    )
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.0.0.0/16' ] }
    subnets: [
      {
        name: 'ollama-subnet'
        properties: {
          addressPrefix: ollamaCidr
          networkSecurityGroup: { id: nsg.id }
        }
      }
      {
        name: 'appservice-subnet'
        properties: {
          addressPrefix: appCidr
          delegations: [ {
            name: 'webapp-delegation'
            properties: { serviceName: 'Microsoft.Web/serverFarms' }
          } ]
        }
      }
    ]
  }
}

output ollamaSubnetId string = vnet.properties.subnets[0].id
output appServiceSubnetId string = vnet.properties.subnets[1].id
