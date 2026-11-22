import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.agente import Resposta
from app.main import Sessoes, app, obter_sessoes


class AgenteFalso:
    def __init__(self):
        self.mensagens: list[str] = []

    def responder(self, mensagem: str) -> Resposta:
        self.mensagens.append(mensagem)
        return Resposta(
            texto=f"resposta {len(self.mensagens)}",
            chamadas=[{"ferramenta": "buscar_pratos", "argumentos": {"termo": mensagem}, "resultado": {}}],
            modelo="modelo-falso",
        )


class AgenteIndisponivel:
    def responder(self, mensagem: str) -> Resposta:
        raise errors.ServerError(503, {"error": {"code": 503, "message": "sobrecarga", "status": "UNAVAILABLE"}})


@pytest.fixture
def sessoes():
    return Sessoes(fabrica=AgenteFalso, limite=2)


@pytest.fixture
def cliente(sessoes):
    app.dependency_overrides[obter_sessoes] = lambda: sessoes
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(cliente):
    assert cliente.get("/health").json() == {"status": "ok"}


def test_primeira_mensagem_cria_sessao(cliente):
    corpo = cliente.post("/chat", json={"mensagem": "pizza"}).json()
    assert corpo["sessao_id"]
    assert corpo["resposta"] == "resposta 1"
    assert corpo["modelo"] == "modelo-falso"
    assert corpo["ferramentas"] == [{"ferramenta": "buscar_pratos", "argumentos": {"termo": "pizza"}}]


def test_mesma_sessao_mantem_historico(cliente):
    sessao_id = cliente.post("/chat", json={"mensagem": "oi"}).json()["sessao_id"]
    corpo = cliente.post("/chat", json={"mensagem": "de novo", "sessao_id": sessao_id}).json()
    assert corpo["sessao_id"] == sessao_id
    assert corpo["resposta"] == "resposta 2"


def test_sessoes_diferentes_sao_isoladas(cliente):
    cliente.post("/chat", json={"mensagem": "oi"})
    corpo = cliente.post("/chat", json={"mensagem": "oi"}).json()
    assert corpo["resposta"] == "resposta 1"


def test_limite_descarta_sessao_mais_antiga(cliente, sessoes):
    ids = [cliente.post("/chat", json={"mensagem": "oi"}).json()["sessao_id"] for _ in range(3)]
    assert sessoes.remover(ids[0]) is False
    assert sessoes.remover(ids[2]) is True


@pytest.mark.parametrize("corpo", [{}, {"mensagem": ""}, {"mensagem": "x" * 1001}])
def test_mensagem_invalida_retorna_422(cliente, corpo):
    assert cliente.post("/chat", json=corpo).status_code == 422


def test_modelo_indisponivel_retorna_503():
    app.dependency_overrides[obter_sessoes] = lambda: Sessoes(fabrica=AgenteIndisponivel)
    try:
        resposta = TestClient(app).post("/chat", json={"mensagem": "oi"})
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 503
    assert "sobrecarga" in resposta.json()["detail"]


def test_encerrar_sessao(cliente):
    sessao_id = cliente.post("/chat", json={"mensagem": "oi"}).json()["sessao_id"]
    assert cliente.delete(f"/chat/{sessao_id}").status_code == 204
    assert cliente.delete(f"/chat/{sessao_id}").status_code == 404
