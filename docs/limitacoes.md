# Limitações conhecidas

## Qualidade das respostas
- **Sem memória de conversa.** Cada pergunta é independente. Perguntas de seguimento como "e o segundo?" não funcionam, porque a busca só enxerga o texto enviado.
- **Depende dos documentos.** Se o material for curto, vago ou mal escrito, a recuperação e a resposta pioram. Há poucos documentos de teste, então a avaliação de qualidade é limitada.
- **Recuperação semântica imperfeita.** A Knowledge Base quase sempre devolve algum trecho, mesmo para perguntas sem relação. O filtro por `SCORE_MINIMO` e as regras do prompt reduzem o problema, mas não o eliminam, e o valor ideal precisa ser calibrado a cada mudança de documentos.
- **O modelo pode errar mesmo com contexto.** Ele pode interpretar mal um trecho ou citar o número errado. As referências mostram o que o modelo **citou**, e não uma verificação de que a citação está correta.
- **Qualidade em português não medida.** Não houve avaliação formal com um conjunto de perguntas e respostas esperadas.
- **Chunking e parsing padrão.** O parsing padrão extrai texto; tabelas complexas e imagens dentro de PDFs podem se perder. Arquivos acima de 50 MB são ignorados na ingestão.
- **Respostas em texto simples.** O prompt pede texto sem Markdown, e a tela não renderiza formatação. Se o modelo usar Markdown, os símbolos aparecem crus.

## Operação
- **Sincronização manual.** Documentos novos ou alterados só entram na busca depois de um **Sync** da Knowledge Base.
- **Latência.** Cada pergunta faz duas chamadas em sequência (recuperação e geração), e a resposta leva alguns segundos. O limite de 30 s do API Gateway é o teto: acima disso a requisição falha.
- **Região única** (`us-east-1`), sem redundância entre regiões.
- **Observabilidade básica.** Há logs no CloudWatch, mas não há métricas customizadas nem alarmes de erro.
- **Criação manual no console.** A infraestrutura não está em código (IaC); reproduzir o ambiente depende do passo a passo da documentação.

## Segurança
- **API pública.** Não há autenticação: qualquer pessoa com a URL pode fazer perguntas e receber trechos dos documentos no campo `referencias`. A proteção é o limite de requisições do API Gateway (e, se configurado, o limite de concorrência da Lambda). Por isso, **nenhum documento que não possa ser lido por qualquer pessoa deve ir para o bucket.**
- **Risco de custo.** Um abuso ainda consome Bedrock até o limite configurado. Mantenha um alerta de orçamento ativo.
- **Injeção de prompt mitigada, não eliminada.** O escape de tags, as regras do prompt e a instrução de ignorar comandos dentro dos trechos reduzem o risco, mas não o garantem.
- **CORS.** Em desenvolvimento a origem permitida pode ser `*`; antes de publicar, restrinja às origens reais do frontend.

## Testes
- Os testes automatizados cobrem a Lambda com clientes simulados; **não há** testes automatizados do frontend nem testes de integração com a AWS real.
- Os testes manuais dependem de quem os executa e não rodam em integração contínua.

## Próximos passos possíveis
1. Enviar o histórico da conversa para permitir perguntas de seguimento.
2. Autenticação (por exemplo, Amazon Cognito ou chave de API) e restrição de origem no CORS.
3. Sincronização automática da Knowledge Base ao enviar arquivos.
4. Conjunto de avaliação com perguntas e respostas esperadas, para medir a qualidade ao mudar chunking, `N_RESULTADOS` ou o prompt.
5. Renderizar Markdown na tela e mostrar o trecho citado ao passar o mouse sobre a fonte.
6. Infraestrutura como código e integração contínua para rodar os testes a cada commit.
7. Alarmes no CloudWatch para taxa de erro e custo.