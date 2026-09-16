# Backlog

Ideias e pedidos que ficaram pra depois — não implementados ainda. Ordem de listagem, não de prioridade.

## ~~1. Integração com a API do SuperFrete~~ ✅ feito em 2026-09-15
Calculadora de frete real no topo de "Anúncios Salvos" (`POST /api/frete/calcular`, testado contra a API de produção). Fluxo completo de etiqueta (`/api/vendas/<id>/etiqueta`) também implementado — cria pedido, paga com o saldo da carteira e grava o link do PDF + rastreio.

Testado com dinheiro de verdade: um pedido com endereço errado deu erro e foi cancelado direto no painel da SuperFrete. Como `etiqueta_url` já tinha sido gravado, o app só oferecia "Ver etiqueta" (uma etiqueta cancelada), sem jeito de gerar outra depois de corrigir o endereço — resolvido no item 11.

## ~~2. Dados de pagamento pro comprador~~ ✅ feito em 2026-09-16
Chave PIX fixa (47999641711, Nubank, Gustavo Baumgarten) guardada como constante no front (`PIX_INFO`) — decisão tomada: não entra no texto do anúncio (mantém o anúncio limpo), fica só salva pra copiar. Botão "Copiar dados PIX pra enviar" no modal de venda, dentro de "Pagamento e detalhes da venda".

## ~~3. Botão "Confirmar venda" num anúncio salvo~~ ✅ feito em 2026-09-14, unificado com o modal de venda em 2026-09-15
O botão "Vendida" na lista agora abre o mesmo modal de registrar venda (item 6), pré-carregado com as cartas do anúncio — dá pra escolher o grupo de WhatsApp de onde veio o comprador e adicionar outras cartas antes de confirmar. `POST /api/anuncios/<id>/vender` foi removido; `POST /api/vendas` passou a aceitar `anuncio_ids` (ver item 10) e marca o(s) anúncio(s) como vendido(s) na mesma transação da venda.

## ~~4. Guardar número de vendas~~ ✅ feito em 2026-09-14, expandido em 2026-09-15
Virou uma aba "Vendidos" completa dentro de "Anúncios Salvos": indicadores (receita total, nº de vendas, ticket médio, quebra por origem), gráfico de receita por dia, e tabela `venda` própria (independente de `anuncio` — cobre vendas externas também).

## ~~5. Reformular a aba Salvos pra formato de lista~~ ✅ feito em 2026-09-14
Grid de cards virou lista (uma linha por anúncio) com Editar/Vendida/Excluir inline. Botões de copiar saíram da listagem (copiar já é feito de dentro do Editar).

## ~~6. Registrar venda externa~~ ✅ feito em 2026-09-15
Botão "+ Registrar venda externa" na aba Vendidos: onde foi vendido (grupo de WhatsApp/anúncio/outro), qual grupo, pra quem, cartas vendidas, frete, e endereço completo do comprador (usado depois pra gerar etiqueta). Mesmo modal serve pra editar qualquer venda já registrada.

## ~~7. Mais informações no registro/edição da venda~~ ✅ feito em 2026-09-16
Nova seção "Pagamento e detalhes da venda" no modal: forma de pagamento (PIX/dinheiro/cartão/transferência/outro), status do pagamento (pendente/recebido — aparece como chip colorido na lista), desconto (R$) e observação livre. Indicador "A receber" (soma de vendas com pagamento pendente) na aba Vendidos.

## ~~8. [bug] Imagem da carta não aparece na lista de Vendidos~~ ✅ feito em 2026-09-15
O snapshot `cards` da venda agora carrega `image_url` (tanto vindo do fluxo "Vendida" quanto preservado ao editar). `GET /api/vendas` também traz `capa_anuncio` (foto do anúncio ligado). A lista usa a imagem da primeira carta ou, na falta dela, a capa do anúncio — só cai no ícone genérico pra venda externa sem nenhuma das duas.

## ~~9. Mais campos de origem/data/pagamento na venda~~ ✅ feito em 2026-09-16
- **Origem "Jornada Games"** — nova opção em "onde foi vendido".
- **Data da venda** (`data_venda`) — separada de `criado_em`; usada na lista, no gráfico de receita por dia e por padrão preenchida com a data de hoje. Anúncios/vendas antigas foram migradas usando a data de criação como data da venda.
- **Data de envio (Correios)** (`data_envio`) e **comprovante de envio** (foto anexada, comprimida no navegador) — pedido junto (não estava no item original, veio no mesmo pacote).
- **Data do recebimento** (`data_recebimento`) — separada da data da venda.
- **Como foi pago** e **onde foi pago/banco** (`banco_recebimento`) — cobertos junto do item 7.

## ~~10. Salvar anúncio por carta + juntar cartas de vários anúncios numa venda~~ ✅ feito em 2026-09-15
Um anúncio com várias cartas continua gerando UM texto (o post completo, pra colar no WhatsApp), mas ao salvar agora cria **uma linha em `anuncio` por carta** (cada uma com o mesmo texto e um `lote_id` em comum — guardado, mas sem selo visual na lista; removido a pedido em 2026-09-16). Isso permite marcar/vender cada carta do post separadamente.

Na aba Anúncios, cada linha ganhou um checkbox e um botão "Gerar venda com selecionadas": dá pra marcar cartas de anúncios diferentes (de lotes diferentes até) e gerar **uma venda só**, com um comprador e um frete cobrindo todas. `venda.anuncio_ids` (array, substituiu o `anuncio_id` singular) guarda todos os anúncios ligados — excluir a venda desfaz o status `vendida` de todos eles.

## ~~11. [bug] Sem jeito de gerar outra etiqueta depois de uma cancelada~~ ✅ feito em 2026-09-15, reorganizado em 2026-09-15
Uma vez que `etiqueta_url` era gravado, a tela só mostrava "Ver etiqueta" pra sempre — mesmo que o pedido tivesse sido cancelado na SuperFrete (ex.: erro no endereço, corrigido só depois). Adicionado "Verificar status" (consulta a SuperFrete, só leitura) e "Etiqueta cancelada, gerar outra" (esquece a etiqueta gravada aqui, com confirmação). Depois, os botões (que tinham virado 3-4 por linha) foram reunidos num botão só "SuperFrete" que abre um modal organizado com tudo dentro.

## 12. [bug em aberto] "Verificar status" retorna erro de parse
Testado com uma venda real: `GET /api/vendas/<id>/etiqueta/status` devolveu "Expecting value: line 1 column 1 (char 0)" — a SuperFrete respondeu sem corpo (ou não-JSON) pro endpoint `GET /api/v0/orders/{order_id}`, que foi mapeado a partir do pacote `deco-cx/apps/superfrete` (comunidade, não é doc oficial — não consegui confirmar contra https://superfrete.readme.io/reference, bloqueado pra mim nesta sessão). O tratamento de erro no backend foi melhorado (mostra o corpo cru da resposta em vez de estourar o erro de parse), mas o endpoint em si ainda pode estar errado — próximo teste do botão "Verificar status" deve trazer uma mensagem mais útil (corpo da resposta) pra descobrir o path certo.

## ~~13. [bug] Preço da Liga mostrando "sem preço em cache" com preço disponível~~ ✅ feito em 2026-09-16
`buscar_carta()` só lia `liga_catalog_map.liga_price` (atualizado uma vez no mapeamento inicial, raramente de novo) e ignorava `liga_price_snapshot.price` (valor do scraper ao vivo, mais recente e com mais cobertura pra variantes específicas). Confirmado direto no banco (`optcg-db`): 157 cartas tinham `liga_price` nulo mas já tinham preço no snapshot — nos dois casos onde ambos existem, nunca divergem, só o snapshot é mais completo. Corrigido: agora prefere sempre `liga_price_snapshot.price` (com a data de checagem certa), e só cai pro valor do `liga_catalog_map` quando o snapshot ainda não tem nada.

---
*Adicionado em 2026-09-14, atualizado em 2026-09-16. Atualize este arquivo conforme os itens forem sendo feitos ou o escopo mudar.*
