import json
from functools import lru_cache
from pathlib import Path

CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "data" / "cardapio.json"


@lru_cache(maxsize=1)
def carregar_cardapio(caminho: Path = CAMINHO_PADRAO) -> tuple[dict, ...]:
    # Tupla para que o resultado em cache não seja alterado por quem o consome.
    with open(caminho, encoding="utf-8") as arquivo:
        return tuple(json.load(arquivo))
