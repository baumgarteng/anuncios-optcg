# Backlog

Ideias e pedidos que ficaram pra depois — não implementados ainda. Ordem de listagem, não de prioridade.

## 1. Integração com a API do SuperFrete
Conectar direto na API do SuperFrete pra calcular o frete real (peso/dimensão do envelope/pacote da carta) em vez de só citar "Envio pelo SuperFrete com opção a sua escolha" no texto do anúncio. Possivelmente também gerar a etiqueta de envio depois que a venda é confirmada (ver item 3).

## 2. Dados de pagamento pro comprador
Adicionar um jeito de guardar (e opcionalmente incluir no anúncio ou mandar direto pro interessado) os dados de pagamento — chave PIX, ou outro método aceito. Definir se isso entra no texto do anúncio, fica só salvo pra copiar quando alguém se interessa, ou aparece em algum outro fluxo.

## 3. Botão "Confirmar venda" num anúncio salvo
No anúncio já salvo (aba Salvos), um botão pra marcar que aquela carta vendeu — ligado ao item 5 (nova coluna/ação "Vendida" na lista) e ao item 4 (contagem de vendas).

## 4. Guardar número de vendas
A partir da confirmação de venda (item 3), manter um contador/histórico de quantas vendas aconteceram — base pra alguma futura tela de estatísticas (quanto vendeu, de quê, quando).

## 5. Reformular a aba Salvos pra formato de lista
Trocar o grid de cards atual por uma lista (uma linha por anúncio), com os botões **Editar**, **Excluir** e **Vendida** direto na linha. O botão de copiar sai da listagem — copiar passa a ser feito de dentro do fluxo de edição (que já tem Copiar texto/Copiar imagens).

---
*Adicionado em 2026-09-14. Atualize este arquivo conforme os itens forem sendo feitos ou o escopo mudar.*
