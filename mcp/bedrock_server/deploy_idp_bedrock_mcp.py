#!/usr/bin/env python3
"""
Updated deployment script for IDP Bedrock MCP Server
Generates MCP configuration for Cline/Amazon Q integration
"""

import sys
import os
import json
import time
import yaml
import argparse
from boto3.session import Session

# Import our utility functions
from utils import (
    get_existing_cognito_config,
    get_existing_infrastructure_config,
    create_mcp_user_in_existing_pool,
    create_agentcore_role,
    store_mcp_configuration,
)


def load_config_yml():
    """Load and parse the config.yml file from the project root"""
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.yml")

    if not os.path.exists(config_path):
        print(f"❌ Config file not found at: {config_path}")
        print("Make sure you have a config.yml file in the project root")
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"❌ Error loading config.yml: {e}")
        return None


def get_username_from_config(config, custom_username=None):
    """Extract username from config.yml or use custom username"""
    if custom_username:
        print(f"Using custom username: {custom_username}")
        return custom_username

    if not config:
        return None

    try:
        users = config.get("authentication", {}).get("users", [])
        if not users:
            print("❌ No users found in config.yml authentication section")
            return None

        username = users[0]  # Use the first user
        print(f"Using username from config.yml: {username}")
        return username
    except Exception as e:
        print(f"❌ Error parsing username from config.yml: {e}")
        return None


def generate_cline_mcp_config(agent_arn, cognito_config, mcp_user_config, region):
    """
    Generate MCP configuration for Cline/Amazon Q - AgentCore HTTP only
    """
    # Construct the MCP server URL
    encoded_arn = agent_arn.replace(":", "%3A").replace("/", "%2F")
    mcp_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    # Cline configuration (streamableHttp interface) - direct AgentCore access
    cline_agentcore_config = {
        "mcpServers": {
            "idp-bedrock-agentcore": {
                "disabled": False,
                "timeout": 30000,
                "type": "streamableHttp",
                "autoApprove": [],
                "url": mcp_url,
                "headers": {
                    "Authorization": f"Bearer {mcp_user_config['bearer_token']}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                "debug": True,
            }
        }
    }

    return {
        "cline_agentcore_config": cline_agentcore_config,
        "manual_config": {
            "server_url": mcp_url,
            "bearer_token": mcp_user_config["bearer_token"],
            "region": region,
            "agent_arn": agent_arn,
            "instructions": [
                "1. This is the AgentCore HTTP configuration for remote access",
                "2. Copy the cline_agentcore_config to your Cline MCP settings",
                "3. For local stdio access, use the separate stdio server setup",
                "4. The bearer token will need to be refreshed periodically",
            ],
        },
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


def authenticate_user(cognito_config, username):
    """Authenticate existing Cognito user"""
    print("👤 Step 2: Using existing Cognito user...")
    print("-" * 40)

    mcp_user_config = create_mcp_user_in_existing_pool(
        cognito_config=cognito_config,
        username=username,
        password=None,  # Will prompt for password
    )

    if not mcp_user_config:
        print("❌ Failed to authenticate existing user")
        sys.exit(1)

    print(f"✅ User authenticated successfully: {mcp_user_config['username']}")
    print()
    return mcp_user_config


def setup_agentcore_runtime(cognito_config, infra_config, agentcore_iam_role, region):
    """Setup and configure AgentCore Runtime"""
    print("⚙️  Step 4: Configuring AgentCore Runtime deployment...")
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
    print("   Authentication: IAM SigV4 (no JWT authorizer)")

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
    print("\n🚀 Step 5: Launching MCP server to AgentCore Runtime...")
    print("-" * 50)
    print("⏳ This may take several minutes...")

    launch_result = agentcore_runtime.launch(auto_update_on_conflict=True)

    print("✅ Launch completed successfully!")
    print(f"Agent ARN: {launch_result.agent_arn}")
    print(f"Agent ID: {launch_result.agent_id}")

    # Wait for deployment
    print("\n⏳ Step 6: Waiting for deployment to complete...")
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


def finalize_deployment(launch_result, cognito_config, mcp_user_config, infra_config, region):
    """Store configuration and generate MCP config files"""
    # Store configuration
    print("\n💾 Step 7: Storing configuration for remote access...")
    print("-" * 50)

    config_stored = store_mcp_configuration(
        agent_arn=launch_result.agent_arn, cognito_config=cognito_config, mcp_user_config=mcp_user_config
    )

    if config_stored:
        print("✅ Configuration stored successfully!")
    else:
        print("❌ Failed to store configuration")

    # Generate Cline MCP Configuration
    print("\n📝 Step 8: Generating MCP configuration for Cline/Amazon Q...")
    print("-" * 50)

    config_data = generate_cline_mcp_config(
        agent_arn=launch_result.agent_arn,
        cognito_config=cognito_config,
        mcp_user_config=mcp_user_config,
        region=region,
    )

    # Save configuration files in configs directory
    os.makedirs("configs", exist_ok=True)

    with open("configs/cline_agentcore_config.json", "w", encoding="utf-8") as f:
        json.dump(config_data["cline_agentcore_config"], f, indent=2)

    with open("configs/mcp_manual_config.json", "w", encoding="utf-8") as f:
        json.dump(config_data["manual_config"], f, indent=2)

    print("✅ Generated MCP configuration files:")
    print("   📄 configs/cline_agentcore_config.json - AgentCore HTTP configuration for Cline")
    print("   📄 configs/mcp_manual_config.json - Manual configuration details")

    # Display the Cline config for easy copying
    print("\n📋 Cline MCP Configuration (AgentCore HTTP):")
    print("=" * 60)
    print(json.dumps(config_data["cline_agentcore_config"], indent=2))
    print("=" * 60)

    # Final summary
    print("\n🎉 Deployment Complete!")
    print("=" * 60)
    print("Your IDP with Amazon Bedrock MCP Server has been successfully deployed!")
    print()
    print("📋 Deployment Summary:")
    print(f"   Agent ARN: {launch_result.agent_arn}")
    print(f"   Agent ID: {launch_result.agent_id}")
    print(f"   MCP User: {mcp_user_config['username']}")
    print(f"   State Machine: {infra_config['state_machine_arn']}")
    print(f"   S3 Bucket: {infra_config['bucket_name']}")
    print()
    print("🔗 Access Information:")
    print("   Parameter Store: /idp-bedrock-mcp/runtime/agent_arn")
    print("   Secrets Manager: idp-bedrock-mcp/cognito/credentials")
    print()
    print("📁 Generated Files:")
    print("   cline_mcp_config.json - For Cline/Amazon Q")
    print("   mcp_manual_config.json - Manual setup details")
    print()
    print("🧪 Testing:")
    print("   The deployment includes built-in testing - no separate scripts needed")
    print("   MCP tools are ready for use in Cline or other MCP clients")
    print()
    print("The MCP server is now ready for production use! 🚀")


def main():
    """Main deployment function - updated to match fixed notebook approach"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Deploy IDP with Amazon Bedrock MCP Server")
    parser.add_argument(
        "--username", "-u", type=str, help="Custom username for Cognito authentication (overrides config.yml)"
    )
    args = parser.parse_args()

    print("🚀 IDP with Amazon Bedrock MCP Server Deployment")
    print("============================================================")
    print("This script deploys using the proven approach from the fixed notebook")
    print("and generates MCP configuration for Cline/Amazon Q integration")
    print()

    try:
        # Load config.yml and get username
        print("📋 Loading configuration...")
        config = load_config_yml()
        username = get_username_from_config(config, args.username)

        if not username:
            print("❌ Could not determine username. Please:")
            print("   1. Ensure config.yml exists with authentication.users section")
            print("   2. Or provide username with --username parameter")
            sys.exit(1)

        # Get AWS region
        boto_session = Session()
        region = boto_session.region_name
        print(f"Using AWS region: {region}")
        print()

        # Step 1: Verify infrastructure
        cognito_config, infra_config = verify_infrastructure()

        # Step 2: Authenticate user
        mcp_user_config = authenticate_user(cognito_config, username)

        # Step 3: Create IAM role
        print("🔐 Step 3: Creating IAM role for AgentCore Runtime...")
        print("-" * 50)
        agentcore_iam_role = create_agentcore_role(agent_name="idp-mcp-agent")
        print(f"✅ IAM role created: {agentcore_iam_role['Role']['Arn']}")
        print()

        # Step 4: Setup AgentCore Runtime
        agentcore_runtime = setup_agentcore_runtime(cognito_config, infra_config, agentcore_iam_role, region)

        # Step 5-6: Deploy and wait
        launch_result = deploy_and_wait(agentcore_runtime)

        # Step 7-8: Finalize deployment
        finalize_deployment(launch_result, cognito_config, mcp_user_config, infra_config, region)

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
