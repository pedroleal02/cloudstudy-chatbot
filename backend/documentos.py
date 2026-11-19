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


def listar(usuario_id, notebook_id):
    notebooks.obter(usuario_id, notebook_id)
    itens = notebooks._consultar(modelos.pk_usuario(usuario_id), modelos.prefixo_documentos(notebook_id))
    return [modelos.para_api(i) for i in itens]


def confirmar(usuario_id, notebook_id, documento_id):
    """Confere no S3 que o arquivo chegou e marca o documento como ENVIADO (idempotente)."""
    item = _ler(usuario_id, notebook_id, documento_id)
    if item.get("status") != "PENDENTE":
        return modelos.para_api(item)

    try:
        _s3().head_object(Bucket=_bucket(), Key=_chave_do_usuario(item, usuario_id))
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise ArquivoNaoEnviado()
        raise

    pk, sk = _chaves(usuario_id, notebook_id, documento_id)
    try:
        r = _tabela().update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET #status = :novo, #atualizado = :agora",
            ConditionExpression="attribute_exists(pk)",  # não recria um documento apagado no meio do caminho
            ExpressionAttributeNames={"#status": "status", "#atualizado": "atualizadoEm"},
            ExpressionAttributeValues={":novo": "ENVIADO", ":agora": modelos.agora_iso()},
            ReturnValues="ALL_NEW",
        )
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise DocumentoNaoEncontrado()
        raise
    return modelos.para_api(r["Attributes"])


def excluir(usuario_id, notebook_id, documento_id):
    item = _ler(usuario_id, notebook_id, documento_id)
    # arquivo primeiro: se falhar, o registro continua e a exclusão pode ser repetida
    _s3().delete_object(Bucket=_bucket(), Key=_chave_do_usuario(item, usuario_id))
    pk, sk = _chaves(usuario_id, notebook_id, documento_id)
    _tabela().delete_item(Key={"pk": pk, "sk": sk})


def apagar_todos(usuario_id, notebook_id):
    """Apaga os arquivos e os registros de todos os documentos de um notebook (exclusão em cascata)."""
    pk = modelos.pk_usuario(usuario_id)
    prefixo = modelos.prefixo_documentos(notebook_id)
    itens = notebooks._consultar(pk, prefixo)

    chaves = [_chave_do_usuario(i, usuario_id) for i in itens]
    for inicio in range(0, len(chaves), LOTE_EXCLUSAO_S3):
        lote = chaves[inicio:inicio + LOTE_EXCLUSAO_S3]
        r = _s3().delete_objects(Bucket=_bucket(), Delete={"Objects": [{"Key": k} for k in lote], "Quiet": True})
        if r.get("Errors"):
            raise RuntimeError(f"Falha ao apagar {len(r['Errors'])} arquivo(s) do S3.")
    notebooks._apagar_prefixo(pk, prefixo)