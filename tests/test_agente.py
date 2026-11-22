from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from app.agente import Agente


def erro_servidor():
    return errors.ServerError(503, {"error": {"code": 503, "message": "sobrecarga", "status": "UNAVAILABLE"}})


def erro_cliente(codigo):
    return errors.ClientError(codigo, {"error": {"code": codigo, "message": "erro", "status": "X"}})


class ModelosFalsos:
    """Simula client.models: cada modelo devolve uma resposta pronta ou levanta um erro."""

    def __init__(self, comportamento: dict):
        self.comportamento = comportamento
        self.chamados: list[str] = []

    def generate_content(self, model, contents, config):
        self.chamados.append(model)
        resultado = self.comportamento[model]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


def resposta_texto(texto):
    return SimpleNamespace(
        text=texto,
        function_calls=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


@pytest.fixture
def agente(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "chave-falsa")
    return Agente(modelo="principal", modelo_reserva="reserva")


def usar(agente, comportamento):
    modelos = ModelosFalsos(comportamento)
    agente.cliente = SimpleNamespace(models=modelos)
    return modelos


def test_usa_modelo_principal_quando_disponivel(agente):
    modelos = usar(agente, {"principal": resposta_texto("oi"), "reserva": resposta_texto("reserva")})
    resposta = agente.responder("olá")
    assert (resposta.texto, resposta.modelo) == ("oi", "principal")
    assert modelos.chamados == ["principal"]


@pytest.mark.parametrize("falha", [erro_servidor(), erro_cliente(429), httpx.ReadTimeout("lento")])
def test_cai_para_reserva_em_instabilidade_ou_cota(agente, falha):
    modelos = usar(agente, {"principal": falha, "reserva": resposta_texto("do reserva")})
    resposta = agente.responder("olá")
    assert (resposta.texto, resposta.modelo) == ("do reserva", "reserva")
    assert modelos.chamados == ["principal", "reserva"]


def test_erro_de_requisicao_nao_tenta_reserva(agente):
    modelos = usar(agente, {"principal": erro_cliente(400), "reserva": resposta_texto("x")})
    with pytest.raises(errors.ClientError):
        agente.responder("olá")
    assert modelos.chamados == ["principal"]


def test_propaga_erro_quando_todos_falham(agente):
    usar(agente, {"principal": erro_servidor(), "reserva": erro_cliente(429)})
    with pytest.raises(errors.ClientError):
        agente.responder("olá")


def test_executar_ferramenta_com_argumento_invalido_devolve_erro():
    assert "erro" in Agente._executar("buscar_pratos", {"campo_inexistente": 1})
    assert "erro" in Agente._executar("ferramenta_fantasma", {})
