import pytest

from app.ferramentas import buscar_pratos, filtrar_por_preco, montar_pedido


def nomes(resultado: dict) -> set[str]:
    return {p["nome"] for p in resultado["pratos"]}


class TestBuscarPratos:
    def test_busca_por_termo(self):
        assert nomes(buscar_pratos("salmão")) == {
            "Combinado Salmão 20 peças",
            "Temaki de Salmão",
            "Hot Roll Philadelphia",
        }

    def test_ignora_acentos_e_maiusculas(self):
        assert nomes(buscar_pratos("ACAI")) == {"Açaí 500ml"}

    def test_filtra_por_categoria(self):
        resultado = buscar_pratos(categoria="pizza")
        assert len(resultado["pratos"]) == 5
        assert all(p["categoria"] == "pizza" for p in resultado["pratos"])

    def test_exige_todas_as_tags(self):
        resultado = buscar_pratos(tags=["vegano", "sem_gluten"])
        assert resultado["pratos"]
        for prato in resultado["pratos"]:
            assert {"vegano", "sem_gluten"} <= set(prato["tags"])

    def test_aceita_tag_escrita_naturalmente(self):
        assert nomes(buscar_pratos(tags=["Sem Glúten", "vegano"])) == nomes(
            buscar_pratos(tags=["sem_gluten", "vegano"])
        )

    def test_combina_termo_e_tag(self):
        assert nomes(buscar_pratos("pizza", tags=["vegano"])) == {"Pizza Vegana de Legumes"}

    def test_sem_resultados(self):
        assert buscar_pratos("lasanha") == {"pratos": []}

    def test_tag_invalida_retorna_erro(self):
        resultado = buscar_pratos(tags=["keto"])
        assert "erro" in resultado
        assert resultado["pratos"] == []


class TestFiltrarPorPreco:
    def test_respeita_faixa_e_ordena(self):
        precos = [p["preco"] for p in filtrar_por_preco(30, 20)["pratos"]]
        assert precos
        assert all(20 <= preco <= 30 for preco in precos)
        assert precos == sorted(precos)

    def test_filtra_por_categoria(self):
        resultado = filtrar_por_preco(40, categoria="pizza")
        assert nomes(resultado) == {"Calzone de Frango"}

    def test_preco_impossivel_retorna_vazio(self):
        assert filtrar_por_preco(2) == {"pratos": []}

    @pytest.mark.parametrize("preco_max, preco_min", [(-1, 0), (10, 20)])
    def test_faixa_invalida_retorna_erro(self, preco_max, preco_min):
        assert "erro" in filtrar_por_preco(preco_max, preco_min)


class TestMontarPedido:
    def test_calcula_total(self):
        pedido = montar_pedido([1, 3], [2, 1])
        assert pedido["total"] == pytest.approx(42.90 * 2 + 49.90)
        assert pedido["erros"] == []
        assert "aviso" not in pedido

    def test_agrupa_ids_repetidos(self):
        pedido = montar_pedido([28, 28, 28])
        assert [(i["id"], i["quantidade"]) for i in pedido["itens"]] == [(28, 3)]
        assert pedido["total"] == pytest.approx(22.90 * 3)

    def test_soma_quantidades_de_ids_repetidos(self):
        pedido = montar_pedido([6, 10, 6], [1, 1, 2])
        assert [(i["id"], i["quantidade"]) for i in pedido["itens"]] == [(6, 3), (10, 1)]

    def test_quantidade_padrao_e_um(self):
        pedido = montar_pedido([6])
        assert pedido["itens"][0]["quantidade"] == 1
        assert pedido["total"] == pytest.approx(29.90)

    def test_avisa_restaurantes_diferentes(self):
        pedido = montar_pedido([1, 30])
        assert len(pedido["restaurantes"]) == 2
        assert "aviso" in pedido

    def test_id_inexistente_vira_erro_sem_derrubar_pedido(self):
        pedido = montar_pedido([1, 999])
        assert len(pedido["itens"]) == 1
        assert "999" in pedido["erros"][0]

    def test_quantidade_invalida(self):
        pedido = montar_pedido([1], [0])
        assert pedido["itens"] == []
        assert pedido["erros"]

    @pytest.mark.parametrize("ids, quantidades", [([], None), ([1, 2], [1])])
    def test_entrada_invalida_retorna_erro(self, ids, quantidades):
        assert "erro" in montar_pedido(ids, quantidades)
