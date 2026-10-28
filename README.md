# Tutor virtual com RAG na AWS

Chatbot de estudos que responde perguntas **com base nos materiais do próprio curso**. As respostas citam os trechos usados (`[1]`, `[2]`...) e o tutor avisa quando os materiais não cobrem o assunto, em vez de inventar.

## Como funciona

1. Os documentos de estudo ficam num bucket **S3** privado e são indexados por uma **Knowledge Base do Amazon Bedrock**.
2. O frontend envia a pergunta para uma **HTTP API** (API Gateway), que aciona uma **Lambda**.
3. A Lambda valida a pergunta, **recupera os trechos mais relevantes** da Knowledge Base e envia trechos + pergunta a um **modelo de linguagem do Bedrock**.
4. O modelo responde apoiado nos trechos; a API devolve a resposta e as **referências** efetivamente citadas.

Diagramas e decisões de projeto: [docs/arquitetura.md](docs/arquitetura.md).

## Estrutura do repositório

> Ajuste os nomes das pastas conforme o seu repositório.

```
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── script.js
│   └── config.js              # URL da API
├── backend/
│   ├── lambda_function.py     # Lambda: validação, RAG e tratamento de erros
│   └── test_lambda_function.py
├── iam/                       # políticas IAM usadas (referência)
├── docs/
│   ├── arquitetura.md
│   ├── testes.md
│   ├── limitacoes.md
│   ├── seguranca.md
│   ├── s3.md
│   ├── knowledge-base.md
│   ├── rag.md
│   └── api.md
└── README.md
```

## Como executar

### Frontend (local)

1. Em `frontend/config.js`, coloque a URL da API (Invoke URL do API Gateway, **sem** `/chat` no final).
2. Sirva a pasta com qualquer servidor estático:
   ```bash
   cd frontend
   python -m http.server 8000
   ```
3. Abra `http://localhost:8000`.

### Backend (AWS)

O backend é criado manualmente no console da AWS, na região `us-east-1`. Ordem recomendada:

1. **S3**: bucket privado com os documentos em `documentos/` ([docs/s3.md](docs/s3.md)).
2. **Knowledge Base**: fonte S3, embeddings Titan Text Embeddings V2, armazenamento S3 Vectors; depois **Sync** ([docs/knowledge-base.md](docs/knowledge-base.md)).
3. **Lambda**: cole `backend/lambda_function.py`, timeout de 30 s, memória de 256 MB, handler `lambda_function.lambda_handler`, e as permissões IAM de [docs/seguranca.md](docs/seguranca.md).
4. **API Gateway**: HTTP API com a rota `POST /chat` integrada à Lambda e CORS configurado ([docs/api.md](docs/api.md)).

#### Variáveis de ambiente da Lambda

| Variável | Obrigatória | Descrição |
|---|---|---|
| `MODEL_ID` | sim | ID do modelo (ou do perfil de inferência) do Bedrock |
| `KB_ID` | sim | ID da Knowledge Base (10 caracteres) |
| `N_RESULTADOS` | não (padrão `4`) | Quantidade de trechos recuperados por pergunta |
| `SCORE_MINIMO` | não (padrão `0`) | Score mínimo para aceitar um trecho; calibrar pelos logs |

### Atualizar os documentos

Ao adicionar, alterar ou apagar arquivos no S3, rode **Sync** na fonte de dados da Knowledge Base. A sincronização não é automática.

## Testes

- **Automatizados** (clientes da AWS simulados, sem custo): `pip install boto3` e `python -m unittest test_lambda_function -v`.
- **Manuais** na API real: cenários e resultados em [docs/testes.md](docs/testes.md).

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/arquitetura.md](docs/arquitetura.md) | Diagramas, serviços AWS e decisões de projeto |
| [docs/testes.md](docs/testes.md) | Testes executados e resultados |
| [docs/limitacoes.md](docs/limitacoes.md) | Limitações conhecidas e próximos passos |
| [docs/seguranca.md](docs/seguranca.md) | Validação, erros, permissões IAM e S3 |

## Limitações em resumo

O tutor não tem memória entre perguntas, a API é pública (protegida só por limite de requisições) e a qualidade das respostas depende dos documentos enviados. Detalhes em [docs/limitacoes.md](docs/limitacoes.md).