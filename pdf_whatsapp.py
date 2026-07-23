import streamlit as st
import psycopg2
import pdfplumber
import re
from datetime import datetime
import time
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
# CONFIGURAÇÕES DE BANCO
# ==========================================
class Config:
    DB_HOST = os.getenv("DB_HOST", st.secrets.get("DB_HOST", "dpg-d9gndgjrjlhs73coubhg-a.ohio-postgres.render.com"))
    DB_NAME = os.getenv("DB_NAME", st.secrets.get("DB_NAME", "painel_ilnq"))
    DB_USER = os.getenv("DB_USER", st.secrets.get("DB_USER", "deolin"))
    DB_PASS = os.getenv("DB_PASS", st.secrets.get("DB_PASS", "bEjZL9Cjbqjfe7qfzBwpBX36JgUJknhe"))
    DB_PORT = os.getenv("DB_PORT", st.secrets.get("DB_PORT", "5432"))

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
                    SELECT empresa, departamento, codigo, descricao, preco_texto
                    FROM produtos
                    ORDER BY id ASC;
                """)
                rows = cur.fetchall()
                colunas = ["empresa", "departamento", "codigo", "descricao", "preco_texto"]
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

                # DETECÇÃO PRECISA DE DEPARTAMENTO (Ex: "DEPARTAMENTO : A) CORTES BOVINOS")
                if "DEPARTAMENTO" in linha_upper or "SEÇÃO" in linha_upper or "SETOR" in linha_upper:
                    dept_nome = linha_limpa.replace("DEPARTAMENTO", "").replace(":", "").strip()
                    if dept_nome:
                        current_dept = dept_nome
                    continue

                # Ignora cabeçalhos do PDF
                if "CÓDIGO" in linha_upper or "DESCRIÇÃO" in linha_upper or "PREÇO" in linha_upper or "PÁGINA" in linha_upper:
                    continue

                # Processa linhas de produto com código no início
                partes = [p.strip() for p in linha_limpa.split() if p.strip()]
                
                # Exemplo de validação se a linha começa com o código do produto (Ex: 230041 ou 10700)
                if len(partes) >= 3 and partes[0].isdigit():
                    codigo = partes[0]
                    preco_texto = partes[-1]
                    
                    # Garante que o preço capturado tenha formato válido (ex: 19,99 ou 38,99)
                    if "," in preco_texto or "." in preco_texto:
                        # Limpa códigos intermediários do PDF da descrição
                        descricao = " ".join(partes[1:-1])
                        # Remove sequências numéricas isoladas no meio da descrição
                        descricao = re.sub(r'\b\d{2,4}\b', '', descricao).strip()

                        dados_pagina.append({
                            "empresa": empresa_nome,
                            "departamento": current_dept,
                            "codigo": codigo,
                            "descricao": descricao,
                            "chave_comparacao": codigo,
                            "embalagem": "Unidade",
                            "estoque_texto": "Disponível",
                            "preco_texto": f"R$ {preco_texto}",
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

            # Processamento em ordem sequencial para manter a ordem exata dos departamentos
            todos_produtos = []
            for i in range(total_paginas):
                res = PDFParserService.processar_pagina((pdf_bytes, i, empresa_nome))
                todos_produtos.extend(res)

            if not todos_produtos:
                st.warning("Nenhum produto foi lido do PDF. Verifique se o formato do PDF possui texto selecionável.")
                return False

            conn = DatabaseService.get_connection()
            if not conn: return False

            with conn.cursor() as cur:
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
# GERADOR DE TABELA FORMATADA PARA WHATSAPP
# ==========================================
def gerador_mensagem_whatsapp(produtos, empresa):
    if not produtos: return ""
    
    msg = f"🥩 *TABELA DE PREÇOS - {empresa}*\n"
    msg += f"📅 _Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}_\n"
    msg += "===================================\n"

    dept_atual = ""
    for p in produtos:
        if p["departamento"] != dept_atual:
            dept_atual = p["departamento"]
            msg += f"\n🔷 *{dept_atual}*\n"
            msg += "-----------------------------------\n"
        
        msg += f"• *[{p['codigo']}]* {p['descricao']} - *{p['preco_texto']}*\n"

    return msg

# ==========================================
# INTERFACE STREAMLIT
# ==========================================
def main():
    st.title("🥩 Painel ILNQ - Sincronizador")

    st.sidebar.header("⚙️ Configurações")
    nome_empresa = st.sidebar.selectbox("Empresa", ["FENIX FOODS", "ISABEEF", "BARON", "DIVERSOS"])
    whatsapp_numero = st.sidebar.text_input("WhatsApp Destino", value="5511948017644")

    if st.sidebar.button("🗑️ Limpar Banco de Dados"):
        DatabaseService.limpar_tabela()
        st.sidebar.success("Banco limpo com sucesso!")
        st.rerun()

    st.subheader("Olá, Deolin!")

    uploaded_file = st.file_uploader("Selecione o arquivo PDF da tabela", type=["pdf"])

    if uploaded_file and st.button("⚡ Sincronizar Tabela", type="primary"):
        with st.spinner("Lendo tabela PDF e separando departamentos..."):
            if PDFParserService.sincronizar_pdf_no_banco(uploaded_file, nome_empresa):
                st.success("Tabela sincronizada com sucesso!")
                time.sleep(1)
                st.rerun()

    st.markdown("---")

    produtos = DatabaseService.buscar_produtos()

    if produtos:
        st.subheader("📲 Tabela Formatada para WhatsApp")
        texto_wa = gerador_mensagem_whatsapp(produtos, nome_empresa)
        
        # Gera o link para disparo direto no WhatsApp
        numero_limpo = re.sub(r'\D', '', whatsapp_numero)
        link_wa = f"https://wa.me/{numero_limpo}?text={urllib.parse.quote(texto_wa)}"
        
        st.markdown(f'''
            <a href="{link_wa}" target="_blank">
                <button style="background-color:#25D366; color:white; border:none; padding:12px 20px; font-size:16px; font-weight:bold; border-radius:8px; cursor:pointer; width:100%;">
                    💬 Abrir e Enviar no WhatsApp
                </button>
            </a>
        ''', unsafe_allow_html=True)

        st.text_area("Prévia da Mensagem:", value=texto_wa, height=250)

        st.markdown("---")
        st.subheader("📋 Produtos no Banco de Dados")
        st.dataframe(produtos, use_container_width=True)
    else:
        st.info("Nenhum produto cadastrado no momento. Faça o upload do arquivo PDF e clique em '⚡ Sincronizar Tabela'.")

if __name__ == "__main__":
    main()
