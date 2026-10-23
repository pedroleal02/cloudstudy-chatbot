import json

def gerar_resposta(message):
    return f"Você disse: {message}"

def response(status, payload):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }

def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "JSON inválido"})

    message = (body.get("message") or "").strip()
    if not message:
        return response(400, {"error": "O campo 'message' é obrigatório"})

    try:
        reply = gerar_resposta(message)  # sua lógica atual do chatbot
        return response(200, {"reply": reply})
    except Exception as e:
        print("Erro:", e)  # vai para o CloudWatch
        return response(500, {"error": "Erro interno ao gerar resposta"})