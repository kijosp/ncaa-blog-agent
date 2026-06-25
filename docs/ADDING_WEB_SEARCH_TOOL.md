# Adding Web Search Tool to AgentCore Gateway

## Overview

The AgentCore Web Search tool is a managed MCP connector that provides web search capabilities to any agent connected to the Gateway. This document covers what was needed to add it to the FAST stack and the decisions made along the way.

## What We Did

### 1. Added the Web Search Target to CDK (`infra-cdk/lib/backend-stack.ts`)

CDK's L1 types don't support the `Connector` property under `McpTargetConfiguration` yet (the feature is newer than the CDK types). We tried three approaches:

- **`as any` cast** — CDK still validates during synthesis and rejects unknown properties
- **`addPropertyOverride`** — CDK validates the base props before applying overrides, so it fails on the dummy placeholder
- **`CfnResource` (what worked)** — fully bypasses CDK type validation by using a raw CloudFormation resource

```typescript
const webSearchTarget = new cdk.CfnResource(this, "WebSearchTarget", {
  type: "AWS::BedrockAgentCore::GatewayTarget",
  properties: {
    GatewayIdentifier: gateway.attrGatewayIdentifier,
    Name: "web-search-tool",
    Description: "AgentCore Web Search connector for blog discovery",
    TargetConfiguration: {
      Mcp: {
        Connector: {
          Source: { ConnectorId: "web-search" },
          Configurations: [{ Name: "WebSearch", ParameterValues: {} }],
        },
      },
    },
    CredentialProviderConfigurations: [
      { CredentialProviderType: "GATEWAY_IAM_ROLE" },
    ],
  },
})
webSearchTarget.addDependency(gateway)
```

### 2. Added IAM Permission for Web Search

The Gateway role needs `bedrock-agentcore:InvokeWebSearch` permission on the AWS-owned web search resource:

```typescript
gatewayRole.addToPolicy(
  new iam.PolicyStatement({
    effect: iam.Effect.ALLOW,
    actions: ["bedrock-agentcore:InvokeWebSearch"],
    resources: [
      `arn:aws:bedrock-agentcore:${this.region}:aws:tool/web-search.v1`,
    ],
  })
)
```

### 3. Removed Cedar Policy Engine

This was the most significant change. We removed the Cedar policy engine entirely from the CDK stack. See the section below for why.

## Why We Removed Cedar

### What Cedar Does

Cedar is a deny-by-default policy language. The FAST template shipped with a Cedar policy that controlled which authenticated users (by department) could call which tools on the Gateway. The action names follow the pattern `<target-name>___<tool-name>`.

### Why It's Not Relevant for This Use Case

Our use case has:
- A **backend extraction agent** that searches blogs for injury/schedule news on behalf of users
- A **backend discovery agent** (batch job) that finds blog URLs
- **All authenticated users** should have the same access to the same tools — there's no per-user or per-department restriction needed

Cedar adds value when you have multiple user roles with different tool permissions (e.g., "finance can use the billing tool, guests cannot"). We don't have that requirement. Removing it simplifies the stack and eliminates a deploy-time failure.

### The Problem We Had With Cedar

When we added the Web Search tool and tried to permit it in Cedar:

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"sample-tool-target___text_analysis_tool",
    AgentCore::Action::"web-search-tool___WebSearch"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when { ... };
```

**The deploy failed repeatedly** with `CREATE_FAILED` on the Cedar policy. The root cause was a race condition:

1. The Cedar policy engine validates the policy against the Gateway's tool schema
2. To build that schema, the policy engine calls `ListGatewayTargets` / `GetGatewayTarget` on the Gateway
3. During initial stack creation, the Web Search target wasn't fully registered yet (or the policy engine's IAM role didn't have permissions to read the gateway) when the policy was being validated
4. Result: policy validation fails with "Insufficient permissions to call gateway with ID ..."

**What we tried:**
- Adding `cedarPolicy.node.addDependency(webSearchTarget)` — didn't help because CloudFormation reports the target as created before the policy engine's internal schema sync completes
- Two-step deploy (first without web search in Cedar, then add it) — works but adds operational complexity
- Orphaned policy engine cleanup between retries

**What we decided:** Since Cedar isn't needed for our use case, removing it was the cleanest fix. The Gateway still authenticates requests via JWT — it just doesn't do per-tool authorization. All tools are accessible to any authenticated caller.

### How to Add Cedar Back (If Needed Later)

If you later need per-user tool restrictions:
1. Restore the Cedar policy section from git history
2. Deploy the stack **without** the web search action in Cedar first
3. After the stack is stable, add the web search action and redeploy (the policy engine will have synced by then)
4. Or use `validationMode: "IGNORE_ALL_FINDINGS"` when creating the policy (skips schema validation)

## Verification

After successful deploy, verify the Web Search tool is accessible:

```bash
# Check target status
GATEWAY_ID=$(aws bedrock-agentcore-control list-gateways --profile fanduel --region us-east-1 --query 'items[0].gatewayId' --output text)
aws bedrock-agentcore-control list-gateway-targets --gateway-identifier $GATEWAY_ID --profile fanduel --region us-east-1

# Check it appears in tools/list via MCP (needs M2M token)
# Should show: web-search-tool___WebSearch
curl -X POST "$GATEWAY_URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1, "params": {}}'
```

## References

- Web Search Tool docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html
- Target configuration: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-api-target-config.html
- CloudFormation GatewayTarget: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gatewaytarget.html
- Gateway Service Role permissions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-api-target-config.html#gateway-add-target-api-connector-web-search-service-role
- AgentCore pricing: https://aws.amazon.com/bedrock/agentcore/pricing/
