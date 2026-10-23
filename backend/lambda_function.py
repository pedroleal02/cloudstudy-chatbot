import json
import os
import boto3
from botocore.exceptions import ClientError

# Inicializa o cliente direto do Bedrock Runtime (não precisa de ARN de agente)
bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

# Modelo padrão (Claude 3 Haiku)
MODEL_ID = "us.anthropic.claude-3-haiku-20240307-v1:0"

def lambda_handler(event, context):
    try:
        if "body" in event and event["body"] is not None:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event
    except (json.JSONDecodeError, TypeError):
        body = {}

    question = body.get("question") or body.get("mensagem")

    if not question or not isinstance(question, str) or not question.strip():
        return {
            "statusCode": 400,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({"status": "error", "message": "A pergunta é inválida ou está vazia."}, ensure_ascii=False)
        }

    user_text = question.strip()

    try:
        # Chamada direta ao modelo usando a API Converse
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_text}]
                }
            ],
            system=[
                {
                    "text": "Você é um tutor educacional paciente, claro e didático."
                }
            ],
            inferenceConfig={
                "maxTokens": 500,
                "temperature": 0.7
            }
        )

        bot_reply = response["output"]["message"]["content"][0]["text"]

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "status": "success",
                "received_question": user_text,
                "response": bot_reply
            }, ensure_ascii=False)
        }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        print(f"[ERRO BEDROCK] {error_code}: {error_msg}")

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "status": "error",
                "message": f"Erro no Bedrock ({error_code}): {error_msg}"
            }, ensure_ascii=False)
        }