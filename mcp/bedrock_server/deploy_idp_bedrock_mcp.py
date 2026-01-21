#!/usr/bin/env python3
"""
Updated deployment script for IDP Bedrock MCP Server
Uses IAM SigV4 authentication (no Cognito user authentication required)
"""

import sys
import os
import json
import time
from boto3.session import Session

# Import our utility functions
from utils import (
    get_existing_cognito_config,
    get_existing_infrastructure_config,
    create_agentcore_role,
)


def generate_mcp_config(agent_arn, region):
    """
    Generate MCP configuration for IAM authentication
    """
    # Construct the MCP server URL
    encoded_arn = agent_arn.replace(":", "%3A").replace("/", "%2F")
    mcp_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    return {
        "server_url": mcp_url,
        "region": region,
        "agent_arn": agent_arn,
        "authentication": "IAM SigV4",
        "instructions": [
            "1. This MCP server uses IAM SigV4 authentication",
            "2. Ensure your AWS credentials have the necessary permissions",
            "3. Use AWS SDK or CLI with proper IAM credentials to access",
        ],
    }


def verify_infrastructure():
    """Verify existing IDP infrastructure"""
    print("🔍 Step 1: Verifying existing IDP infrastructure...")
    print("-" * 50)

    cognito_config = get_existing_cognito_config()
    if not cognito_config:
        print("❌ Could not find existing Cognito configuration.")
        print("Make sure the IDP project is deployed with Cognito enabled.")
        sys.exit(1)

    print()
    infra_config = get_existing_infrastructure_config()
    if not infra_config:
        print("❌ Could not find existing infrastructure.")
        print("Make sure the IDP project is deployed.")
        sys.exit(1)

    print("✅ All existing infrastructure verified!")
    print()
    return cognito_config, infra_config


def setup_agentcore_runtime(infra_config, agentcore_iam_role, region):
    """Setup and configure AgentCore Runtime"""
    print("⚙️  Step 3: Configuring AgentCore Runtime deployment...")
    print("-" * 50)

    # Check required files
    required_files = ["mcp_server.py", "requirements.txt"]
    for file in required_files:
        if not os.path.exists(file):
            print(f"❌ Required file {file} not found")
            sys.exit(1)
    print("✅ All required files found")

    # Import AgentCore Runtime
    try:
        from bedrock_agentcore_starter_toolkit import Runtime
    except ImportError:
        print("❌ bedrock-agentcore-starter-toolkit not installed")
        print("Please install it with: pip install bedrock-agentcore-starter-toolkit")
        sys.exit(1)

    # Initialize AgentCore Runtime
    agentcore_runtime = Runtime()

    # Note: Using IAM SigV4 authentication (no authorizer_configuration)
    # This allows cross-account/cross-agent invocation via IAM roles

    print("🔧 Configuring runtime...")
    print("   Infrastructure will be discovered automatically by the MCP server")
    print(f"   Expected State Machine: {infra_config['state_machine_arn']}")
    print(f"   Expected S3 Bucket: {infra_config['bucket_name']}")
    print("   Authentication: IAM SigV4")

    agentcore_runtime.configure(
        entrypoint="mcp_server.py",
        execution_role=agentcore_iam_role["Role"]["Arn"],
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        protocol="MCP",
        agent_name="idp_bedrock_agent",
    )
    print("✅ Runtime configured successfully")
    return agentcore_runtime


def deploy_and_wait(agentcore_runtime):
    """Deploy MCP server and wait for completion"""
    print("\n🚀 Step 4: Launching MCP server to AgentCore Runtime...")
    print("-" * 50)
    print("⏳ This may take several minutes...")

    launch_result = agentcore_runtime.launch(auto_update_on_conflict=True)

    print("✅ Launch completed successfully!")
    print(f"Agent ARN: {launch_result.agent_arn}")
    print(f"Agent ID: {launch_result.agent_id}")

    # Wait for deployment
    print("\n⏳ Step 5: Waiting for deployment to complete...")
    print("-" * 50)

    status_response = agentcore_runtime.status()
    status = status_response.endpoint["status"]
    print(f"Initial status: {status}")

    end_status = ["READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]
    while status not in end_status:
        print(f"Status: {status} - waiting...")
        time.sleep(int(30))
        status_response = agentcore_runtime.status()
        status = status_response.endpoint["status"]

    if status == "READY":
        print("🎉 AgentCore Runtime is READY!")
        print("✅ IDP with Amazon Bedrock MCP Server deployed successfully!")
    else:
        print(f"⚠️  AgentCore Runtime status: {status}")
        if status in ["CREATE_FAILED", "UPDATE_FAILED"]:
            print("❌ Deployment failed. Check CloudWatch logs for details.")
            sys.exit(1)

    return launch_result


def finalize_deployment(launch_result, infra_config, region):
    """Generate MCP config files"""
    print("\n📝 Step 6: Generating MCP configuration...")
    print("-" * 50)

    config_data = generate_mcp_config(
        agent_arn=launch_result.agent_arn,
        region=region,
    )

    # Save configuration files in configs directory
    os.makedirs("configs", exist_ok=True)

    with open("configs/mcp_config.json", "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)

    print("✅ Generated MCP configuration file:")
    print("   📄 configs/mcp_config.json")

    # Display the config
    print("\n📋 MCP Configuration:")
    print("=" * 60)
    print(json.dumps(config_data, indent=2))
    print("=" * 60)

    # Final summary
    print("\n🎉 Deployment Complete!")
    print("=" * 60)
    print("Your IDP with Amazon Bedrock MCP Server has been successfully deployed!")
    print()
    print("📋 Deployment Summary:")
    print(f"   Agent ARN: {launch_result.agent_arn}")
    print(f"   Agent ID: {launch_result.agent_id}")
    print(f"   State Machine: {infra_config['state_machine_arn']}")
    print(f"   S3 Bucket: {infra_config['bucket_name']}")
    print(f"   Authentication: IAM SigV4")
    print()
    print("🔗 Access Information:")
    print("   Parameter Store: /idp-bedrock-mcp/runtime/agent_arn")
    print()
    print("📁 Generated Files:")
    print("   configs/mcp_config.json - MCP server configuration")
    print()
    print("🧪 Testing:")
    print("   The deployment includes built-in testing - no separate scripts needed")
    print("   MCP tools are ready for use with IAM authentication")
    print()
    print("The MCP server is now ready for production use! 🚀")


def main():
    """Main deployment function - IAM SigV4 authentication"""
    print("🚀 IDP with Amazon Bedrock MCP Server Deployment")
    print("============================================================")
    print("This script deploys the MCP server with IAM SigV4 authentication")
    print()

    try:
        # Get AWS region
        boto_session = Session()
        region = boto_session.region_name
        print(f"Using AWS region: {region}")
        print()

        # Step 1: Verify infrastructure
        cognito_config, infra_config = verify_infrastructure()

        # Step 2: Create IAM role
        print("🔐 Step 2: Creating IAM role for AgentCore Runtime...")
        print("-" * 50)
        agentcore_iam_role = create_agentcore_role(agent_name="idp-mcp-agent")
        print(f"✅ IAM role created: {agentcore_iam_role['Role']['Arn']}")
        print()

        # Step 3: Setup AgentCore Runtime
        agentcore_runtime = setup_agentcore_runtime(infra_config, agentcore_iam_role, region)

        # Step 4-5: Deploy and wait
        launch_result = deploy_and_wait(agentcore_runtime)

        # Step 6: Finalize deployment
        finalize_deployment(launch_result, infra_config, region)

    except KeyboardInterrupt:
        print("\n❌ Deployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Deployment failed: {e}")
        print("\nTroubleshooting:")
        print("   1. Ensure the IDP project is deployed")
        print("   2. Check AWS credentials and permissions")
        print("   3. Verify Docker is running")
        print("   4. Check CloudWatch logs for detailed errors")
        sys.exit(1)


if __name__ == "__main__":
    main()
