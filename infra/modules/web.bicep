param location string
param tags object
param stage string
param environmentName string
@maxLength(32)
param appName string
param identityId string
param identityClientId string
param identityPrincipalId string
param registryLoginServer string
param logAnalyticsName string
param appInsightsName string
param foundryProjectEndpoint string
param agentName string
param exists bool

var monitoringMetricsPublisherRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource webMonitoringPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, identityPrincipalId, monitoringMetricsPublisherRoleId)
  scope: appInsights
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringMetricsPublisherRoleId
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// Keeps the image azd last deployed when infrastructure is re-provisioned.
module fetchLatestImage 'fetch-container-image.bicep' = {
  name: 'web-fetch-image'
  params: {
    exists: exists
    name: appName
  }
}

resource webApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryLoginServer
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: exists ? fetchLatestImage.outputs.containers[0].image : placeholderImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'AZURE_AI_AGENT_NAME', value: agentName }
            { name: 'APP_STAGE', value: stage }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
          ]
        }
      ]
      scale: {
        minReplicas: stage == 'prod' ? 1 : 0
        maxReplicas: stage == 'prod' ? 5 : 2
      }
    }
  }
}

output name string = webApp.name
output uri string = 'https://${webApp.properties.configuration.ingress.fqdn}'
