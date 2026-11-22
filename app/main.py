import uuid
from collections import OrderedDict
from collections.abc import Callable

import httpx
from fastapi import Depends, FastAPI, HTTPException
from google.genai import errors
from pydantic import BaseModel, Field

from app.agente import Agente

MAX_SESSOES = 500

app = FastAPI(
    title="Agente de Pedidos",
    description="Agente de delivery com LLM que busca pratos e monta pedidos usando tool calling.",
    version="0.1.0",
)


class ChatEntrada(BaseModel):
    mensagem: str = Field(min_length=1, max_length=1000, examples=["Quero algo vegano até R$ 30"])
    sessao_id: str | None = Field(default=None, description="Omita na primeira mensagem.")


class ChamadaFerramenta(BaseModel):
    ferramenta: str
    argumentos: dict


class ChatSaida(BaseModel):
    sessao_id: str
    resposta: str
    ferramentas: list[ChamadaFerramenta]
    modelo: str


class Sessoes:
    """Guarda um agente por conversa. Em memória: some ao reiniciar (em produção, Redis)."""

    def __init__(self, fabrica: Callable[[], Agente] = Agente, limite: int = MAX_SESSOES):
        self.fabrica = fabrica
        self.limite = limite
        self._agentes: OrderedDict[str, Agente] = OrderedDict()

    def obter(self, sessao_id: str | None) -> tuple[str, Agente]:
        if sessao_id in self._agentes:
            self._agentes.move_to_end(sessao_id)
            return sessao_id, self._agentes[sessao_id]
        novo_id = sessao_id or uuid.uuid4().hex
        self._agentes[novo_id] = self.fabrica()
        if len(self._agentes) > self.limite:
            self._agentes.popitem(last=False)
        return novo_id, self._agentes[novo_id]

    def remover(self, sessao_id: str) -> bool:
        return self._agentes.pop(sessao_id, None) is not None


_sessoes = Sessoes()


def obter_sessoes() -> Sessoes:
    return _sessoes


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatSaida)
def chat(entrada: ChatEntrada, sessoes: Sessoes = Depends(obter_sessoes)) -> ChatSaida:
    sessao_id, agente = sessoes.obter(entrada.sessao_id)
    try:
        resposta = agente.responder(entrada.mensagem)
    except errors.APIError as erro:
        raise HTTPException(status_code=503, detail=f"Modelo indisponível no momento: {erro.message}") from erro
    except httpx.TimeoutException as erro:
        raise HTTPException(status_code=503, detail="O modelo demorou demais para responder.") from erro

    return ChatSaida(
        sessao_id=sessao_id,
        resposta=resposta.texto,
        ferramentas=[
            ChamadaFerramenta(ferramenta=c["ferramenta"], argumentos=c["argumentos"])
            for c in resposta.chamadas
        ],
        modelo=resposta.modelo,
    )


@app.delete("/chat/{sessao_id}", status_code=204)
def encerrar(sessao_id: str, sessoes: Sessoes = Depends(obter_sessoes)) -> None:
    if not sessoes.remover(sessao_id):
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
