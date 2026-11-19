# Documentos no frontend

Dentro de cada notebook aparece o painel **Documentos**: enviar (PDF, `.md`, `.txt`, até 50 MB), listar com o estado de cada arquivo e excluir.

## Como funciona

1. O navegador valida o arquivo (tipo, vazio, 50 MB, nome até 255 caracteres) antes de chamar a API.
2. `POST /notebooks/{id}/documentos/upload` registra o documento (PENDENTE) e devolve o POST pré-assinado.
3. O navegador envia o arquivo **direto ao S3** (sem o token do Cognito; a autorização está nos campos assinados).
4. `POST .../{documentoId}/confirmar` confere o arquivo e inicia a indexação.
5. Enquanto houver documento em ENVIADO ou INDEXANDO, a lista é consultada de novo (3 s, 4 s, 6 s, 8 s, 10 s e depois 15 s), só com a aba visível, e pausa após 60 consultas ("Atualizar" retoma).

| Estado | Mostrado como |
|---|---|
| PENDENTE | Envio incompleto (pode excluir) |
| ENVIADO | Aguardando indexação |
| INDEXANDO | Indexando… |
| PRONTO | Pronto |
| ERRO | Erro ao indexar |

Se o envio ao S3 ou a confirmação falhar, o registro criado é excluído e o arquivo aparece no aviso de erro. A exclusão pede um segundo clique ("Confirmar exclusão") em até 4 s.

## Passo obrigatório no S3: CORS do bucket

O navegador só consegue enviar para o S3 se o bucket permitir a origem do site. Sem isso o envio falha com "Não foi possível enviar o arquivo". É gratuito. S3 → bucket → **Permissions** → **Cross-origin resource sharing (CORS)**:

```json
[
  {
    "AllowedOrigins": ["https://pedroleal02.github.io", "http://localhost:5500", "http://127.0.0.1:5500"],
    "AllowedMethods": ["POST"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": [],
    "MaxAgeSeconds": 3000
  }
]
```

Ajuste as origens se o seu site ou a porta do Live Server forem outros (origem = esquema + domínio, sem caminho).

## Custo

Nenhum serviço novo. Cada consulta da lista chama o API Gateway e a Lambda (e a Lambda consulta o estado da indexação). O intervalo crescente e a pausa mantêm isso baixo. Cada documento indexado continua gerando o custo de embeddings do Bedrock, como já acontecia no commit 9.

## Testar

```bash
node --test frontend/*.test.js
```

Manual (com o login real):

1. Abra um notebook sem documentos: o painel abre sozinho com a mensagem de vazio.
2. Envie um `.txt` pequeno: Preparando envio → Enviando → Confirmando → Indexando → Pronto (sem recarregar a página).
3. Envie um `.exe` e um arquivo vazio: recusados no navegador, com o motivo.
4. Exclua um documento (dois cliques) e confira que ele some do S3 e do DynamoDB.
5. Troque de notebook durante a indexação: o painel mostra só os documentos do notebook aberto.
6. Com duas contas, confira que uma não vê os documentos da outra.
7. Pergunte no chat sobre o conteúdo do documento que está Pronto.
