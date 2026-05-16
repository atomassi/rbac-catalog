// ============================================================================
// Module: Ubuntu 24.04 VM running Ollama
// ============================================================================
// Ollama is installed via cloud-init (scripts/install-ollama.cloud-init.yaml).
// After first boot, SSH in to load the fine-tuned model:
//   ssh azureuser@<vm-ip>
//   ollama pull qwen2.5:7b
//   ollama create qwen-rbac-v5 -f /opt/ollama/Modelfile.qwen-rbac-v5
//
// SSH access is gated at the NSG level (see modules/network.bicep), which
// allows traffic only from the operator IP passed to that module.
// ============================================================================

metadata description = 'Ubuntu 24.04 LTS VM hosting Ollama, with SSH-key auth and cloud-init bootstrap.'

@description('VM name.')
param vmName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('VM size.')
param vmSize string = 'Standard_B2als_v2'

@description('SSH public key authorized on the VM.')
param sshPublicKey string

@description('Subnet resource ID where the NIC is created.')
param subnetId string

@description('Admin username on the VM.')
param adminUsername string = 'azureuser'

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: '${vmName}-pip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static', publicIPAddressVersion: 'IPv4' }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-01-01' = {
  name: '${vmName}-nic'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [ {
      name: 'ipconfig1'
      properties: {
        subnet: { id: subnetId }
        privateIPAllocationMethod: 'Dynamic'
        publicIPAddress: { id: publicIp.id }
      }
    } ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        diskSizeGB: 32
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [ {
            path: '/home/${adminUsername}/.ssh/authorized_keys'
            keyData: sshPublicKey
          } ]
        }
      }
      customData: base64(loadTextContent('../scripts/install-ollama.cloud-init.yaml'))
    }
    networkProfile: { networkInterfaces: [ { id: nic.id } ] }
  }
}

output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output publicIp string = publicIp.properties.ipAddress
