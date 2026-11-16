"""
Notebooks: regras de negócio e acesso ao DynamoDB (commit 5).

Cada função recebe o `usuario_id` (o `sub` do token validado pelo API Gateway) e
nunca confia em dono vindo do corpo ou da URL. Toda leitura e escrita usa
pk = USER#<sub>, então um notebook de outra pessoa simplesmente não existe para
quem chama: o resultado é NaoEncontrado (404), igual a um ID que nunca existiu.

A camada HTTP (rotas, status e JSON) fica em lambda_function.py.
Chaves e itens: modelos.py.
"""
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import modelos

logger = logging.getLogger()

MAX_PAGINAS = 20  # trava de segurança nas paginações de Query

tabela = None  # criada na primeira chamada; os testes substituem por uma tabela falsa


class NaoEncontrado(Exception):
    """O recurso não existe ou não pertence ao usuário (os dois casos são iguais de propósito)."""


def _tabela():
    global tabela
    if tabela is None:
        nome = os.environ.get("TABLE_NAME")
        if not nome:
            raise RuntimeError("Variável de ambiente TABLE_NAME não configurada.")
        tabela = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION"),
            config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2, "mode": "standard"}),
        ).Table(nome)
    return tabela


def _chaves(usuario_id, notebook_id):
    """(pk, sk) do notebook. ID malformado vira 404: não revela nada sobre o formato."""
    pk = modelos.pk_usuario(usuario_id)
    try:
        sk = modelos.sk_notebook(notebook_id)
    except modelos.DadoInvalido:
        raise NaoEncontrado()
    return pk, sk


def _ler(usuario_id, notebook_id):
    pk, sk = _chaves(usuario_id, notebook_id)
    item = _tabela().get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")
    if not item or not modelos.pertence_ao_usuario(item, usuario_id):
        raise NaoEncontrado()
    return item


def _consultar(pk, prefixo):
    parametros = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefixo)",
        "ExpressionAttributeValues": {":pk": pk, ":prefixo": prefixo},
        "ConsistentRead": True,
    }
    itens = []
    for _ in range(MAX_PAGINAS):
        r = _tabela().query(**parametros)
        itens.extend(r.get("Items", []))
        ultima = r.get("LastEvaluatedKey")
        if not ultima:
            return itens
        parametros["ExclusiveStartKey"] = ultima
    raise RuntimeError("Consulta com páginas demais.")


def _apagar_prefixo(pk, prefixo):
    """Apaga, página por página, todos os itens do usuário cuja sort key começa com o prefixo."""
    parametros = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefixo)",
        "ExpressionAttributeValues": {":pk": pk, ":prefixo": prefixo},
        "ConsistentRead": True,
        "ProjectionExpression": "pk, sk",
    }
    while True:
        r = _tabela().query(**parametros)
        with _tabela().batch_writer() as lote:
            for item in r.get("Items", []):
                lote.delete_item(Key={"pk": pk, "sk": item["sk"]})
        ultima = r.get("LastEvaluatedKey")
        if not ultima:
            return
        parametros["ExclusiveStartKey"] = ultima


# ---------- operações ----------


def criar(usuario_id, corpo):
    if not isinstance(corpo, dict):
        raise modelos.DadoInvalido("Envie um JSON com o nome do notebook.")
    item = modelos.novo_notebook(usuario_id, corpo.get("nome"), corpo.get("descricao"))
    _tabela().put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    return modelos.para_api(item)


def listar(usuario_id):
    itens = _consultar(modelos.pk_usuario(usuario_id), modelos.PREFIXO_NOTEBOOKS)
    return [modelos.para_api(i) for i in itens]


def obter(usuario_id, notebook_id):
    return modelos.para_api(_ler(usuario_id, notebook_id))


def atualizar(usuario_id, notebook_id, corpo):
    pk, sk = _chaves(usuario_id, notebook_id)
    campos = modelos.atualizacao_notebook(corpo)

    nomes = {"#atualizado": "atualizadoEm"}
    valores = {":atualizado": modelos.agora_iso()}
    partes = ["#atualizado = :atualizado"]
    for i, (campo, valor) in enumerate(campos.items()):
        nomes[f"#c{i}"] = campo
        valores[f":v{i}"] = valor
        partes.append(f"#c{i} = :v{i}")

    try:
        r = _tabela().update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET " + ", ".join(partes),
            # sem isto o UpdateItem criaria um notebook "fantasma" para um ID inexistente
            ConditionExpression="attribute_exists(pk)",
            ExpressionAttributeNames=nomes,
            ExpressionAttributeValues=valores,
            ReturnValues="ALL_NEW",
        )
    except ClientError as erro:
        if erro.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise NaoEncontrado()
        raise
    return modelos.para_api(r["Attributes"])


def excluir(usuario_id, notebook_id):
    _ler(usuario_id, notebook_id)  # 404 se não existir ou não for do usuário
    pk, sk = _chaves(usuario_id, notebook_id)

    # Filhos primeiro e o notebook por último: se algo falhar no meio, o notebook
    # continua existindo e o usuário pode repetir a exclusão.
    # Documentos (S3 e Knowledge Base) e conversas passam a existir nos commits 8 e 11;
    # os itens de cada um já são apagados aqui. A limpeza dos arquivos no S3 entra no commit 8.
    _apagar_prefixo(pk, modelos.prefixo_mensagens_notebook(notebook_id))
    _apagar_prefixo(pk, modelos.prefixo_conversas(notebook_id))
    _apagar_prefixo(pk, modelos.prefixo_documentos(notebook_id))
    _tabela().delete_item(Key={"pk": pk, "sk": sk})