Pergunta válida (deve dar 200 com a resposta do modelo):

curl -i -X POST "https://w5rd9nyes0.execute-api.us-east-1.amazonaws.com/chat" \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "O que é uma variável em programação?"}'

Pergunta vazia (deve dar 400):

curl -i -X POST "https://w5rd9nyes0.execute-api.us-east-1.amazonaws.com/chat" \
  -H "Content-Type: application/json" \
  -d '{"pergunta": ""}'

JSON quebrado (deve dar 400):

curl -i -X POST "https://w5rd9nyes0.execute-api.us-east-1.amazonaws.com/chat" \
  -H "Content-Type: application/json" \
  -d '{pergunta: sem aspas'

Rota errada (deve dar 404):

curl -i "https://w5rd9nyes0.execute-api.us-east-1.amazonaws.com/chat"

Pré-verificação do CORS (deve dar 204 com cabeçalhos access-control-allow-*):

curl -i -X OPTIONS "https://w5rd9nyes0.execute-api.us-east-1.amazonaws.com/chat" \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"