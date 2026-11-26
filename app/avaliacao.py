"""Avaliação do agente: roda casos fixos e mede quantos passam em critérios objetivos.

Uso:
    python -m app.avaliacao                          # modelos do .env, com fallback
    python -m app.avaliacao --modelo gemini-3.5-flash  # um modelo só, sem fallback
"""

import argparse
import json
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import httpx
from google.genai import errors

from app.agente import Agente, Resposta

RAIZ = Path(__file__).resolve().parent.parent
CASOS = RAIZ / "data" / "avaliacao.json"
RESULTADOS = RAIZ / "resultados"


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    # "76.70" e "76,70" contam igual: o modelo às vezes escreve no formato americano.
    return re.sub(r"(\d)\.(\d{2})\b", r"\1,\2", sem_acento)


def _ultimo_pedido(chamadas: list[dict]) -> dict[str, int] | None:
    pedidos = [c for c in chamadas if c["ferramenta"] == "montar_pedido" and "itens" in c["resultado"]]
    if not pedidos:
        return None
    quantidades: dict[str, int] = {}
    for item in pedidos[-1]["resultado"]["itens"]:
        quantidades[str(item["id"])] = quantidades.get(str(item["id"]), 0) + item["quantidade"]
    return quantidades


def avaliar(caso: dict, texto: str, chamadas: list[dict]) -> list[str]:
    """Devolve as falhas do caso; lista vazia significa que passou."""
    falhas = []
    resposta = _normalizar(texto)
    usadas = {c["ferramenta"] for c in chamadas}

    for termo in caso.get("menciona", []):
        if _normalizar(termo) not in resposta:
            falhas.append(f"não mencionou {termo!r}")
    for termo in caso.get("nao_menciona", []):
        if _normalizar(termo) in resposta:
            falhas.append(f"mencionou {termo!r}")
    if "menciona_algum" in caso and not any(_normalizar(t) in resposta for t in caso["menciona_algum"]):
        falhas.append(f"não mencionou nenhum de {caso['menciona_algum']}")
    for ferramenta in caso.get("nao_chama", []):
        if ferramenta in usadas:
            falhas.append(f"chamou {ferramenta}")
    if caso.get("sem_ferramentas") and chamadas:
        falhas.append(f"chamou ferramentas: {sorted(usadas)}")
    if "pedido" in caso:
        pedido = _ultimo_pedido(chamadas)
        if pedido != caso["pedido"]:
            falhas.append(f"pedido {pedido} diferente de {caso['pedido']}")
    return falhas


def _e_instabilidade(erro: Exception) -> bool:
    if isinstance(erro, (errors.ServerError, httpx.TimeoutException)):
        return True
    return isinstance(erro, errors.ClientError) and erro.code == 429


def rodar_caso(caso: dict, criar_agente, tentativas: int = 3, espera: float = 15.0) -> dict:
    inicio = time.monotonic()
    for tentativa in range(1, tentativas + 1):
        agente = criar_agente()
        chamadas: list[dict] = []
        resposta = Resposta(texto="")
        try:
            for mensagem in caso["mensagens"]:
                resposta = agente.responder(mensagem)
                chamadas.extend(resposta.chamadas)
            falhas, status = avaliar(caso, resposta.texto, chamadas), None
            break
        except Exception as erro:
            if not _e_instabilidade(erro):
                falhas, status = [f"erro: {type(erro).__name__}: {str(erro)[:120]}"], None
                break
            # Falha do provedor não mede o agente: repete o caso do zero e, se persistir, tira da conta.
            falhas, status = [f"provedor: {str(erro)[:80]}"], "indisponivel"
            if tentativa < tentativas:
                time.sleep(espera * tentativa)

    return {
        "id": caso["id"],
        "status": status or ("passou" if not falhas else "falhou"),
        "falhas": falhas,
        "tentativas": tentativa,
        "segundos": round(time.monotonic() - inicio, 1),
        "modelo": resposta.modelo,
        "ferramentas": [{"nome": c["ferramenta"], "argumentos": c["argumentos"]} for c in chamadas],
        "resposta": resposta.texto,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modelo", help="Roda só com este modelo, sem fallback.")
    parser.add_argument("--pausa", type=float, default=4.0, help="Segundos entre casos (limite por minuto da API).")
    parser.add_argument("--casos", nargs="*", help="Ids de casos para rodar só alguns.")
    args = parser.parse_args()

    casos = json.loads(CASOS.read_text(encoding="utf-8"))
    if args.casos:
        casos = [c for c in casos if c["id"] in args.casos]

    def criar_agente():
        return Agente(modelo=args.modelo, modelo_reserva="") if args.modelo else Agente()

    resultados = []
    for i, caso in enumerate(casos):
        if i:
            time.sleep(args.pausa)
        resultado = rodar_caso(caso, criar_agente)
        resultados.append(resultado)
        marca = {"passou": "✅", "falhou": "❌", "indisponivel": "⚠️ "}[resultado["status"]]
        print(f"{marca} {caso['id']:<32} {resultado['segundos']:>5}s  {resultado['modelo'] or '-'}")
        for falha in resultado["falhas"]:
            print(f"     ↳ {falha}")

    avaliados = [r for r in resultados if r["status"] != "indisponivel"]
    acertos = sum(r["status"] == "passou" for r in avaliados)
    tempo_medio = sum(r["segundos"] for r in avaliados) / len(avaliados) if avaliados else 0.0
    modelos = sorted({r["modelo"] for r in avaliados if r["modelo"]})
    taxa = f"{acertos / len(avaliados):.0%}" if avaliados else "-"
    print(f"\nAcertos: {acertos}/{len(avaliados)} ({taxa}) · tempo médio {tempo_medio:.1f}s"
          f" · modelos: {', '.join(modelos) or '-'}")
    if len(avaliados) < len(resultados):
        print(f"Fora da conta por instabilidade do provedor: {len(resultados) - len(avaliados)} caso(s)")

    RESULTADOS.mkdir(exist_ok=True)
    nome = args.modelo or "padrao"
    destino = RESULTADOS / f"avaliacao-{nome}.json"
    destino.write_text(json.dumps({
        "data": datetime.now().isoformat(timespec="seconds"),
        "modelo_pedido": args.modelo,
        "acertos": acertos,
        "avaliados": len(avaliados),
        "indisponiveis": len(resultados) - len(avaliados),
        "tempo_medio_s": round(tempo_medio, 1),
        "casos": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultado salvo em {destino.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
