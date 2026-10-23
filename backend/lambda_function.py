import json
import os
import uuid
import boto3
from botocore.exceptions import ClientError

# Inicializa o cliente do Amazon Bedrock AgentCore Runtime
agentcore_client = boto3.client(
    "bedrock-agentcore", 
    region_name=os.environ.get("AWS_REGION", "us-east-1")
)

# ARN do Agent Runtime criado no Amazon Bedrock AgentCore
AGENT_RUNTIME_ARN = os.environ.get(
    "AGENT_RUNTIME_ARN", 
    "arn:aws:bedrock-agentcore:us-east-1:SUA_CONTA_AWS:agent-runtime/SEU_AGENT_RUNTIME_ID"
)

def lambda_handler(event, context):
    try:
        if "body" in event and event["body"] is not None:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event
    except (json.JSONDecodeError, TypeError):
        body = {}

    question = body.get("question") or body.get("mensagem")
    session_id = body.get("sessionId") or str(uuid.uuid4())

    # Validação de entrada
    if not question or not isinstance(question, str) or not question.strip():
        return {
            "statusCode": 400,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "status": "error", 
                "message": "A pergunta é inválida ou está vazia."
            }, ensure_ascii=False)
        }

    user_text = question.strip()

    try:
        payload = json.dumps({
            "prompt": user_text,
            "system_prompt": "Você é um tutor educacional paciente, claro e didático."
        }).encode("utf-8")

        # Invocação incluindo o parâmetro obrigatório 'agentRuntimeArn'
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=payload
        )

        # Leitura do stream/body da resposta
        response_body = json.loads(response["response"].read().decode("utf-8"))
        bot_reply = response_body.get("completion") or response_body.get("response") or "Resposta processada pelo AgentCore."

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "status": "success",
                "sessionId": session_id,
                "received_question": user_text,
                "response": bot_reply
            }, ensure_ascii=False)
        }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        print(f"[ERRO AGENTCORE] {error_code}: {error_msg}")

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "status": "error",
                "message": f"Erro no Amazon Bedrock AgentCore ({error_code}): {error_msg}"
            }, ensure_ascii=False)
        }