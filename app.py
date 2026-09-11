import os, json, base64, re, datetime
import psycopg
from psycopg.rows import dict_row
import urllib.request
import urllib.parse
from flask import Flask, request, jsonify, render_template

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPTCG_LIVE_URL = os.environ.get("OPTCG_LIVE_URL", "https://optcg-cloud-v2.onrender.com")

app = Flask(__name__)


# ---------------------------------------------------------------- banco
def conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS anuncio (
  id            bigserial PRIMARY KEY,
  criado_em     timestamptz NOT NULL DEFAULT now(),
  titulo        text,
  texto_curto   text,
  texto_completo text,
  observacao    text,
  total         numeric(10,2),
  cards         jsonb NOT NULL DEFAULT '[]'::jsonb,
  status        text NOT NULL DEFAULT 'rascunho'
);
CREATE TABLE IF NOT EXISTS anuncio_imagem (
  id          bigserial PRIMARY KEY,
  anuncio_id  bigint NOT NULL REFERENCES anuncio(id) ON DELETE CASCADE,
  ordem       int NOT NULL DEFAULT 0,
  mime        text NOT NULL DEFAULT 'image/jpeg',
  dados       text NOT NULL
);
CREATE INDEX IF NOT EXISTS anuncio_criado_idx ON anuncio (criado_em DESC);
CREATE INDEX IF NOT EXISTS anuncio_imagem_anuncio_idx ON anuncio_imagem (anuncio_id);
"""


def init_db():
    """Cria as tabelas novas. Não toca em nada que já existe no banco."""
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(SCHEMA)
            c.commit()
        app.logger.info("schema ok")
    except Exception as e:
        app.logger.error("falha ao preparar schema: %s", e)


# ---------------------------------------------------------------- catálogo
BASE_RE = re.compile(r"^([A-Z0-9]+-\d+)", re.I)

# Classificação de cada sufixo REAL da Liga BR — copiada verbatim de
# optcg-cloud/app.py (_LIGA_SUFFIX_VARIANT / _LIGA_PROMO_SUFFIXES), a mesma
# tabela que o scanner usa. A Liga tem 150+ sufixos diferentes (AA, PA, GS,
# TF, EA, 3A, SEC, RE, SN, PF, FA, SP, P, WP, JR, além de dezenas de siglas
# de torneio/evento) — nunca dá pra cobrir isso com uma lista fixa pequena,
# então usamos a classificação por "balde" (alt_art/manga/parallel/serial/
# reprint/promo) em vez de casar sufixo por sufixo.
_LIGA_SUFFIX_VARIANT = {
    "AA": "alt_art", "MA": "manga", "GS": "alt_art", "TF": "alt_art", "EA": "alt_art",
    "3A": "alt_art", "SEC": "alt_art", "RE": "reprint", "SN": "serial",
    "PA": "parallel", "PF": "parallel", "FA": "parallel", "SP": "parallel", "P": "parallel",
    "WP": "promo", "JR": "promo",
}
_LIGA_PROMO_SUFFIXES = {
    "CS", "OC", "OF", "OP", "RC", "RF", "RP", "TC", "BS", "CP", "SW",
    "CW", "CC", "GC", "SH", "FR", "PR", "PW", "AE", "AT", "CB", "UD",
    "UW", "OB", "RT", "NW", "NY", "TP", "TP4", "TTC", "SC", "BC", "EP",
    "CF", "CF2", "CT", "CT2", "SB", "SG", "CH", "EW", "IB", "EF", "CE",
    "QU", "QW", "SR", "OW", "RW", "RS", "GF", "F1", "F2", "F3",
    "FB", "MP", "VJ", "JW", "IK", "PP", "BW", "BP", "AS", "JU", "LE",
    "LT", "IA", "CG", "TS", "EG", "SL", "NE", "SJ", "DO", "R1", "1A",
    "2A", "3W", "LA", "DD", "DF", "JP", "RB", "SD", "BG",
    "WF", "GO", "C1", "C2", "C3", "SA", "EH", "CO", "W2", "PC", "PS",
    "TA", "TW", "ST", "TB", "BO", "I2", "WT", "AP",
}

# variante que o usuário digita/o identificador de IA devolve (mesmo conceito
# de VARIANT_TYPE_TO_VARIANT lá embaixo) → balde de sufixo da Liga que
# procura.
_VARIANTE_BUCKET = {
    "": "base", "AA": "alt_art", "SA": "alt_art", "MA": "manga",
    "TR": "serial", "SP": "parallel", "SF": "parallel",
}


def _liga_suffix_bucket(suffix):
    """Classifica um liga_suffix real num balde (base/alt_art/manga/parallel/
    serial/reprint/promo/outro), igual ao scanner do optcg-cloud."""
    if not suffix:
        return "base"
    s = suffix.upper().lstrip("-")
    vt = _LIGA_SUFFIX_VARIANT.get(s)
    if vt:
        return vt
    if s in _LIGA_PROMO_SUFFIXES:
        return "promo"
    if re.match(r"^\d+$", s):
        return "serial"
    return "outro"


def base_code(code):
    m = BASE_RE.match((code or "").strip().upper())
    return m.group(1) if m else ""


def buscar_carta(code, variant=""):
    """Dados da carta no catálogo + preço de referência da Liga."""
    base = base_code(code)
    if not base:
        return None
    variant = (variant or "").strip().upper()
    alvo_bucket = _VARIANTE_BUCKET.get(variant, "outro")

    with conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT id, base_id, name, rarity, category, colors, cost, power, counter,
                      types, effect, trigger_text, variant_type, image_url
                 FROM catalog
                WHERE id = %s OR base_id = %s
                ORDER BY (id = %s) DESC, id
                LIMIT 20""",
            (base, base, base),
        )
        linhas = cur.fetchall()
        if not linhas:
            return None

        # preço de referência: casa pelo BALDE do sufixo real da Liga (nunca
        # pelo número interno _p1/_p2 do catalog_id, que é só ordem de
        # importação). Mantém a linha mesmo com liga_price nulo — sem isso
        # perdíamos liga_page_url (necessário pra buscar ao vivo e pro
        # histórico) sempre que o snapshot de preço tivesse zerado mas o
        # histórico de preço ainda existisse.
        cur.execute(
            """SELECT catalog_id, liga_code, liga_price, liga_preco_min, liga_preco_max,
                      liga_suffix, liga_page_url, liga_image_url, updated_at
                 FROM liga_catalog_map
                WHERE base_code = %s AND liga_page_url IS NOT NULL
                ORDER BY updated_at DESC NULLS LAST""",
            (base,),
        )
        candidatos = cur.fetchall()

    diretos = [p for p in candidatos if _liga_suffix_bucket(p["liga_suffix"]) == alvo_bucket]
    if not diretos and alvo_bucket in ("alt_art", "parallel"):
        # A Liga BR não usa uma sigla única e consistente pro "print especial"
        # de cada carta (uma carta chama de AA, outra chama a mesma ideia de
        # PA/GS/FA...). Quando só existe UM candidato que não é o print base
        # nem uma sigla de torneio/promo, esse candidato só pode ser o print
        # especial da carta — mesma regra de fallback que o optcg-cloud usa
        # ("única parallel") quando o sufixo não bate com a tabela conhecida.
        nao_base_promo = [p for p in candidatos
                          if _liga_suffix_bucket(p["liga_suffix"]) not in ("base", "promo")]
        if len(nao_base_promo) == 1:
            diretos = nao_base_promo
    elif not diretos and alvo_bucket == "base":
        diretos = [p for p in candidatos if not p["liga_suffix"]]
    if not diretos and variant and variant not in _VARIANTE_BUCKET:
        # variante digitada não é uma das categorias conhecidas (AA/MA/TR/...)
        # — trata como o sufixo literal da Liga mesmo (ex.: usuário digitou
        # "PA" ou "GS" direto), sem inventar bucket pra ela.
        diretos = [p for p in candidatos if (p["liga_suffix"] or "").upper() == variant]

    ref = diretos[0] if diretos else None

    # a linha do catálogo tem que ser a da VARIANTE pedida — cada variante
    # (base/_p1/_p2...) pode ter imagem e raridade diferentes. Prioridade:
    # 1) o catalog_id que a própria Liga já associou a essa variante (link
    #    real do banco, não um palpite); 2) id exato base+sufixo digitado;
    # 3) variant_type quando o catálogo classificou a arte. Só cai pra
    # "qualquer linha completa" se nada bateu — e aí NÃO mostra a imagem de
    # outra variante (ver image_url abaixo), pra não passar a arte errada
    # como se fosse a da variante que o vendedor está anunciando.
    por_liga = next((l for l in linhas if ref and l["id"] == ref["catalog_id"]), None) if ref else None
    suf = "_" + variant.lower() if variant else ""
    exata = next((l for l in linhas if l["id"] == base + suf), None)
    tipo_alvo = {"AA": "alt_art", "SA": "alt_art", "MA": "manga"}.get(variant)
    por_tipo = next((l for l in linhas if l["variant_type"] == tipo_alvo), None) if tipo_alvo else None
    carta_variante = por_liga or exata or por_tipo
    carta = carta_variante or next((l for l in linhas if l["id"] == base), None) \
        or next((l for l in linhas if l["rarity"]), linhas[0])
    imagem_da_variante_certa = bool(carta_variante) or not variant

    # A imagem oficial do catalog (site da Bandai) costuma bloquear hotlink e
    # dar erro no navegador. A Liga BR guarda sua própria cópia da imagem por
    # anúncio (liga_image_url) — já hospedada de um jeito que carrega sem
    # bloqueio. Quando a variante bateu com um anúncio real da Liga, prefere
    # essa imagem (é a MESMA carta, só um espelho mais confiável), e só cai
    # pra imagem do catalog quando a Liga não tem nada pra essa variante.
    imagem = (ref["liga_image_url"] if ref and ref.get("liga_image_url") else None) \
        or (carta["image_url"] if imagem_da_variante_certa else None)

    def limpa(v):
        if not v:
            return []
        try:
            return json.loads(v)
        except Exception:
            return [x for x in re.split(r"[,\[\]\"]+", v) if x.strip()]

    return {
        "code": base,
        "variant": variant,
        "name": carta["name"],
        "rarity": carta["rarity"],
        "category": carta["category"],
        "colors": limpa(carta["colors"]),
        "cost": carta["cost"],
        "power": carta["power"],
        "counter": carta["counter"],
        "types": limpa(carta["types"]),
        "effect": carta["effect"],
        "trigger": carta["trigger_text"],
        "image_url": imagem,
        "completo": bool(carta["rarity"]),
        "liga": None if not ref else {
            "preco": float(ref["liga_price"]) if ref["liga_price"] is not None else None,
            "min": float(ref["liga_preco_min"]) if ref["liga_preco_min"] else None,
            "max": float(ref["liga_preco_max"]) if ref["liga_preco_max"] else None,
            "catalog_id": ref["catalog_id"],
            "codigo": ref["liga_code"],
            "url": ref["liga_page_url"],
            "atualizado": ref["updated_at"].isoformat() if ref["updated_at"] else None,
        },
    }


# ---------------------------------------------------------------- claude
def claude(messages, max_tokens=1400):
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada")
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "messages": messages}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return "\n".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")


def parse_json(txt):
    """Extrai o primeiro objeto JSON da resposta, ignorando qualquer texto
    que venha antes ou depois — a IA às vezes escreve um comentário depois
    do JSON, o que quebrava o find('{')/rfind('}') antigo com 'Extra data'."""
    t = txt.replace("```json", "").replace("```", "").strip()
    a = t.find("{")
    if a < 0:
        raise ValueError("resposta sem JSON")
    try:
        obj, _ = json.JSONDecoder().raw_decode(t, a)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON inválido na resposta: {e}")
    return obj


# ---------------------------------------------------------------- rotas
@app.get("/")
def home():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM catalog")
            n = cur.fetchone()["n"]
        return jsonify(ok=True, catalogo=n, ia=bool(ANTHROPIC_KEY))
    except Exception as e:
        return jsonify(ok=False, erro=str(e)), 500


@app.get("/api/carta")
def api_carta():
    c = buscar_carta(request.args.get("code", ""), request.args.get("variant", ""))
    if not c:
        return jsonify(erro="carta não encontrada no catálogo"), 404
    return jsonify(c)


@app.post("/api/atualizar-preco")
def api_atualizar_preco():
    body = request.json or {}
    liga_url = body.get("liga_url")
    if not liga_url:
        return jsonify(erro="liga_url obrigatório"), 400
    try:
        req = urllib.request.Request(
            OPTCG_LIVE_URL.rstrip("/") + "/liga/refresh",
            data=json.dumps({"liga_url": liga_url, "source": "anuncios_optcg"}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            resultado = json.loads(r.read())
    except Exception as e:
        return jsonify(erro=f"scraper ao vivo indisponível: {e}"), 502
    if resultado.get("error"):
        return jsonify(erro=resultado["error"]), 502
    carta = buscar_carta(body.get("code", ""), body.get("variant", ""))
    if not carta:
        return jsonify(erro="carta não encontrada após atualizar"), 404
    return jsonify(carta)


@app.get("/api/historico-preco")
def api_historico_preco():
    liga_url = request.args.get("liga_url", "")
    if not liga_url:
        return jsonify(erro="liga_url obrigatório"), 400
    try:
        qs = urllib.parse.urlencode({"url": liga_url})
        with urllib.request.urlopen(OPTCG_LIVE_URL.rstrip("/") + "/liga/history?" + qs, timeout=20) as r:
            return jsonify(json.loads(r.read()))
    except Exception as e:
        return jsonify(erro=f"histórico indisponível: {e}"), 502


@app.post("/api/traduzir")
def api_traduzir():
    body = request.json or {}
    texto = (body.get("texto") or "").strip()
    if not texto:
        return jsonify(erro="texto obrigatório"), 400
    try:
        traduzido = claude([{"role": "user", "content": (
            "Traduza este texto de efeito de carta do One Piece Card Game para português do Brasil. "
            "Mantenha termos de jogo entre colchetes como estão (ex.: [Blocker], [On Play], [DON!!x1]), "
            "traduzindo só o texto ao redor deles. Responda SÓ com a tradução, sem aspas, sem comentário, "
            "sem markdown:\n\n" + texto)}], 400)
    except Exception as e:
        return jsonify(erro=f"falha ao traduzir: {e}"), 502
    return jsonify(traduzido=traduzido.strip())


@app.post("/api/identificar")
def api_identificar():
    imgs = (request.json or {}).get("imagens") or []
    if not imgs:
        return jsonify(erro="nenhuma imagem enviada"), 400

    content = []
    for b64 in imgs[:4]:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": b64.split(",")[-1]}})
    content.append({"type": "text", "text": (
        "Estas fotos são de cartas do One Piece Card Game à venda. Identifique CADA carta distinta.\n\n"
        "O código está impresso na carta, no canto inferior direito: OP17-062, ST01-001, EB01-006, "
        "DON-003, P-084. Leia da imagem, não adivinhe — se não conseguir ler com confiança, deixe "
        "code vazio e confidence baixa.\n\n"
        "Classifique variant_type pela ARTE da carta, NÃO por uma sigla impressa (a maioria das "
        "cartas não imprime sufixo de variante nenhum). CHEQUE PRIMEIRO se há um número de série "
        "impresso (tipo 0123/1500, geralmente no canto inferior) — se tiver, é \"serial\" mesmo que "
        "a arte também pareça dramática/full art (Treasure Rare tem as duas coisas juntas, mas o que "
        "importa aqui é o número de série). Só depois de descartar isso, avalie o resto:\n"
        '- "base": arte padrão do set, composição normal, moldura colorida, texto de efeito legível\n'
        '- "serial": tem número de série impresso (ex.: 0123/1500) — confira isso ANTES de "alt_art"\n'
        '- "alt_art": ilustração alternativa sem número de série — arte bem diferente da base, '
        "geralmente sangria total (a arte cobre a carta inteira sem moldura), composição mais "
        "dramática\n"
        '- "manga": arte em preto e branco estilo mangá\n'
        "Leia a cor pela mandala/roda de cores no canto inferior esquerdo da carta, não pela "
        "ilustração.\n\n"
        "Responda SÓ com JSON, sem markdown:\n"
        '{"cards":[{"code":"OP17-062","variant_type":"alt_art","name":"Kaido","confidence":"alta"}]}\n\n'
        "confidence é alta, media ou baixa.")})

    try:
        data = parse_json(claude([{"role": "user", "content": content}], 1000))
    except Exception as e:
        return jsonify(erro=f"falha na identificação: {e}"), 502

    # A variante vem da classificação visual da arte (variant_type), nunca de uma sigla lida —
    # esse mapeamento é fixo e controlado aqui, não um chute da IA (mesmo princípio de não
    # inventar variante usado em buscar_carta).
    VARIANT_TYPE_TO_VARIANT = {"alt_art": "AA", "manga": "MA", "serial": "TR", "base": "", "reprint": ""}

    saida = []
    for c in data.get("cards", [])[:6]:
        code = (c.get("code") or "").upper().strip()
        variant = VARIANT_TYPE_TO_VARIANT.get((c.get("variant_type") or "").lower().strip(), "")
        item = {"code": code, "variant": variant, "name": c.get("name") or "",
                "confidence": c.get("confidence") or "media", "verified": False}
        try:
            ref = buscar_carta(code, variant)
        except Exception:
            ref = None
        if ref:
            ref["confidence"] = item["confidence"]
            ref["verified"] = True
            item = ref
        saida.append(item)
    return jsonify(cards=saida)


REGRAS = """REGRAS OBRIGATÓRIAS:
- Português brasileiro. Tom de colecionador pra colecionador: animado e envolvente, nunca publicitário raso.
- NÃO mencione efeito, cor(es), custo, poder ou arquétipos/tipos da carta — esses DADOS DE JOGO não
  entram no anúncio de jeito nenhum.
- Você PODE (e deve, principalmente no completo) usar curiosidades sobre o PERSONAGEM no universo
  One Piece — quem é, um momento marcante, uma característica marcante dele. Isso é bem-vindo e deixa
  o anúncio mais gostoso de ler. Não invente nada que não seja conhecido do universo.
- Sempre comece com uma chamada curta e chamativa sobre a carta ou personagem (pode ter 1 emoji nela,
  só nela), seguida da linha com nome e código em negrito: *Nome da carta | CODIGO-VARIANTE*
  (negrito do WhatsApp é *asterisco simples* de cada lado — nunca use ** duplo nem outro markdown).
- Linha de preço logo depois, EXATAMENTE como veio na ficha (copie o texto do campo "preco" sem
  alterar um caractere), seguida de "+ frete". Formato brasileiro de moeda: vírgula pros centavos,
  ponto pros milhares — nunca escreva ponto como separador de centavos (R$ 279,18 está certo,
  R$ 279.18 está errado). Se pct_abaixo_mdl vier preenchido, acrescente entre parênteses no formato
  "(-X% MDL)". Se vier nulo, não escreva nada sobre desconto ou referência de preço.
- Se quantidade for maior que 1, informe quantas unidades estão disponíveis dessa carta.
- Se falar de embalagem, use apenas a ideia "em sleeve e bem protegida". NUNCA mencione toploader,
  caixa, plástico ou qualquer outro detalhe de embalagem.
- SE A LISTA TIVER MAIS DE UMA CARTA, O ANÚNCIO TEM QUE FALAR DE TODAS, NENHUMA DE FORA. Uma chamada
  de abertura só (pode citar o lote como um todo), depois um bloco por carta — nome+código em negrito
  e preço de cada uma.
- NÃO escreva nada sobre envio/frete, condição de troca, forma de pagamento ou link de loja — isso é
  adicionado por fora, automaticamente, depois do seu texto. Termine seu texto logo após o(s)
  bloco(s) de carta (preço/desconto/estado/observação), sem nenhuma linha de fechamento sobre esses
  assuntos."""


LOJA_URL = "https://www.jornadagames.com/store/baumgartengustavo"

# emoji fixo por frase pronta (mesmo texto exato do checkbox no front-end) —
# garante que cada frase sempre sai com a mesma referência visual, sem
# depender da IA escolher um emoji diferente a cada anúncio.
_FRASE_EMOJI = {
    "Carta rara, poucas unidades no mercado.": "💎",
    "Saiu do Booster direto pro Sleeve e Binder.": "🛡️",
    "Somente venda, sem trocas.": "🚫",
    "Envio pelo SuperFrete com opção a sua escolha.": "🚚",
    "Aceito pagamento com cartão de crédito, consulte taxas.": "💳",
}


@app.post("/api/gerar")
def api_gerar():
    body = request.json or {}
    cards = body.get("cards") or []
    if not cards:
        return jsonify(erro="nenhuma carta informada"), 400
    incluir_loja = bool(body.get("incluir_loja"))
    frases_extras = [f.strip() for f in (body.get("frases") or []) if isinstance(f, str) and f.strip()]

    ficha = []
    for c in cards:
        preco = c.get("preco")
        liga = (c.get("liga") or {}).get("preco")
        # o desconto anunciado sempre vem do preço FINAL comparado ao preço da
        # Liga — nunca do sinal bruto do campo de ajuste (esse é só o controle
        # que o vendedor usa pra chegar no preço; um ajuste positivo pode até
        # deixar a carta mais cara que a Liga, e aí não tem desconto nenhum
        # pra anunciar).
        pct = None
        if preco and liga and liga > 0:
            calc = round((1 - float(preco) / float(liga)) * 100)
            if calc > 0:
                pct = calc
        ficha.append({
            "codigo": c.get("code", "") + ("-" + c["variant"] if c.get("variant") else ""),
            "nome": c.get("name"),
            "estado": c.get("estado") or "Mint",
            "quantidade": int(c.get("quantidade") or 1),
            "preco": f"R$ {float(preco):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if preco else None,
            "pct_abaixo_mdl": pct,
        })

    multiplas = len(ficha) > 1
    prompt = (
        "Você escreve anúncios de venda de cartas do One Piece Card Game para grupos de WhatsApp de "
        "colecionadores brasileiros. O vendedor é um colecionador que joga com o filho e vende do "
        "próprio acervo.\n\n"
        + (f"SÃO {len(ficha)} CARTAS NESTE LOTE — TODAS elas têm que aparecer no anúncio, cada uma "
           "com seu próprio bloco de nome+código em negrito e preço. Não escreva só sobre a primeira.\n\n"
           if multiplas else "") +
        "CARTAS:\n" + json.dumps(ficha, ensure_ascii=False, indent=1) +
        "\n\nOBSERVAÇÃO DO VENDEDOR: " + (body.get("observacao") or "nenhuma") +
        "\n\n" + REGRAS +
        '\n\nFORMATO — responda SÓ com este JSON:\n'
        '{"titulo":"chamada curta e chamativa sobre ' + ('o lote' if multiplas else 'a carta ou personagem')
        + ', pode ter 1 emoji",'
        '"curto":"chamada + ' + (f'um bloco por carta ({len(ficha)} cartas, todas)' if multiplas
                                  else '*Nome da carta | CODIGO-VARIANTE* em negrito')
        + ' com preço + frete (com -X% MDL se houver) — direto mas não seco, sem linha de envio/frases '
        'extras/loja no final (isso é adicionado por fora)",'
        '"completo":"chamada, 2 a 4 linhas de curiosidade real sobre ' + ('algum personagem do lote' if multiplas
                                                                          else 'o personagem') +
        ' no universo One Piece, depois ' + (f'um bloco por carta ({len(ficha)} cartas, todas)' if multiplas
                                              else 'o bloco da carta')
        + ' com nome+código em negrito, preço, desconto e estado, observação do vendedor se houver — '
        'sem dados de jogo (efeito, cor, custo, poder, arquétipo) e sem linha de envio/frases extras/loja '
        'no final (isso é adicionado por fora)"}'
    )
    try:
        d = parse_json(claude([{"role": "user", "content": prompt}], 1600 + 400 * len(ficha)))
    except Exception as e:
        return jsonify(erro=f"falha ao gerar: {e}"), 502

    # defesa extra: se o modelo trocar a vírgula por ponto num preço (formato
    # americano) mesmo depois de instruído a não fazer isso, corrige aqui —
    # sabemos exatamente qual string cada preço deveria ser, então é uma
    # substituição exata, não um regex genérico chutando separador decimal.
    precos_certos = [item["preco"] for item in ficha if item.get("preco")]
    for campo in ("titulo", "curto", "completo"):
        texto = d.get(campo)
        if not texto:
            continue
        for preco_certo in precos_certos:
            preco_errado = preco_certo.replace(",", ".")
            if preco_errado != preco_certo:
                texto = texto.replace(preco_errado, preco_certo)
        d[campo] = texto

    # Envio/frases extras/link da loja são texto FIXO e conhecido — monta essa
    # parte em Python (emoji certo, espaçamento, quebra de linha) em vez de
    # confiar na IA pra formatar igual toda vez. Some com linha em branco
    # entre o bloco da carta e esse bloco de informações, e mais uma antes do
    # link da loja, pra separar visualmente no WhatsApp.
    info = "📍 Envio por conta do comprador, saindo de Joinville/SC."
    for f in frases_extras:
        info += f"\n{_FRASE_EMOJI.get(f, '▪️')} {f}"
    if incluir_loja:
        info += f"\n\n🛒 Mais cartas na minha loja no JornadaGames:\n{LOJA_URL}"
    for campo in ("curto", "completo"):
        texto = (d.get(campo) or "").rstrip()
        d[campo] = (texto + "\n\n" + info) if texto else info

    return jsonify(titulo=d.get("titulo", ""), curto=d.get("curto", ""), completo=d.get("completo", ""))


@app.post("/api/anuncios")
def api_salvar():
    b = request.json or {}
    cards = b.get("cards") or []
    total = sum(float(c.get("preco") or 0) * int(c.get("quantidade") or 1) for c in cards)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO anuncio (titulo, texto_curto, texto_completo, observacao, total, cards, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'publicado') RETURNING id""",
                (b.get("titulo"), b.get("curto"), b.get("completo"), b.get("observacao"),
                 total, json.dumps(cards, ensure_ascii=False)))
            aid = cur.fetchone()["id"]
            for i, img in enumerate((b.get("imagens") or [])[:4]):
                cur.execute(
                    "INSERT INTO anuncio_imagem (anuncio_id, ordem, dados) VALUES (%s,%s,%s)",
                    (aid, i, img))
            c.commit()
        return jsonify(id=aid)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.get("/api/anuncios/<int:aid>")
def api_anuncio_detalhe(aid):
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT id, criado_em, titulo, texto_curto, texto_completo, observacao, total, cards
                     FROM anuncio WHERE id = %s""", (aid,))
            row = cur.fetchone()
            if not row:
                return jsonify(erro="anúncio não encontrado"), 404
            cur.execute(
                "SELECT dados FROM anuncio_imagem WHERE anuncio_id = %s ORDER BY ordem", (aid,))
            imagens = [r["dados"] for r in cur.fetchall()]
        row["criado_em"] = row["criado_em"].isoformat()
        row["total"] = float(row["total"] or 0)
        row["imagens"] = imagens
        return jsonify(row)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.put("/api/anuncios/<int:aid>")
def api_anuncio_atualizar(aid):
    b = request.json or {}
    cards = b.get("cards") or []
    total = sum(float(c.get("preco") or 0) * int(c.get("quantidade") or 1) for c in cards)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """UPDATE anuncio SET titulo=%s, texto_curto=%s, texto_completo=%s, observacao=%s,
                          total=%s, cards=%s WHERE id=%s""",
                (b.get("titulo"), b.get("curto"), b.get("completo"), b.get("observacao"),
                 total, json.dumps(cards, ensure_ascii=False), aid))
            cur.execute("DELETE FROM anuncio_imagem WHERE anuncio_id = %s", (aid,))
            for i, img in enumerate((b.get("imagens") or [])[:4]):
                cur.execute(
                    "INSERT INTO anuncio_imagem (anuncio_id, ordem, dados) VALUES (%s,%s,%s)",
                    (aid, i, img))
            c.commit()
        return jsonify(id=aid)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.get("/api/anuncios")
def api_listar():
    q = (request.args.get("q") or "").strip()
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT a.id, a.criado_em, a.titulo, a.texto_curto, a.texto_completo,
                          a.total, a.cards,
                          (SELECT dados FROM anuncio_imagem i
                            WHERE i.anuncio_id = a.id ORDER BY ordem LIMIT 1) AS capa
                     FROM anuncio a
                    WHERE %s = '' OR a.cards::text ILIKE '%%'||%s||'%%'
                    ORDER BY a.criado_em DESC LIMIT 100""", (q, q))
            rows = cur.fetchall()
        for r in rows:
            r["criado_em"] = r["criado_em"].isoformat()
            r["total"] = float(r["total"] or 0)
        return jsonify(anuncios=rows)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.delete("/api/anuncios/<int:aid>")
def api_excluir(aid):
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM anuncio WHERE id = %s", (aid,))
            c.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(erro=str(e)), 500


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
