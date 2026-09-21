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

## ~~14. Guardar o preço da Liga da época do anúncio pra comparar com a venda~~ ✅ feito em 2026-09-16
`anuncio.cards[i].liga` (preço/mínimo/máximo da Liga no momento do anúncio) já era salvo automaticamente — confirmado direto no banco. O que faltava: essa referência era descartada quando a carta virava uma venda (tanto no botão "Vendida" quanto em "Gerar venda com selecionadas" e no salvamento final do modal). Agora `venda.cards[i].liga` carrega a mesma referência congelada da época do anúncio, e o modal de venda mostra, por carta, o preço/mínimo da Liga na época e quanto % o preço de venda ficou acima/abaixo disso.

## ~~15. Conectar com a Jornada Games como referência de preço~~ ✅ feito em 2026-09-16
Chave de API (`JORNADAGAMES_API_KEY`) guardada como variável de ambiente no serviço, nunca no código. Botão "JornadaGames" junto de Atualizar/Histórico/Ver na Liga em cada carta: busca o nome/código na API pública deles (`GET /v1/public/cards/search`, restrito a `game=one-piece-tcg`) e mostra num modal o preço e a liquidez de cada variante encontrada. É só referência — não altera o preço do anúncio nem qualquer outro cálculo.

A API não devolve uma contagem exata de estoque no endpoint de busca em lote (só no book de ofertas por SKU individual, mais caro — a doc deles recomenda a busca em lote justamente pra evitar isso). Por isso, ao lado do preço, mostramos `liquidityScore` (0–100, atividade de negociação em 30 dias) rotulado como "Liquidez" — é o indicador mais próximo de "estoque" disponível numa consulta só, mas não é uma contagem literal de unidades.

**Ajuste em 2026-09-16**: a busca inicial usava o nome da carta, então trazia TODAS as impressões daquele personagem em qualquer set (ex.: toda "Vinsmoke Reiju"). Corrigido pra buscar pelo código exato (`OP12-063`) e filtrar o resultado só às variantes daquele mesmo código. Cada resultado agora também busca `/v1/public/cards/{id}/prices` pra trazer o tipo do preço (anúncio ativo vs. última venda concluída), o nível de liquidez (baixa/média/alta, além da nota 0–100) e a lista de SKUs disponíveis — e o nome da carta virou link direto pra página dela em jornadagames.com.

O primeiro filtro por código exato passou perto demais: a Jornada Games trata cada arte alternativa como um card à parte, mas com o mesmo código base + sufixo (ex.: `OP12-063` pra arte normal e `OP12-063-AA` pra "Alternate Art") — a comparação exata descartava a variante AA. Corrigido pra casar o código exato OU o código exato seguido de um sufixo (`-AA`, `-P1`, etc.), confirmado direto nos logs de produção com a Reiju (OP12-063): a busca já trazia as duas, só o filtro é que jogava uma fora.

O campo "N SKU(s) disponível(is)" também saiu errado — o `skus[]` do `/prices` é só a lista de combinações de condição×idioma que a Jornada Games rastreia (ex.: 12 = 6 condições × EN/JP), não uma contagem de estoque real; duas cartas sem nenhum vendedor mostravam "12 SKUs disponíveis" como se tivessem. Trocado por um indicador de "oferta ativa agora" / "sem oferta ativa no momento", baseado no `market.price.kind` ("ask" = tem alguém vendendo agora).

**Estoque real, 2026-09-16**: confirmado nos logs de produção que dá sim pra saber quantos anúncios/unidades tem à venda, só que não vem no `/prices` sem parâmetro — é preciso consultar `/prices?sku=<id>` pra CADA SKU (condição×idioma) e somar `orderBook.sellOrders[].quantity` (unidades) e `.orderCount` (nº de anúncios). Como isso custa até 12 chamadas extras por carta, só faz essa varredura quando já se sabe que há oferta ativa (`disponivel === true`) — nas cartas sem oferta, pula direto. Também corrigido de brinde: o preço vindo do `/prices` usava a chave errada (`amountCents`, que não existe) em vez de `amount` (a API já devolve em centavos) — o preço mostrado só não estava errado até aqui por coincidência (caía no valor da busca em lote, que tem a chave certa).

## ~~16. Criar Anúncios em Lote (uma foto = uma carta = um anúncio)~~ ✅ feito em 2026-09-18
Botão "Criar Anúncios em Lote" (cor azul, distinto do "Gerar venda com selecionadas") na aba Anúncios, ao lado dele. Abre um modal só com um seletor de fotos (múltiplas) — sem drag&drop, só o diálogo de arquivos mesmo. Ao clicar "Iniciar", processa uma foto por vez, com barra de progresso e log ao vivo:

1. Identifica a carta na foto (`/api/identificar`, mesma IA usada na tela de criação normal).
2. Preenche o preço com o valor da Liga, se achou referência (`aplicarPrecoConhecido`, a mesma função já usada depois de identificar/buscar carta).
3. Salva direto como um anúncio novo (`POST /api/anuncios`, reaproveitado sem mudança nenhuma no backend — ele já aceita `cards`+`imagens` sem `titulo`/`curto`/`completo`, ficam nulos e a lista não depende deles).

Cada foto gera um anúncio, identificada ou não (se a IA não conseguir ler, cria mesmo assim vazio — dá pra editar na mão depois, esse é o ponto do recurso: ter todas as cartas listadas pra venda rapidamente). A lista de Anúncios Salvos é recarregada depois de cada anúncio criado, então ela vai enchendo conforme o lote processa.

## ~~17. Senha simples pro painel de anúncios + CTA de WhatsApp no binder~~ ✅ feito em 2026-09-21
Todo o painel (`/`, todas as rotas `/api/*`) agora pede uma senha simples (`APP_LOGIN_SENHA`, padrão `optcg`) antes de deixar entrar — não é segurança de verdade, é só pra afastar visita curiosa mexendo nos anúncios quando o link do binder circula por aí. Login guarda uma sessão de 400 dias (`app.permanent_session_lifetime`) num cookie assinado, então não pede senha de novo no mesmo navegador. `APP_SECRET_KEY` guardado como variável de ambiente (senão cada deploy trocaria a chave e derrubaria todo mundo logado). Link "Sair" discreto no cabeçalho.

Os binders públicos (`/binder/<token>`) e o `/healthz` ficam de fora do gate — quem recebe o link do binder nunca vê nem precisa da senha, só o token da URL.

De brinde, um botão de WhatsApp (verde, com o ícone) no rodapé do binder público: "Quer saber mais sobre o sistema de anúncios de OPTCG? Chame no WhatsApp", linkando pro (47) 99964-1711 — usado o número completo de 11 dígitos (mesmo da chave PIX já configurada), já que o que veio no pedido (`4799964711`, 10 dígitos) parecia faltar um dígito pro padrão de celular brasileiro.

## ~~18. Nome do anúncio (obrigatório com mais de uma carta) + tag de "N cartas" na lista~~ ✅ feito em 2026-09-21
Campo "Nome do anúncio" aparece na tela de criação assim que tem mais de uma carta no anúncio (`#nome-anuncio-wrap`), e vira obrigatório pra salvar — sem ele, o "Salvar no banco" recusa com um erro. Esse nome passa a ser o nome mostrado na lista de Anúncios Salvos pra qualquer carta daquele lote (em vez do nome da carta), com uma tag "N cartas" do lado indicando que faz parte de um lote.

Também disponível no Editar: como cada carta de um lote vira sua própria linha no banco (ver item 10), editar uma delas só carrega aquela carta no formulário — mas o campo "Nome do anúncio" aparece mesmo assim quando a linha faz parte de um lote (`GET /api/anuncios/<id>` agora devolve `lote_tamanho`), e salvar propaga o nome novo pras outras linhas do mesmo lote (`UPDATE ... WHERE lote_id = ...`), pra não ficar cada carta com um nome diferente pro mesmo post.

## ~~19. Estado padrão Near Mint + lista de Anúncios em duas listas paginadas + ajustes no binder~~ ✅ feito em 2026-09-21
Pacote de ajustes pedidos numa tacada só:

- **Estado padrão**: todo anúncio novo (pela tela normal ou pelo "Criar Anúncios em Lote") já nasce com estado "Near Mint" em vez de "Mint" (`blank()` no front, fallback no backend também atualizado). Migração de backfill em `init_db()` trocou `Mint` → `Near Mint` em todos os anúncios **abertos** (não mexeu no histórico do que já foi vendido).
- **Anúncios Salvos em duas listas**: "Cartas avulsas" (lote de 1 carta) e "Anúncios em lote" (2+ cartas), cada uma com paginação própria de 10 linhas (`#saved-singles`/`#saved-lotes`, `renderListaSalvos`), mantendo a ordenação por data de criação (mais recente primeiro) que já existia. Ações (Editar/Vendida/Excluir) e seleção continuam funcionando igual, só mudou a divisão visual.
- **Binder padrão 4×4** em vez de 3×3.
- **Binder não mostra mais anúncios de lote** (2+ cartas) — o binder é uma vitrine carta a carta; `_binder_cartas()` agora calcula `lote_tamanho` (mesma lógica de `api_listar`) e só inclui linhas com `lote_tamanho = 1`. A consulta já rodava ao vivo a cada carregamento da página (sempre pegava o valor mais recente salvo), isso não mudou.
- **Removida** a mensagem "Nenhuma carta disponível neste binder no momento" (bloco `.empty`) — tirada a pedido.
- **Seleção de cartas no binder público**: cada carta ganhou um checkbox (canto superior esquerdo da foto). O botão de WhatsApp no rodapé, que antes só convidava a chamar sobre o sistema, agora muda de texto/link conforme a seleção: com 1+ cartas marcadas vira "Enviar N carta(s) selecionada(s) por WhatsApp" e abre o WhatsApp com uma mensagem pronta listando nome, código e preço de cada uma escolhida. Sem seleção, mantém a mensagem genérica de antes.
- **Banner no topo do binder**: "Selecione as cartas que te interessam e mande no final por WhatsApp suas escolhas automaticamente." — avisa o visitante sobre o recurso novo.

## ~~20. Layout centralizado e mais largo em telas de PC + confirmação da venda combinada~~ ✅ feito em 2026-09-21
`.wrap` (todo o corpo da página) não tinha `margin:0 auto`, então em telas largas o conteúdo ficava colado na esquerda em vez de centralizado — mesmo bug que o binder público já não tinha (ele já usava `margin:0 auto`). Corrigido em `.top`, `.tabs` e `.wrap`, e a largura máxima subiu de 1240px pra 1440px pra aproveitar melhor telas de PC. `.tabs` precisou de uma conta (`margin-left:max(22px, calc((100vw - 1440px)/2 + 22px))`) pra continuar alinhado com o conteúdo centralizado abaixo dele, já que é uma pílula de largura própria (não dá pra só centralizar ela inteira sem desalinhar do resto).

Sobre "as listas de avulsas e lotes têm que estar na mesma página pra combinar numa venda": conferido no código — já funciona assim desde o item 19. As duas listas (`#saved-singles`/`#saved-lotes`) sempre estiveram lado a lado na mesma tela (nunca em abas separadas), e a seleção (`vendaSelecionados`) é um único conjunto compartilhado entre as duas — marcar uma carta avulsa numa lista e uma carta de um lote na outra e clicar "Gerar venda com selecionadas" sempre juntou tudo numa venda só. Pra deixar isso visível (e não só confiável por trás dos panos), o resumo acima das listas agora mostra ao vivo quantas estão selecionadas, com a mensagem "cartas avulsas e lotes combinam numa venda só".

---
*Adicionado em 2026-09-14, atualizado em 2026-09-21. Atualize este arquivo conforme os itens forem sendo feitos ou o escopo mudar.*
