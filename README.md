# anuncios-optcg

Gerador de anúncios de cartas One Piece TCG para grupos de WhatsApp.

Foto da carta -> a IA lê o código impresso -> o catálogo do Postgres preenche
nome, raridade, cor, custo, poder e efeito -> você informa o preço -> o texto do
anúncio sai pronto, em versão curta e completa, com o negrito do WhatsApp.

## Como roda

- Flask + Postgres (banco `optcg-db`, já existente)
- Lê as tabelas `catalog` e `liga_catalog_map` (somente leitura)
- Cria e escreve nas tabelas próprias `anuncio` e `anuncio_imagem`
- O `% abaixo da MDL` é calculado a partir de `liga_catalog_map.liga_price`

## Variáveis de ambiente

| Variável | Obrigatória | Observação |
|---|---|---|
| `DATABASE_URL` | sim | connection string interna do `optcg-db` |
| `ANTHROPIC_API_KEY` | sim | usada para identificar a carta e escrever o texto |
| `ANTHROPIC_MODEL` | não | padrão `claude-sonnet-4-6` |

## Local

    pip install -r requirements.txt
    export DATABASE_URL=... ANTHROPIC_API_KEY=...
    python app.py

## Render

- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT`
- `/healthz` responde com o total de cartas do catálogo e se a chave da IA está presente

## Regras fixas dos anúncios

Estão no `REGRAS` do `app.py`. A principal: a embalagem é descrita apenas como
"em sleeve e bem protegida", sem mencionar toploader nem qualquer outro detalhe.
