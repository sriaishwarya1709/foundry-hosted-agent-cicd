param location string
param tags object
param registryName string
param foundryProjectPrincipalId string
param webPrincipalId string
param principalId string
param principalType string

var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var acrPushRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '8311e382-0749-4cb8-b61a-304f252e45ec'
)

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Premium'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

resource projectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, foundryProjectPrincipalId, acrPullRoleId)
  scope: registry
  properties: {
    principalId: foundryProjectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource webAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, webPrincipalId, acrPullRoleId)
  scope: registry
  properties: {
    principalId: webPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource principalAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, principalId, acrPushRoleId)
  scope: registry
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: acrPushRoleId
  }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
