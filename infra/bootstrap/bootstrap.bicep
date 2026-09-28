targetScope = 'subscription'

@description('Azure region for the managed identity resource group.')
param location string = 'swedencentral'

@description('Resource group that contains the GitHub deployment identity.')
param identityResourceGroupName string = 'rg-github-identities'

@description('Name of the user-assigned managed identity used by GitHub Actions.')
param identityName string = 'github-foundry-hosted-agent'

@description('Repository subject prefix returned by the GitHub OIDC customization API.')
param githubSubjectPrefix string

@description('GitHub Environments referenced by the staged deployment workflow.')
param githubEnvironments array = [
  'dev'
  'test'
  'prod'
]

var contributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b24988ac-6180-42a0-ab88-20f7382dd24c'
)
var roleBasedAccessControlAdministratorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'f58310d9-a9f6-439a-9e8d-f62e7b41a168'
)
var identityResourceId = resourceId(
  subscription().subscriptionId,
  identityResourceGroupName,
  'Microsoft.ManagedIdentity/userAssignedIdentities',
  identityName
)

resource identityResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: identityResourceGroupName
  location: location
  tags: {
    workload: 'foundry-hosted-agent-cicd'
    purpose: 'github-oidc-bootstrap'
  }
}

module githubIdentity 'github-identity.bicep' = {
  name: 'github-identity-bootstrap'
  scope: identityResourceGroup
  params: {
    location: location
    identityName: identityName
    githubSubjectPrefix: githubSubjectPrefix
    githubEnvironments: githubEnvironments
  }
}

// Subscription scope because each stage creates its own resource group.
resource contributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, identityResourceId, contributorRoleId)
  properties: {
    principalId: githubIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: contributorRoleId
  }
}

resource roleAdministratorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, identityResourceId, roleBasedAccessControlAdministratorRoleId)
  properties: {
    principalId: githubIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleBasedAccessControlAdministratorRoleId
  }
}

output AZURE_CLIENT_ID string = githubIdentity.outputs.clientId
output AZURE_PRINCIPAL_ID string = githubIdentity.outputs.principalId
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId
output AZURE_LOCATION string = location
