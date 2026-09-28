param location string
param tags object
param accountName string
param projectName string
param stage string
param modelName string
param modelCapacity int
param appInsightsName string
param principalId string
param principalType string
param webPrincipalId string

var foundryUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '53ca6127-db72-4b80-b1b0-d745d6d5456d'
)
var monitoringMetricsPublisherRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: false
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  name: modelName
  parent: account
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  name: projectName
  parent: account
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    displayName: projectName
    description: 'Hosted agent project for the ${stage} stage'
  }
  dependsOn: [
    modelDeployment
  ]
}

// The platform's supported App Insights association stores the connection string on the connection.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  name: '${account.name}/${project.name}/${appInsights.name}'
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: appInsights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
}

resource projectMonitoringPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, project.id, monitoringMetricsPublisherRoleId)
  scope: appInsights
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringMetricsPublisherRoleId
  }
}

// Hosted agents call the model through the project identity; Foundry User is required at account scope.
resource projectFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, foundryUserRoleId)
  scope: account
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRoleId
  }
}

resource principalFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, foundryUserRoleId)
  scope: account
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: foundryUserRoleId
  }
}

resource webFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, webPrincipalId, foundryUserRoleId)
  scope: account
  properties: {
    principalId: webPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRoleId
  }
}

output accountName string = account.name
output accountId string = account.id
output projectName string = project.name
output projectPrincipalId string = project.identity.principalId
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
