import streamlit as st
import psycopg2
import pdfplumber
import re
from datetime import datetime
import time
import requests
import os
import io
import concurrent.futures

# ==========================================
# CONFIGURAÇÃO DA PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Painel ILNQ - Sincronizador de Tabelas",
    page_icon="🥩",
    layout="wide"
)

# ==========================================
# 1. CONFIGURAÇÕES & CONSTANTES
# ==========================================
class Config:
    @staticmethod
    def get_db_url():
        # Tenta pegar dos Segredos do Streamlit ou do ambiente
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
        return os.environ.get("DATABASE_URL", "")

    # Mapeamento de Tabelas/Departamentos Isabeef
    TABELAS_ISABEEF_SET = {
        "BOVINO CONGELADO", "BOVINO RESFRIADO", "BOVINO",
        "SUÍNO CONGELADO", "SUÍNO RESFRIADO", "SUÍNO",
        "AVES CONGELADO", "AVES RESFRIADO", "AVES",
        "LINGUIÇAS / EMBUTIDOS", "EMBUTIDOS", "PEIXES",
        "DIVERSOS", "CORTE BOVINO", "MIÚDOS", "CARNE MOÍDA"
    }

    # Mapeamento de Tabelas/Departamentos Baron
    TABELAS_BARON_SET = {
        "BOVINOS", "SUINOS", "AVES", "OUTROS", "CORTE BOVINO"
    }

# ==========================================
# 2. CONEXÃO COM O BANCO DE DADOS
# ==========================================
class DatabaseService:
    @staticmethod
    def get_connection():
        db_url = Config.get_db_url()
        if not db_url:
            st.error("DATABASE_URL não configurada nos Secrets ou Ambiente.")
            return None
        try:
            conn = psycopg2.connect(db_url)
            return conn
        except Exception as e:
            st.error(f"Erro ao conectar ao Banco de Dados: {e}")
            return None

    @staticmethod
    def buscar_produtos():
        conn = DatabaseService.get_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT empresa, departamento, codigo, descricao, embalagem, preco_texto, estoque_texto
                    FROM produtos
                    ORDER BY departamento, codigo;
                """)
                rows = cur.fetchall()
                colunas = ["empresa", "departamento", "codigo", "descricao", "embalagem", "preco_texto", "estoque_texto"]
                return [dict(zip(colunas, r)) for r in rows]
        except Exception as e:
            st.error(f"Erro ao carregar produtos do banco: {e}")
            return []
        finally:
            conn.close()

# ==========================================
# 3. LEITURA E PARSER DE PDF
# ==========================================
class PDFParserService:

    @staticmethod
    def limpar_valor_numerico(texto):
        if not texto:
            return 0.0
        try:
            # Limpa caracteres e converte formato brasileiro (1.234,56 -> 1234.56)
            limpo = re.sub(r'[^\d,\.]', '', str(texto))
            if ',' in limpo:
                limpo = limpo.replace('.', '').replace(',', '.')
            return float(limpo)
        except Exception:
            return 0.0

    @staticmethod
    def processar_linha_isabeef(linha, departamento_atual):
        """Processa uma linha do PDF no padrão Isabeef"""
        partes = [p.strip() for p in linha.split() if p.strip()]
        if len(partes) < 4:
            return None

        # Padrão típico de código Isabeef (números ou alfanumérico no início)
        if not partes[0].isdigit():
            return None

        codigo = partes[0]
        
        # Identifica preços e estoques no final da linha
        preco_texto = partes[-1] if len(partes) > 1 else ""
        embalagem = partes[-2] if len(partes) > 2 else "Unidade"
        
        # O meio da linha compõe a descrição
        descricao = " ".join(partes[1:-2]) if len(partes) > 3 else " ".join(partes[1:])

        preco_num = PDFParserService.limpar_valor_numerico(preco_texto)

        return {
            "empresa": "ISABEEF",
            "departamento": departamento_atual,
            "codigo": codigo,
            "descricao": descricao,
            "chave_comparacao": codigo,
            "embalagem": embalagem,
            "estoque_texto": "Disponível",
            "preco_texto": preco_texto,
            "preco_num": preco_num,
            "estoque_num": 1.0
        }

    @staticmethod
    def processar_linha_generica(linha, departamento_atual, empresa_nome):
        partes = [p.strip() for p in linha.split() if p.strip()]
        if len(partes) < 3:
            return None

        codigo = partes[0]
        preco_texto = partes[-1]
        descricao = " ".join(partes[1:-1])

        return {
            "empresa": empresa_nome,
            "departamento": departamento_atual,
            "codigo": codigo,
            "descricao": descricao,
            "chave_comparacao": codigo,
            "embalagem": "Unidade",
            "estoque_texto": "Disponível",
            "preco_texto": preco_texto,
            "preco_num": PDFParserService.limpar_valor_numerico(preco_texto),
            "estoque_num": 1.0
        }

    @staticmethod
    def processar_pagina(args):
        pdf_bytes, numero_pagina, empresa_nome = args
        dados_pagina = []

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[numero_pagina]
            texto = page.extract_text(x_tolerance=1.5)
            if not texto:
                return []

            # VARIÁVEL DE DEPARTAMENTO DINÂMICA
            current_dept = "BOVINO CONGELADO"

            for linha in texto.split("\n"):
                linha_limpa = linha.strip()
                if not linha_limpa:
                    continue

                linha_upper = linha_limpa.upper()

                # CORREÇÃO: Detecta se a linha é o título de um novo departamento
                if linha_upper in Config.TABELAS_ISABEEF_SET or linha_upper in Config.TABELAS_BARON_SET:
                    current_dept = linha_upper
                    continue

                # Se for linha de cabeçalho da tabela, ignora
                if "CÓDIGO" in linha_upper or "DESCRIÇÃO" in linha_upper or "PREÇO" in linha_upper:
                    continue

                if empresa_nome == "ISABEEF":
                    item = PDFParserService.processar_linha_isabeef(linha_limpa, current_dept)
                else:
                    item = PDFParserService.processar_linha_generica(linha_limpa, current_dept, empresa_nome)

                if item:
                    dados_pagina.append(item)

        return dados_pagina

    @staticmethod
    def sincronizar_pdf_no_banco(uploaded_file, empresa_nome):
        try:
            pdf_bytes = uploaded_file.read()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                total_paginas = len(pdf.pages)

            # Processamento paralelo de páginas para maior velocidade
            args_list = [(pdf_bytes, i, empresa_nome) for i in range(total_paginas)]
            todos_produtos = []

            with concurrent.futures.ThreadPoolExecutor() as executor:
                resultados = executor.map(PDFParserService.processar_pagina, args_list)
                for res in resultados:
                    todos_produtos.extend(res)

            if not todos_produtos:
                st.warning("Nenhum produto foi extraído do PDF.")
                return False

            # Persistência no PostgreSQL
            conn = DatabaseService.get_connection()
            if not conn:
                return False

            with conn.cursor() as cur:
                # Remove registros antigos da empresa para manter apenas os atualizados
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
            st.error(f"Erro ao sincronizar PDF no Banco: {e}")
            return False

# ==========================================
# 4. SERVIÇO DE NOTIFICAÇÃO (WHATSAPP)
# ==========================================
class NotificationService:
    @staticmethod
    def disparar_mensagem_assincrona(numero, mensagem):
        """Função para envio da notificação"""
        # Exemplo estruturado de notificação/integração
        try:
            api_url = os.environ.get("WHATSAPP_API_URL", "")
            if api_url:
                requests.post(api_url, json={"number": numero, "message": mensagem}, timeout=5)
        except Exception as e:
            print(f"Erro ao enviar notificação: {e}")

# ==========================================
# 5. INTERFACE STREAMLIT (UI)
# ==========================================
def main():
    st.title("🥩 Painel ILNQ - Sincronização e Tabela")

    # BARRA LATERAL (SIDEBAR)
    st.sidebar.header("⚙️ Configurações")
    nome_empresa = st.sidebar.selectbox("Empresa", ["ISABEEF", "BARON", "DIVERSOS"])
    whatsapp_numero = st.sidebar.text_input("WhatsApp Destino", value="5511948017644")

    if st.sidebar.button("🚪 Sair"):
        st.sidebar.success("Desconectado")

    st.subheader(f"Olá, Deolin!")

    # CARREGAMENTO DE ARQUIVO PDF
    uploaded_file = st.file_uploader("Selecione o arquivo PDF da tabela", type=["pdf"])

    if uploaded_file and st.button("⚡ Sincronizar", type="primary"):
        with st.spinner("Lendo PDF e sincronizando departamentos com o banco de dados..."):
            sucesso = PDFParserService.sincronizar_pdf_no_banco(uploaded_file, nome_empresa)
            if sucesso:
                st.success(f"Tabela de {nome_empresa} sincronizada com sucesso!")

                # CORREÇÃO: Disparo da notificação para o WhatsApp
                if whatsapp_numero:
                    msg = f"✅ *Tabela {nome_empresa} atualizada com sucesso no sistema!*"
                    NotificationService.disparar_mensagem_assincrona(whatsapp_numero, msg)
                    st.info(f"Notificação WhatsApp enviada para {whatsapp_numero}!")

                time.sleep(1.5)
                st.rerun()

    st.markdown("---")

    # EXIBIÇÃO DA TABELA DE PRODUTOS
    st.subheader("📋 Produtos Cadastrados")
    produtos = DatabaseService.buscar_produtos()

    if produtos:
        st.dataframe(produtos, use_container_width=True)
    else:
        st.info("Nenhum produto cadastrado no momento. Faça o upload e clique em '⚡ Sincronizar'.")

if __name__ == "__main__":
    main()
