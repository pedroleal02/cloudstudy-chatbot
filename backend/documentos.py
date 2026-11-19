"""
Documentos dos usuários (commits 7 e 8): envio seguro ao S3 e registro no DynamoDB.

Fluxo de um documento:

  1. POST .../documentos/upload   -> registra o documento (status PENDENTE) e devolve um POST
                                     pré-assinado. O arquivo vai direto do navegador ao S3.
  2. POST .../{documentoId}/confirmar -> confere no S3 que o arquivo chegou e passa o status
                                     para ENVIADO (depois, PRONTO/ERRO virão da Knowledge Base).
  3. GET  .../documentos          -> lista os documentos do notebook.
  4. DELETE .../{documentoId}     -> apaga o arquivo no S3 e o registro.

Propriedade: todo acesso começa pela partição do usuário do token (USER#<sub>) e pelo
notebook. Documento de outro usuário, de outro notebook ou inexistente: sempre 404.

O arquivo é gravado em usuarios/<sub>/<notebook>/<documento>.<tipo>; o nome original fica
só no registro. O envio usa uma política assinada que fixa chave, tipo de conteúdo e o
tamanho exato declarado.
"""
import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import modelos
import notebooks

logger = logging.getLogger()

EXPIRA_EM_SEGUNDOS = 300
LOTE_EXCLUSAO_S3 = 1000  # limite do DeleteObjects

s3 = None  # criado na primeira chamada; os testes substituem por um cliente falso
agente = None  # bedrock-agent (sincronização da Knowledge Base); idem


class DocumentoNaoEncontrado(notebooks.NaoEncontrado):
    """O documento não existe neste notebook do usuário (inclui documentos de outras pessoas)."""


class ArquivoNaoEnviado(Exception):
    """Confirmação pedida, mas o arquivo ainda não está no S3."""


def _s3():
    global s3
    if s3 is None:
        regiao = os.environ.get("AWS_REGION")
        s3 = boto3.client(
            "s3",
            region_name=regiao,
            # endpoint regional: evita redirecionamentos que quebram o POST do navegador
            endpoint_url=f"https://s3.{regiao}.amazonaws.com" if regiao else None,
            config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5,
                          retries={"max_attempts": 2, "mode": "standard"}),
        )
    return s3


def _agente():
    global agente
    if agente is None:
        agente = boto3.client(
            "bedrock-agent",
            region_name=os.environ.get("AWS_REGION"),
            config=Config(connect_timeout=3, read_timeout=8, retries={"max_attempts": 2, "mode": "standard"}),
        )
    return agente


def _config_kb():
    kb, fonte = os.environ.get("KB_ID"), os.environ.get("DATA_SOURCE_ID")
    if not kb or not fonte:
        raise RuntimeError("Variáveis KB_ID e DATA_SOURCE_ID não configuradas.")
    return kb, fonte


def _bucket():
    nome = os.environ.get("DOCS_BUCKET")
    if not nome:
        raise RuntimeError("Variável de ambiente DOCS_BUCKET não configurada.")
    return nome


def _tabela():
    return notebooks._tabela()  # a mesma tabela dos notebooks


def _chaves(usuario_id, notebook_id, documento_id):
    pk = modelos.pk_usuario(usuario_id)
    try:
        sk = modelos.sk_documento(notebook_id, documento_id)
    except modelos.DadoInvalido:
        raise DocumentoNaoEncontrado()  # ID malformado = inexistente
    return pk, sk


def _ler(usuario_id, notebook_id, documento_id):
    notebooks.obter(usuario_id, notebook_id)  # NaoEncontrado: notebook inexistente ou de outro usuário
    pk, sk = _chaves(usuario_id, notebook_id, documento_id)
    item = _tabela().get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")
    if not item or not modelos.pertence_ao_usuario(item, usuario_id):
        raise DocumentoNaoEncontrado()
    return item


def _chave_do_usuario(item, usuario_id):
    """Defesa em profundidade: só apaga arquivos dentro da pasta do próprio usuário."""
    chave = item.get("chaveS3")
    if not isinstance(chave, str) or not chave.startswith(f"{modelos.PREFIXO_S3_USUARIOS}{usuario_id}/"):
        raise RuntimeError("Registro de documento com chave de S3 fora da pasta do usuário.")
    return chave


# ---------- operações ----------


def solicitar_upload(usuario_id, notebook_id, corpo):
    if not isinstance(corpo, dict):
        raise modelos.DadoInvalido("Envie um JSON com o nome e o tamanho do arquivo.")
    nome, tipo = modelos.validar_arquivo(corpo.get("nome"), corpo.get("tamanhoBytes"))
    tamanho = corpo["tamanhoBytes"]

    notebooks.obter(usuario_id, notebook_id)  # NaoEncontrado se não existir ou não for do usuário

    documento_id = modelos.novo_id()
    chave = modelos.chave_s3(usuario_id, notebook_id, documento_id, tipo)
    tipo_conteudo = modelos.TIPOS_DE_CONTEUDO[tipo]
    item = modelos.novo_documento(usuario_id, notebook_id, nome, tamanho, chave, documento_id=documento_id)

    post = _s3().generate_presigned_post(
        Bucket=_bucket(),
        Key=chave,
        Fields={"Content-Type": tipo_conteudo},
        Conditions=[
            {"Content-Type": tipo_conteudo},
            ["content-length-range", tamanho, tamanho],  # tamanho exato: não dá para declarar 1 KB e enviar 50 MB
        ],
        ExpiresIn=EXPIRA_EM_SEGUNDOS,
    )
    # só registra depois de a assinatura dar certo (nada de registro sem URL)
    _tabela().put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    logger.info("Upload autorizado: usuario=%s tipo=%s tamanho=%s", usuario_id[:8], tipo, tamanho)

    return {
        "documento": modelos.para_api(item),
        # o navegador envia um POST multipart com todos os `campos` e, por último, o campo `file`
        "upload": {"url": post["url"], "campos": post["fields"], "expiraEmSegundos": EXPIRA_EM_SEGUNDOS},
    }


def nomes_por_id(usuario_id, notebook_id):
    """{documentoId: nome original} dos documentos do notebook (para citar a fonte pelo nome)."""
    itens = notebooks._consultar(modelos.pk_usuario(usuario_id), modelos.prefixo_documentos(notebook_id))
    return {i["documentoId"]: i["nome"] for i in itens}


# ---------- sincronização com a Knowledge Base ----------


def _iniciar_indexacao():
    """Dispara a sincronização. Devolve o ID do job, ou None se já há uma em andamento."""
    kb, fonte = _config_kb()
    try:
        r = _agente().start_ingestion_job(knowledgeBaseId=kb, dataSourceId=fonte)
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") == "ConflictException":
            return None
        raise
    return r["ingestionJob"]["ingestionJobId"]


def _indexar_sem_falhar():
    """Depois de apagar arquivos: a próxima sincronização tira os trechos do índice. Não bloqueia a exclusão."""
    try:
        _iniciar_indexacao()
    except Exception:
        logger.warning("Não foi possível iniciar a sincronização da Knowledge Base.", exc_info=True)


def _resultado_do_job(job_id):
    """'PRONTO', 'ERRO' ou None (ainda em andamento)."""
    kb, fonte = _config_kb()
    job = _agente().get_ingestion_job(knowledgeBaseId=kb, dataSourceId=fonte, ingestionJobId=job_id)["ingestionJob"]
    estado = job.get("status")
    if estado == "COMPLETE":
        return "ERRO" if (job.get("statistics") or {}).get("numberOfDocumentsFailed") else "PRONTO"
    if estado in ("FAILED", "STOPPED"):
        return "ERRO"
    return None


def _gravar_estado(usuario_id, item, status, job_id=None):
    nomes = {"#status": "status", "#atualizado": "atualizadoEm"}
    valores = {":novo": status, ":agora": modelos.agora_iso()}
    expressao = "SET #status = :novo, #atualizado = :agora"
    if job_id is not None:
        nomes["#job"] = "ingestionJobId"
        valores[":job"] = job_id
        expressao += ", #job = :job"
    try:
        r = _tabela().update_item(
            Key={"pk": modelos.pk_usuario(usuario_id), "sk": item["sk"]},
            UpdateExpression=expressao,
            ConditionExpression="attribute_exists(pk)",  # não recria um documento apagado no meio do caminho
            ExpressionAttributeNames=nomes,
            ExpressionAttributeValues=valores,
            ReturnValues="ALL_NEW",
        )
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise DocumentoNaoEncontrado()
        raise
    return r["Attributes"]


def _atualizar_estados(usuario_id, itens):
    """ENVIADO tenta iniciar a indexação; INDEXANDO confere o job. Um job vale para vários documentos."""
    resultados_dos_jobs = {}
    novo_job, tentou = None, False
    atualizados = []
    for item in itens:
        try:
            if item.get("status") == "ENVIADO":
                if not tentou:
                    tentou, novo_job = True, _iniciar_indexacao()
                if novo_job:
                    item = _gravar_estado(usuario_id, item, "INDEXANDO", novo_job)
            elif item.get("status") == "INDEXANDO":
                job_id = item.get("ingestionJobId")
                if job_id not in resultados_dos_jobs:
                    resultados_dos_jobs[job_id] = _resultado_do_job(job_id)
                if resultados_dos_jobs[job_id]:
                    item = _gravar_estado(usuario_id, item, resultados_dos_jobs[job_id])
        except DocumentoNaoEncontrado:
            continue  # apagado agora há pouco
        atualizados.append(item)
    return atualizados


# ---------- operações ----------


def listar(usuario_id, notebook_id):
    notebooks.obter(usuario_id, notebook_id)
    itens = notebooks._consultar(modelos.pk_usuario(usuario_id), modelos.prefixo_documentos(notebook_id))
    try:
        itens = _atualizar_estados(usuario_id, itens)
    except Exception:  # a listagem não pode quebrar por causa da Knowledge Base
        logger.warning("Não foi possível atualizar o estado de indexação.", exc_info=True)
    return [modelos.para_api(i) for i in itens]


def confirmar(usuario_id, notebook_id, documento_id):
    """Confere o arquivo no S3, grava os metadados e dispara a indexação (idempotente)."""
    item = _ler(usuario_id, notebook_id, documento_id)
    if item.get("status") != "PENDENTE":
        return modelos.para_api(item)

    chave = _chave_do_usuario(item, usuario_id)
    try:
        _s3().head_object(Bucket=_bucket(), Key=chave)
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise ArquivoNaoEnviado()
        raise

    # Os metadados vão ANTES da indexação: é por eles que a busca separa usuários e notebooks.
    # Quem grava são só a Lambda: a política do upload não permite criar outra chave.
    _s3().put_object(
        Bucket=_bucket(),
        Key=modelos.chave_metadados(chave),
        Body=json.dumps(modelos.metadados_kb(usuario_id, notebook_id, documento_id)).encode("utf-8"),
        ContentType="application/json",
    )
    job_id = _iniciar_indexacao()  # None: já há uma sincronização em andamento; a listagem tenta de novo
    item = _gravar_estado(usuario_id, item, "INDEXANDO" if job_id else "ENVIADO", job_id)
    return modelos.para_api(item)


def _apagar_do_s3(chaves):
    for inicio in range(0, len(chaves), LOTE_EXCLUSAO_S3):
        lote = chaves[inicio:inicio + LOTE_EXCLUSAO_S3]
        r = _s3().delete_objects(Bucket=_bucket(), Delete={"Objects": [{"Key": k} for k in lote], "Quiet": True})
        if r.get("Errors"):
            raise RuntimeError(f"Falha ao apagar {len(r['Errors'])} arquivo(s) do S3.")


def excluir(usuario_id, notebook_id, documento_id):
    item = _ler(usuario_id, notebook_id, documento_id)
    chave = _chave_do_usuario(item, usuario_id)
    # arquivos primeiro: se falhar, o registro continua e a exclusão pode ser repetida
    _apagar_do_s3([chave, modelos.chave_metadados(chave)])
    pk, sk = _chaves(usuario_id, notebook_id, documento_id)
    _tabela().delete_item(Key={"pk": pk, "sk": sk})
    _indexar_sem_falhar()


def apagar_todos(usuario_id, notebook_id):
    """Apaga os arquivos, os metadados e os registros de todos os documentos de um notebook."""
    pk = modelos.pk_usuario(usuario_id)
    prefixo = modelos.prefixo_documentos(notebook_id)
    itens = notebooks._consultar(pk, prefixo)

    chaves = []
    for i in itens:
        chave = _chave_do_usuario(i, usuario_id)
        chaves += [chave, modelos.chave_metadados(chave)]
    _apagar_do_s3(chaves)
    notebooks._apagar_prefixo(pk, prefixo)
    if chaves:
        _indexar_sem_falhar()
