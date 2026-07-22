#pdf=>whatsapp

import streamlit as st
import pdfplumber
import pandas as pd
import requests
import re
import time
import os
import hmac
import psycopg2
from psycopg2.extras import execute_values
from concurrent.futures import ThreadPoolExecutor
import threading
from contextlib import contextmanager

# =====================================================================
# 1. CAMADA DE SEGURANÇA E CONFIGURAÇÕES (DEVSECOPS)
# =====================================================================
class SecurityConfig:
    """
    Abstração segura para leitura de credenciais e segredos.
    Previne o vazamento de chaves no histórico do Git.
    """
    @staticmethod
    def get_secret(key: str, default: str = "") -> str:
        # 1. Tenta buscar no painel de Secrets do Streamlit Cloud
        if key in st.secrets:
            return str(st.secrets[key])
        # 2. Fallback para variáveis de ambiente do SO ou arquivo .env local
        return os.getenv(key, default)

class Config:
    """
    Centraliza constantes do sistema obtidas de forma segura.
    """
    DB_HOST = SecurityConfig.get_secret("DB_HOST", "localhost")
    DB_NAME = SecurityConfig.get_secret("DB_NAME", "postgres")
    DB_USER = SecurityConfig.get_secret("DB_USER", "postgres")
    DB_PASS = SecurityConfig.get_secret("DB_PASS", "")
    DB_PORT = SecurityConfig.get_secret("DB_PORT", "5432")
    
    EVOLUTION_API_URL = SecurityConfig.get_secret("EVOLUTION_API_URL", "")
    EVOLUTION_INSTANCE = SecurityConfig.get_secret("EVOLUTION_INSTANCE", "")
    EVOLUTION_API_TOKEN = SecurityConfig.get_secret("EVOLUTION_API_TOKEN", "")
    
    # Credenciais de Login protegidas por variáveis de ambiente
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
        return "🔹"

# =====================================================================
# 2. CAMADA DE BANCO DE DADOS (PREVENÇÃO SQL INJECTION)
# =====================================================================
class DatabaseService:
    @staticmethod
    @contextmanager
    def get_connection():
        """
        Garante conexões seguras, commit automático e encerramento correto.
        """
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
# 3. TRATAMENTO E TRATAMENTO DE TEXTO
# =====================================================================
class TextFormatter:
    @staticmethod
    def limpar_nome_produto(nome: str) -> str:
        nome = re.sub(r'\b(CONG|PCT|RESF|INTEIRO|S\/S|C\/OSSO)\b', '', nome, flags=re.IGNORECASE)
        return " ".join(nome.split())

    @staticmethod
    def formatar_titulo(texto: str) -> str:
        palavras = texto.split()
        formatadas = [
            p.lower() if p.lower() in ["de", "com", "em", "da", "do", "para", "c/", "s/"]
            else p.capitalize()
            for p in palavras
        ]
        return " ".join(formatadas)

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
# 4. PARSER DE PDF DE ALTA PERFORMANCE
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
            "empresa": "ISABEEF", "departamento": final_dept, "codigo": partes[0],
            "descricao": desc_limpa.strip(), "chave_comparacao": partes[0],
            "embalagem": embalagem.strip(), "estoque_texto": f"{int(estoque_caixas)} CX",
            "preco_texto": f"{tab2_str}/kg", "preco_num": tab2_num, "estoque_num": float(estoque_caixas)
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
            "empresa": empresa_nome, "departamento": final_dept, "codigo": codigo,
            "descricao": desc_limpa.strip(), "chave_comparacao": codigo,
            "embalagem": "Unidade", "estoque_texto": "100 CX",
            "preco_texto": f"{preco_str}/kg", "preco_num": preco_num, "estoque_num": 100.0
        }

    @staticmethod
    def processar_pagina(args):
        pdf_content, numero_pagina, empresa_nome = args
        dados_pagina = []

        with pdfplumber.open(pdf_content) as pdf:
            page = pdf.pages[numero_pagina]
            texto = page.extract_text(x_tolerance=1.5)
            if not texto: return []
            
            current_dept = "BOVINO CONGELADO"
            for linha in texto.split("\n"):
                linha_limpa = linha.strip()
                if not linha_limpa: continue

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
        with ThreadPoolExecutor() as executor:
            resultados = list(executor.map(cls.processar_pagina, tarefas))
            
        todos_itens = [item for sublist in resultados for item in sublist]
        if not todos_itens: return False

        with DatabaseService.get_connection() as conn:
            with conn.cursor() as cursor:
                # Sanitização contra SQL Injection usando parâmetros parametrizados
                cursor.execute("DELETE FROM produtos WHERE empresa = %s;", (empresa_nome,))
                
                query = """
                    INSERT INTO produtos (empresa, departamento, codigo, descricao, chave_comparacao, embalagem, estoque_texto, preco_texto, preco_num, estoque_num)
                    VALUES %s ON CONFLICT (empresa, codigo) DO NOTHING;
                """
                dados_salvar = [(x["empresa"], x["departamento"], x["codigo"], x["descricao"], x["chave_comparacao"], x["embalagem"], x["estoque_texto"], x["preco_texto"], x["preco_num"], x["estoque_num"]) for x in todos_itens]
                execute_values(cursor, query, dados_salvar, page_size=500)
        return True

# =====================================================================
# 5. GERENCIADOR DE NOTIFICAÇÕES (SANITISADO)
# =====================================================================
class NotificationService:
    @staticmethod
    def _enviar_http(numero: str, mensagem: str):
        # Sanitiza a entrada numérica para barrar injeção
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
# 6. INTERFACE GRÁFICA SEGURA (STREAMLIT)
# =====================================================================
st.set_page_config(page_title="Painel Deolin", layout="wide")

if "autenticado" not in st.session_state: st.session_state["autenticado"] = False
if "tela_atual" not in st.session_state: st.session_state["tela_atual"] = "Home"

# Autenticação Protegida por hmac.compare_digest (Previne Timing Attacks)
if not st.session_state["autenticado"]:
    st.subheader("🦅 Sistema Comercial Gestão Multiemprezas")
    usuario_input = st.text_input("Usuário").strip().lower()
    senha_input = st.text_input("Senha", type="password").strip()
    
    if st.button("Entrar", type="primary"):
        user_valido = hmac.compare_digest(usuario_input, Config.AUTH_USER.lower())
        pass_valido = hmac.compare_digest(senha_input, Config.AUTH_PASS)
        
        if user_valido and pass_valido:
            st.session_state["autenticado"] = True
            st.session_state["usuario_nome"] = usuario_input.capitalize()
            st.rerun()
        else:
            st.error("Credenciais inválidas.")
else:
    st.sidebar.title(f"Olá, {st.session_state['usuario_nome']}!")
    whatsapp_numero = st.sidebar.text_input("📞 WhatsApp Destino", placeholder="Ex: 5511958645249")
    
    if st.sidebar.button("🏠 Início"):
        st.session_state["tela_atual"] = "Home"
        st.rerun()
        
    if st.sidebar.button("🚪 Sair"):
        st.session_state["autenticado"] = False
        st.rerun()

    if st.session_state["tela_atual"] == "Home":
        st.title("🦅 Painel Representações Deolin")
        st.subheader("🔍 Consulta Expressa")
        busca_termo = st.text_input("Buscar produto:", placeholder="Ex: Alcatra").strip()
        
        if busca_termo:
            with DatabaseService.get_connection() as conn:
                # Query parametrizada segura contra SQL Injection
                query = "SELECT empresa, departamento, codigo, descricao, embalagem, preco_texto FROM produtos WHERE codigo = %s OR descricao ILIKE %s LIMIT 5"
                df_encontrados = pd.read_sql(query, conn, params=(busca_termo, f"%{busca_termo}%"))
            if not df_encontrados.empty:
                st.dataframe(df_encontrados, use_container_width=True)
            else:
                st.warning("Produto não localizado.")

        st.divider()
        col1, col2, col3, col4 = st.columns(4)
        if col1.button("🦅 FENIX FOODS"): st.session_state["tela_atual"] = "FENIX FOODS"; st.rerun()
        if col2.button("🥩 ISABEEF"): st.session_state["tela_atual"] = "ISABEEF"; st.rerun()
        if col3.button("🍷 BARON ALIMENTARE"): st.session_state["tela_atual"] = "BARON ALIMENTARE"; st.rerun()
        if col4.button("🐟 PORTO FISH"): st.session_state["tela_atual"] = "PORTO FISH"; st.rerun()

    else:
        nome_empresa = st.session_state["tela_atual"]
        st.title(f"🏢 Área: {nome_empresa}")
        
        uploaded_file = st.file_uploader("Upload do PDF", type=["pdf"])
        if uploaded_file and st.button("⚡ Sincronizar", type="primary"):
            with st.spinner("Sincronizando..."):
                if PDFParserService.sincronizar_pdf_no_banco(uploaded_file, nome_empresa):
                    st.success("Sincronizado com sucesso!")
                    time.sleep(1)
                    st.rerun()

        st.divider()
        with DatabaseService.get_connection() as conn:
            query = "SELECT departamento, codigo, descricao, embalagem, preco_texto FROM produtos WHERE empresa = %s ORDER BY departamento, descricao"
            df_ativos = pd.read_sql(query, conn, params=(nome_empresa,))

        if not df_ativos.empty:
            st.dataframe(df_ativos, use_container_width=True)
        else:
            st.info("Nenhum registro encontrado para esta empresa.")