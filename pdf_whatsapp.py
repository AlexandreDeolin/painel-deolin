import streamlit as st
import psycopg2
import pdfplumber
import re
from datetime import datetime
import time
import requests
import os
import io
import urllib.parse
import concurrent.futures

# ==========================================
# CONFIGURAÇÃO DA PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Painel ILNQ - Sincronizador",
    page_icon="🥩",
    layout="wide"
)

# ==========================================
# CONFIGURAÇÕES DE BANCO E CONSTANTES
# ==========================================
class Config:
    DB_HOST = os.getenv("DB_HOST", st.secrets.get("DB_HOST", "dpg-d9gndgjrjlhs73coubhg-a.ohio-postgres.render.com"))
    DB_NAME = os.getenv("DB_NAME", st.secrets.get("DB_NAME", "painel_ilnq"))
    DB_USER = os.getenv("DB_USER", st.secrets.get("DB_USER", "deolin"))
    DB_PASS = os.getenv("DB_PASS", st.secrets.get("DB_PASS", "bEjZL9Cjbqjfe7qfzBwpBX36JgUJknhe"))
    DB_PORT = os.getenv("DB_PORT", st.secrets.get("DB_PORT", "5432"))

    DEPARTAMENTOS_CONHECIDOS = {
        "BOVINO CONGELADO", "BOVINO RESFRIADO", "BOVINO", "BOVINOS",
        "SUÍNO CONGELADO", "SUÍNO RESFRIADO", "SUÍNO", "SUINOS",
        "AVES CONGELADO", "AVES RESFRIADO", "AVES",
        "LINGUIÇAS / EMBUTIDOS", "EMBUTIDOS", "PEIXES",
        "DIVERSOS", "CORTE BOVINO", "MIÚDOS", "CARNE MOÍDA", "OUTROS"
    }

class DatabaseService:
    @staticmethod
    def get_connection():
        try:
            return psycopg2.connect(
                host=Config.DB_HOST,
                database=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASS,
                port=Config.DB_PORT
            )
        except Exception as e:
            st.error(f"Erro ao conectar ao Banco: {e}")
            return None

    @staticmethod
    def buscar_produtos():
        conn = DatabaseService.get_connection()
        if not conn: return []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT empresa, departamento, codigo, descricao, embalagem, preco_texto
                    FROM produtos
                    ORDER BY departamento, codigo;
                """)
                rows = cur.fetchall()
                colunas = ["empresa", "departamento", "codigo", "descricao", "embalagem", "preco_texto"]
                return [dict(zip(colunas, r)) for r in rows]
        except Exception as e:
            st.error(f"Erro ao buscar produtos: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def limpar_tabela():
        conn = DatabaseService.get_connection()
        if conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE produtos;")
            conn.commit()
            conn.close()

# ==========================================
# PARSER DE PDF AJUSTADO
# ==========================================
class PDFParserService:
    @staticmethod
    def limpar_valor_numerico(texto):
        if not texto: return 0.0
        try:
            limpo = re.sub(r'[^\d,\.]', '', str(texto))
            if ',' in limpo:
                limpo = limpo.replace('.', '').replace(',', '.')
            return float(limpo)
        except Exception:
            return 0.0

    @staticmethod
    def processar_pagina(args):
        pdf_bytes, numero_pagina, empresa_nome = args
        dados_pagina = []

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[numero_pagina]
            texto = page.extract_text(x_tolerance=1.5)
            if not texto: return []

            current_dept = "GERAL"

            for linha in texto.split("\n"):
                linha_limpa = linha.strip()
                if not linha_limpa: continue

                linha_upper = linha_limpa.upper()

                # Atualiza departamento caso a linha seja uma categoria
                if any(dep in linha_upper for dep in Config.DEPARTAMENTOS_CONHECIDOS):
                    current_dept = linha_upper
                    continue

                if "CÓDIGO" in linha_upper or "DESCRIÇÃO" in linha_upper or "PREÇO" in linha_upper:
                    continue

                partes = [p.strip() for p in linha_limpa.split() if p.strip()]
                if len(partes) >= 3:
                    codigo = partes[0]
                    preco_texto = partes[-1]
                    descricao = " ".join(partes[1:-1])

                    dados_pagina.append({
                        "empresa": empresa_nome,
                        "departamento": current_dept,
                        "codigo": codigo,
                        "descricao": descricao,
                        "chave_comparacao": codigo,
                        "embalagem": "Unidade",
                        "estoque_texto": "Disponível",
                        "preco_texto": preco_texto,
                        "preco_num": PDFParserService.limpar_valor_numerico(preco_texto),
                        "estoque_num": 1.0
                    })

        return dados_pagina

    @staticmethod
    def sincronizar_pdf_no_banco(uploaded_file, empresa_nome):
        try:
            pdf_bytes = uploaded_file.read()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                total_paginas = len(pdf.pages)

            args_list = [(pdf_bytes, i, empresa_nome) for i in range(total_paginas)]
            todos_produtos = []

            with concurrent.futures.ThreadPoolExecutor() as executor:
                resultados = executor.map(PDFParserService.processar_pagina, args_list)
                for res in resultados:
                    todos_produtos.extend(res)

            if not todos_produtos:
                st.warning("Nenhum produto foi lido do PDF.")
                return False

            conn = DatabaseService.get_connection()
            if not conn: return False

            with conn.cursor() as cur:
                # Substitui os produtos para a empresa selecionada
                cur.execute("DELETE FROM produtos WHERE empresa = %s;", (empresa_nome,))

                query_insert = """
                    INSERT INTO produtos 
                    (empresa, departamento, codigo, descricao, chave_comparacao, embalagem, estoque_texto, preco_texto, preco_num, estoque_num)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (empresa, codigo) DO NOTHING;
                """
                for p in todos_produtos:
                    cur.execute(query_insert, (
                        p["empresa"], p["departamento"], p["codigo"], p["descricao"],
                        p["chave_comparacao"], p["embalagem"], p["estoque_texto"],
                        p["preco_texto"], p["preco_num"], p["estoque_num"]
                    ))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            st.error(f"Erro ao sincronizar: {e}")
            return False

# ==========================================
# GERADOR DE TABELA PARA WHATSAPP
# ==========================================
def gerador_mensagem_whatsapp(produtos, empresa):
    if not produtos: return ""
    
    msg = f"🥩 *TABELA DE PREÇOS - {empresa}*\n"
    msg += f"📅 _Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n"

    dept_atual = ""
    for p in produtos:
        if p["departamento"] != dept_atual:
            dept_atual = p["departamento"]
            msg += f"\n🔹 *{dept_atual}*\n"
            msg += "-----------------------------------\n"
        
        msg += f"• *[{p['codigo']}]* {p['descricao']} - *{p['preco_texto']}*\n"

    return msg

# ==========================================
# INTERFACE STREAMLIT
# ==========================================
def main():
    st.title("🥩 Painel ILNQ - Sincronizador")

    st.sidebar.header("⚙️ Configurações")
    nome_empresa = st.sidebar.selectbox("Empresa", ["ISABEEF", "BARON", "DIVERSOS", "FENIX FOODS"])
    whatsapp_numero = st.sidebar.text_input("WhatsApp Destino", value="5511948017644")

    if st.sidebar.button("🗑️ Limpar Banco de Dados"):
        DatabaseService.limpar_tabela()
        st.sidebar.success("Banco limpo!")
        st.rerun()

    st.subheader("Olá, Deolin!")

    uploaded_file = st.file_uploader("Selecione o arquivo PDF da tabela", type=["pdf"])

    if uploaded_file and st.button("⚡ Sincronizar", type="primary"):
        with st.spinner("Lendo tabela e atualizando banco de dados..."):
            if PDFParserService.sincronizar_pdf_no_banco(uploaded_file, nome_empresa):
                st.success("Tabela sincronizada com sucesso!")
                time.sleep(1)
                st.rerun()

    st.markdown("---")

    produtos = DatabaseService.buscar_produtos()

    if produtos:
        st.subheader("📲 Tabela Formatada para WhatsApp")
        texto_wa = gerador_mensagem_whatsapp(produtos, nome_empresa)
        
        # Botão para abrir o WhatsApp diretamente
        numero_limpo = re.sub(r'\D', '', whatsapp_numero)
        link_wa = f"https://wa.me/{numero_limpo}?text={urllib.parse.quote(texto_wa)}"
        
        st.markdown(f'''
            <a href="{link_wa}" target="_blank">
                <button style="background-color:#25D366; color:white; border:none; padding:12px 20px; font-size:16px; border-radius:8px; cursor:pointer; width:100%;">
                    💬 Abrir e Enviar no WhatsApp
                </button>
            </a>
        ''', unsafe_allow_html=True)

        st.text_area("Prévia da Mensagem:", value=texto_wa, height=200)

        st.markdown("---")
        st.subheader("📋 Produtos no Banco de Dados")
        st.dataframe(produtos, use_container_width=True)
    else:
        st.info("Nenhum produto cadastrado. Faça o upload do PDF e clique em '⚡ Sincronizar'.")

if __name__ == "__main__":
    main()
