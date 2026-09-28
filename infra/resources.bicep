param location string
param token string
param stage string
param tags object
param principalId string
param principalType string
param modelName string
param modelCapacity int
param agentName string
param webExists bool

var projectName = 'proj-${stage}'

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-web-${token}'
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    tags: tags
    logAnalyticsName: 'log-${token}'
    appInsightsName: 'appi-${token}'
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    location: location
    tags: tags
    accountName: 'aif-${token}'
    projectName: projectName
    stage: stage
    modelName: modelName
    modelCapacity: modelCapacity
    appInsightsName: monitoring.outputs.appInsightsName
    principalId: principalId
    principalType: principalType
    webPrincipalId: webIdentity.properties.principalId
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    location: location
    tags: tags
    registryName: 'cr${token}'
    foundryProjectPrincipalId: foundry.outputs.projectPrincipalId
    webPrincipalId: webIdentity.properties.principalId
    principalId: principalId
    principalType: principalType
  }
}

module web 'modules/web.bicep' = {
  name: 'web'
  params: {
    location: location
    tags: tags
    stage: stage
    environmentName: 'cae-${token}'
    appName: 'ca-web-${token}'
    identityId: webIdentity.id
    identityClientId: webIdentity.properties.clientId
    identityPrincipalId: webIdentity.properties.principalId
    registryLoginServer: registry.outputs.loginServer
    logAnalyticsName: monitoring.outputs.logAnalyticsName
    appInsightsName: monitoring.outputs.appInsightsName
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    agentName: agentName
    exists: webExists
  }
}

output foundryAccountName string = foundry.outputs.accountName
output foundryAccountId string = foundry.outputs.accountId
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output registryName string = registry.outputs.name
output registryLoginServer string = registry.outputs.loginServer
output applicationInsightsConnectionString string = monitoring.outputs.connectionString
output webAppName string = web.outputs.name
output webAppUri string = web.outputs.uri
