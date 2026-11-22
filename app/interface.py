import json
import logging
import os

import gradio as gr
import httpx
from gradio import ChatMessage
from google.genai import errors

from app.agente import Agente

EXEMPLOS = [
    "Quero algo vegano e sem glúten até R$ 35",
    "Tem comida japonesa até 30 reais?",
    "Qual a pizza mais barata?",
    "Quero 2 smash clássicos e uma batata",
    "Quero pizza por R$ 2",
]

DESCRICAO = """Agente de delivery com LLM: entende o pedido em linguagem natural e usa **ferramentas em Python**
para buscar pratos, filtrar por preço e montar o pedido num cardápio fictício. Abra os blocos 🔧 para ver
qual ferramenta o modelo decidiu chamar e com quais argumentos."""


def _resumir_resultado(resultado: dict) -> str:
    if "erro" in resultado:
        return f"⚠️ {resultado['erro']}"
    if "pratos" in resultado:
        return f"{len(resultado['pratos'])} prato(s) encontrado(s)"
    if "total" in resultado:
        total = f"{resultado['total']:.2f}".replace(".", ",")
        return f"{len(resultado['itens'])} item(ns), total R$ {total}"
    return ""


def _mensagem_ferramenta(chamada: dict) -> ChatMessage:
    argumentos = json.dumps(chamada["argumentos"], ensure_ascii=False, indent=2)
    return ChatMessage(
        role="assistant",
        content=f"```json\n{argumentos}\n```\n{_resumir_resultado(chamada['resultado'])}",
        metadata={"title": f"🔧 {chamada['ferramenta']}", "status": "done"},
    )


def responder(mensagem: str, historico: list, agente: Agente | None):
    mensagem = mensagem.strip()
    if not mensagem:
        yield "", historico, agente
        return

    # Um agente por aba do navegador: conversas de visitantes diferentes não se misturam.
    agente = agente or Agente()
    historico = historico + [ChatMessage(role="user", content=mensagem)]
    pensando = ChatMessage(
        role="assistant",
        content="",
        metadata={"title": "⏳ Consultando o cardápio...", "status": "pending"},
    )
    yield "", historico + [pensando], agente

    try:
        resposta = agente.responder(mensagem)
    except (errors.APIError, httpx.TimeoutException):
        historico.append(ChatMessage(
            role="assistant",
            content="O modelo está indisponível ou sem cota no momento. Tente de novo em instantes.",
        ))
        yield "", historico, agente
        return

    historico.extend(_mensagem_ferramenta(c) for c in resposta.chamadas)
    historico.append(ChatMessage(role="assistant", content=resposta.texto))
    yield "", historico, agente


def criar_interface() -> gr.Blocks:
    with gr.Blocks(title="Agente de Pedidos") as demo:
        gr.Markdown("# 🛵 Agente de Pedidos com LLM")
        gr.Markdown(DESCRICAO)

        agente = gr.State(None)
        chat = gr.Chatbot(height=480, label="Conversa")
        with gr.Row():
            entrada = gr.Textbox(
                placeholder="Ex.: quero algo vegetariano até R$ 40",
                show_label=False,
                scale=5,
                autofocus=True,
            )
            enviar = gr.Button("Enviar", variant="primary", scale=1)
        gr.Examples(EXEMPLOS, inputs=entrada, label="Experimente")
        limpar = gr.Button("Nova conversa")

        for gatilho in (entrada.submit, enviar.click):
            gatilho(responder, [entrada, chat, agente], [entrada, chat, agente])
        limpar.click(lambda: ([], None), outputs=[chat, agente])

    return demo


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    criar_interface().launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT", "7860")))
