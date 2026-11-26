import logging
import os
import time
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from app.ferramentas import buscar_pratos, filtrar_por_preco, montar_pedido

load_dotenv()
log = logging.getLogger("agente")

FERRAMENTAS = {f.__name__: f for f in (buscar_pratos, filtrar_por_preco, montar_pedido)}
MAX_ITERACOES = 5
TIMEOUT_MS = 20_000
MODELO_PADRAO = "gemini-3.1-flash-lite"
RESERVAS_PADRAO = "gemini-3.5-flash,gemini-3.8-flash"
PAUSA_COTA_S = 600
PAUSA_INSTABILIDADE_S = 60

INSTRUCOES = """Você é o assistente de pedidos de um app de delivery. Responda em português, de forma curta e simpática.

Regras:
- Use SEMPRE as ferramentas para consultar pratos, preços e totais. Nunca invente pratos, preços ou restaurantes.
- Restrições alimentares (vegano, vegetariano, sem glúten, sem lactose, picante) vão no parâmetro tags.
- Quando o usuário pedir pratos específicos, chame montar_pedido direto com os ids das buscas. Ele só monta
  um resumo, não finaliza a compra, então não peça confirmação antes. Use o parâmetro quantidades.
- Mostre os itens e o total exatamente como montar_pedido devolver, e repasse qualquer aviso ou erro.
- Se nenhum prato atender ao pedido, diga isso com clareza e sugira a alternativa mais próxima.
- Só pergunte antes de montar se não der para saber qual prato do cardápio o usuário quer.
- Assuntos fora de comida e pedidos: diga educadamente que só ajuda com pedidos."""


@dataclass
class Resposta:
    texto: str
    chamadas: list[dict] = field(default_factory=list)
    modelo: str = ""


class Agente:
    def __init__(self, modelo: str | None = None, modelo_reserva: str | None = None):
        self.cliente = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            # Por padrão o SDK insiste por minutos num modelo sobrecarregado; limitamos para
            # que o modelo reserva assuma rápido.
            http_options=types.HttpOptions(
                timeout=TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(
                    attempts=2,
                    initial_delay=1.0,
                    max_delay=4.0,
                    # Sem 429: cota esgotada não volta em segundos, repetir só atrasa o fallback.
                    http_status_codes=[500, 502, 503, 504],
                ),
            ),
        )
        # None usa o .env; "" desliga o fallback (útil para comparar modelos na avaliação).
        if modelo_reserva is None:
            modelo_reserva = os.getenv("GEMINI_MODEL_RESERVA", RESERVAS_PADRAO)
        self.modelos = [
            m.strip()
            for m in [modelo or os.getenv("GEMINI_MODEL", MODELO_PADRAO), *modelo_reserva.split(",")]
            if m.strip()
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=INSTRUCOES,
            tools=list(FERRAMENTAS.values()),
            # Desligado para controlar o loop aqui e registrar cada chamada de ferramenta.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        self.historico: list[types.Content] = []

    # Compartilhado entre todas as sessões: se um modelo caiu para um usuário, caiu para todos.
    _pausados_ate: dict[str, float] = {}

    def _modelos_disponiveis(self) -> list[str]:
        agora = time.monotonic()
        livres = [m for m in self.modelos if self._pausados_ate.get(m, 0) <= agora]
        # Se todos estão pausados, tenta assim mesmo em vez de recusar sem perguntar.
        return livres or self.modelos

    def _pausar(self, modelo: str, segundos: float) -> None:
        self._pausados_ate[modelo] = time.monotonic() + segundos

    def _gerar(self) -> tuple[types.GenerateContentResponse, str]:
        ultimo_erro = None
        for modelo in self._modelos_disponiveis():
            inicio = time.monotonic()
            try:
                resposta = self.cliente.models.generate_content(
                    model=modelo, contents=self.historico, config=self.config
                )
                log.info("modelo=%s ok em %.1fs", modelo, time.monotonic() - inicio)
                return resposta, modelo
            except (errors.ServerError, httpx.TimeoutException) as erro:
                # 5xx ou timeout é instabilidade do provedor: tenta o modelo reserva em vez de falhar.
                log.warning("modelo=%s instavel (%s) em %.1fs", modelo, type(erro).__name__, time.monotonic() - inicio)
                self._pausar(modelo, PAUSA_INSTABILIDADE_S)
                ultimo_erro = erro
            except errors.ClientError as erro:
                # A cota é por modelo, então 429 em um não impede o reserva. Outros 4xx são bug nosso.
                if erro.code != 429:
                    raise
                log.warning("modelo=%s sem cota, pausado por %ds", modelo, PAUSA_COTA_S)
                self._pausar(modelo, PAUSA_COTA_S)
                ultimo_erro = erro
        raise ultimo_erro

    def responder(self, mensagem: str) -> Resposta:
        self.historico.append(types.Content(role="user", parts=[types.Part.from_text(text=mensagem)]))
        chamadas: list[dict] = []

        for _ in range(MAX_ITERACOES):
            resposta, modelo = self._gerar()
            conteudo = resposta.candidates[0].content
            self.historico.append(conteudo)

            if not resposta.function_calls:
                return Resposta(texto=resposta.text or "", chamadas=chamadas, modelo=modelo)

            partes_resultado = []
            for chamada in resposta.function_calls:
                argumentos = dict(chamada.args or {})
                resultado = self._executar(chamada.name, argumentos)
                chamadas.append({"ferramenta": chamada.name, "argumentos": argumentos, "resultado": resultado})
                partes_resultado.append(
                    types.Part.from_function_response(name=chamada.name, response=resultado)
                )
            self.historico.append(types.Content(role="user", parts=partes_resultado))

        return Resposta(
            texto="Desculpe, não consegui concluir. Pode reformular o pedido?",
            chamadas=chamadas,
            modelo=modelo,
        )

    @staticmethod
    def _executar(nome: str, argumentos: dict) -> dict:
        funcao = FERRAMENTAS.get(nome)
        if funcao is None:
            return {"erro": f"Ferramenta desconhecida: {nome}"}
        try:
            return funcao(**argumentos)
        except (TypeError, ValueError) as erro:
            # Argumento malformado vindo do modelo: devolve o erro para ele corrigir na próxima volta.
            return {"erro": f"Argumentos inválidos para {nome}: {erro}"}

    def limpar(self) -> None:
        self.historico.clear()


if __name__ == "__main__":
    agente = Agente()
    print("Agente de pedidos. Digite 'sair' para encerrar.")
    while (mensagem := input("\nVocê: ").strip()).lower() != "sair":
        resposta = agente.responder(mensagem)
        for chamada in resposta.chamadas:
            print(f"  [ferramenta] {chamada['ferramenta']}({chamada['argumentos']})")
        print(f"Agente ({resposta.modelo}): {resposta.texto}")
