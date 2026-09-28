targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('azd environment name, one per stage (for example hosted-agents-dev). Drives the resource group and resource names.')
param environmentName string

@allowed([
  'dev'
  'test'
  'prod'
])
@description('Deployment stage. Controls the Foundry project name, tags, and web scaling.')
param stage string = 'dev'

@description('Azure region for all resources. Must support Foundry hosted agents and the selected model.')
param location string = 'swedencentral'

@description('Object ID that deploys and invokes agents: the developer locally, the GitHub OIDC identity in CI.')
param principalId string

@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param principalType string = 'ServicePrincipal'

@description('Model name, also used as the deployment name.')
param modelName string = 'gpt-4.1-mini'

@description('Model deployment capacity in thousands of tokens per minute.')
param modelCapacity int = 30

@description('Hosted agent name; must match src/agent/agent.yaml.')
param agentName string = 'contoso-support-desk'

@description('Set by azd when the web container app already exists so re-provisioning keeps the deployed image.')
param webExists bool = false

var token = toLower(uniqueString(subscription().id, environmentName, location))
var resourceGroupName = 'rg-${environmentName}'
var tags = {
  'azd-env-name': environmentName
  stage: stage
  workload: 'foundry-hosted-agent-cicd'
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'hosted-agent-resources'
  scope: resourceGroup
  params: {
    location: location
    token: token
    stage: stage
    tags: tags
    principalId: principalId
    principalType: principalType
    modelName: modelName
    modelCapacity: modelCapacity
    agentName: agentName
    webExists: webExists
  }
}

output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_STAGE string = stage
output AZURE_AI_ACCOUNT_NAME string = resources.outputs.foundryAccountName
output AZURE_AI_ACCOUNT_ID string = resources.outputs.foundryAccountId
output AZURE_AI_PROJECT_NAME string = resources.outputs.foundryProjectName
output FOUNDRY_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = modelName
output AZURE_AI_AGENT_NAME string = agentName
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.registryName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.applicationInsightsConnectionString
output SERVICE_WEB_NAME string = resources.outputs.webAppName
output SERVICE_WEB_URI string = resources.outputs.webAppUri
