param location string
param identityName string
param githubSubjectPrefix string
param githubEnvironments array

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: {
    workload: 'foundry-hosted-agent-cicd'
    purpose: 'github-oidc'
  }
}

@batchSize(1)
resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = [
  for githubEnvironment in githubEnvironments: {
    name: 'github-${githubEnvironment}'
    parent: identity
    properties: {
      audiences: [
        'api://AzureADTokenExchange'
      ]
      issuer: 'https://token.actions.githubusercontent.com'
      subject: '${githubSubjectPrefix}:environment:${githubEnvironment}'
    }
  }
]

output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
