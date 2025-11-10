from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import sqlite3
import google.generativeai as genai
from datetime import datetime
from flask_cors import CORS
import re

# =============================
# CONFIG GEMINI
# =============================
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("API key not found. Please set the GOOGLE_API_KEY environment variable.")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

system_prompt = (
    "Você é um assistente virtual de saúde chamado SUSBot, responsável por conversar com pacientes "
    "de forma educada, empática e objetiva.\n"
    "Seu papel é ajudar o paciente a informar dados básicos para o cadastro: "
    "nome, idade, endereço (rua, número e CEP), telefone e sintomas.\n"
    "⚙️ Importante:\n"
    "- Extraia automaticamente os dados das mensagens do paciente.\n"
    "- Não confirme nem invente dados.\n"
    "- Se faltar algum dado, pergunte apenas o que falta de forma educada.\n"
    "- Quando todos os dados forem coletados, agradeça e envie o link da consulta."
)

# =============================
# BANCO SQLITE
# =============================
DB_PATH = r"db\clinica.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.commit()
    conn.close()

def salvar_dialogo(autor, mensagem):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO dialogos (timestamp, autor, mensagem) VALUES (?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), autor, str(mensagem))
    )
    conn.commit()
    conn.close()

def salvar_paciente(dados):
    print("💾 Salvando paciente:", dados)  # Debug: verifica o que vai ser salvo
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO pacientes (nome, idade, endereco, telefone, sintomas, data_registro) VALUES (?, ?, ?, ?, ?, ?)",
            (
                dados.get("nome", ""),
                dados.get("idade", ""),
                dados.get("endereco", ""),
                dados.get("telefone", ""),
                dados.get("sintomas", ""),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        conn.commit()
        print("✅ Dados do paciente salvos no banco!")
    except Exception as e:
        print("❌ Erro ao salvar paciente:", e)
    finally:
        conn.close()

# ANÁLISE DOS DADOS

def analisar_dados(mensagem):
    dados = {}

    # Nome
    padroes_nome = [
        r"meu nome é\s*([A-Za-zÀ-ÿ\s]+)",
        r"chamo[-\s]*me\s*([A-Za-zÀ-ÿ\s]+)",
        r"nome\s*[:\-]?\s*([A-Za-zÀ-ÿ\s]+)"
    ]
    for p in padroes_nome:
        if m := re.search(p, mensagem, re.IGNORECASE):
            dados["nome"] = m.group(1).strip().title()
            break

    # Idade
    padroes_idade = [
        r"tenho\s*(\d{1,3})\s*anos",
        r"idade\s*[:\-]?\s*(\d{1,3})"
    ]
    for p in padroes_idade:
        if m := re.search(p, mensagem, re.IGNORECASE):
            idade = int(m.group(1))
            if 0 < idade < 120:
                dados["idade"] = str(idade)
                break

    # Endereço
    padroes_endereco = [
        r"moro (?:na|no|em)\s*([A-Za-zÀ-ÿ\s]+,\s*\d+,\s*\d{5}-?\d{3})",  # Rua, número, CEP
        r"endere[cç]o\s*[:\-]?\s*([A-Za-zÀ-ÿ\s]+,\s*\d+,\s*\d{5}-?\d{3})",
        r"(?:rua|avenida|av\.?)\s*([A-Za-zÀ-ÿ\s]+,\s*\d+,\s*\d{5}-?\d{3})"
    ]
    for p in padroes_endereco:
        if m := re.search(p, mensagem, re.IGNORECASE):
            dados["endereco"] = m.group(1).strip().title()
            break

    # Telefone
    if m := re.search(r"(\(?\d{2}\)?\s*\d{4,5}-?\d{4})", mensagem):
        dados["telefone"] = re.sub(r"\D", "", m.group(1))

    # Sintomas
    padroes_sintomas = [
        r"sinto\s*([A-Za-zÀ-ÿ\s,]+)",
        r"estou com\s*([A-Za-zÀ-ÿ\s,]+)",
        r"sintomas\s*[:\-]?\s*(.*)"
    ]
    for p in padroes_sintomas:
        if m := re.search(p, mensagem, re.IGNORECASE):
            dados["sintomas"] = m.group(1).strip().capitalize()
            break

    print("🔍 Dados extraídos:", dados)
    return dados

def dados_completos(dados):
    campos = ["nome", "idade", "endereco", "telefone", "sintomas"]
    return all(campo in dados and dados[campo] for campo in campos)

def campos_faltando(dados):
    campos = ["nome", "idade", "endereco", "telefone", "sintomas"]
    return [c for c in campos if not dados.get(c)]

# FLASK

app = Flask(__name__)
CORS(app)
init_db()

paciente_cache = {}

@app.route("/chat", methods=["POST"])
def chat_api():
    global paciente_cache

    data = request.json
    user_id = data.get("user_id", "teste")  # Usa "teste" se não houver ID
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"error": "Mensagem vazia"}), 400

    salvar_dialogo("Usuário", user_message)

    if user_id not in paciente_cache:
        paciente_cache[user_id] = {}

    novos_dados = analisar_dados(user_message)
    paciente_cache[user_id].update(novos_dados)

    dados_usuario = paciente_cache[user_id]
    faltando = campos_faltando(dados_usuario)

    print("📝 Dados do paciente atual:", dados_usuario)
    print("⚠️ Campos faltando:", faltando)

    if not faltando:
        salvar_paciente(dados_usuario)
        resumo = "\n".join([f"- {k.capitalize()}: {v}" for k, v in dados_usuario.items()])
        ai_message = (
            "✅ Todos os seus dados foram coletados com sucesso!\n\n"
            f"📋 Resumo dos seus dados:\n{resumo}\n\n"
            "💬 Segue o link para sua consulta:\n👉 https://meet.google.com/ovr-ocwa-mxi"
        )
        paciente_cache.pop(user_id)
    else:
        # Aqui ainda chama o modelo para resposta parcial
        resumo = "\n".join([f"- {k.capitalize()}: {v}" for k, v in dados_usuario.items() if v])
        faltando_texto = ", ".join(faltando)
        contexto = (
            f"Até agora, o paciente informou:\n{resumo or '(nenhum dado ainda)'}.\n\n"
            f"Ainda faltam: {faltando_texto}.\n"
            "Peça apenas as informações que faltam, de forma educada e breve."
        )
        prompt_final = f"{contexto}\n\nUsuário: {user_message}"
        response = model.generate_content(prompt_final)
        ai_message = response.text if hasattr(response, 'text') else str(response)

    salvar_dialogo("Assistente", ai_message)
    return jsonify({"reply": ai_message})

@app.route("/history", methods=["GET"])
def get_history():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, autor, mensagem FROM dialogos ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"timestamp": ts, "autor": autor, "mensagem": msg} for ts, autor, msg in rows])

@app.route("/pacientes", methods=["GET"])
def get_pacientes():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT nome, idade, endereco, telefone, sintomas, data_registro FROM pacientes")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([
        {"nome": r[0], "idade": r[1], "endereco": r[2], "telefone": r[3], "sintomas": r[4], "data_registro": r[5]}
        for r in rows
    ])

if __name__ == "__main__":
    app.run(port=5000, debug=True)
