import os, json, base64, re, datetime
import psycopg
from psycopg.rows import dict_row
import urllib.request
from flask import Flask, request, jsonify, render_template

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

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

# variante informada -> sufixos de catalog_id, na ordem de preferência
VARIANTES = {
    "":   ["", "_pa"],
    "AA": ["_aa", "_p1"],
    "SA": ["_aa", "_p1"],
    "SP": ["_ma", "_p2"],
    "MA": ["_ma", "_p2"],
    "SEC": ["", "_p1"],
    "TR": ["_tr", ""],
    "SF": ["", "_p1"],
}


def base_code(code):
    m = BASE_RE.match((code or "").strip().upper())
    return m.group(1) if m else ""


def buscar_carta(code, variant=""):
    """Dados da carta no catálogo + preço de referência da Liga."""
    base = base_code(code)
    if not base:
        return None
    variant = (variant or "").strip().upper()
    sufixos = VARIANTES.get(variant, ["_" + variant.lower(), "_p1", ""])

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

        # a linha base é a mais completa; algumas importações vêm vazias
        carta = next((l for l in linhas if l["rarity"]), linhas[0])

        # preço de referência: tenta os sufixos na ordem, depois qualquer um do base
        cur.execute(
            """SELECT catalog_id, liga_price, liga_preco_min, liga_preco_max,
                      liga_suffix, liga_page_url, updated_at
                 FROM liga_catalog_map
                WHERE base_code = %s AND liga_price IS NOT NULL""",
            (base,),
        )
        precos = {p["catalog_id"]: p for p in cur.fetchall()}

    ref = None
    for suf in sufixos:
        if base + suf in precos:
            ref = precos[base + suf]
            break
    if ref is None and precos:
        ref = sorted(precos.values(), key=lambda p: p["liga_price"])[0]

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
        "image_url": carta["image_url"],
        "completo": bool(carta["rarity"]),
        "liga": None if not ref else {
            "preco": float(ref["liga_price"]),
            "min": float(ref["liga_preco_min"]) if ref["liga_preco_min"] else None,
            "max": float(ref["liga_preco_max"]) if ref["liga_preco_max"] else None,
            "catalog_id": ref["catalog_id"],
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
    t = txt.replace("```json", "").replace("```", "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("resposta sem JSON")
    return json.loads(t[a:b + 1])


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
        "DON-003, P-084. Leia da imagem, não adivinhe.\n\n"
        "Sufixos de variante quando visíveis: AA (alternate art), SA (super alternate art), "
        "SP ou MA (manga rare), TR (treasure rare), SF (foil especial).\n\n"
        "Responda SÓ com JSON, sem markdown:\n"
        '{"cards":[{"code":"OP17-062","variant":"SA","name":"Kaido","confidence":"alta"}]}\n\n'
        "variant é string vazia na arte normal. confidence é alta, media ou baixa. "
        "Se não conseguir ler o código, deixe code vazio e confidence baixa.")})

    try:
        data = parse_json(claude([{"role": "user", "content": content}], 1000))
    except Exception as e:
        return jsonify(erro=f"falha na identificação: {e}"), 502

    saida = []
    for c in data.get("cards", [])[:6]:
        code = (c.get("code") or "").upper().strip()
        variant = (c.get("variant") or "").upper().strip()
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
- Português brasileiro. Tom de colecionador para colecionador: entusiasmado e direto, nunca publicitário.
- Se falar de embalagem, use apenas a ideia "em sleeve e bem protegida". NUNCA mencione toploader,
  caixa, plástico ou qualquer outro detalhe de embalagem.
- Envio pelos Correios (SEDEX), saindo de Joinville/SC, com rastreio.
- Preço exatamente como veio na ficha, seguido de "+ frete".
- Só cite desconto se pct_abaixo_mdl vier preenchido, no formato "X% abaixo do valor de referência MDL".
  Se vier nulo, não invente nada sobre referência de preço.
- Não invente raridade, poder, efeito ou tiragem. Use só o que está na ficha. Você pode usar o que
  se sabe do personagem no universo One Piece para escrever a chamada.
- Negrito do WhatsApp é *asteriscos simples*. Nada de markdown, ## ou **.
- Emojis com moderação, no início das linhas-chave."""


@app.post("/api/gerar")
def api_gerar():
    body = request.json or {}
    cards = body.get("cards") or []
    if not cards:
        return jsonify(erro="nenhuma carta informada"), 400

    ficha = []
    for c in cards:
        preco = c.get("preco")
        liga = (c.get("liga") or {}).get("preco")
        pct = None
        if preco and liga and liga > 0:
            calc = round((1 - float(preco) / float(liga)) * 100)
            if calc > 0:
                pct = calc
        if c.get("pct_manual"):
            pct = int(c["pct_manual"])
        ficha.append({
            "codigo": c.get("code", "") + ("-" + c["variant"] if c.get("variant") else ""),
            "nome": c.get("name"), "raridade": c.get("rarity"), "categoria": c.get("category"),
            "cores": c.get("colors"), "custo": c.get("cost"), "poder": c.get("power"),
            "arquetipos": c.get("types"), "efeito": c.get("effect"),
            "estado": c.get("estado") or "Mint",
            "preco": f"R$ {float(preco):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if preco else None,
            "pct_abaixo_mdl": pct,
        })

    prompt = (
        "Você escreve anúncios de venda de cartas do One Piece Card Game para grupos de WhatsApp de "
        "colecionadores brasileiros. O vendedor é um colecionador que joga com o filho e vende do "
        "próprio acervo.\n\nCARTAS:\n" + json.dumps(ficha, ensure_ascii=False, indent=1) +
        "\n\nOBSERVAÇÃO DO VENDEDOR: " + (body.get("observacao") or "nenhuma") +
        "\n\n" + REGRAS +
        '\n\nFORMATO — responda SÓ com este JSON:\n'
        '{"titulo":"chamada com 1 emoji, curta e forte",'
        '"curto":"anúncio de 4 a 6 linhas: chamada, carta e código, preço, uma linha de envio",'
        '"completo":"anúncio de 10 a 16 linhas: chamada, o que torna a carta especial, carta e código, '
        'preço e desconto, estado, envio e embalagem, fechamento convidando a chamar no privado"}'
    )
    try:
        d = parse_json(claude([{"role": "user", "content": prompt}], 1600))
    except Exception as e:
        return jsonify(erro=f"falha ao gerar: {e}"), 502
    return jsonify(titulo=d.get("titulo", ""), curto=d.get("curto", ""), completo=d.get("completo", ""))


@app.post("/api/anuncios")
def api_salvar():
    b = request.json or {}
    cards = b.get("cards") or []
    total = sum(float(c.get("preco") or 0) for c in cards)
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
