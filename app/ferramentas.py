import unicodedata

from app.cardapio import carregar_cardapio

TAGS_VALIDAS = {"vegetariano", "vegano", "sem_gluten", "sem_lactose", "picante"}


def _normalizar(texto: str) -> str:
    # Remove acentos para que "glúten" encontre "gluten" e "açaí" encontre "acai".
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower().replace("_", " ").strip()


def _texto_pesquisavel(prato: dict) -> str:
    campos = [prato["nome"], prato["descricao"], prato["restaurante"], prato["categoria"], *prato["tags"]]
    return _normalizar(" ".join(campos))


def _resumo(prato: dict) -> dict:
    return {
        "id": prato["id"],
        "nome": prato["nome"],
        "restaurante": prato["restaurante"],
        "categoria": prato["categoria"],
        "preco": prato["preco"],
        "tags": list(prato["tags"]),
    }


def _validar_tags(tags: list[str] | None) -> list[str]:
    normalizadas = [_normalizar(t).replace(" ", "_") for t in tags or []]
    invalidas = [t for t in normalizadas if t not in TAGS_VALIDAS]
    if invalidas:
        raise ValueError(f"Tags inválidas: {invalidas}. Use: {sorted(TAGS_VALIDAS)}")
    return normalizadas


def buscar_pratos(termo: str = "", categoria: str = "", tags: list[str] | None = None) -> dict:
    """Busca pratos no cardápio por texto livre, categoria e restrições alimentares.

    Args:
        termo: Palavras a procurar no nome, descrição ou restaurante (ex: "salmão", "frango").
        categoria: Categoria exata, como pizza, hamburguer, japonesa, brasileira, saudavel,
            sobremesa, bebida ou acompanhamento. Vazio para todas.
        tags: Restrições que o prato deve ter: vegetariano, vegano, sem_gluten, sem_lactose, picante.
    """
    try:
        tags_exigidas = _validar_tags(tags)
    except ValueError as erro:
        return {"erro": str(erro), "pratos": []}

    palavras = _normalizar(termo).split()
    categoria_norm = _normalizar(categoria)

    encontrados = []
    for prato in carregar_cardapio():
        if categoria_norm and _normalizar(prato["categoria"]) != categoria_norm:
            continue
        if not all(tag in prato["tags"] for tag in tags_exigidas):
            continue
        texto = _texto_pesquisavel(prato)
        if not all(palavra in texto for palavra in palavras):
            continue
        encontrados.append(_resumo(prato))

    return {"pratos": encontrados}


def filtrar_por_preco(preco_max: float, preco_min: float = 0.0, categoria: str = "") -> dict:
    """Lista pratos dentro de uma faixa de preço, do mais barato para o mais caro.

    Args:
        preco_max: Preço máximo em reais.
        preco_min: Preço mínimo em reais.
        categoria: Categoria para restringir a busca. Vazio para todas.
    """
    if preco_max < 0 or preco_min < 0:
        return {"erro": "Preços não podem ser negativos.", "pratos": []}
    if preco_min > preco_max:
        return {"erro": "preco_min não pode ser maior que preco_max.", "pratos": []}

    categoria_norm = _normalizar(categoria)
    pratos = [
        _resumo(p)
        for p in carregar_cardapio()
        if preco_min <= p["preco"] <= preco_max
        and (not categoria_norm or _normalizar(p["categoria"]) == categoria_norm)
    ]
    pratos.sort(key=lambda p: p["preco"])
    return {"pratos": pratos}


def montar_pedido(ids: list[int], quantidades: list[int] | None = None) -> dict:
    """Monta um pedido a partir dos ids dos pratos e calcula o total.

    Args:
        ids: Ids dos pratos, obtidos com buscar_pratos ou filtrar_por_preco.
        quantidades: Quantidade de cada prato, na mesma ordem de ids. Se omitido, 1 de cada.
    """
    if not ids:
        return {"erro": "O pedido precisa de pelo menos um prato."}
    quantidades = quantidades or [1] * len(ids)
    if len(quantidades) != len(ids):
        return {"erro": "ids e quantidades precisam ter o mesmo tamanho."}

    por_id = {p["id"]: p for p in carregar_cardapio()}
    itens, erros = [], []
    for prato_id, quantidade in zip(ids, quantidades):
        prato = por_id.get(int(prato_id))
        if prato is None:
            erros.append(f"Prato com id {prato_id} não existe.")
        elif quantidade < 1:
            erros.append(f"Quantidade inválida para {prato['nome']}: {quantidade}.")
        else:
            itens.append({
                "id": prato["id"],
                "nome": prato["nome"],
                "restaurante": prato["restaurante"],
                "quantidade": int(quantidade),
                "subtotal": round(prato["preco"] * quantidade, 2),
            })

    restaurantes = sorted({item["restaurante"] for item in itens})
    pedido = {
        "itens": itens,
        "total": round(sum(item["subtotal"] for item in itens), 2),
        "restaurantes": restaurantes,
        "erros": erros,
    }
    # Num app de delivery cada pedido sai de um restaurante; o agente deve avisar o usuário.
    if len(restaurantes) > 1:
        pedido["aviso"] = "Itens de restaurantes diferentes geram entregas separadas."
    return pedido
