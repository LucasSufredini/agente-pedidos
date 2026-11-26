"""Servidor único: API em /chat, /docs e /health, interface Gradio em /."""

import logging

import gradio as gr

from app.interface import criar_interface
from app.main import app as api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# As rotas da API são registradas antes do mount, então têm prioridade sobre a interface em "/".
app = gr.mount_gradio_app(api, criar_interface(), path="/")
