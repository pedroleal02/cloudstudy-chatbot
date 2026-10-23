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

# Slot para o ARN do Agent Runtime.
# Cole o ARN copiado do console do AgentCore na string abaixo ou configure a variável de ambiente AGENT_RUNTIME_ARN na Lambda.
AGENT_RUNTIME_ARN = os.environ.get(
    "AGENT_RUNTIME_ARN", 
    "arn:aws:bedrock-agentcore:us-east-1:427713164331:runtime/tutor_chatbot_runtime-ejxnxj66rT"
)

def lambda_handler(event, context):
    # 1. Parsing do corpo da requisição
    try:
        if "body" in event and event["body"] is not None:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event
    except (json.JSONDecodeError, TypeError):
        body = {}

    question = body.get("question") or body.get("mensagem")
    session_id = body.get("sessionId") or str(uuid.uuid4())

    # 2. Validação da entrada
    if not question or not isinstance(question, str) or not question.strip():
        return {
            "statusCode": 400,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
            },
            "body": json.dumps({
                "status": "error", 
                "message": "A pergunta é inválida ou está vazia."
            }, ensure_ascii=False)
        }

    user_text = question.strip()

    try:
        # 3. Formatação do payload para o modelo Strands/AgentCore
        payload = json.dumps({
            "prompt": user_text,
            "user_message": user_text
        }).encode("utf-8")

        # 4. Invocação usando o parâmetro obrigatório agentRuntimeArn
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=payload
        )

        # 5. Leitura da resposta retornada pelo AgentCore
        response_body = json.loads(response["response"].read().decode("utf-8"))
        
        # O modelo Strands do AgentCore retorna a mensagem dentro de 'result' ou 'completion'
        if isinstance(response_body, dict):
            bot_reply = (
                response_body.get("result", {}).get("message") or 
                response_body.get("completion") or 
                response_body.get("response") or 
                str(response_body)
            )
        else:
            bot_reply = str(response_body)

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
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
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
            },
            "body": json.dumps({
                "status": "error",
                "message": f"Erro no Amazon Bedrock AgentCore ({error_code}): {error_msg}"
            }, ensure_ascii=False)
        }

    except Exception as e:
        print(f"[ERRO INESPERADO] {str(e)}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
            },
            "body": json.dumps({
                "status": "error",
                "message": f"Erro interno no processamento: {str(e)}"
            }, ensure_ascii=False)
        }