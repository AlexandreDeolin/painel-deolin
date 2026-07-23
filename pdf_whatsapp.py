import streamlit as st
import pdfplumber
import pandas as pd
import requests
import re
import time
import os
import hmac
import urllib.parse
import psycopg2
from psycopg2.extras import execute_values
from concurrent.futures import ThreadPoolExecutor
import threading
from datetime import datetime
from contextlib import contextmanager

# =====================================================================
# CONFIGURAÇÃO DA PÁGINA E ESTILIZAÇÃO VISUAL (RESPONSIVA)
# =====================================================================
st.set_page_config(
    page_title="Painel Deolin", 
    page_icon="🦅", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# CSS com alvos diretos para sobrescrever o estilo nativo do Streamlit
st.markdown("""
<style>
    /* Estilização Geral do Container Principal */
    .main .block-container {
        padding-top: 2rem !important;
        padding-bottom: 3rem !important;
        max-width: 800px !important;
        margin: 0 auto !important;
    }
    
    /* Títulos Responsivos e Centralizados */
    .titulo-principal {
        font-size: clamp(1.6rem, 5vw, 2.2rem) !important;
        font-weight: 800 !important;
        text-align: center !important;
        color: #FFFFFF !important;
        margin-bottom: 0.5rem !important;
        line-height: 1.2 !important;
    }
    
    .subtitulo-principal {
        font-size: clamp(0.95rem, 3vw, 1.2rem) !important;
        font-weight: 400 !important;
        text-align: center !important;
        color: #A0AEC0 !important;
        margin-bottom: 2rem !important;
    }

    /* FORÇAR FORMA E LARGURA IGUAL PARA TODOS OS BOTÕES NO MOBILE E DESKTOP */
    div[data-testid="stButton"] {
        width: 100% !important;
    }

    div[data-testid="stButton"] > button {
        width: 100% !important;
        border-radius: 10px !important;
        min-height: 3.2rem !important;
        font-size: 1rem !important;
        font-weight: 700 !important;
        background-color: #1E293B !important;
        color: #F8FAFC !important;
        border: 1px solid #334155 !important;
        transition: all 0.2s ease-in-out !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2) !important;
        margin-bottom: 0.5rem !important;
    }
    
    div[data-testid="stButton"] > button:hover {
        background-color: #334155 !important;
        border-color: #38BDF8 !important;
        color: #38BDF8 !important;
    }

    /* Botão Verde de Destaque do WhatsApp */
    .btn-whatsapp {
        background-color: #25D366 !important;
        color: white !important;
        border: none !important;
        padding: 14px 20px !important;
        font-size: 16px !important;
        font-weight: bold !important;
        border-radius: 10px !important;
        cursor: pointer !important;
        width: 100% !important;
        text-align: center !important;
        display: block !important;
        box-shadow: 0 4px 12px rgba(37, 211, 102, 0.3) !important;
        text-decoration: none !important;
    }

    /* Ajuste da Caixa de Busca */
    div[data-testid="stTextInput"] input {
        border-radius: 10px !important;
        text-align: center !important;
    }

    /* Esconder Elementos de Menu Padrão */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# =====================================================================
# 1. SEGURANÇA E CONFIGURAÇÕES
# =====================================================================
class SecurityConfig:
    @staticmethod
    def get_secret(key: str, default: str = "") -> str:
        if key in st.secrets:
            return str(st.secrets[key])
        return os.getenv(key, default)

class Config:
    DB_HOST = SecurityConfig.get_secret("DB_HOST", "dpg-d9gndgjrjlhs73coubhg-a.ohio-postgres.render.com")
    DB_NAME = SecurityConfig.get_secret("DB_NAME", "painel_ilnq")
    DB_USER = SecurityConfig.get_secret("DB_USER", "deolin")
    DB_PASS = SecurityConfig.get_secret("DB_PASS", "bEjZL9Cjbqjfe7qfzBwpBX36JgUJknhe")
    DB_PORT = SecurityConfig.get_secret("DB_PORT", "5432")
    
    EVOLUTION_API_URL = SecurityConfig.get_secret("EVOLUTION_API_URL", "")
    EVOLUTION_INSTANCE = SecurityConfig.get_secret("EVOLUTION_INSTANCE", "")
    EVOLUTION_API_TOKEN = SecurityConfig.get_secret("EVOLUTION_API_TOKEN", "")
    
    AUTH_USER = SecurityConfig.get_secret("APP_USER", "deolin")
    AUTH_PASS = SecurityConfig.get_secret("APP_PASS", "fenixfoods2026")

    TABELAS_ISABEEF = [
        "MIUDO BOVINO CONGELADO", "BOVINO CONGELADO", "BOVINO RESFRIADO",
        "SUINO CONGELADO", "SUINO SALGADO", "AVE CONGELADA",
        "PESCADO CONGELADO", "EMBUTIDOS", "VEGETAIS CONGELADOS"
    ]
    TABELAS_ISABEEF_SET = set(TABELAS_ISABEEF)

    TABELAS_BARON = [
        "FRANGOS", "SUÍNOS", "SALGADOS", "RESFRIADOS CORTES BOVINOS", 
        "CORTES BOVINOS CONGELADOS", "CORDEIROS", "BATATAS", 
        "FRIOS E LATICÍNIOS", "ULTRACONGELADOS", "PESCADOS", "EMBUTIDOS"
    ]
    TABELAS_BARON_SET = set(TABELAS_BARON)

    EMOJIS_DEPT = {
        "MIUDO BOVINO CONGELADO": "🥩🧊 *MIÚDO BOVINO CONGELADO*",
        "BOVINO CONGELADO": "🥩🧊 *BOVINO CONGELADO*",
        "BOVINO RESFRIADO": "🥩✨ *BOVINO RESFRIADO*",
        "SUINO CONGELADO": "🐖🧊 *SUÍNO CONGELADO*",
        "SUINO SALGADO": "🐖🧂 *SUÍNO SALGADO*",
        "AVE CONGELADA": "🐔🧊 *AVE CONGELADA*",
        "PESCADO CONGELADO": "🐟🧊 *PESCADO CONGELADO*",
        "EMBUTIDOS": "🌭 *EMBUTIDOS E PROCESSADOS*",
        "VEGETAIS CONGELADOS": "🥦🧊 *VEGETAIS CONGELADOS*",
        "CORDEIROS": "🐑 *CORDEIROS (CONGELADOS)* 🧊",
        "FRANGOS": "🍗🐔 *FRANGOS*",
        "SUÍNOS": "🐷🧊 *SUÍNOS*",
        "SALGADOS": "🥓🧂 *SALGADOS*",
        "RESFRIADOS CORTES BOVINOS": "🥩✨ *RESFRIADOS CORTES BOVINOS*",
        "CORTES BOVINOS CONGELADOS": "🥩🧊 *CORTES BOVINOS CONGELADOS*",
        "FRIOS E LATICÍNIOS": "🧀🧀 *FRIOS E LATICÍNIOS*",
        "ULTRACONGELADOS": "❄️⚡ *ULTRACONGELADOS*",
        "PESCADOS": "🎣 _PESCADOS_✨",
        "BATATAS": "🍟 *BATATAS, CEBOLAS & PETISCOS* 🧊",
    }

    @staticmethod
    def obter_emoji_dept(dept: str) -> str:
        dept_upper = dept.upper().strip()
        for key, value in Config.EMOJIS_DEPT.items():
            if key in dept_upper:
                return value
        return f"🔷 *DEPARTAMENTO {dept_upper}*"

    @staticmethod
    def obter_emoji_produto(nome_produto: str) -> str:
        nome_upper = nome_produto.upper()
        mapeamento = {
            "FRANGO": "🍗", "COXA": "🍗", "ASA": "🍗", "PEIXE": "🐟", "CAÇÃO": "🐟",
            "BACON": "🥓", "CALABRESA": "🍕", "QUEIJO": "🧀", "BATATA": "🍟",
            "BOVINO": "🥩", "BOV": "🥩", "ALCATRA": "🥩", "CONTRA": "🥩", 
            "MIGNON": "🥩", "SUINO": "🐖", "MIUDO": "🥩", "CORDEIRO": "🐑"
        }
        for chave, emoji in mapeamento.items():
            if chave in nome_upper: 
                return emoji
        return "•"

# =====================================================================
# 2. BANCO DE DADOS
# =====================================================================
class DatabaseService:
    @staticmethod
    @contextmanager
    def get_connection():
        conn = psycopg2.connect(
            host=Config.DB_HOST, database=Config.DB_NAME,
            user=Config.DB_USER, password=Config.DB_PASS, port=Config.DB_PORT
        )
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

# =====================================================================
# 3. TRATAMENTO DE TEXTO
# =====================================================================
class TextFormatter:
    @staticmethod
    def limpar_nome_produto(nome: str) -> str:
        nome = re.sub(r'\b(CONG|PCT|RESF|INTEIRO|S\/S|C\/OSSO)\b', '', nome, flags=re.IGNORECASE)
        return " ".join(nome.split())

    @staticmethod
    def categorizar_estrito(nome_produto: str, departamento_atual: str, empresa_nome: str) -> str:
        nome_lower = nome_produto.lower()
        dept_clean = departamento_atual.upper().strip()

        if empresa_nome == "ISABEEF":
            if dept_clean in Config.TABELAS_ISABEEF_SET:
                if dept_clean == "SUINO CONGELADO" and any(x in nome_lower for x in ["salg", "toucinho", "bacon"]):
                    return "SUINO SALGADO"
                return dept_clean
            
            if any(x in nome_lower for x in ["suino", "suina", "porco", "pernil", "lombo"]):
                return "SUINO SALGADO" if "salg" in nome_lower else "SUINO CONGELADO"
            
            if any(x in nome_lower for x in ["bovino", "bov", "acem", "alcatra", "picanha"]):
                return "BOVINO RESFRIADO" if "resf" in dept_clean.lower() else "BOVINO CONGELADO"

            return dept_clean

        return dept_clean

# =====================================================================
# 4. PARSER DE PDF
# =====================================================================
class PDFParserService:
    @staticmethod
    def validar_estoque_minimo(nome_produto: str, estoque_num: float) -> bool:
        nome_upper = nome_produto.upper()
        if any(x in nome_upper for x in ["AVE", "FRANGO", "COXA", "ASA", "PEITO"]):
            return estoque_num >= 30.0
        if any(x in nome_upper for x in ["BOVINO", "BOV", "ALCATRA", "CONTRA", "MIGNON"]):
            return estoque_num >= 15.0
        return estoque_num >= 5.0

    @staticmethod
    def processar_linha_isabeef(linha_limpa: str, current_dept: str):
        partes = linha_limpa.split()
        if len(partes) < 6 or not partes[0].isdigit():
            return None

        try:
            tab2_str = partes[-1]
            qtde_str = partes[-4]
            if not ("," in tab2_str or "." in tab2_str):
                return None

            tab2_num = float(tab2_str.replace(".", "").replace(",", "."))
            qtde_num = float(qtde_str.replace(".", "").replace(",", "."))
        except (ValueError, IndexError):
            return None

        partes_produto = partes[1:-4]
        descricao_completa = " ".join(partes_produto)

        peso_caixa = 20.0
        match_peso = re.search(r'(?:CX|CAIXA|PCT)?\s*(\d+(?:[.,]\d+)?)\s*KG', descricao_completa, re.IGNORECASE)
        if match_peso:
            try: peso_caixa = float(match_peso.group(1).replace(",", "."))
            except ValueError: pass

        estoque_caixas = round(qtde_num / peso_caixa) if peso_caixa > 0 else round(qtde_num / 20.0)

        embalagem = "Unidade"
        match_embalagem = re.search(r'\b(Caixa|CX|PCT|Embalagem)\s*\d+(?:[.,]\d+)?\s*(?:kg|g|un)?', descricao_completa, re.IGNORECASE)
        desc_sem_emb = descricao_completa.replace(match_embalagem.group(0), "").strip() if match_embalagem else descricao_completa

        desc_limpa = TextFormatter.limpar_nome_produto(desc_sem_emb)

        if not PDFParserService.validar_estoque_minimo(desc_limpa, estoque_caixas):
            return None

        final_dept = TextFormatter.categorizar_estrito(desc_limpa, current_dept, "ISABEEF")

        return {
            "empresa": "ISABEEF", "departamento": final_dept.strip().upper(), "codigo": partes[0],
            "descricao": desc_limpa.strip(), "chave_comparacao": partes[0],
            "embalagem": embalagem.strip(), "estoque_texto": f"{int(estoque_caixas)} CX",
            "preco_texto": f"R$ {tab2_str}/kg", "preco_num": tab2_num, "estoque_num": float(estoque_caixas)
        }

    @staticmethod
    def processar_linha_generica(linha_limpa: str, current_dept: str, empresa_nome: str):
        regex_preco = re.compile(r'(?:\$\s*)?(\d{1,4}(?:\.\d{3})*,\d{2})')
        match_preco = regex_preco.search(linha_limpa)
        if not match_preco: return None

        preco_str = match_preco.group(1)
        bloco_esq = linha_limpa[:match_preco.start()].strip().split()
        if not bloco_esq or not bloco_esq[0].isdigit(): return None

        codigo = bloco_esq[0]
        desc_bruta = " ".join(bloco_esq[1:])
        
        try: preco_num = float(preco_str.replace(".", "").replace(",", "."))
        except ValueError: return None

        desc_limpa = TextFormatter.limpar_nome_produto(desc_bruta)
        final_dept = TextFormatter.categorizar_estrito(desc_limpa, current_dept, empresa_nome)

        return {
            "empresa": empresa_nome, "departamento": final_dept.strip().upper(), "codigo": codigo,
            "descricao": desc_limpa.strip(), "chave_comparacao": codigo,
            "embalagem": "Unidade", "estoque_texto": "100 CX",
            "preco_texto": f"R$ {preco_str}/kg", "preco_num": preco_num, "estoque_num": 100.0
        }

    @staticmethod
    def processar_pagina(args):
        pdf_content, numero_pagina, empresa_nome = args
        dados_pagina = []

        with pdfplumber.open(pdf_content) as pdf:
            page = pdf.pages[numero_pagina]
            texto = page.extract_text(x_tolerance=1.5)
            if not texto: return []
            
            current_dept = "GERAL"
            for linha in texto.split("\n"):
                linha_limpa = linha.strip()
                if not linha_limpa: continue

                linha_upper = linha_limpa.upper()

                if "DEPARTAMENTO" in linha_upper or "SEÇÃO" in linha_upper or "SETOR" in linha_upper:
                    dept_extraido = linha_limpa.replace("DEPARTAMENTO", "").replace(":", "").replace("A)", "").replace("B)", "").replace("C)", "").strip()
                    if dept_extraido: current_dept = dept_extraido.upper()
                    continue

                if linha_upper in Config.TABELAS_ISABEEF_SET or linha_upper in Config.TABELAS_BARON_SET:
                    current_dept = linha_upper
                    continue

                if empresa_nome == "ISABEEF":
                    item = PDFParserService.processar_linha_isabeef(linha_limpa, current_dept)
                else:
                    item = PDFParserService.processar_linha_generica(linha_limpa, current_dept, empresa_nome)

                if item: dados_pagina.append(item)

        return dados_pagina

    @classmethod
    def sincronizar_pdf_no_banco(cls, pdf_file, empresa_nome: str) -> bool:
        with pdfplumber.open(pdf_file) as pdf:
            total_paginas = len(pdf.pages)
        
        tarefas = [(pdf_file, i, empresa_nome) for i in range(total_paginas)]
        
        resultados = []
        for t in tarefas:
            resultados.append(cls.processar_pagina(t))
            
        todos_itens = [item for sublist in resultados for item in sublist]
        if not todos_itens: return False

        with DatabaseService.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM produtos WHERE empresa = %s;", (empresa_nome,))
                
                query = """
                    INSERT INTO produtos (empresa, departamento, codigo, descricao, chave_comparacao, embalagem, estoque_texto, preco_texto, preco_num, estoque_num)
                    VALUES %s ON CONFLICT (empresa, codigo) DO NOTHING;
                """
                dados_salvar = [(x["empresa"], x["departamento"], x["codigo"], x["descricao"], x["chave_comparacao"], x["embalagem"], x["estoque_texto"], x["preco_texto"], x["preco_num"], x["estoque_num"]) for x in todos_itens]
                execute_values(cursor, query, dados_salvar, page_size=500)
        return True

# =====================================================================
# 5. GERENCIADOR DE NOTIFICAÇÕES
# =====================================================================
class NotificationService:
    @staticmethod
    def gerar_texto_por_departamento(df_produtos, empresa_nome: str, dept_alvo: str) -> str:
        df_dept = df_produtos[df_produtos['departamento'] == dept_alvo]
        if df_dept.empty: return ""
        
        emoji_titulo = Config.obter_emoji_dept(dept_alvo)
        
        msg = f"🥩 *{empresa_nome}*\n"
        msg += f"{emoji_titulo}\n"
        msg += f"📅 _Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}_\n"
        msg += "===================================\n"

        for _, row in df_dept.iterrows():
            emoji_prod = Config.obter_emoji_produto(str(row['descricao']))
            msg += f"{emoji_prod} *[{row['codigo']}]* {row['descricao']} - *{row['preco_texto']}*\n"

        return msg

    @staticmethod
    def _enviar_http(numero: str, mensagem: str):
        numero_limpo = re.sub(r'\D', '', numero)
        if not numero_limpo or len(numero_limpo) < 10: return
        
        numero_formatado = f"{numero_limpo}@s.whatsapp.net"
            
        url = f"{Config.EVOLUTION_API_URL}/message/sendText/{Config.EVOLUTION_INSTANCE}"
        headers = {"Content-Type": "application/json", "apikey": Config.EVOLUTION_API_TOKEN}
        payload = {"number": numero_formatado, "text": str(mensagem), "options": {"delay": 1200}}
        
        try:
            requests.post(url, json=payload, headers=headers, timeout=10)
        except Exception:
            pass

    @classmethod
    def disparar_mensagem_assincrona(cls, numero: str, message: str):
        threading.Thread(target=cls._enviar_http, args=(numero, message), daemon=True).start()

# =====================================================================
# 6. INTERFACE GRÁFICA MODERNA
# =====================================================================
if "autenticado" not in st.session_state: st.session_state["autenticado"] = False
if "tela_atual" not in st.session_state: st.session_state["tela_atual"] = "Home"

if not st.session_state["autenticado"]:
    st.markdown('<div class="titulo-principal">🦅 Deolin Comercial</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitulo-principal">Acesse com suas credenciais</div>', unsafe_allow_html=True)
    
    usuario_input = st.text_input("Usuário").strip().lower()
    senha_input = st.text_input("Senha", type="password").strip()
    
    if st.button("🔑 ACESSAR SISTEMA", type="primary", use_container_width=True):
        user_valido = hmac.compare_digest(usuario_input, Config.AUTH_USER.lower())
        pass_valido = hmac.compare_digest(senha_input, Config.AUTH_PASS)
        
        if user_valido and pass_valido:
            st.session_state["autenticado"] = True
            st.session_state["usuario_nome"] = usuario_input.capitalize()
            st.rerun()
        else:
            st.error("Credenciais inválidas.")
else:
    # Sidebar
    st.sidebar.title(f"👤 {st.session_state['usuario_nome']}")
    whatsapp_numero = st.sidebar.text_input("📞 WhatsApp Destino", value="5511948017644")
    
    if st.sidebar.button("🏠 Início", use_container_width=True):
        st.session_state["tela_atual"] = "Home"
        st.rerun()
        
    if st.sidebar.button("🚪 Sair", use_container_width=True):
        st.session_state["autenticado"] = False
        st.rerun()

    # Tela Inicial (Home)
    if st.session_state["tela_atual"] == "Home":
        st.markdown('<div class="titulo-principal">🦅 Painel Representações Deolin</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitulo-principal">Selecione uma empresa ou faça uma busca expressa</div>', unsafe_allow_html=True)
        
        # Busca Expressa
        busca_termo = st.text_input("🔍 Buscar Produto:", placeholder="Digite Alcatra, Cação, etc.").strip()
        
        if busca_termo:
            with DatabaseService.get_connection() as conn:
                query = "SELECT empresa, departamento, codigo, descricao, embalagem, preco_texto FROM produtos WHERE codigo = %s OR descricao ILIKE %s LIMIT 10"
                df_encontrados = pd.read_sql(query, conn, params=(busca_termo, f"%{busca_termo}%"))
            if not df_encontrados.empty:
                st.dataframe(df_encontrados, use_container_width=True)
            else:
                st.warning("Nenhum produto localizado com esse termo.")

        st.markdown("<br>", unsafe_allow_html=True)
        
        # BOTÕES DAS EMPRESAS COM TMANHO UNIFORME E TELA CHEIA (use_container_width=True)
        if st.button("🦅 FENIX FOODS", use_container_width=True): 
            st.session_state["tela_atual"] = "FENIX FOODS"
            st.rerun()
            
        if st.button("🍷 BARON ALIMENTARE", use_container_width=True): 
            st.session_state["tela_atual"] = "BARON ALIMENTARE"
            st.rerun()

        if st.button("🥩 ISABEEF", use_container_width=True): 
            st.session_state["tela_atual"] = "ISABEEF"
            st.rerun()
            
        if st.button("🐟 PORTO FISH", use_container_width=True): 
            st.session_state["tela_atual"] = "PORTO FISH"
            st.rerun()

    else:
        # Tela Interna da Empresa
        nome_empresa = st.session_state["tela_atual"]
        st.markdown(f'<div class="titulo-principal">🏢 {nome_empresa}</div>', unsafe_allow_html=True)
        
        with st.expander("📤 Atualizar Tabela via PDF", expanded=False):
            uploaded_file = st.file_uploader("Selecione o arquivo PDF", type=["pdf"])
            if uploaded_file and st.button("⚡ Sincronizar PDF", type="primary", use_container_width=True):
                with st.spinner("Sincronizando PDF e separando departamentos..."):
                    if PDFParserService.sincronizar_pdf_no_banco(uploaded_file, nome_empresa):
                        st.success("Tabela atualizada com sucesso!")
                        time.sleep(1)
                        st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        with DatabaseService.get_connection() as conn:
            query = "SELECT departamento, codigo, descricao, embalagem, preco_texto FROM produtos WHERE empresa = %s ORDER BY departamento, id ASC"
            df_ativos = pd.read_sql(query, conn, params=(nome_empresa,))

        if not df_ativos.empty:
            lista_deptos = list(df_ativos['departamento'].unique())
            depto_selecionado = st.selectbox("📂 Escolha o Departamento:", options=lista_deptos)

            if depto_selecionado:
                texto_wa = NotificationService.gerar_texto_por_departamento(df_ativos, nome_empresa, depto_selecionado)

                num_clean = re.sub(r'\D', '', whatsapp_numero)
                link_wa = f"https://wa.me/{num_clean}?text={urllib.parse.quote(texto_wa)}"
                
                st.markdown(f'''
                    <a href="{link_wa}" target="_blank" style="text-decoration: none;">
                        <div class="btn-whatsapp">
                            💬 Enviar "{depto_selecionado}" no WhatsApp
                        </div>
                    </a>
                ''', unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)
                
                if st.button("🚀 Disparar Todos Departamentos Automaticamente", use_container_width=True):
                    for d in lista_deptos:
                        t_msg = NotificationService.gerar_texto_por_departamento(df_ativos, nome_empresa, d)
                        NotificationService.disparar_mensagem_assincrona(whatsapp_numero, t_msg)
                        time.sleep(1)
                    st.success("Todas as listas foram enviadas no segundo plano!")

                st.text_area("Prévia da Mensagem:", value=texto_wa, height=200)

            st.divider()
            st.subheader("📋 Tabela do Setor")
            df_filtrado_tela = df_ativos[df_ativos['departamento'] == depto_selecionado]
            st.dataframe(df_filtrado_tela, use_container_width=True)
        else:
            st.info("Nenhum registro encontrado para esta empresa. Faça o upload do PDF acima.")
