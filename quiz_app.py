# streamlit_quiz_app.py
# -------------------------------------------------------------
# App de questões para estudo (GO - Obstetrícia)
# - Carrega CSV: id, tema, enunciado, alternativa_a, ..., alternativa_e, correta, explicacao, dificuldade, tags
# - Perguntas aleatórias sem repetição até esgotar (com filtro por tema e DIFICULDADE)
# - Feedback Correto/Errado + justificativa
# - Estatísticas e gráfico de erros por tema
# - Timer por questão (opcional) com penalização automática ao expirar
# - Randomização da ordem das alternativas (gabarito consistente)
# Correções:
# - "Próxima pergunta" libera com um clique (sem dupla contagem)
# - Progresso e posição da questão atual exibidos corretamente
# - Filtro de DIFICULDADE (novo)
# -------------------------------------------------------------

import time
import random
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

# =======================
# 🔐 Login v2 (robusto + diagnóstico)
# =======================
import os, hmac, hashlib, streamlit as st

def _sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest().lower()

def _get_expected_hash(username: str | None) -> tuple[str | None, str]:
    """
    Retorna (hash_esperado, modo).
    Prioridade:
      1) users[username] (per-user, se username preenchido)
      2) PASSWORD_PLAINTEXT (secrets)   ← prioridade sobre SHA global
      3) PASSWORD_SHA256   (secrets)
      4) PASSWORD_PLAINTEXT (env)
      5) PASSWORD_SHA256    (env)
    """
    # 1) per-user
    users = st.secrets.get("users", None)
    if isinstance(users, dict) and (username or "").strip():
        u = (username or "").strip()
        h = users.get(u)
        if h:
            return str(h).strip().lower(), "per_user"

    # 2) plaintext em secrets (prioridade)
    p = st.secrets.get("PASSWORD_PLAINTEXT", "").strip()
    if p:
        return _sha256(p), "plaintext->hash(secrets)"

    # 3) sha256 em secrets
    h = st.secrets.get("PASSWORD_SHA256", "").strip().lower()
    if h:
        return h, "sha256(secrets)"

    # 4) plaintext via env
    p = os.getenv("PASSWORD_PLAINTEXT", "").strip()
    if p:
        return _sha256(p), "plaintext->hash(env)"

    # 5) sha256 via env
    h = os.getenv("PASSWORD_SHA256", "").strip().lower()
    if h:
        return h, "sha256(env)"

    return None, "missing"

def check_password() -> bool:
    if st.session_state.get("auth_ok"):
        return True

    st.markdown("""
    <style>
    .login-card{max-width:420px;margin:3rem auto;padding:1.25rem 1.5rem;border:1px solid #e5e7eb;border-radius:0.75rem;background:#fff}
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-card">', unsafe_allow_html=True)
    st.subheader("🔐 Acesso")

    col1, col2 = st.columns([1,1])
    with col1:
        username = st.text_input("Usuário (deixe em branco se senha global)", key="__usr__")
    with col2:
        password = st.text_input("Senha", type="password", key="__pwd__")

    # tira espaços invisíveis que às vezes o teclado móvel insere
    username = (username or "").strip()
    password = (password or "").strip()

    info = st.empty()
    ok = st.button("Entrar", use_container_width=True)

    # Diagnóstico (não revela segredos, mostra só metadados)
    with st.expander("Ajuda / Diagnóstico"):
        exp, mode = _get_expected_hash(username or None)
        st.caption(f"🔎 Modo detectado: **{mode}**")
        st.caption(f"Secrets disponíveis: {list(st.secrets.keys())}")
        if exp:
            st.caption(f"Hash esperado (prefixo): `{exp[:8]}…`")
        if password:
            st.caption(f"Hash digitado (prefixo): `{_sha256(password)[:8]}…`")

    if ok:
        expected, mode = _get_expected_hash(username or None)
        if not expected:
            info.error("Senha/usuário não configurados. Defina em *Settings → Secrets* (Streamlit Cloud).")
            return False

        if password and hmac.compare_digest(_sha256(password), expected):
            st.session_state["auth_ok"] = True
            st.session_state["user"] = username or "Usuário"
            try:
                st.experimental_rerun()
            except Exception:
                st.rerun()
            return True
        else:
            info.error("Credenciais inválidas.")
            return False

    st.markdown('</div>', unsafe_allow_html=True)
    return False

if not check_password():
    st.stop()

# Botão de sair no sidebar
with st.sidebar:
    if st.button("Sair"):
        for k in ("auth_ok","user","__usr__","__pwd__"):
            st.session_state.pop(k, None)
        try:
            st.experimental_rerun()
        except Exception:
            st.rerun()
# --- fim do login ---


st.set_page_config(
    page_title="Banco de Questões GO - Obstetrícia",
    page_icon="🩺",
    layout="wide"
)

# Mapas de dificuldade
DIFF_LABELS = {1: "Fácil", 2: "Médio", 3: "Difícil", 4: "Muito difícil"}

# =========================
# Helpers de estado
# =========================

def init_state():
    defaults = {
        "df": None,
        "filtered_df": None,
        "order": [],                 # índices (linhas) do filtered_df em ordem aleatória
        "pos": 0,                    # posição atual no vetor order
        "feedback_shown": False,     # True após confirmar a resposta
        "history": [],               # respostas do usuário
        "stats": {"answered": 0, "correct": 0, "wrong": 0},
        "tema_filtro": [],           # filtros de tema
        "dificuldade_filtro": [],    # filtros de dificuldade (1..4)
        "ready": False,              # rodada inicializada
        "answered_ids": set(),       # evita dupla contagem por questão
        "shuffle_map": {},           # id_da_questao -> ordem fixa das letras originais exibidas em A..E
        "timer_enabled": False,
        "timer_duration": 60,        # segundos por questão
        "question_start_ts": None,   # time.time() ao exibir nova questão
        "timeout_recorded_ids": set()# evita duplo registro por timeout
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def load_csv(file) -> pd.DataFrame:
    import io, csv, unicodedata

    # Lê bytes (funciona tanto para file-like do Streamlit quanto para caminho str)
    if hasattr(file, "read"):  # uploaded file
        raw = file.read()
    else:  # caminho em disco
        with open(file, "rb") as f:
            raw = f.read()

    # Tenta codificações típicas
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            detected_encoding = enc
            break
        except Exception:
            continue
    if text is None:
        st.error("Não foi possível decodificar o arquivo (UTF-8/Latin-1).")
        st.stop()

    # Sniffer de delimitador
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",",";","\t","|"])
        sep = dialect.delimiter
    except Exception:
        # fallback: tenta automático do pandas (sep=None) e depois tentativas manuais
        sep = None

    # Tenta leitura
    try:
        df = pd.read_csv(io.StringIO(text), sep=sep, engine="python")
        detected_sep = sep if sep is not None else "auto"
    except Exception:
        # tentativas manuais
        for sep_try in [",",";","\t","|"]:
            try:
                df = pd.read_csv(io.StringIO(text), sep=sep_try, engine="python")
                detected_sep = sep_try
                break
            except Exception:
                df = None
        if df is None:
            st.error("Erro ao ler o CSV. Tente exportar novamente em UTF-8 e com vírgula como separador.")
            st.stop()

    # Remove colunas "Unnamed"
    df = df[[c for c in df.columns if not str(c).lower().startswith("unnamed")]]

    # Normaliza nomes de colunas (tira acento e espaços extras)
    def norm_col(c):
        c = str(c).strip().lower()
        c = "".join(ch for ch in unicodedata.normalize("NFD", c) if unicodedata.category(ch) != "Mn")
        return c

    df.columns = [norm_col(c) for c in df.columns]

    # Mapeia nomes esperados (aceita variações: explicação/explicacao; tema / topico etc.)
    aliases = {
        "id":"id",
        "tema":"tema",
        "topico":"tema",
        "enunciado":"enunciado",
        "pergunta":"enunciado",
        "alternativa_a":"alternativa_a",
        "a":"alternativa_a",
        "alternativa_b":"alternativa_b",
        "b":"alternativa_b",
        "alternativa_c":"alternativa_c",
        "c":"alternativa_c",
        "alternativa_d":"alternativa_d",
        "d":"alternativa_d",
        "alternativa_e":"alternativa_e",
        "e":"alternativa_e",
        "correta":"correta",
        "gabarito":"correta",
        "explicacao":"explicacao",
        "explicacao/justificativa":"explicacao",
        "explicacao_justificativa":"explicacao",
        "explicacao_ou_justificativa":"explicacao",
        "explicacaoo":"explicacao",
        "explicacao/":"explicacao",
        "explicacao:":"explicacao",
        "explicacao (justificativa)":"explicacao",
        "explicacao (comentario)":"explicacao",
        "explicacao/comentario":"explicacao",
        "explicacaooucomentario":"explicacao",
        "explicacaoo(comentario)":"explicacao",
        "explicacao(comentario)":"explicacao",
        "explicacaojustificativa":"explicacao",
        "explicacao_":"explicacao",
        "explicacao ":"explicacao",
        "explicacao (":"explicacao",
        "explicacao)": "explicacao",
        "explicacao (resumo)": "explicacao",
        "explicacao-resumo":"explicacao",
        "explicacao-res":"explicacao",
        "dificuldade":"dificuldade",
        "nivel":"dificuldade",
        "tags":"tags"
    }
    rename = {c: aliases[c] for c in df.columns if c in aliases}
    df = df.rename(columns=rename)

    expected_cols = [
        "id","tema","enunciado",
        "alternativa_a","alternativa_b","alternativa_c","alternativa_d","alternativa_e",
        "correta","explicacao","dificuldade","tags"
    ]
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        st.error(
            f"CSV lido com separador '{detected_sep}' e encoding '{detected_encoding}', "
            f"mas faltam colunas: {missing}. Verifique o cabeçalho do arquivo."
        )
        st.stop()

    # Normaliza dificuldade para 1..4
    if df["dificuldade"].dtype == object:
        map_txt = {
            "facil":1,"fácil":1,"easy":1,
            "medio":2,"médio":2,"medium":2,
            "dificil":3,"difícil":3,"hard":3,
            "muito dificil":4,"muito difícil":4,"very hard":4
        }
        s = df["dificuldade"].astype(str).str.strip().str.lower()
        df["dificuldade"] = pd.to_numeric(s.map(map_txt), errors="coerce").fillna(2).astype(int)
    else:
        df["dificuldade"] = pd.to_numeric(df["dificuldade"], errors="coerce").fillna(2).astype(int)
    df["dificuldade"] = df["dificuldade"].clip(1,4)

    return df

def reset_round():
    """Reinicia a rodada mantendo o DataFrame atual e os filtros aplicados."""
    df = st.session_state.df.copy()

    temas = st.session_state.tema_filtro or sorted(df["tema"].dropna().unique().tolist())
    difs  = st.session_state.dificuldade_filtro or sorted(df["dificuldade"].dropna().unique().tolist())

    filtered = df[(df["tema"].isin(temas)) & (df["dificuldade"].isin(difs))].reset_index(drop=True)

    if filtered.empty:
        st.warning("Nenhuma questão encontrada para os filtros selecionados (tema/dificuldade).")
        st.session_state.ready = False
        return

    order = list(range(len(filtered)))
    random.shuffle(order)

    st.session_state.filtered_df = filtered
    st.session_state.order = order
    st.session_state.pos = 0
    st.session_state.feedback_shown = False
    st.session_state.history = []
    st.session_state.stats = {"answered": 0, "correct": 0, "wrong": 0}
    st.session_state.answered_ids = set()
    st.session_state.shuffle_map = {}
    st.session_state.timeout_recorded_ids = set()
    st.session_state.ready = True
    st.session_state.question_start_ts = time.time()

def start_new_round_from_theme_change():
    """Quando usuário altera filtros de tema; recomeça a rodada."""
    if st.session_state.df is not None:
        reset_round()

def get_current_row():
    if not st.session_state.ready:
        return None
    if st.session_state.pos >= len(st.session_state.order):
        return None
    idx = st.session_state.order[st.session_state.pos]
    return st.session_state.filtered_df.iloc[idx]

def ensure_shuffle_for_question(qid: str):
    """Gera e fixa uma ordem de alternativas por questão para estabilidade entre reruns."""
    if qid not in st.session_state.shuffle_map:
        original_letters = ["A","B","C","D","E"]
        random.shuffle(original_letters)
        st.session_state.shuffle_map[qid] = original_letters

def build_display_options(row):
    """Retorna:
       - displayed_options: dict {'A': 'texto', 'B': 'texto', ...} com ordem aleatória por questão
       - displayed_correct_letter: letra A..E correta NA EXIBIÇÃO atual
       - original_map: dict displayed_letter -> original_letter (para histórico)"""
    qid = str(row["id"])
    ensure_shuffle_for_question(qid)
    order = st.session_state.shuffle_map[qid]  # lista de letras originais na ordem exibida

    original_texts = {
        "A": row["alternativa_a"],
        "B": row["alternativa_b"],
        "C": row["alternativa_c"],
        "D": row["alternativa_d"],
        "E": row["alternativa_e"],
    }

    displayed_letters = ["A","B","C","D","E"]
    displayed_options = {}
    original_map = {}

    for i, disp_letter in enumerate(displayed_letters):
        orig_letter = order[i]
        displayed_options[disp_letter] = original_texts[orig_letter]
        original_map[disp_letter] = orig_letter

    original_correct = str(row["correta"]).strip().upper()
    inv_map = {v: k for k, v in original_map.items()}
    displayed_correct_letter = inv_map[original_correct]

    return displayed_options, displayed_correct_letter, original_map

def record_answer(row, selected_displayed_letter: str, displayed_correct_letter: str, timeout=False):
    """Registra resposta do usuário com proteção anti-dupla contagem."""
    qid = str(row["id"])
    if qid in st.session_state.answered_ids:
        return  # já contabilizada

    is_correct = (selected_displayed_letter == displayed_correct_letter) and (not timeout)

    st.session_state.stats["answered"] += 1
    if is_correct:
        st.session_state.stats["correct"] += 1
    else:
        st.session_state.stats["wrong"] += 1

    st.session_state.history.append({
        "id": qid,
        "tema": row["tema"],
        "dificuldade": int(row["dificuldade"]),
        "selected": selected_displayed_letter if not timeout else "— (tempo)",
        "correct": displayed_correct_letter,
        "acertou": is_correct,
        "timeout": timeout
    })

    st.session_state.answered_ids.add(qid)

def next_question():
    st.session_state.pos += 1
    st.session_state.feedback_shown = False
    st.session_state.question_start_ts = time.time()

# =========================
# UI - Header
# =========================

init_state()

st.markdown("""
<style>
.badge {display:inline-block; padding:0.25rem 0.5rem; border-radius:999px; font-size:0.75rem; font-weight:600; background:#f1f5f9;}
.badge-blue {background:#e0f2fe;}
.badge-amber {background:#fef3c7;}
.card {padding:1rem 1.25rem; border:1px solid #e5e7eb; border-radius:0.75rem; background:#ffffff;}
.prompt {font-size:1.1rem; line-height:1.6;}
.option {padding:0.5rem 0.75rem; border-radius:0.5rem; background:#f8fafc; margin-bottom:0.25rem;}
.correct {border-left:6px solid #16a34a; background:#ecfdf5;}
.wrong {border-left:6px solid #dc2626; background:#fef2f2;}
.small {font-size:0.875rem; color:#475569;}
.timer {font-weight:600;}
</style>
""", unsafe_allow_html=True)

left, right = st.columns([0.7, 0.3], gap="large")
with left:
    st.title("🩺 Banco de Questões – Obstetrícia (GO)")
    st.caption("Estudo ativo com questões aleatórias, feedback imediato, timer e estatísticas por tema.")
with right:
    st.metric("Questões no banco", value="—")

# =========================
# Sidebar - Configurações
# =========================

with st.sidebar:
    st.header("Configurações")
    st.write("Carregue o **CSV** ou use `questoes_obstetricia_completo.csv` no diretório do app.")

    uploaded = st.file_uploader("CSV de questões", type=["csv"])

    if uploaded is not None:
        try:
            st.session_state.df = load_csv(uploaded)
        except Exception as e:
            st.error(f"Erro ao ler o CSV enviado: {e}")
    else:
        # tenta carregar padrão
        try:
            st.session_state.df = load_csv("questoes_obstetricia_completo.csv")
        except Exception:
            st.info("Nenhum arquivo padrão encontrado. Faça o upload do CSV para começar.")

    if st.session_state.df is not None:
        st.success("Banco carregado com sucesso.")
        st.metric("Total de questões", len(st.session_state.df))

        # --- Filtro por TEMA ---
        temas = sorted(st.session_state.df["tema"].dropna().unique().tolist())
        st.session_state.tema_filtro = st.multiselect(
            "Filtrar por tema (opcional):",
            temas,
            default=temas,
            on_change=start_new_round_from_theme_change
        )

        # --- Filtro por DIFICULDADE (NOVO) ---
        niv_disponiveis = sorted(st.session_state.df["dificuldade"].dropna().astype(int).unique().tolist())
        labels_disponiveis = [DIFF_LABELS.get(int(n), f"Nível {int(n)}") for n in niv_disponiveis]
        label2num = {DIFF_LABELS[k]: k for k in DIFF_LABELS}

        sel_labels = st.multiselect(
            "Filtrar por dificuldade (opcional):",
            options=labels_disponiveis,
            default=labels_disponiveis,
            help="Se nada marcado, o app usa todas as dificuldades."
        )
        # salva no estado como números (1..4)
        st.session_state.dificuldade_filtro = [label2num[l] for l in sel_labels] if sel_labels else []

        # Botão para aplicar o filtro de dificuldade
        if st.button("Aplicar filtros de dificuldade", use_container_width=True):
            reset_round()

        # Timer
        st.session_state.timer_enabled = st.checkbox("⏱️ Ativar timer por questão", value=st.session_state.timer_enabled)
        st.session_state.timer_duration = st.number_input(
            "Duração do timer (segundos)", min_value=10, max_value=600, step=10,
            value=int(st.session_state.timer_duration)
        )

        col_sb1, col_sb2 = st.columns(2)
        with col_sb1:
            if st.button("🔀 Iniciar / Reiniciar rodada", use_container_width=True):
                reset_round()
        with col_sb2:
            if st.button("🧹 Limpar estatísticas", use_container_width=True):
                st.session_state.history = []
                st.session_state.stats = {"answered": 0, "correct": 0, "wrong": 0}
                st.session_state.answered_ids = set()
                st.session_state.timeout_recorded_ids = set()
                try:
                    st.toast("Estatísticas zeradas.")
                except Exception:
                    st.success("Estatísticas zeradas.")

# =========================
# Corpo principal
# =========================

if st.session_state.df is None:
    st.stop()

# Atualiza métrica do header com total de questões
with right:
    if st.session_state.ready and st.session_state.filtered_df is not None:
        st.metric("Questões no banco", value=len(st.session_state.filtered_df))
    else:
        st.metric("Questões no banco", value=len(st.session_state.df))

if not st.session_state.ready:
    st.info("Selecione os temas/dificuldades (opcional), ajuste o timer e clique em **Iniciar / Reiniciar rodada** na barra lateral para começar.")
    st.stop()

total = len(st.session_state.order)
pos = st.session_state.pos
answered = st.session_state.stats["answered"]
correct = st.session_state.stats["correct"]
wrong = st.session_state.stats["wrong"]

# Cabeçalho de progresso (agora mostra questão atual 1-based)
st.markdown(f"**Questão {min(pos+1, total)} de {total}**  •  Respondidas: **{answered}/{total}**")
st.progress((pos) / total if total else 0, text=f"Concluídas: {pos}/{total}")

row = get_current_row()

if row is None:
    st.success("🎉 Você respondeu todas as perguntas desta rodada!")
    acc = (correct / answered * 100) if answered else 0.0
    st.metric("Aproveitamento", f"{acc:.1f}%")
    col_end1, col_end2 = st.columns([0.4,0.6])
    with col_end1:
        if st.button("🔁 Reiniciar com os mesmos filtros"):
            reset_round()
    with col_end2:
        st.write("Dica: ajuste os filtros na barra lateral para focar onde houve mais erro.")
    st.balloons()
    st.stop()

# ---------------------------
# Cartão da questão
# ---------------------------
qid = str(row["id"])
displayed_options, displayed_correct_letter, original_map = build_display_options(row)

st.markdown('<div class="card">', unsafe_allow_html=True)
top_cols = st.columns([0.5,0.2,0.3])
with top_cols[0]:
    st.markdown(f"**{row['id']}**")
    st.markdown(
        f'<span class="badge badge-blue">{row["tema"]}</span> &nbsp; '
        f'<span class="badge badge-amber">Dificuldade: {DIFF_LABELS.get(int(row["dificuldade"]), "Médio")}</span>',
        unsafe_allow_html=True
    )
with top_cols[1]:
    st.metric("Respondidas", answered)
with top_cols[2]:
    acc = (correct / answered * 100) if answered else 0.0
    st.metric("Aproveitamento", f"{acc:.1f}%")

# Timer (visual + penalização ao expirar)
if st.session_state.timer_enabled:
    if st.session_state.question_start_ts is None:
        st.session_state.question_start_ts = time.time()
    elapsed = time.time() - st.session_state.question_start_ts
    remaining = int(st.session_state.timer_duration - elapsed)
    if remaining < 0:
        remaining = 0
    st.markdown(f'⏱️ <span class="timer">Tempo restante:</span> **{remaining}s**', unsafe_allow_html=True)

    # Se tempo acabou e ainda não mostramos feedback, registrar automático (errado por tempo)
    if remaining == 0 and (not st.session_state.feedback_shown) and (qid not in st.session_state.timeout_recorded_ids):
        record_answer(row, selected_displayed_letter="—", displayed_correct_letter=displayed_correct_letter, timeout=True)
        st.session_state.feedback_shown = True
        st.session_state.timeout_recorded_ids.add(qid)

st.markdown(f'<div class="prompt">{row["enunciado"]}</div>', unsafe_allow_html=True)
st.divider()

# Opções A-E (randomizadas mas estáveis por questão)
options = displayed_options  # dict displayed_letter -> text

# Radio com chave estável por pergunta
radio_key = f"radio_{qid}"
labels = [f"{k}) {v}" for k, v in options.items()]
disabled = st.session_state.feedback_shown  # desativa após confirmar

choice_label = st.radio(
    "Escolha uma alternativa:",
    options=labels,
    index=None,
    key=radio_key,
    disabled=disabled,
    label_visibility="collapsed"
)

# Mapeia label escolhido para letra exibida A-E
selected_displayed_letter = None
if choice_label:
    selected_displayed_letter = choice_label.split(")")[0]

cols_btn = st.columns([0.5, 0.5])
with cols_btn[0]:
    confirm = st.button("✅ Confirmar resposta", use_container_width=True, disabled=st.session_state.feedback_shown)
with cols_btn[1]:
    prox = st.button("➡️ Próxima pergunta", use_container_width=True, disabled=not st.session_state.feedback_shown)

feedback_placeholder = st.container()

# Confirmação: registra uma única vez e revela feedback
if confirm and not st.session_state.feedback_shown:
    if not selected_displayed_letter:
        st.warning("Por favor, selecione uma alternativa antes de confirmar.")
    else:
        record_answer(row, selected_displayed_letter, displayed_correct_letter, timeout=False)
        st.session_state.feedback_shown = True

# Feedback (após confirmar ou timeout)
if st.session_state.feedback_shown:
    with feedback_placeholder:
        is_correct = False
        # Busca o último registro desta questão no histórico para firmar o status
        for item in reversed(st.session_state.history):
            if item["id"] == qid:
                is_correct = item["acertou"]
                break

        if is_correct:
            st.success(f"✅ **Correta!**")
        else:
            last = next((h for h in reversed(st.session_state.history) if h["id"] == qid), None)
            if last and last.get("timeout"):
                st.error(f"⌛ **Tempo esgotado.** A alternativa correta é **{displayed_correct_letter}**.")
            else:
                st.error(f"❌ **Errada.** A correta é **{displayed_correct_letter}**.")

        # Mostra alternativas com destaque
        st.markdown("**Alternativas:**")
        for k, v in options.items():
            klass = "option"
            marked = None
            last = next((h for h in reversed(st.session_state.history) if h["id"] == qid), None)
            if last and not last.get("timeout"):
                marked = last["selected"]

            if k == displayed_correct_letter:
                klass += " correct"
            elif marked and k == marked and k != displayed_correct_letter:
                klass += " wrong"
            st.markdown(f'<div class="{klass}"><strong>{k})</strong> {v}</div>', unsafe_allow_html=True)

        st.markdown("**Justificativa:**")
        st.info(row["explicacao"])

# Próxima questão
if prox and st.session_state.feedback_shown:
    next_question()

st.markdown('</div>', unsafe_allow_html=True)  # fecha card

# =========================
# Estatísticas
# =========================
st.divider()
st.subheader("📊 Estatísticas da Rodada")

col_s1, col_s2, col_s3, col_s4 = st.columns(4)
with col_s1:
    st.metric("Respondidas", st.session_state.stats["answered"])
with col_s2:
    st.metric("Corretas", st.session_state.stats["correct"])
with col_s3:
    st.metric("Erradas", st.session_state.stats["wrong"])
with col_s4:
    acc = (st.session_state.stats["correct"] / st.session_state.stats["answered"] * 100) if st.session_state.stats["answered"] else 0.0
    st.metric("Aproveitamento", f"{acc:.1f}%")

hist_df = pd.DataFrame(st.session_state.history)
if not hist_df.empty:
    erros_por_tema = (hist_df.assign(err=lambda d: ~d["acertou"])
                      .groupby("tema")["err"].sum()
                      .sort_values(ascending=False))
    tema_pior = erros_por_tema.index[0] if not erros_por_tema.empty and erros_por_tema.iloc[0] > 0 else "—"

    left_stats, right_stats = st.columns([0.55, 0.45])
    with left_stats:
        st.markdown(f"**Tema com mais erros:** {tema_pior}")
        st.dataframe(hist_df.tail(10).rename(columns={
            "id":"ID","tema":"Tema","dificuldade":"Dificuldade",
            "selected":"Marcada","correct":"Correta","acertou":"Acertou?","timeout":"Timeout?"
        }), use_container_width=True, height=260)

    with right_stats:
        fig, ax = plt.subplots(figsize=(6,3.2))
        if not erros_por_tema.empty:
            ax.bar(erros_por_tema.index, erros_por_tema.values)
            ax.set_title("Erros por tema")
            ax.set_xlabel("Tema")
            ax.set_ylabel("Erros")
            ax.tick_params(axis='x', rotation=45, labelsize=8)
        else:
            ax.text(0.5, 0.5, "Sem dados de erro ainda", ha='center', va='center')
            ax.axis('off')
        st.pyplot(fig, use_container_width=True)
else:
    st.info("Responda algumas questões para ver estatísticas e gráficos.")
