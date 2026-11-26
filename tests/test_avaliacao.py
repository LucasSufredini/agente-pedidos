import json

import pytest
from google.genai import errors

from app.agente import Resposta
from app.avaliacao import CASOS, avaliar, rodar_caso
from app.cardapio import carregar_cardapio
from app.ferramentas import montar_pedido


def chamada_pedido(ids, quantidades):
    return {"ferramenta": "montar_pedido", "argumentos": {}, "resultado": montar_pedido(ids, quantidades)}


def test_passa_quando_tudo_confere():
    caso = {"menciona": ["Calzone"], "nao_menciona": ["Margherita"], "pedido": {"5": 1}}
    assert avaliar(caso, "Seu Calzone saiu!", [chamada_pedido([5], [1])]) == []


def test_ignora_acentos_maiusculas_e_formato_do_preco():
    caso = {"menciona": ["Mousse de Maracujá", "76,70"]}
    assert avaliar(caso, "MOUSSE DE MARACUJA por R$ 76.70", []) == []


@pytest.mark.parametrize("caso, texto, chamadas", [
    ({"menciona": ["Sunomono"]}, "Temos temaki", []),
    ({"nao_menciona": ["Paris"]}, "A capital é Paris", []),
    ({"menciona_algum": ["não temos", "nenhuma"]}, "Claro!", []),
    ({"nao_chama": ["montar_pedido"]}, "", [chamada_pedido([1], [1])]),
    ({"sem_ferramentas": True}, "", [{"ferramenta": "buscar_pratos", "argumentos": {}, "resultado": {}}]),
    ({"pedido": {"6": 2}}, "", [chamada_pedido([6], [1])]),
    ({"pedido": {"6": 2}}, "", []),
])
def test_detecta_falhas(caso, texto, chamadas):
    assert avaliar(caso, texto, chamadas)


def test_soma_itens_repetidos_do_pedido():
    chamada = {"ferramenta": "montar_pedido", "argumentos": {}, "resultado": {"itens": [
        {"id": 28, "quantidade": 1}, {"id": 28, "quantidade": 2},
    ]}}
    assert avaliar({"pedido": {"28": 3}}, "", [chamada]) == []


def test_considera_o_ultimo_pedido_montado():
    chamadas = [chamada_pedido([1], [1]), chamada_pedido([1, 3], [2, 1])]
    assert avaliar({"pedido": {"1": 2, "3": 1}}, "", chamadas) == []


class AgenteRoteirizado:
    """Cada instância consome o próximo comportamento: uma exceção ou um texto de resposta."""

    def __init__(self, roteiro):
        self.roteiro = roteiro

    def __call__(self):
        acao = self.roteiro.pop(0)

        class _Agente:
            def responder(self, mensagem):
                if isinstance(acao, Exception):
                    raise acao
                return Resposta(texto=acao, modelo="falso")

        return _Agente()


def erro_503():
    return errors.ServerError(503, {"error": {"code": 503, "message": "sobrecarga", "status": "UNAVAILABLE"}})


CASO = {"id": "x", "mensagens": ["oi"], "menciona": ["ok"]}


def test_repete_quando_o_provedor_falha_e_depois_passa():
    resultado = rodar_caso(CASO, AgenteRoteirizado([erro_503(), "ok"]), espera=0)
    assert (resultado["status"], resultado["tentativas"]) == ("passou", 2)


def test_instabilidade_persistente_fica_fora_da_conta():
    resultado = rodar_caso(CASO, AgenteRoteirizado([erro_503()] * 3), espera=0)
    assert resultado["status"] == "indisponivel"


def test_erro_do_proprio_codigo_conta_como_falha_sem_repetir():
    resultado = rodar_caso(CASO, AgenteRoteirizado([KeyError("bug"), "ok"]), espera=0)
    assert (resultado["status"], resultado["tentativas"]) == ("falhou", 1)


def test_resposta_errada_conta_como_falha():
    assert rodar_caso(CASO, AgenteRoteirizado(["nada a ver"]), espera=0)["status"] == "falhou"


def test_casos_de_avaliacao_sao_consistentes_com_o_cardapio():
    casos = json.loads(CASOS.read_text(encoding="utf-8"))
    ids_cardapio = {str(p["id"]) for p in carregar_cardapio()}
    assert len(casos) == 15
    assert len({c["id"] for c in casos}) == len(casos)
    for caso in casos:
        assert caso["mensagens"]
        for prato_id in caso.get("pedido", {}):
            assert prato_id in ids_cardapio, caso["id"]
