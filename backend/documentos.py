"""
Documentos dos usuários no S3 (commit 7): envio seguro por POST pré-assinado.

O arquivo vai direto do navegador para o S3, sem passar pela Lambda nem pelo limite
de 10 MB do API Gateway. A Lambda só autoriza o envio:

  * confere que o notebook é do usuário autenticado (404 se não for);
  * valida nome (extensão .md, .txt ou .pdf) e tamanho (até 50 MB);
  * define a chave do arquivo: usuarios/<sub>/<notebook>/<documento>.<tipo>;
  * assina uma política que só aceita ESSA chave, ESSE tipo de conteúdo e EXATAMENTE
    o tamanho declarado, válida por poucos minutos.

Registro dos metadados no DynamoDB, listagem e exclusão entram no commit 8.
"""
import logging
import os

import boto3
from botocore.config import Config

import modelos
import notebooks

logger = logging.getLogger()

EXPIRA_EM_SEGUNDOS = 300

s3 = None  # criado na primeira chamada; os testes substituem por um cliente falso


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


def solicitar_upload(usuario_id, notebook_id, corpo):
    if not isinstance(corpo, dict):
        raise modelos.DadoInvalido("Envie um JSON com o nome e o tamanho do arquivo.")
    nome, tipo = modelos.validar_arquivo(corpo.get("nome"), corpo.get("tamanhoBytes"))
    tamanho = corpo["tamanhoBytes"]

    notebooks.obter(usuario_id, notebook_id)  # NaoEncontrado se não existir ou não for do usuário

    documento_id = modelos.novo_id()
    chave = modelos.chave_s3(usuario_id, notebook_id, documento_id, tipo)
    tipo_conteudo = modelos.TIPOS_DE_CONTEUDO[tipo]

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
    logger.info("Upload autorizado: usuario=%s tipo=%s tamanho=%s", usuario_id[:8], tipo, tamanho)

    return {
        "documento": {
            "documentoId": documento_id,
            "notebookId": notebook_id,
            "nome": nome,
            "tipo": tipo,
            "tamanhoBytes": tamanho,
        },
        # o navegador envia um POST multipart com todos os `campos` e, por último, o campo `file`
        "upload": {
            "url": post["url"],
            "campos": post["fields"],
            "expiraEmSegundos": EXPIRA_EM_SEGUNDOS,
        },
    }