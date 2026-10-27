import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODEL_ID = os.environ["MODEL_ID"]

# cliente criado fora do handler: é reaproveitado entre invocações
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=os.environ.get("AWS_REGION"),
    config=Config(read_timeout=25, retries={"max_attempts": 2, "mode": "standard"}),
)

SYSTEM_PROMPT = """Você é um tutor paciente e didático.
- Responda sempre em português do Brasil.
- Explique passo a passo, com linguagem simples e um exemplo curto.
- Se a pergunta for ambígua, peça esclarecimento antes de responder.
- Se não souber a resposta, diga que não sabe; nunca invente.
- Responda no máximo em 3 parágrafos."""


def resposta(status, corpo):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(corpo, ensure_ascii=False),
    }


def extrair_pergunta(event):
    # aceita teste direto no console ({"pergunta": ...}) e Function URL/API Gateway (body em string)
    corpo = event.get("body", event)
    if isinstance(corpo, str):
        corpo = json.loads(corpo)
    if not isinstance(corpo, dict):
        raise ValueError("corpo inválido")
    return (corpo.get("pergunta") or "").strip()


def lambda_handler(event, context):
    try:
        pergunta = extrair_pergunta(event)
    except (ValueError, TypeError):
        return resposta(400, {"erro": "Envie um JSON no formato {\"pergunta\": \"...\"}"})

    if not pergunta:
        return resposta(400, {"erro": "A pergunta não pode estar vazia."})
    if len(pergunta) > 2000:
        return resposta(400, {"erro": "A pergunta é longa demais (máx. 2000 caracteres)."})

    try:
        r = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": pergunta}]}],
            inferenceConfig={"maxTokens": 600, "temperature": 0.3},
        )
        blocos = r["output"]["message"]["content"]
        texto = "".join(b.get("text", "") for b in blocos).strip()
        return resposta(200, {"resposta": texto})

    except ClientError as erro:
        codigo = erro.response["Error"]["Code"]
        logger.error("Erro do Bedrock: %s | %s", codigo, erro)

        if codigo == "ThrottlingException":
            return resposta(429, {"erro": "Muitas requisições. Tente novamente em instantes."})
        if codigo == "ModelTimeoutException":
            return resposta(504, {"erro": "O modelo demorou demais para responder."})
        # AccessDenied, Validation etc. são problemas de configuração: não vazam para o usuário
        return resposta(500, {"erro": "Erro interno ao consultar o modelo."})

    except BotoCoreError as erro:
        logger.error("Erro de conexão/SDK: %s", erro)
        return resposta(502, {"erro": "Não foi possível falar com o serviço de IA."})