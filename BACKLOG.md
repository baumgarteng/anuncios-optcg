# Backlog

Ideias e pedidos que ficaram pra depois — não implementados ainda. Ordem de listagem, não de prioridade.

## 1. Integração com a API do SuperFrete
Conectar direto na API do SuperFrete pra calcular o frete real (peso/dimensão do envelope/pacote da carta) em vez de só citar "Envio pelo SuperFrete com opção a sua escolha" no texto do anúncio. Possivelmente também gerar a etiqueta de envio depois que a venda é confirmada (ver item 3).

## 2. Dados de pagamento pro comprador
Adicionar um jeito de guardar (e opcionalmente incluir no anúncio ou mandar direto pro interessado) os dados de pagamento — chave PIX, ou outro método aceito. Definir se isso entra no texto do anúncio, fica só salvo pra copiar quando alguém se interessa, ou aparece em algum outro fluxo.

## ~~3. Botão "Confirmar venda" num anúncio salvo~~ ✅ feito em 2026-09-14
`POST /api/anuncios/<id>/vender` alterna entre `vendida`/`publicado`, botão "Vendida" na linha da lista (vira "Desfazer venda" quando já vendida).

## ~~4. Guardar número de vendas~~ ✅ feito em 2026-09-14
Contagem por `COUNT(*) WHERE status='vendida'` (sem tabela separada) — mostrado como "N vendidas de M anúncios" acima da lista. Ainda não tem uma tela de estatísticas própria (quando/o quê vendeu ao longo do tempo) — só o total.

## ~~5. Reformular a aba Salvos pra formato de lista~~ ✅ feito em 2026-09-14
Grid de cards virou lista (uma linha por anúncio) com Editar/Vendida/Excluir inline; linha "vendida" fica esmaecida com nome riscado. Botões de copiar saíram da listagem (copiar já é feito de dentro do Editar).

---
*Adicionado em 2026-09-14. Atualize este arquivo conforme os itens forem sendo feitos ou o escopo mudar.*
