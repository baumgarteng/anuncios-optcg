# Backlog

Ideias e pedidos que ficaram pra depois — não implementados ainda. Ordem de listagem, não de prioridade.

## ~~1. Integração com a API do SuperFrete~~ ✅ feito em 2026-09-15
Calculadora de frete real no topo de "Anúncios Salvos" (`POST /api/frete/calcular`, testado contra a API de produção). Fluxo completo de etiqueta (`/api/vendas/<id>/etiqueta`) também implementado — cria pedido, paga com o saldo da carteira e grava o link do PDF + rastreio — mas **ainda não testado com dinheiro de verdade**. Teste com cuidado na primeira venda real antes de confiar nele.

## 2. Dados de pagamento pro comprador
Adicionar um jeito de guardar (e opcionalmente incluir no anúncio ou mandar direto pro interessado) os dados de pagamento — chave PIX, ou outro método aceito. Definir se isso entra no texto do anúncio, fica só salvo pra copiar quando alguém se interessa, ou aparece em algum outro fluxo.

## ~~3. Botão "Confirmar venda" num anúncio salvo~~ ✅ feito em 2026-09-14, unificado com o modal de venda em 2026-09-15
O botão "Vendida" na lista agora abre o mesmo modal de registrar venda (item 6), pré-carregado com as cartas do anúncio — dá pra escolher o grupo de WhatsApp de onde veio o comprador e adicionar outras cartas antes de confirmar. `POST /api/anuncios/<id>/vender` foi removido; `POST /api/vendas` passou a aceitar `anuncio_id` opcional e marca o anúncio como vendido na mesma transação da venda.

## ~~4. Guardar número de vendas~~ ✅ feito em 2026-09-14, expandido em 2026-09-15
Virou uma aba "Vendidos" completa dentro de "Anúncios Salvos": indicadores (receita total, nº de vendas, ticket médio, quebra por origem), gráfico de receita por dia, e tabela `venda` própria (independente de `anuncio` — cobre vendas externas também).

## ~~5. Reformular a aba Salvos pra formato de lista~~ ✅ feito em 2026-09-14
Grid de cards virou lista (uma linha por anúncio) com Editar/Vendida/Excluir inline. Botões de copiar saíram da listagem (copiar já é feito de dentro do Editar).

## ~~6. Registrar venda externa~~ ✅ feito em 2026-09-15
Botão "+ Registrar venda externa" na aba Vendidos: onde foi vendido (grupo de WhatsApp/anúncio/outro), qual grupo, pra quem, cartas vendidas, frete, e endereço completo do comprador (usado depois pra gerar etiqueta). Mesmo modal serve pra editar qualquer venda já registrada.

## 7. Mais informações no registro/edição da venda
O modal de venda (registrar/editar) já tem pra quem foi e como ficou o frete — falta pelo menos **tipo de pagamento** (PIX, dinheiro, cartão, transferência) e vale avaliar mais campos que façam sentido pro controle: status do pagamento (recebido/pendente), desconto negociado, observação livre da venda.

## 8. [bug] Imagem da carta não aparece na lista de Vendidos
Toda linha da aba Vendidos mostra o ícone genérico, nunca a foto real. Causa: o snapshot `cards` salvo em `venda` não carrega `image_url`, e a lista nem tenta usar a capa do anúncio de origem. Corrigir: quando a venda vem de um anúncio (marcar como vendida), copiar o `image_url` de cada carta pro snapshot; na renderização da lista, usar a imagem da primeira carta ou, na falta dela, a capa do anúncio ligado (`anuncio_id`) — só cai no ícone genérico mesmo pra venda externa sem foto.

## 9. Mais campos de origem/data/pagamento na venda (relacionado ao item 7)
- **Origem "Jornada Games"** como opção nova em "onde foi vendido" (hoje só tem grupo de WhatsApp/anúncio/outro).
- **Data da venda** separada da data de criação do registro (`criado_em`) — pra quando o registro é feito depois do fato.
- **Data do recebimento do dinheiro** — separada da data da venda (nem sempre cai no mesmo dia).
- **Como foi pago** (PIX, dinheiro, cartão, transferência — mesmo campo já listado no item 7).
- **Onde foi pago / banco** — em qual conta/banco o dinheiro caiu.

---
*Adicionado em 2026-09-14, atualizado em 2026-09-15. Atualize este arquivo conforme os itens forem sendo feitos ou o escopo mudar.*
