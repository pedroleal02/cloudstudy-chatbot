import json

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
            "body": json.dumps({
                "status": "error",
                "message": "A pergunta enviada é inválida ou está vazia."
            }, ensure_ascii=False)
        }

    user_text = question.strip()
    bot_reply = f"Resposta temporária da Lambda: Recebi sua mensagem '{user_text}'. O modelo de IA será conectado em breve!"

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