import os, json, base64, re, datetime, uuid
import psycopg
from psycopg.rows import dict_row
import urllib.request
import urllib.parse
import urllib.error
import numpy as np
import cv2
from flask import Flask, request, jsonify, render_template

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPTCG_LIVE_URL = os.environ.get("OPTCG_LIVE_URL", "https://optcg-cloud-v2.onrender.com")
SUPERFRETE_TOKEN = os.environ.get("SUPERFRETE_TOKEN", "")
SUPERFRETE_BASE = os.environ.get("SUPERFRETE_BASE", "https://api.superfrete.com")

# endereço fixo de origem (remetente) — sempre o mesmo, informado pelo usuário
ORIGEM_ENDERECO = {
    "name": "Gustavo Baumgarten", "address": "Rua Henrique Meyer", "number": "184",
    "complement": "ap 1208", "district": "Centro", "city": "Joinville",
    "state_abbr": "SC", "postal_code": "89201405",
}
ORIGEM_CEP = "89201405"
# dimensões/peso padrão pra 1 carta em sleeve + toploader dentro de um
# envelope rígido pequeno — ajustável por requisição quando precisar.
PACOTE_PADRAO = {"height": 2, "width": 12, "length": 17, "weight": 0.08}
SUPERFRETE_SERVICOS = {"PAC": 1, "SEDEX": 2, "Mini Envios": 17, "Jadlog": 3, "Loggi": 31, "J&T": 33}


def _superfrete(path, body):
    """POST autenticado na API da SuperFrete (produção). Nunca chamamos
    /cart ou /checkout sem confirmação explícita do usuário — checkout gasta
    saldo real da carteira dele."""
    if not SUPERFRETE_TOKEN:
        raise RuntimeError("SUPERFRETE_TOKEN não configurado")
    req = urllib.request.Request(
        SUPERFRETE_BASE.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + SUPERFRETE_TOKEN,
            "User-Agent": "AnunciosOPTCG/1.0 (gustavo.baumgarten@gmail.com)",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="replace")
        raise RuntimeError(f"SuperFrete {e.code}: {detalhe[:300]}")


def _superfrete_get(path):
    """GET autenticado na API da SuperFrete — só consulta, nunca gasta
    saldo. Usado pra checar o status real de um pedido já criado (ex.:
    descobrir se foi cancelado depois por erro no endereço).

    O path exato (GET /api/v0/orders/{order_id}) foi mapeado a partir do
    pacote open-source deco-cx/apps/superfrete, não da doc oficial — por
    isso o tratamento de erro aqui é generoso: se a resposta não vier em
    JSON, mostra o corpo cru em vez de estourar um erro de parse opaco,
    pra dar pista de qual é o formato/endpoint certo."""
    if not SUPERFRETE_TOKEN:
        raise RuntimeError("SUPERFRETE_TOKEN não configurado")
    req = urllib.request.Request(
        SUPERFRETE_BASE.rstrip("/") + path,
        headers={
            "Authorization": "Bearer " + SUPERFRETE_TOKEN,
            "User-Agent": "AnunciosOPTCG/1.0 (gustavo.baumgarten@gmail.com)",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            corpo = r.read()
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="replace")
        raise RuntimeError(f"SuperFrete {e.code}: {detalhe[:300]}")
    if not corpo:
        raise RuntimeError(
            "SuperFrete respondeu sem conteúdo — o endpoint de status pode não "
            "existir nessa conta/plano ou exigir outra URL. Confirme em "
            "https://superfrete.readme.io/reference qual é o endpoint certo."
        )
    try:
        return json.loads(corpo)
    except json.JSONDecodeError:
        raise RuntimeError(f"SuperFrete não devolveu JSON, corpo recebido: {corpo[:300]!r}")


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
CREATE TABLE IF NOT EXISTS venda (
  id                bigserial PRIMARY KEY,
  anuncio_id        bigint REFERENCES anuncio(id) ON DELETE SET NULL,
  criado_em         timestamptz NOT NULL DEFAULT now(),
  origem            text NOT NULL DEFAULT 'outro',
  origem_detalhe    text,
  comprador         text,
  cards             jsonb NOT NULL DEFAULT '[]'::jsonb,
  preco_total       numeric(10,2),
  endereco          jsonb,
  frete_servico     text,
  frete_valor       numeric(10,2),
  frete_order_id    text,
  etiqueta_url      text,
  etiqueta_rastreio text,
  etiqueta_status   text
);
CREATE INDEX IF NOT EXISTS venda_criado_idx ON venda (criado_em DESC);
CREATE INDEX IF NOT EXISTS venda_anuncio_idx ON venda (anuncio_id);
"""


def init_db():
    """Cria as tabelas novas. Não toca em nada que já existe no banco."""
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(SCHEMA)
            # coluna nova numa tabela que já existe — ADD COLUMN IF NOT EXISTS
            # é aditivo e seguro, diferente de mexer em coluna já existente.
            cur.execute("ALTER TABLE anuncio ADD COLUMN IF NOT EXISTS vendido_em timestamptz")
            # lote_id agrupa as linhas que nasceram do mesmo clique em "Salvar
            # no banco" (um anúncio com várias cartas agora vira uma linha por
            # carta — ver api_salvar) — só pra mostrar "parte de um lote de N"
            # na lista, não afeta venda nenhuma.
            cur.execute("ALTER TABLE anuncio ADD COLUMN IF NOT EXISTS lote_id text")
            cur.execute("CREATE INDEX IF NOT EXISTS anuncio_lote_idx ON anuncio (lote_id)")
            # venda passa a poder ligar em VÁRIOS anúncios (vender cartas de
            # anúncios diferentes numa venda só, mesmo frete/comprador).
            # anuncio_id (singular) fica na tabela só como dado histórico —
            # nada no código lê/escreve nele depois deste backfill.
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS anuncio_ids bigint[] NOT NULL DEFAULT '{}'")
            cur.execute(
                """UPDATE venda SET anuncio_ids = ARRAY[anuncio_id]
                    WHERE anuncio_id IS NOT NULL AND anuncio_ids = '{}'""")
            cur.execute("CREATE INDEX IF NOT EXISTS venda_anuncio_ids_idx ON venda USING GIN (anuncio_ids)")
            # pagamento, datas e comprovante — item 7/9 do backlog.
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS forma_pagamento text")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS pagamento_status text")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS desconto numeric(10,2)")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS observacao text")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS data_venda date")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS data_envio date")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS data_recebimento date")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS banco_recebimento text")
            cur.execute("ALTER TABLE venda ADD COLUMN IF NOT EXISTS comprovante_envio text")
            cur.execute(
                "UPDATE venda SET data_venda = criado_em::date WHERE data_venda IS NULL")
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
            """SELECT m.catalog_id, m.liga_code, m.liga_price, m.liga_preco_min, m.liga_preco_max,
                      m.liga_suffix, m.liga_page_url, m.liga_image_url, m.updated_at,
                      s.store_count, s.total_stock, s.checked_at AS estoque_checado_em
                 FROM liga_catalog_map m
                 LEFT JOIN liga_price_snapshot s ON s.liga_url = m.liga_page_url
                WHERE m.base_code = %s AND m.liga_page_url IS NOT NULL
                ORDER BY m.updated_at DESC NULLS LAST""",
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
            "lojas": ref["store_count"],
            "estoque_total": ref["total_stock"],
            "estoque_atualizado": ref["estoque_checado_em"].isoformat() if ref["estoque_checado_em"] else None,
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


@app.post("/api/melhorar-foto")
def api_melhorar_foto():
    """Melhora qualidade da foto (ruído/nitidez/contraste local) sem alterar
    o conteúdo — nada de super-resolução generativa, que pode "inventar"
    textura/detalhe que não existe na foto real. Só processamento de imagem
    clássico (denoise + CLAHE + unsharp mask), determinístico e sem chamar IA
    externa nenhuma."""
    dados = (request.json or {}).get("imagem") or ""
    m = re.match(r"^data:image/\w+;base64,(.+)$", dados, re.S)
    if not m:
        return jsonify(erro="imagem inválida"), 400
    try:
        raw = base64.b64decode(m.group(1))
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("formato de imagem não reconhecido")
    except Exception as e:
        return jsonify(erro=f"imagem inválida: {e}"), 400

    # limite de tamanho pra manter o processamento rápido num servidor sem GPU
    max_lado = 1600
    h, w = img.shape[:2]
    if max(h, w) > max_lado:
        escala = max_lado / max(h, w)
        img = cv2.resize(img, (int(w * escala), int(h * escala)), interpolation=cv2.INTER_AREA)

    try:
        # 1) remove ruído de sensor/compressão preservando bordas (evita
        #    borrar o texto/arte da carta)
        den = cv2.fastNlMeansDenoisingColored(img, None, 6, 6, 7, 21)

        # 2) contraste local adaptativo — compensa luz desigual e reflexo de
        #    sleeve sem estourar o resto da foto (CLAHE só no canal de luz)
        lab = cv2.cvtColor(den, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

        # 3) nitidez (unsharp mask) pra recuperar detalhe fino que o denoise
        #    suaviza — reforça bordas já existentes, não desenha nada novo
        blur = cv2.GaussianBlur(out, (0, 0), sigmaX=3)
        out = cv2.addWeighted(out, 1.5, blur, -0.5, 0)
    except Exception as e:
        return jsonify(erro=f"falha ao processar imagem: {e}"), 500

    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return jsonify(erro="falha ao gerar imagem processada"), 500
    b64 = base64.b64encode(buf.tobytes()).decode()
    return jsonify(imagem=f"data:image/jpeg;base64,{b64}")


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
    """Um anúncio pode falar de várias cartas juntas (o texto gerado menciona
    todas), mas cada carta vira sua PRÓPRIA linha em `anuncio` — assim dá pra
    marcar/vender cada carta de um post separadamente depois, sem depender
    das outras. Todas as linhas nascidas deste clique guardam o mesmo texto
    (é o post como foi anunciado) e o mesmo `lote_id`, só pra saber depois
    que vieram do mesmo anúncio."""
    b = request.json or {}
    cards = b.get("cards") or []
    if not cards:
        return jsonify(erro="nenhuma carta informada"), 400
    lote_id = str(uuid.uuid4())
    imagens = (b.get("imagens") or [])[:4]
    ids = []
    try:
        with conn() as c, c.cursor() as cur:
            for i, card in enumerate(cards):
                total_carta = float(card.get("preco") or 0) * int(card.get("quantidade") or 1)
                cur.execute(
                    """INSERT INTO anuncio (titulo, texto_curto, texto_completo, observacao, total, cards,
                                             status, lote_id)
                       VALUES (%s,%s,%s,%s,%s,%s,'publicado',%s) RETURNING id""",
                    (b.get("titulo"), b.get("curto"), b.get("completo"), b.get("observacao"),
                     total_carta, json.dumps([card], ensure_ascii=False), lote_id))
                aid = cur.fetchone()["id"]
                ids.append(aid)
                # cada carta leva só a(s) foto(s) DELA: a foto na mesma posição
                # em que ela apareceu no formulário (photos[i] casa com
                # cards[i], mesma convenção da tela de criação) e o verso
                # próprio, se tiver — nunca a foto de outra carta do lote.
                fotos_da_carta = []
                if i < len(imagens):
                    fotos_da_carta.append(imagens[i])
                verso = (card.get("verso") or {}).get("full")
                if verso:
                    fotos_da_carta.append(verso)
                for j, img in enumerate(fotos_da_carta):
                    cur.execute(
                        "INSERT INTO anuncio_imagem (anuncio_id, ordem, dados) VALUES (%s,%s,%s)",
                        (aid, j, img))
            c.commit()
        return jsonify(ids=ids, id=ids[0])
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
                          a.total, a.cards, a.status, a.vendido_em, a.lote_id,
                          CASE WHEN a.lote_id IS NULL THEN 1
                               ELSE COUNT(*) OVER (PARTITION BY a.lote_id) END AS lote_tamanho,
                          (SELECT dados FROM anuncio_imagem i
                            WHERE i.anuncio_id = a.id ORDER BY ordem LIMIT 1) AS capa
                     FROM anuncio a
                    WHERE %s = '' OR a.cards::text ILIKE '%%'||%s||'%%'
                    ORDER BY a.criado_em DESC LIMIT 100""", (q, q))
            rows = cur.fetchall()
            cur.execute("SELECT count(*) AS n FROM anuncio WHERE status = 'vendida'")
            vendidos = cur.fetchone()["n"]
        for r in rows:
            r["criado_em"] = r["criado_em"].isoformat()
            r["vendido_em"] = r["vendido_em"].isoformat() if r["vendido_em"] else None
            r["total"] = float(r["total"] or 0)
        return jsonify(anuncios=rows, vendidos=vendidos)
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


# ---------------------------------------------------------------- vendas
@app.get("/api/cep/<cep>")
def api_cep(cep):
    cep = re.sub(r"\D", "", cep or "")
    if len(cep) != 8:
        return jsonify(erro="CEP inválido — precisa ter 8 dígitos"), 400
    try:
        with urllib.request.urlopen(f"https://viacep.com.br/ws/{cep}/json/", timeout=10) as r:
            d = json.loads(r.read())
    except Exception as e:
        return jsonify(erro=f"falha ao consultar CEP: {e}"), 502
    if d.get("erro"):
        return jsonify(erro="CEP não encontrado"), 404
    return jsonify(
        cep=cep,
        rua=d.get("logradouro") or "",
        complemento=d.get("complemento") or "",
        bairro=d.get("bairro") or "",
        cidade=d.get("localidade") or "",
        uf=d.get("uf") or "",
    )


@app.post("/api/frete/calcular")
def api_frete_calcular():
    body = request.json or {}
    cep = re.sub(r"\D", "", body.get("cep") or "")
    if len(cep) != 8:
        return jsonify(erro="CEP inválido — precisa ter 8 dígitos"), 400
    pacote = {
        "height": float(body.get("altura") or PACOTE_PADRAO["height"]),
        "width": float(body.get("largura") or PACOTE_PADRAO["width"]),
        "length": float(body.get("comprimento") or PACOTE_PADRAO["length"]),
        "weight": float(body.get("peso") or PACOTE_PADRAO["weight"]),
    }
    try:
        resultado = _superfrete("/api/v0/calculator", {
            "from": {"postal_code": ORIGEM_CEP},
            "to": {"postal_code": cep},
            "services": "1,2,17,3,31,33",
            "package": pacote,
            "options": {"own_hand": False, "receipt": False, "insurance_value": 0,
                        "use_insurance_value": False},
        })
    except Exception as e:
        return jsonify(erro=f"falha ao calcular frete: {e}"), 502
    opcoes = [o for o in resultado if not o.get("has_error")] if isinstance(resultado, list) else []
    return jsonify(opcoes=opcoes, cep=cep)


@app.get("/api/vendas")
def api_vendas_listar():
    q = (request.args.get("q") or "").strip()
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """SELECT v.*,
                          (SELECT dados FROM anuncio_imagem i
                            WHERE i.anuncio_id = ANY(v.anuncio_ids) ORDER BY ordem LIMIT 1) AS capa_anuncio
                     FROM venda v
                    WHERE %s = '' OR v.cards::text ILIKE '%%'||%s||'%%'
                       OR v.comprador ILIKE '%%'||%s||'%%'
                    ORDER BY v.criado_em DESC LIMIT 300""", (q, q, q))
            vendas = cur.fetchall()
            cur.execute("SELECT count(*) n, COALESCE(sum(preco_total),0) receita FROM venda")
            tot = cur.fetchone()
            cur.execute(
                """SELECT COALESCE(origem,'outro') o, count(*) n, COALESCE(sum(preco_total),0) receita
                     FROM venda GROUP BY o ORDER BY n DESC""")
            por_origem = cur.fetchall()
        for v in vendas:
            v["criado_em"] = v["criado_em"].isoformat()
            v["preco_total"] = float(v["preco_total"] or 0)
            v["frete_valor"] = float(v["frete_valor"]) if v["frete_valor"] is not None else None
            v["desconto"] = float(v["desconto"]) if v["desconto"] is not None else None
            v["data_venda"] = v["data_venda"].isoformat() if v["data_venda"] else None
            v["data_envio"] = v["data_envio"].isoformat() if v["data_envio"] else None
            v["data_recebimento"] = v["data_recebimento"].isoformat() if v["data_recebimento"] else None
        indicadores = {
            "total_vendas": tot["n"],
            "receita_total": float(tot["receita"] or 0),
            "ticket_medio": (float(tot["receita"]) / tot["n"]) if tot["n"] else 0,
            "por_origem": [{"origem": o["o"], "quantidade": o["n"], "receita": float(o["receita"] or 0)}
                           for o in por_origem],
        }
        return jsonify(vendas=vendas, indicadores=indicadores)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.post("/api/vendas")
def api_vendas_criar():
    """anuncio_ids (opcional) liga a venda a um ou mais anúncios já
    publicados — usado quando o vendedor marca uma ou várias cartas
    anunciadas como vendidas (juntas, mesmo frete, mesmo comprador): o modal
    abre pré-carregado com as cartas escolhidas e, ao salvar, todos os
    anúncios ligados mudam pra status='vendida' na mesma transação."""
    b = request.json or {}
    cards = b.get("cards") or []
    anuncio_ids = b.get("anuncio_ids") or []
    preco = float(b["preco_total"]) if b.get("preco_total") not in (None, "") else \
        sum(float(c.get("preco") or 0) * int(c.get("quantidade") or 1) for c in cards)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO venda (anuncio_ids, origem, origem_detalhe, comprador, cards, preco_total,
                                       frete_servico, frete_valor, forma_pagamento, pagamento_status,
                                       desconto, observacao, data_venda, data_envio, data_recebimento,
                                       banco_recebimento, comprovante_envio)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (anuncio_ids, b.get("origem") or "outro", b.get("origem_detalhe"), b.get("comprador"),
                 json.dumps(cards, ensure_ascii=False), preco,
                 b.get("frete_servico"), b.get("frete_valor"), b.get("forma_pagamento"),
                 b.get("pagamento_status"), b.get("desconto"), b.get("observacao"),
                 b.get("data_venda") or datetime.date.today().isoformat(), b.get("data_envio"),
                 b.get("data_recebimento"), b.get("banco_recebimento"), b.get("comprovante_envio")))
            vid = cur.fetchone()["id"]
            if anuncio_ids:
                cur.execute(
                    "UPDATE anuncio SET status='vendida', vendido_em=now() WHERE id = ANY(%s)", (anuncio_ids,))
            c.commit()
        return jsonify(id=vid)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.put("/api/vendas/<int:vid>")
def api_vendas_atualizar(vid):
    b = request.json or {}
    campos, valores = [], []
    for campo in ("origem", "origem_detalhe", "comprador", "frete_servico", "forma_pagamento",
                  "pagamento_status", "observacao", "data_venda", "data_envio", "data_recebimento",
                  "banco_recebimento", "comprovante_envio"):
        if campo in b:
            campos.append(f"{campo}=%s")
            valores.append(b[campo])
    if "cards" in b:
        campos.append("cards=%s")
        valores.append(json.dumps(b["cards"], ensure_ascii=False))
    if "preco_total" in b:
        campos.append("preco_total=%s")
        valores.append(b["preco_total"])
    if "frete_valor" in b:
        campos.append("frete_valor=%s")
        valores.append(b["frete_valor"])
    if "desconto" in b:
        campos.append("desconto=%s")
        valores.append(b["desconto"])
    if "endereco" in b:
        campos.append("endereco=%s")
        valores.append(json.dumps(b["endereco"], ensure_ascii=False) if b["endereco"] else None)
    if not campos:
        return jsonify(erro="nada pra atualizar"), 400
    valores.append(vid)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(f"UPDATE venda SET {', '.join(campos)} WHERE id=%s", valores)
            if cur.rowcount == 0:
                return jsonify(erro="venda não encontrada"), 404
            c.commit()
        return jsonify(id=vid)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.delete("/api/vendas/<int:vid>")
def api_vendas_excluir(vid):
    """Excluir uma venda também desfaz a marca de vendida em TODOS os
    anúncios ligados (quando existem) — sem isso eles ficariam com
    status='vendida' escondidos da aba Anúncios sem nenhuma venda que os
    explique."""
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT anuncio_ids FROM venda WHERE id = %s", (vid,))
            row = cur.fetchone()
            cur.execute("DELETE FROM venda WHERE id = %s", (vid,))
            if row and row["anuncio_ids"]:
                cur.execute(
                    "UPDATE anuncio SET status='publicado', vendido_em=NULL WHERE id = ANY(%s)",
                    (row["anuncio_ids"],))
            c.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(erro=str(e)), 500


@app.post("/api/vendas/<int:vid>/etiqueta")
def api_vendas_etiqueta(vid):
    """Fluxo completo de etiqueta: cria o pedido na SuperFrete (/cart), paga
    com o saldo da carteira (/checkout) e grava o link do PDF + rastreio.
    Gasta saldo real — só roda com confirmar:true explícito no corpo, além
    da confirmação que o front já pede antes de chamar isso."""
    b = request.json or {}
    if not b.get("confirmar"):
        return jsonify(erro="confirmação obrigatória — isso gera e paga uma etiqueta de verdade"), 400
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT * FROM venda WHERE id = %s", (vid,))
            venda = cur.fetchone()
        if not venda:
            return jsonify(erro="venda não encontrada"), 404
        endereco = venda.get("endereco") or {}
        if not endereco.get("cep"):
            return jsonify(erro="preencha o endereço completo do comprador antes de gerar a etiqueta"), 400

        servico_id = SUPERFRETE_SERVICOS.get(venda.get("frete_servico"), 1)
        cards = venda.get("cards") or []
        produtos = [{"name": (c.get("nome") or c.get("name") or "Carta OPTCG"),
                     "quantity": int(c.get("quantidade") or 1),
                     "unitary_value": float(c.get("preco") or 0)} for c in cards] or \
                   [{"name": "Carta OPTCG", "quantity": 1, "unitary_value": float(venda.get("preco_total") or 0)}]

        pedido = _superfrete("/api/v0/cart", {
            "from": ORIGEM_ENDERECO,
            "to": {
                "name": endereco.get("nome") or venda.get("comprador") or "Comprador",
                "address": endereco.get("rua") or "",
                "number": endereco.get("numero") or "",
                "complement": endereco.get("complemento") or "",
                "district": endereco.get("bairro") or "",
                "city": endereco.get("cidade") or "",
                "state_abbr": (endereco.get("uf") or "").upper(),
                "postal_code": re.sub(r"\D", "", endereco.get("cep") or ""),
                "document": re.sub(r"\D", "", endereco.get("cpf") or "") or None,
                "phone": re.sub(r"\D", "", endereco.get("telefone") or "") or None,
                "email": endereco.get("email") or None,
            },
            "service": servico_id,
            "products": produtos,
            "volumes": PACOTE_PADRAO,
            "options": {"non_commercial": True},
            "platform": "AnunciosOPTCG",
        })
        order_id = pedido.get("id")
        if not order_id:
            raise RuntimeError(f"resposta inesperada da SuperFrete ao criar o pedido: {pedido}")

        pagamento = _superfrete("/api/v0/checkout", {"orders": [order_id]})
        if not pagamento.get("success"):
            raise RuntimeError(f"checkout recusado: {pagamento}")
        info = ((pagamento.get("purchase") or {}).get("orders") or [{}])[0]
        etiqueta_url = (info.get("print") or {}).get("url") or ""
        rastreio = info.get("tracking") or ""

        with conn() as c, c.cursor() as cur:
            cur.execute(
                """UPDATE venda SET frete_order_id=%s, etiqueta_url=%s, etiqueta_rastreio=%s,
                          etiqueta_status='gerada' WHERE id=%s""",
                (order_id, etiqueta_url, rastreio, vid))
            c.commit()
        return jsonify(etiqueta_url=etiqueta_url, rastreio=rastreio)
    except Exception as e:
        try:
            with conn() as c, c.cursor() as cur:
                cur.execute("UPDATE venda SET etiqueta_status='erro' WHERE id=%s", (vid,))
                c.commit()
        except Exception:
            pass
        return jsonify(erro=f"falha ao gerar etiqueta: {e}"), 502


@app.get("/api/vendas/<int:vid>/etiqueta/status")
def api_vendas_etiqueta_status(vid):
    """Consulta o status ATUAL do pedido na SuperFrete (GET — só leitura,
    não gasta saldo nenhum). Serve pra descobrir se uma etiqueta já gerada
    foi cancelada depois (ex.: erro no endereço, resolvido só no painel da
    SuperFrete) sem precisar ir checar lá manualmente. Atualiza
    etiqueta_status aqui com o que a SuperFrete responder."""
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT frete_order_id FROM venda WHERE id = %s", (vid,))
            venda = cur.fetchone()
        if not venda:
            return jsonify(erro="venda não encontrada"), 404
        order_id = venda.get("frete_order_id")
        if not order_id:
            return jsonify(erro="esta venda ainda não tem pedido criado na SuperFrete"), 400
        info = _superfrete_get(f"/api/v0/orders/{order_id}")
        status = info.get("status") or ""
        with conn() as c, c.cursor() as cur:
            cur.execute("UPDATE venda SET etiqueta_status=%s WHERE id=%s", (status, vid))
            c.commit()
        return jsonify(status=status, tracking=info.get("tracking") or "",
                       eventos=info.get("tracking_events") or [])
    except Exception as e:
        return jsonify(erro=f"falha ao consultar status: {e}"), 502


@app.post("/api/vendas/<int:vid>/etiqueta/resetar")
def api_vendas_etiqueta_resetar(vid):
    """Esquece a etiqueta gerada nesta venda pra liberar "Gerar etiqueta"
    de novo. NÃO cancela nem reembolsa nada na SuperFrete — isso já deve
    ter sido feito manualmente lá (ou confirmado via /etiqueta/status)
    antes de chamar isso."""
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                """UPDATE venda SET etiqueta_url=NULL, etiqueta_rastreio=NULL,
                          frete_order_id=NULL, etiqueta_status='cancelada' WHERE id=%s""",
                (vid,))
            if cur.rowcount == 0:
                return jsonify(erro="venda não encontrada"), 404
            c.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(erro=str(e)), 500


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
