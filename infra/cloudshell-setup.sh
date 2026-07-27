#!/usr/bin/env bash
# GenQ_AWS one-shot AWS setup - paste this WHOLE file into AWS CloudShell
# (console top-right toolbar -> CloudShell icon, region us-west-2) and
# press Enter. No local AWS CLI or repo clone needed - the template is
# inlined below and CloudShell already has your console credentials.
#
# Creates: ECR repo "genq-aws" + 4 IAM roles (GitHub Actions deploy role,
# ECS task-execution role, the app's Bedrock task role - no API keys -,
# and the ECS infrastructure role). Idempotent: safe to re-run.
set -euo pipefail

REGION=us-west-2
STACK=genq-aws-setup

echo "==> Checking for an existing GitHub OIDC provider..."
if aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text     | grep -q token.actions.githubusercontent.com; then
  CREATE_OIDC=false
  echo "    found one - reusing it (CreateOIDCProvider=false)"
else
  CREATE_OIDC=true
  echo "    none found - the stack will create it"
fi

echo "==> Writing CloudFormation template to /tmp..."
cat > /tmp/genq-aws-setup.yaml <<'TEMPLATE'
# One-time AWS setup for GenQ_AWS on ECS Express Mode.
#
# Upload this in the CloudFormation console (us-west-2) -> Create stack ->
# "Upload a template file". It creates everything the GitHub Actions deploy
# pipeline needs:
#   - ECR repository (genq-aws)
#   - GitHub OIDC provider (keyless auth for GitHub Actions)
#   - genq-github-actions-role   (assumed by the workflow: push image, deploy)
#   - genq-task-execution-role   (pull image, write logs)
#   - genq-task-role             (THE APP'S ROLE: bedrock:InvokeModel — no API keys)
#   - genq-ecs-infrastructure-role (lets ECS create the ALB/networking)
#
# If your account ALREADY has the GitHub OIDC provider
# (IAM -> Identity providers -> token.actions.githubusercontent.com),
# set CreateOIDCProvider=false when creating the stack.
AWSTemplateFormatVersion: "2010-09-09"
Description: GenQ_AWS - ECR + IAM roles for GitHub Actions deploys to ECS Express Mode (Bedrock via task role)

Parameters:
  GitHubRepo:
    Type: String
    Default: venkaat92-Rock/GenQ_AWS
    Description: GitHub org/repo allowed to assume the deploy role
  CreateOIDCProvider:
    Type: String
    Default: "true"
    AllowedValues: ["true", "false"]
    Description: Set false if token.actions.githubusercontent.com already exists in IAM

Conditions:
  MakeOIDC: !Equals [!Ref CreateOIDCProvider, "true"]

Resources:
  EcrRepo:
    Type: AWS::ECR::Repository
    Properties:
      RepositoryName: genq-aws
      EmptyOnDelete: true

  GitHubOIDC:
    Type: AWS::IAM::OIDCProvider
    Condition: MakeOIDC
    Properties:
      Url: https://token.actions.githubusercontent.com
      ClientIdList: [sts.amazonaws.com]
      ThumbprintList: [6938fd4d98bab03faadb97b34396831e3780aea1]

  GitHubActionsRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: genq-github-actions-role
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Federated: !If
                - MakeOIDC
                - !Ref GitHubOIDC
                - !Sub arn:aws:iam::${AWS::AccountId}:oidc-provider/token.actions.githubusercontent.com
            Action: sts:AssumeRoleWithWebIdentity
            Condition:
              StringEquals:
                token.actions.githubusercontent.com:aud: sts.amazonaws.com
              StringLike:
                token.actions.githubusercontent.com:sub: !Sub repo:${GitHubRepo}:*
      Policies:
        - PolicyName: deploy-genq-express
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action: [ecr:GetAuthorizationToken]
                Resource: "*"
              - Effect: Allow
                Action:
                  - ecr:BatchCheckLayerAvailability
                  - ecr:BatchGetImage
                  - ecr:GetDownloadUrlForLayer
                  - ecr:PutImage
                  - ecr:InitiateLayerUpload
                  - ecr:UploadLayerPart
                  - ecr:CompleteLayerUpload
                Resource: !GetAtt EcrRepo.Arn
              - Effect: Allow
                Action:
                  - ecs:CreateCluster
                  - ecs:RegisterTaskDefinition
                  - ecs:CreateExpressGatewayService
                  - ecs:UpdateExpressGatewayService
                  - ecs:DescribeExpressGatewayService
                  - ecs:DescribeClusters
                  - ecs:DescribeServices
                  - ecs:DescribeTaskDefinition
                  - ecs:ListServiceDeployments
                  - ecs:DescribeServiceDeployments
                  - ecs:TagResource
                  - ecs:UntagResource
                Resource: "*"
              - Effect: Allow
                Action: [iam:PassRole]
                Resource:
                  - !GetAtt TaskExecutionRole.Arn
                  - !GetAtt TaskRole.Arn
                  - !GetAtt InfrastructureRole.Arn

  TaskExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: genq-task-execution-role
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: {Service: ecs-tasks.amazonaws.com}
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

  # The application's own credentials: Bedrock via IAM, no API keys anywhere.
  TaskRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: genq-task-role
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: {Service: ecs-tasks.amazonaws.com}
            Action: sts:AssumeRole
      Policies:
        - PolicyName: bedrock-invoke
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - bedrock:InvokeModel
                  - bedrock:InvokeModelWithResponseStream
                Resource: "*"

  InfrastructureRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: genq-ecs-infrastructure-role
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: {Service: ecs.amazonaws.com}
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AmazonECSInfrastructureRoleforExpressGatewayServices

Outputs:
  EcrRepositoryUri:
    Value: !GetAtt EcrRepo.RepositoryUri
  GitHubActionsRoleArn:
    Value: !GetAtt GitHubActionsRole.Arn
  TaskRoleArn:
    Value: !GetAtt TaskRole.Arn
  AccountId:
    Description: Set this as the AWS_ACCOUNT_ID variable in the GitHub repo
    Value: !Ref AWS::AccountId
TEMPLATE

echo "==> Deploying stack $STACK in $REGION (takes ~2 min)..."
aws cloudformation deploy   --region "$REGION"   --stack-name "$STACK"   --template-file /tmp/genq-aws-setup.yaml   --capabilities CAPABILITY_NAMED_IAM   --parameter-overrides "CreateOIDCProvider=$CREATE_OIDC"

echo
echo "==> Stack outputs:"
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK"   --query 'Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}' --output table

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo
echo "=================================================================="
echo "  AWS setup complete."
echo
echo "  YOUR ACCOUNT ID:  $ACCOUNT_ID"
echo
echo "  Final two steps happen on GitHub, not AWS:"
echo "  1. github.com/venkaat92-Rock/GenQ_AWS -> Settings -> Secrets and"
echo "     variables -> Actions -> Variables tab -> New repository variable:"
echo "         Name:  AWS_ACCOUNT_ID     Value: $ACCOUNT_ID"
echo "  2. Repo -> Actions tab -> 'Deploy to AWS (ECS Express)' ->"
echo "     Run workflow.  (~10 min; the deploy step prints the service URL)"
echo "=================================================================="
