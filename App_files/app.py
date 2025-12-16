# app.py
import sqlite3
from datetime import datetime
import base64
import os
import pandas as pd
from pathlib import Path
import json
from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

# ========== Flask приложение ==========
app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'  # Секретный ключ для сессий

# ========== База данных SQLite ==========
def init_db():
    """Инициализация базы данных с таблицами для системы учета заявок"""
    # Подключаемся к существующей базе данных или создаем новую
    db_path = 'service_requests.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Проверяем существование таблицы users
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if cursor.fetchone() is None:
        # Если таблицы нет, создаем её
        create_tables_from_scratch(conn, cursor)
    else:
        # Если таблица существует, проверяем её структуру
        print(f"База данных {db_path} уже существует, используем существующие таблицы")
        # Проверяем, есть ли пользователи в таблице
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        if user_count == 0:
            # Если нет пользователей, загружаем их из Excel
            load_users_from_xlsx(conn, cursor)
    
    conn.commit()
    conn.close()

def create_tables_from_scratch(conn, cursor):
    """Создание всех таблиц с нуля на основе данных из xlsx"""
    print("Создание таблиц базы данных с нуля...")
    
    # Удаляем старые таблицы, если они есть
    cursor.execute("DROP TABLE IF EXISTS service_requests")
    cursor.execute("DROP TABLE IF EXISTS masters")
    cursor.execute("DROP TABLE IF EXISTS users")
    cursor.execute("DROP TABLE IF EXISTS equipment_types")
    cursor.execute("DROP TABLE IF EXISTS status_history")
    
    # Создаем таблицу пользователей (для аутентификации)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        fio TEXT NOT NULL,
        phone TEXT,
        user_type TEXT NOT NULL,  -- 'admin', 'master', 'client', 'operator', 'manager'
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Создаем таблицу мастеров (специалистов)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS masters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_fio TEXT NOT NULL,
        master_phone TEXT,
        master_login TEXT UNIQUE,
        master_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Создаем таблицу типов оборудования
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS equipment_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tech_type TEXT NOT NULL,
        tech_model TEXT NOT NULL,
        UNIQUE(tech_type, tech_model)
    )
    ''')
    
    # Создаем таблицу заявок
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS service_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER UNIQUE NOT NULL,
        start_date TIMESTAMP NOT NULL,
        tech_type TEXT NOT NULL,
        tech_model TEXT NOT NULL,
        problem_description TEXT NOT NULL,
        request_status TEXT NOT NULL,
        completion_date TIMESTAMP,
        days_in_process INTEGER,
        repair_parts TEXT,
        has_comment BOOLEAN DEFAULT FALSE,
        comment_message TEXT,
        master_id INTEGER,
        master_fio TEXT,
        master_phone TEXT,
        client_fio TEXT NOT NULL,
        client_phone TEXT NOT NULL,
        client_login TEXT,
        comment_master_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (master_id) REFERENCES masters(id)
    )
    ''')
    
    # Создаем таблицу истории изменения статусов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        old_status TEXT,
        new_status TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        comment TEXT,
        FOREIGN KEY (request_id) REFERENCES service_requests(request_id)
    )
    ''')
    
    # Загружаем данные из xlsx файла заявок
    load_data_from_xlsx(conn, cursor)
    
    # Загружаем данные пользователей из Excel файла
    load_users_from_xlsx(conn, cursor)
    
    print("Таблицы успешно созданы и данные загружены")

def load_data_from_xlsx(conn, cursor):
    """Загрузка данных из Excel файла заявок в базу данных"""
    try:
        xlsx_file_path = 'service_requests_combined.xlsx'
        if not os.path.exists(xlsx_file_path):
            print(f"Файл {xlsx_file_path} не найден!")
            # Создаем тестовые данные
            create_test_data(conn, cursor)
            return
            
        # Читаем данные из Excel
        df = pd.read_excel(xlsx_file_path, sheet_name='Sheet1')
        print(f"Загружено {len(df)} записей из Excel файла заявок")
        
        # Заполняем таблицу мастеров
        masters_data = df[['master_id', 'master_fio', 'master_phone', 'master_login', 'master_type']].dropna()
        masters_data = masters_data.drop_duplicates(subset=['master_id'])
        
        for _, row in masters_data.iterrows():
            try:
                cursor.execute('''
                INSERT OR IGNORE INTO masters (id, master_fio, master_phone, master_login, master_type)
                VALUES (?, ?, ?, ?, ?)
                ''', (
                    int(row['master_id']) if pd.notna(row['master_id']) else None,
                    row['master_fio'] if pd.notna(row['master_fio']) else '',
                    row['master_phone'] if pd.notna(row['master_phone']) else '',
                    row['master_login'] if pd.notna(row['master_login']) else '',
                    row['master_type'] if pd.notna(row['master_type']) else 'Специалист'
                ))
            except Exception as e:
                print(f"Ошибка при добавлении мастера: {e}")
        
        # Заполняем таблицу заявок
        for idx, row in df.iterrows():
            try:
                # Преобразуем даты
                start_date = row['start_date']
                if isinstance(start_date, pd.Timestamp):
                    start_date = start_date.strftime('%Y-%m-%d %H:%M:%S')
                
                completion_date = row['completion_date']
                if pd.notna(completion_date) and isinstance(completion_date, pd.Timestamp):
                    completion_date = completion_date.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    completion_date = None
                
                # Преобразуем булево значение
                has_comment = bool(row['has_comment']) if pd.notna(row['has_comment']) else False
                
                cursor.execute('''
                INSERT OR REPLACE INTO service_requests (
                    request_id, start_date, tech_type, tech_model, problem_description,
                    request_status, completion_date, days_in_process, repair_parts,
                    has_comment, comment_message, master_id, master_fio, master_phone,
                    client_fio, client_phone, client_login, comment_master_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    int(row['request_id']),
                    start_date,
                    row['tech_type'] if pd.notna(row['tech_type']) else '',
                    row['tech_model'] if pd.notna(row['tech_model']) else '',
                    row['problem_description'] if pd.notna(row['problem_description']) else '',
                    row['request_status'] if pd.notna(row['request_status']) else 'Новая заявка',
                    completion_date,
                    int(row['days_in_process']) if pd.notna(row['days_in_process']) else None,
                    row['repair_parts'] if pd.notna(row['repair_parts']) else '',
                    has_comment,
                    row['comment_message'] if pd.notna(row['comment_message']) else '',
                    int(row['master_id']) if pd.notna(row['master_id']) else None,
                    row['master_fio'] if pd.notna(row['master_fio']) else '',
                    row['master_phone'] if pd.notna(row['master_phone']) else '',
                    row['client_fio'] if pd.notna(row['client_fio']) else '',
                    row['client_phone'] if pd.notna(row['client_phone']) else '',
                    row['client_login'] if pd.notna(row['client_login']) else '',
                    int(row['comment_master_id']) if pd.notna(row['comment_master_id']) else None
                ))
                
            except Exception as e:
                print(f"Ошибка при обработке строки {idx}: {e}")
                print(f"Данные строки: {row}")
        
        print(f"Загружено {len(df)} заявок в базу данных")
        
    except Exception as e:
        print(f"Ошибка при загрузке данных из Excel: {e}")

def load_users_from_xlsx(conn, cursor):
    """Загрузка данных пользователей из Excel файла"""
    try:
        users_file_path = 'inputDataUsers.xlsx'
        if not os.path.exists(users_file_path):
            print(f"Файл {users_file_path} не найден!")
            # Создаем тестовых пользователей
            create_default_users(conn, cursor)
            return
            
        # Читаем данные из Excel
        df = pd.read_excel(users_file_path, sheet_name='Sheet1')
        print(f"Загружено {len(df)} записей из Excel файла пользователей")
        
        # Тип пользователя для соответствия с нашей системой
        type_mapping = {
            'Менеджер': 'admin',
            'Специалист': 'master',
            'Оператор': 'operator',
            'Заказчик': 'client'
        }
        
        for idx, row in df.iterrows():
            try:
                # Получаем тип пользователя
                user_type_excel = row['type'] if pd.notna(row['type']) else 'Заказчик'
                user_type = type_mapping.get(user_type_excel, 'client')
                
                # Хэшируем пароль
                password = row['password'] if pd.notna(row['password']) else 'password123'
                password_hash = generate_password_hash(str(password))
                
                # Добавляем пользователя
                cursor.execute('''
                INSERT OR REPLACE INTO users (login, password_hash, fio, phone, user_type)
                VALUES (?, ?, ?, ?, ?)
                ''', (
                    row['login'] if pd.notna(row['login']) else f'user{idx+1}',
                    password_hash,
                    row['fio'] if pd.notna(row['fio']) else f'Пользователь {idx+1}',
                    row['phone'] if pd.notna(row['phone']) else '',
                    user_type
                ))
                
                # Если пользователь является специалистом, добавляем его в таблицу мастеров
                if user_type == 'master':
                    cursor.execute('''
                    INSERT OR IGNORE INTO masters (master_fio, master_phone, master_login, master_type)
                    VALUES (?, ?, ?, ?)
                    ''', (
                        row['fio'] if pd.notna(row['fio']) else f'Пользователь {idx+1}',
                        row['phone'] if pd.notna(row['phone']) else '',
                        row['login'] if pd.notna(row['login']) else f'user{idx+1}',
                        'Специалист'
                    ))
                
                print(f"Добавлен пользователь: {row['login']} ({user_type})")
                
            except Exception as e:
                print(f"Ошибка при обработке пользователя {idx}: {e}")
                print(f"Данные строки: {row}")
        
        print(f"Загружено {len(df)} пользователей в базу данных")
        
    except Exception as e:
        print(f"Ошибка при загрузке пользователей из Excel: {e}")
        # Создаем пользователей по умолчанию
        create_default_users(conn, cursor)

def create_default_users(conn, cursor):
    """Создание пользователей по умолчанию"""
    default_users = [
        ('admin', 'admin123', 'Администратор Системы', '88001234567', 'admin'),
        ('manager1', 'manager123', 'Менеджер 1', '89501112233', 'admin'),
        ('master1', 'master123', 'Мастер 1', '89502223344', 'master'),
        ('master2', 'master123', 'Мастер 2', '89503334455', 'master'),
        ('operator1', 'operator123', 'Оператор 1', '89504445566', 'operator'),
        ('client1', 'client123', 'Клиент 1', '89151234567', 'client'),
        ('client2', 'client123', 'Клиент 2', '89152345678', 'client'),
    ]
    
    for login, password, fio, phone, user_type in default_users:
        password_hash = generate_password_hash(password)
        cursor.execute('''
        INSERT OR IGNORE INTO users (login, password_hash, fio, phone, user_type)
        VALUES (?, ?, ?, ?, ?)
        ''', (login, password_hash, fio, phone, user_type))
        
        # Если пользователь является специалистом, добавляем его в таблицу мастеров
        if user_type == 'master':
            cursor.execute('''
            INSERT OR IGNORE INTO masters (master_fio, master_phone, master_login, master_type)
            VALUES (?, ?, ?, ?)
            ''', (fio, phone, login, 'Специалист'))
    
    print("Пользователи по умолчанию созданы")

def create_test_data(conn, cursor):
    """Создание тестовых данных, если Excel файл не найден"""
    print("Создание тестовых данных...")
    
    # Тестовые мастера
    test_masters = [
        (1, 'Иванов Иван Иванович', '89501112233', 'master1', 'Специалист'),
        (2, 'Петров Петр Петрович', '89502223344', 'master2', 'Специалист'),
        (3, 'Сидорова Анна Владимировна', '89503334455', 'master3', 'Специалист'),
    ]
    
    for master in test_masters:
        cursor.execute('''
        INSERT OR IGNORE INTO masters (id, master_fio, master_phone, master_login, master_type)
        VALUES (?, ?, ?, ?, ?)
        ''', master)
    
    # Тестовые заявки
    from datetime import datetime, timedelta
    
    test_requests = [
        (1, '2023-06-06 00:00:00', 'Кондиционер', 'TCL TAC-12CHSA', 
         'Не охлаждает воздух', 'В процессе ремонта', None, 923, '',
         True, 'Всё сделаем!', 1, 'Иванов Иван Иванович', '89501112233',
         'Смирнов Алексей', '89151234567', 'client1', 1),
        
        (2, '2023-05-05 00:00:00', 'Кондиционер', 'Electrolux EACS/I-09HAT', 
         'Выключается сам по себе', 'В процессе ремонта', None, 955, '',
         True, 'Требуется диагностика', 2, 'Петров Петр Петрович', '89502223344',
         'Козлова Мария', '89152345678', 'client2', 2),
        
        (3, '2023-07-07 00:00:00', 'Увлажнитель воздуха', 'Xiaomi Smart Humidifier', 
         'Пар имеет неприятный запах', 'Завершена', '2023-08-01 00:00:00', 25, '',
         True, 'Починен, заменен фильтр', 3, 'Сидорова Анна Владимировна', '89503334455',
         'Николаев Дмитрий', '89153456789', 'client3', 3),
    ]
    
    for request_data in test_requests:
        cursor.execute('''
        INSERT INTO service_requests (
            request_id, start_date, tech_type, tech_model, problem_description,
            request_status, completion_date, days_in_process, repair_parts,
            has_comment, comment_message, master_id, master_fio, master_phone,
            client_fio, client_phone, client_login, comment_master_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', request_data)
    
    print("Тестовые данные созданы")

# Инициализация БД
init_db()

# Функция для создания логотипа
def create_logo():
    try:
        # Пробуем прочитать файл logo.png
        with open('logo.png', 'rb') as f:
            logo_data = f.read()
            logo_base64 = base64.b64encode(logo_data).decode('utf-8')
            return "data:image/png;base64," + logo_base64
    except FileNotFoundError:
        # Если файл не найден, создаем SVG логотип
        print("Файл logo.png не найден, используется SVG логотип")
        svg_content = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="60" viewBox="0 0 200 60" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#667eea;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#764ba2;stop-opacity:1" />
        </linearGradient>
        <linearGradient id="grad2" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#4facfe;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#00f2fe;stop-opacity:1" />
        </linearGradient>
    </defs>
    <rect width="200" height="60" rx="12" fill="url(#grad1)"/>
    <rect x="15" y="10" width="40" height="40" rx="8" fill="url(#grad2)"/>
    <path d="M25,25 L45,25 M25,30 L45,30 M25,35 L45,35" stroke="white" stroke-width="2" stroke-linecap="round"/>
    <text x="65" y="28" font-family="Arial, sans-serif" font-size="14" font-weight="bold" fill="white">SERVICE</text>
    <text x="65" y="42" font-family="Arial, sans-serif" font-size="12" fill="rgba(255,255,255,0.8)">CENTER</text>
</svg>'''
        
        logo_base64 = "data:image/svg+xml;base64," + base64.b64encode(svg_content.encode('utf-8')).decode('utf-8')
        return logo_base64

logo_base64 = create_logo()

# ========== Маршруты Flask ==========
@app.route('/', methods=['GET', 'POST'])
def index():
    """Главная страница с аутентификацией"""
    if 'user_id' in session:
        # Если пользователь уже вошел, показываем главную страницу
        return render_main_page()
    elif request.method == 'POST':
        # Обработка данных входа из формы
        return handle_login_form()
    else:
        # Показываем страницу входа
        return render_login_page()

def handle_login_form():
    """Обработка данных входа из формы"""
    login = request.form.get('login')
    password = request.form.get('password')
    
    if not login or not password:
        return render_login_page(error="Введите логин и пароль")
    
    try:
        conn = sqlite3.connect('service_requests.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Ищем пользователя
        cursor.execute("SELECT * FROM users WHERE login = ?", (login,))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            # Устанавливаем сессию
            session['user_id'] = user['id']
            session['user_login'] = user['login']
            session['user_name'] = user['fio']
            session['user_type'] = user['user_type']
            
            conn.close()
            return render_main_page()
        else:
            conn.close()
            return render_login_page(error="Неверный логин или пароль")
            
    except Exception as e:
        return render_login_page(error=f"Ошибка сервера: {str(e)}")

def render_login_page(error=None):
    """Рендеринг страницы входа"""
    error_html = f'''
    <div style="background-color: #fee; color: #c00; padding: 10px; border-radius: 5px; margin-bottom: 20px; text-align: center;">
        {error}
    </div>
    ''' if error else ''
    
    login_html = f'''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Вход - Сервисный центр</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {{
                --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                --secondary-gradient: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                --accent-color: #4f46e5;
                --bg-primary: #f8fafc;
                --text-primary: #1e293b;
                --text-secondary: #64748b;
                --shadow-lg: 0 20px 25px -5px rgba(0,0,0,0.1);
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background: var(--primary-gradient);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            
            .login-container {{
                width: 100%;
                max-width: 400px;
            }}
            
            .login-card {{
                background: white;
                border-radius: 20px;
                padding: 40px;
                box-shadow: var(--shadow-lg);
                text-align: center;
            }}
            
            .logo {{
                width: 80px;
                height: 80px;
                margin: 0 auto 20px;
                border-radius: 12px;
                background: var(--secondary-gradient);
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-size: 36px;
            }}
            
            h1 {{
                color: var(--text-primary);
                margin-bottom: 10px;
                font-size: 28px;
            }}
            
            .subtitle {{
                color: var(--text-secondary);
                margin-bottom: 30px;
                font-size: 14px;
            }}
            
            .form-group {{
                margin-bottom: 20px;
                text-align: left;
            }}
            
            label {{
                display: block;
                margin-bottom: 8px;
                color: var(--text-primary);
                font-weight: 500;
            }}
            
            input {{
                width: 100%;
                padding: 14px 18px;
                border: 2px solid #e2e8f0;
                border-radius: 10px;
                font-size: 16px;
                transition: border-color 0.3s;
            }}
            
            input:focus {{
                outline: none;
                border-color: var(--accent-color);
            }}
            
            button {{
                width: 100%;
                padding: 14px;
                background: var(--primary-gradient);
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s;
            }}
            
            button:hover {{
                transform: translateY(-2px);
            }}
            
            .test-accounts {{
                margin-top: 30px;
                padding: 20px;
                background: #f1f5f9;
                border-radius: 10px;
                text-align: left;
            }}
            
            .test-accounts h3 {{
                margin-bottom: 10px;
                font-size: 16px;
            }}
            
            .account-item {{
                margin-bottom: 8px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="login-container">
            <div class="login-card">
                <div class="logo">
                    <i class="fas fa-tools"></i>
                </div>
                <h1>Сервисный центр</h1>
                <p class="subtitle">Система учета заявок на ремонт оборудования</p>
                
                {error_html}
                
                <form method="POST" action="/">
                    <div class="form-group">
                        <label for="login">Логин</label>
                        <input type="text" id="login" name="login" required placeholder="Введите логин">
                    </div>
                    
                    <div class="form-group">
                        <label for="password">Пароль</label>
                        <input type="password" id="password" name="password" required placeholder="Введите пароль">
                    </div>
                    
                    <button type="submit">Войти</button>
                </form>
                
                <div class="test-accounts">
                    <h3>Тестовые аккаунты из файла:</h3>
                    <div class="account-item"><strong>Менеджер:</strong> login1 / pass1</div>
                    <div class="account-item"><strong>Специалист:</strong> login2 / pass2</div>
                    <div class="account-item"><strong>Заказчик:</strong> login7 / pass7</div>
                    <div class="account-item"><strong>Оператор:</strong> login4 / pass4</div>
                </div>
            </div>
        </div>
    </body>
    </html>
    '''
    return login_html

def render_main_page():
    """Рендеринг главной страницы после входа"""
    # Получаем информацию о пользователе
    user_type = session.get('user_type', 'client')
    user_name = session.get('user_name', 'Пользователь')
    user_login = session.get('user_login', '')
    
    # Определяем доступные разделы в зависимости от типа пользователя
    can_view_masters = user_type in ['admin', 'manager', 'master', 'operator']
    can_create_requests = user_type in ['admin', 'manager', 'client', 'operator']
    can_view_stats = user_type in ['admin', 'manager', 'operator']
    can_assign_masters = user_type in ['admin', 'manager', 'operator']
    
    # Русское название типа пользователя
    user_type_names = {
        'admin': 'Администратор',
        'manager': 'Менеджер',
        'master': 'Специалист',
        'operator': 'Оператор',
        'client': 'Заказчик'
    }
    user_type_display = user_type_names.get(user_type, user_type)
    
    # HTML контент главной страницы
    main_html = f'''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Сервисный центр - Учет заявок</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {{
                --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                --secondary-gradient: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                --accent-color: #4f46e5;
                --bg-primary: #f8fafc;
                --bg-card: #ffffff;
                --text-primary: #1e293b;
                --text-secondary: #64748b;
                --border-color: #e2e8f0;
                --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.1);
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'Inter', sans-serif;
                background-color: var(--bg-primary);
                color: var(--text-primary);
            }}
            
            .container {{
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
            }}
            
            .header {{
                background: var(--primary-gradient);
                color: white;
                padding: 20px 30px;
                border-radius: 15px;
                margin-bottom: 30px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            
            .user-info {{
                display: flex;
                align-items: center;
                gap: 15px;
            }}
            
            .user-avatar {{
                width: 40px;
                height: 40px;
                background: var(--secondary-gradient);
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
            }}
            
            .nav-cards {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            
            .nav-card {{
                background: var(--bg-card);
                padding: 25px;
                border-radius: 12px;
                border: 1px solid var(--border-color);
                cursor: pointer;
                transition: all 0.3s;
            }}
            
            .nav-card:hover {{
                transform: translateY(-5px);
                box-shadow: var(--shadow-md);
            }}
            
            .nav-card-icon {{
                font-size: 36px;
                margin-bottom: 15px;
                color: var(--accent-color);
            }}
            
            .content-section {{
                background: var(--bg-card);
                padding: 30px;
                border-radius: 12px;
                border: 1px solid var(--border-color);
                margin-bottom: 30px;
                display: none;
            }}
            
            .content-section.active {{
                display: block;
            }}
            
            .table-container {{
                overflow-x: auto;
                margin-top: 20px;
            }}
            
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            
            th, td {{
                padding: 12px 15px;
                text-align: left;
                border-bottom: 1px solid var(--border-color);
            }}
            
            th {{
                background-color: #f8fafc;
                font-weight: 600;
            }}
            
            .badge {{
                padding: 5px 10px;
                border-radius: 15px;
                font-size: 12px;
                font-weight: 600;
            }}
            
            .badge-new {{ background: #dbeafe; color: #1e40af; }}
            .badge-process {{ background: #fef3c7; color: #92400e; }}
            .badge-completed {{ background: #d1fae5; color: #065f46; }}
            .badge-waiting {{ background: #f3e8ff; color: #6b21a8; }}
            
            .logout-btn {{
                padding: 8px 16px;
                background: rgba(255,255,255,0.2);
                border: none;
                color: white;
                border-radius: 8px;
                cursor: pointer;
            }}
            
            .logout-btn:hover {{
                background: rgba(255,255,255,0.3);
            }}
            
            .status-select {{
                padding: 5px 10px;
                border-radius: 5px;
                border: 1px solid var(--border-color);
            }}
            
            .action-btn {{
                padding: 5px 10px;
                margin: 2px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 12px;
            }}
            
            .btn-view {{ background: #dbeafe; color: #1e40af; }}
            .btn-edit {{ background: #fef3c7; color: #92400e; }}
            .btn-assign {{ background: #dcfce7; color: #166534; }}
            
            /* Модальное окно */
            .modal {{
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.5);
                z-index: 1000;
                align-items: center;
                justify-content: center;
            }}
            
            .modal-content {{
                background: white;
                padding: 30px;
                border-radius: 10px;
                min-width: 300px;
                max-width: 500px;
                max-height: 80vh;
                overflow-y: auto;
            }}
            
            .modal-header {{
                margin-bottom: 20px;
                border-bottom: 1px solid var(--border-color);
                padding-bottom: 10px;
            }}
            
            .modal-footer {{
                margin-top: 20px;
                text-align: right;
                border-top: 1px solid var(--border-color);
                padding-top: 10px;
            }}
            
            .modal-btn {{
                padding: 8px 16px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                margin-left: 10px;
            }}
            
            .modal-btn-primary {{
                background: var(--accent-color);
                color: white;
            }}
            
            .modal-btn-secondary {{
                background: #ccc;
                color: black;
            }}
            
            .master-list {{
                max-height: 300px;
                overflow-y: auto;
                border: 1px solid var(--border-color);
                border-radius: 5px;
                padding: 10px;
            }}
            
            .master-item {{
                padding: 10px;
                border-bottom: 1px solid var(--border-color);
                cursor: pointer;
                transition: background 0.2s;
            }}
            
            .master-item:hover {{
                background: #f8fafc;
            }}
            
            .master-item.selected {{
                background: #e0e7ff;
                border-left: 4px solid var(--accent-color);
            }}
            
            .master-info {{
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            
            .master-name {{
                font-weight: 600;
            }}
            
            .master-type {{
                font-size: 12px;
                color: var(--text-secondary);
                background: #f1f5f9;
                padding: 2px 8px;
                border-radius: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1>Сервисный центр "IT-Сфера"</h1>
                    <p>Учет заявок на ремонт климатического оборудования</p>
                </div>
                <div class="user-info">
                    <div class="user-avatar">{user_name[0] if user_name else '?'}</div>
                    <div>
                        <div><strong>{user_name}</strong></div>
                        <div>{user_type_display}</div>
                    </div>
                    <button class="logout-btn" onclick="logout()">Выйти</button>
                </div>
            </div>
            
            <div class="nav-cards">
                <div class="nav-card" onclick="showSection('requests')">
                    <div class="nav-card-icon"><i class="fas fa-list"></i></div>
                    <h3>Все заявки</h3>
                    <p>Просмотр и управление заявками</p>
                </div>
                
                <div class="nav-card" onclick="showSection('new-request')" {'' if can_create_requests else 'style="display: none;"'}>
                    <div class="nav-card-icon"><i class="fas fa-plus-circle"></i></div>
                    <h3>Новая заявка</h3>
                    <p>Создание новой заявки на ремонт</p>
                </div>
                
                <div class="nav-card" onclick="showSection('stats')" {'' if can_view_stats else 'style="display: none;"'}>
                    <div class="nav-card-icon"><i class="fas fa-chart-bar"></i></div>
                    <h3>Статистика</h3>
                    <p>Аналитика и отчетность</p>
                </div>
                
                <div class="nav-card" onclick="showSection('masters')" {'' if can_view_masters else 'style="display: none;"'}>
                    <div class="nav-card-icon"><i class="fas fa-users"></i></div>
                    <h3>Специалисты</h3>
                    <p>Управление мастерами</p>
                </div>
            </div>
            
            <!-- Секция заявок -->
            <section id="requests" class="content-section active">
                <h2>{'Мои заявки' if user_type == 'master' else 'Все заявки'}</h2>
                <div>
                    <input type="text" id="searchInput" placeholder="Поиск по номеру, клиенту или описанию..." style="width: 100%; padding: 10px; margin-bottom: 20px;">
                    <div class="table-container">
                        <table id="requestsTable">
                            <thead>
                                <tr>
                                    <th>№</th>
                                    <th>Дата</th>
                                    <th>Оборудование</th>
                                    <th>Проблема</th>
                                    <th>Клиент</th>
                                    <th>Статус</th>
                                    <th>Мастер</th>
                                    <th>Действия</th>
                                </tr>
                            </thead>
                            <tbody id="requestsTableBody">
                                <tr><td colspan="8">Загрузка...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>
            
            <!-- Секция новой заявки -->
            <section id="new-request" class="content-section">
                <h2>Новая заявка на ремонт</h2>
                <form id="newRequestForm" style="max-width: 600px;">
                    <div style="display: grid; gap: 20px; margin-top: 20px;">
                        <div>
                            <label>Тип оборудования *</label>
                            <input type="text" id="tech_type" required style="width: 100%; padding: 10px;" placeholder="Кондиционер, увлажнитель и т.д.">
                        </div>
                        <div>
                            <label>Модель устройства *</label>
                            <input type="text" id="tech_model" required style="width: 100%; padding: 10px;" placeholder="Модель устройства">
                        </div>
                        <div>
                            <label>Описание проблемы *</label>
                            <textarea id="problem_description" required style="width: 100%; padding: 10px; min-height: 100px;" placeholder="Подробное описание проблемы"></textarea>
                        </div>
                        <div>
                            <label>ФИО клиента *</label>
                            <input type="text" id="client_fio" required style="width: 100%; padding: 10px;" value="{user_name}" {'' if user_type == 'client' else 'readonly'}>
                        </div>
                        <div>
                            <label>Телефон клиента *</label>
                            <input type="tel" id="client_phone" required style="width: 100%; padding: 10px;" placeholder="+7 (XXX) XXX-XX-XX">
                        </div>
                        <button type="button" onclick="createNewRequest()" style="padding: 12px; background: var(--accent-color); color: white; border: none; border-radius: 8px; cursor: pointer;">
                            <i class="fas fa-plus"></i> Создать заявку
                        </button>
                    </div>
                </form>
            </section>
            
            <!-- Секция статистики -->
            <section id="stats" class="content-section">
                <h2>Статистика работы</h2>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin: 20px 0;">
                    <div style="background: #f8fafc; padding: 20px; border-radius: 10px; text-align: center;">
                        <div style="font-size: 32px; font-weight: bold; color: var(--accent-color);" id="totalRequests">0</div>
                        <div>Всего заявок</div>
                    </div>
                    <div style="background: #f8fafc; padding: 20px; border-radius: 10px; text-align: center;">
                        <div style="font-size: 32px; font-weight: bold; color: #10b981;" id="completedRequests">0</div>
                        <div>Завершено</div>
                    </div>
                    <div style="background: #f8fafc; padding: 20px; border-radius: 10px; text-align: center;">
                        <div style="font-size: 32px; font-weight: bold; color: #f59e0b;" id="avgTime">0</div>
                        <div>Среднее время (дней)</div>
                    </div>
                    <div style="background: #f8fafc; padding: 20px; border-radius: 10px; text-align: center;">
                        <div style="font-size: 32px; font-weight: bold; color: #8b5cf6;" id="inProcess">0</div>
                        <div>В процессе</div>
                    </div>
                </div>
                <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 300px;">
                        <canvas id="statusChart" style="max-width: 100%;"></canvas>
                    </div>
                    <div style="flex: 1; min-width: 300px;">
                        <canvas id="typeChart" style="max-width: 100%;"></canvas>
                    </div>
                </div>
            </section>
            
            <!-- Секция мастеров (только для админов) -->
            <section id="masters" class="content-section">
                <h2>Специалисты</h2>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>ФИО</th>
                                <th>Телефон</th>
                                <th>Логин</th>
                                <th>Тип</th>
                                <th>Заявок в работе</th>
                                <th>Всего заявок</th>
                            </tr>
                        </thead>
                        <tbody id="mastersTableBody">
                            <tr><td colspan="6">Загрузка...</td></tr>
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
        
        <!-- Модальное окно для назначения мастера -->
        <div id="assignMasterModal" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Назначить мастера</h3>
                </div>
                <div id="assignMasterModalBody">
                    <p>Выберите мастера для назначения на заявку:</p>
                    <div class="master-list" id="masterList">
                        <!-- Список мастеров будет загружен здесь -->
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="modal-btn modal-btn-primary" onclick="confirmAssignMaster()">Назначить</button>
                    <button class="modal-btn modal-btn-secondary" onclick="closeAssignMasterModal()">Отмена</button>
                </div>
            </div>
        </div>
        
        <!-- Модальное окно для редактирования заявки -->
        <div id="editRequestModal" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Редактировать заявку</h3>
                </div>
                <div id="editRequestModalBody">
                    <!-- Форма редактирования будет загружена здесь -->
                </div>
                <div class="modal-footer">
                    <button class="modal-btn modal-btn-primary" onclick="confirmEditRequest()">Сохранить</button>
                    <button class="modal-btn modal-btn-secondary" onclick="closeEditRequestModal()">Отмена</button>
                </div>
            </div>
        </div>
        
        <script>
            let currentAssignRequestId = null;
            let selectedMasterId = null;
            let currentEditRequestId = null;
            
            // Показ секций
            function showSection(sectionId) {{
                document.querySelectorAll('.content-section').forEach(section => {{
                    section.classList.remove('active');
                }});
                document.getElementById(sectionId).classList.add('active');
                
                // Загрузка данных для секции
                if (sectionId === 'requests') loadRequests();
                if (sectionId === 'stats') loadStats();
                if (sectionId === 'masters') loadMasters();
            }}
            
            // Загрузка заявок
            async function loadRequests() {{
                try {{
                    const response = await fetch('/api/requests');
                    const requests = await response.json();
                    
                    const tbody = document.getElementById('requestsTableBody');
                    tbody.innerHTML = '';
                    
                    if (requests.length === 0) {{
                        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 20px;">Нет заявок</td></tr>';
                        return;
                    }}
                    
                    requests.forEach(request => {{
                        const row = document.createElement('tr');
                        const statusClass = {{
                            'Новая заявка': 'badge-new',
                            'В процессе ремонта': 'badge-process',
                            'Завершена': 'badge-completed',
                            'Ожидание комплектующих': 'badge-waiting'
                        }}[request.request_status] || 'badge-new';
                        
                        // Кнопки действий в зависимости от типа пользователя
                        let actionButtons = '';
                        const userType = '{user_type}';
                        
                        if (userType === 'admin' || userType === 'manager' || userType === 'operator') {{
                            actionButtons = `
                                <button class="action-btn btn-view" onclick="viewRequest(${{request.request_id}})">Просмотр</button>
                                <button class="action-btn btn-edit" onclick="openEditRequestModal(${{request.request_id}})">Изменить</button>
                                <button class="action-btn btn-assign" onclick="openAssignMasterModal(${{request.request_id}})">Назначить</button>
                            `;
                        }} else if (userType === 'master') {{
                            actionButtons = `
                                <button class="action-btn btn-view" onclick="viewRequest(${{request.request_id}})">Просмотр</button>
                                <button class="action-btn btn-edit" onclick="openEditRequestModal(${{request.request_id}})">Изменить статус</button>
                            `;
                        }} else {{
                            actionButtons = `
                                <button class="action-btn btn-view" onclick="viewRequest(${{request.request_id}})">Просмотр</button>
                            `;
                        }}
                        
                        row.innerHTML = `
                            <td>${{request.request_id}}</td>
                            <td>${{new Date(request.start_date).toLocaleDateString('ru-RU')}}</td>
                            <td>${{request.tech_type}}<br><small>${{request.tech_model}}</small></td>
                            <td>${{request.problem_description}}</td>
                            <td>${{request.client_fio}}<br><small>${{request.client_phone}}</small></td>
                            <td><span class="badge ${{statusClass}}">${{request.request_status}}</span></td>
                            <td>${{request.master_fio || 'Не назначен'}}</td>
                            <td>${{actionButtons}}</td>
                        `;
                        tbody.appendChild(row);
                    }});
                }} catch (error) {{
                    console.error('Ошибка загрузки заявок:', error);
                    document.getElementById('requestsTableBody').innerHTML = '<tr><td colspan="8" style="text-align: center; color: red;">Ошибка загрузки данных</td></tr>';
                }}
            }}
            
            // Открытие модального окна для назначения мастера
            async function openAssignMasterModal(requestId) {{
                if (!{json.dumps(can_assign_masters)}) {{
                    alert('У вас нет прав для назначения мастеров');
                    return;
                }}
                
                currentAssignRequestId = requestId;
                selectedMasterId = null;
                
                try {{
                    const response = await fetch('/api/masters');
                    const masters = await response.json();
                    
                    const masterList = document.getElementById('masterList');
                    masterList.innerHTML = '';
                    
                    if (masters.length === 0) {{
                        masterList.innerHTML = '<p style="text-align: center; padding: 20px;">Нет доступных мастеров</p>';
                    }} else {{
                        masters.forEach(master => {{
                            const masterItem = document.createElement('div');
                            masterItem.className = 'master-item';
                            masterItem.onclick = () => selectMaster(master.id, masterItem);
                            
                            masterItem.innerHTML = `
                                <div class="master-info">
                                    <div>
                                        <div class="master-name">${{master.master_fio}}</div>
                                        <div style="font-size: 12px; color: #666; margin-top: 2px;">${{master.master_phone}}</div>
                                    </div>
                                    <div class="master-type">${{master.master_type}}</div>
                                </div>
                                <div style="font-size: 12px; color: #666; margin-top: 5px;">
                                    Заявок в работе: <strong>${{master.active_requests || 0}}</strong>
                                </div>
                            `;
                            
                            masterList.appendChild(masterItem);
                        }});
                    }}
                    
                    document.getElementById('assignMasterModal').style.display = 'flex';
                }} catch (error) {{
                    console.error('Ошибка загрузки мастеров:', error);
                    alert('Ошибка загрузки списка мастеров');
                }}
            }}
            
            // Выбор мастера
            function selectMaster(masterId, element) {{
                selectedMasterId = masterId;
                
                // Удаляем выделение у всех элементов
                document.querySelectorAll('.master-item').forEach(item => {{
                    item.classList.remove('selected');
                }});
                
                // Добавляем выделение выбранному элементу
                element.classList.add('selected');
            }}
            
            // Подтверждение назначения мастера
            async function confirmAssignMaster() {{
                if (!selectedMasterId) {{
                    alert('Выберите мастера');
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/requests/' + currentAssignRequestId + '/assign', {{
                        method: 'PUT',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ master_id: selectedMasterId }})
                    }});
                    
                    const result = await response.json();
                    if (result.success) {{
                        alert('Мастер успешно назначен на заявку');
                        closeAssignMasterModal();
                        loadRequests();
                        loadStats();
                    }} else {{
                        alert('Ошибка: ' + result.error);
                    }}
                }} catch (error) {{
                    alert('Ошибка соединения с сервером');
                }}
            }}
            
            // Закрытие модального окна назначения мастера
            function closeAssignMasterModal() {{
                document.getElementById('assignMasterModal').style.display = 'none';
                currentAssignRequestId = null;
                selectedMasterId = null;
            }}
            
            // Открытие модального окна для редактирования заявки
            async function openEditRequestModal(requestId) {{
                currentEditRequestId = requestId;
                
                try {{
                    const response = await fetch('/api/requests/' + requestId);
                    const requestData = await response.json();
                    
                    const modalBody = document.getElementById('editRequestModalBody');
                    
                    let statusOptions = '';
                    const statuses = ['Новая заявка', 'В процессе ремонта', 'Завершена', 'Ожидание комплектующих'];
                    const userType = '{user_type}';
                    
                    // Для мастера ограничиваем выбор статусов
                    if (userType === 'master') {{
                        statuses.splice(0, 1); // Удаляем "Новая заявка"
                    }}
                    
                    statuses.forEach(status => {{
                        statusOptions += `<option value="${{status}}" ${{requestData.request_status === status ? 'selected' : ''}}>${{status}}</option>`;
                    }});
                    
                    modalBody.innerHTML = `
                        <div style="display: grid; gap: 15px;">
                            <div>
                                <label>Описание проблемы:</label>
                                <textarea id="editProblemDescription" style="width: 100%; padding: 10px; min-height: 100px;">${{requestData.problem_description}}</textarea>
                            </div>
                            <div>
                                <label>Статус заявки:</label>
                                <select id="editRequestStatus" style="width: 100%; padding: 10px;">
                                    ${{statusOptions}}
                                </select>
                            </div>
                            <div>
                                <label>Запасные части:</label>
                                <input type="text" id="editRepairParts" style="width: 100%; padding: 10px;" value="${{requestData.repair_parts || ''}}" placeholder="Укажите использованные запчасти">
                            </div>
                            <div>
                                <label>Комментарий мастера:</label>
                                <textarea id="editComment" style="width: 100%; padding: 10px; min-height: 80px;">${{requestData.comment_message || ''}}</textarea>
                            </div>
                        </div>
                    `;
                    
                    document.getElementById('editRequestModal').style.display = 'flex';
                }} catch (error) {{
                    console.error('Ошибка загрузки данных заявки:', error);
                    alert('Ошибка загрузки данных заявки');
                }}
            }}
            
            // Подтверждение редактирования заявки
            async function confirmEditRequest() {{
                const updateData = {{
                    problem_description: document.getElementById('editProblemDescription').value,
                    request_status: document.getElementById('editRequestStatus').value,
                    repair_parts: document.getElementById('editRepairParts').value,
                    comment_message: document.getElementById('editComment').value
                }};
                
                try {{
                    const response = await fetch('/api/requests/' + currentEditRequestId, {{
                        method: 'PUT',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(updateData)
                    }});
                    
                    const result = await response.json();
                    if (result.success) {{
                        alert('Заявка успешно обновлена');
                        closeEditRequestModal();
                        loadRequests();
                        loadStats();
                    }} else {{
                        alert('Ошибка: ' + result.error);
                    }}
                }} catch (error) {{
                    alert('Ошибка соединения с сервером');
                }}
            }}
            
            // Закрытие модального окна редактирования
            function closeEditRequestModal() {{
                document.getElementById('editRequestModal').style.display = 'none';
                currentEditRequestId = null;
            }}
            
            // Просмотр заявки
            async function viewRequest(requestId) {{
                try {{
                    const response = await fetch('/api/requests/' + requestId);
                    const requestData = await response.json();
                    
                    let masterInfo = 'Не назначен';
                    if (requestData.master_fio) {{
                        masterInfo = `${{requestData.master_fio}} (${{requestData.master_phone}})`;
                    }}
                    
                    let commentInfo = 'Нет комментариев';
                    if (requestData.comment_message) {{
                        commentInfo = requestData.comment_message;
                    }}
                    
                    let partsInfo = 'Не указаны';
                    if (requestData.repair_parts) {{
                        partsInfo = requestData.repair_parts;
                    }}
                    
                    const message = `
                        Заявка №${{requestData.request_id}}
                        Дата создания: ${{new Date(requestData.start_date).toLocaleDateString('ru-RU')}}
                        Оборудование: ${{requestData.tech_type}} - ${{requestData.tech_model}}
                        Проблема: ${{requestData.problem_description}}
                        Клиент: ${{requestData.client_fio}} (${{requestData.client_phone}})
                        Статус: ${{requestData.request_status}}
                        Мастер: ${{masterInfo}}
                        Запасные части: ${{partsInfo}}
                        Комментарий: ${{commentInfo}}
                        ${{requestData.completion_date ? 'Дата завершения: ' + new Date(requestData.completion_date).toLocaleDateString('ru-RU') : ''}}
                    `;
                    
                    alert(message.replace(/\\n/g, '\\n'));
                }} catch (error) {{
                    alert('Ошибка загрузки данных заявки');
                }}
            }}
            
            // Создание новой заявки
            async function createNewRequest() {{
                const formData = {{
                    tech_type: document.getElementById('tech_type').value,
                    tech_model: document.getElementById('tech_model').value,
                    problem_description: document.getElementById('problem_description').value,
                    client_fio: document.getElementById('client_fio').value,
                    client_phone: document.getElementById('client_phone').value
                }};
                
                if (!formData.tech_type || !formData.tech_model || !formData.problem_description || !formData.client_phone) {{
                    alert('Пожалуйста, заполните все обязательные поля');
                    return;
                }}
                
                try {{
                    const response = await fetch('/api/requests', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(formData)
                    }});
                    
                    const result = await response.json();
                    if (result.success) {{
                        alert('Заявка №' + result.request_id + ' успешно создана!');
                        document.getElementById('newRequestForm').reset();
                        document.getElementById('client_fio').value = '{user_name}';
                        showSection('requests');
                        loadRequests();
                        loadStats(); // Обновляем статистику
                    }} else {{
                        alert('Ошибка: ' + result.error);
                    }}
                }} catch (error) {{
                    alert('Ошибка соединения с сервером');
                }}
            }}
            
            // Загрузка статистики
            async function loadStats() {{
                try {{
                    const response = await fetch('/api/stats');
                    const stats = await response.json();
                    
                    document.getElementById('totalRequests').textContent = stats.total_requests;
                    document.getElementById('completedRequests').textContent = stats.completed_requests;
                    document.getElementById('avgTime').textContent = stats.avg_days || '0';
                    document.getElementById('inProcess').textContent = stats.in_process;
                    
                    // График распределения по статусам
                    const statusCtx = document.getElementById('statusChart').getContext('2d');
                    if (window.statusChart) {{
                        window.statusChart.destroy();
                    }}
                    window.statusChart = new Chart(statusCtx, {{
                        type: 'doughnut',
                        data: {{
                            labels: stats.status_distribution.map(item => item.status),
                            datasets: [{{
                                data: stats.status_distribution.map(item => item.count),
                                backgroundColor: ['#3b82f6', '#f59e0b', '#10b981', '#8b5cf6']
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            plugins: {{
                                title: {{
                                    display: true,
                                    text: 'Распределение по статусам'
                                }}
                            }}
                        }}
                    }});
                    
                    // График распределения по типам оборудования
                    const typeCtx = document.getElementById('typeChart').getContext('2d');
                    if (window.typeChart) {{
                        window.typeChart.destroy();
                    }}
                    window.typeChart = new Chart(typeCtx, {{
                        type: 'bar',
                        data: {{
                            labels: stats.type_distribution.map(item => item.tech_type),
                            datasets: [{{
                                label: 'Количество',
                                data: stats.type_distribution.map(item => item.count),
                                backgroundColor: '#4facfe'
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            plugins: {{
                                title: {{
                                    display: true,
                                    text: 'Распределение по типам оборудования'
                                }}
                            }}
                        }}
                    }});
                }} catch (error) {{
                    console.error('Ошибка загрузки статистики:', error);
                }}
            }}
            
            // Загрузка мастеров
            async function loadMasters() {{
                try {{
                    const response = await fetch('/api/masters');
                    const masters = await response.json();
                    
                    const tbody = document.getElementById('mastersTableBody');
                    tbody.innerHTML = '';
                    
                    masters.forEach(master => {{
                        const row = document.createElement('tr');
                        row.innerHTML = `
                            <td>${{master.master_fio}}</td>
                            <td>${{master.master_phone}}</td>
                            <td>${{master.master_login}}</td>
                            <td>${{master.master_type}}</td>
                            <td>${{master.active_requests || 0}}</td>
                            <td>${{master.total_requests || 0}}</td>
                        `;
                        tbody.appendChild(row);
                    }});
                }} catch (error) {{
                    console.error('Ошибка загрузки мастеров:', error);
                }}
            }}
            
            // Выход из системы
            async function logout() {{
                await fetch('/api/logout');
                window.location.href = '/';
            }}
            
            // Инициализация при загрузке
            document.addEventListener('DOMContentLoaded', () => {{
                loadRequests();
                loadStats();
                
                // Закрытие модальных окон при клике вне их
                document.addEventListener('click', (event) => {{
                    if (event.target.classList.contains('modal')) {{
                        event.target.style.display = 'none';
                    }}
                }});
            }});
        </script>
    </body>
    </html>
    '''
    return main_html

# ========== API маршруты ==========

@app.route('/api/logout')
def logout_api():
    """Выход из системы"""
    session.clear()
    return jsonify({"success": True})

@app.route('/api/requests')
def get_requests():
    """Получение всех заявок"""
    try:
        conn = sqlite3.connect('service_requests.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Фильтрация в зависимости от роли пользователя
        user_type = session.get('user_type')
        user_login = session.get('user_login')
        
        if user_type == 'client':
            # Клиент видит только свои заявки
            cursor.execute('''
                SELECT * FROM service_requests 
                WHERE client_login = ? 
                ORDER BY start_date DESC
            ''', (user_login,))
        elif user_type == 'master':
            # Специалист видит только закрепленные за ним заявки
            # Получаем ID мастера по его логину
            cursor.execute("SELECT id FROM masters WHERE master_login = ?", (user_login,))
            master_result = cursor.fetchone()
            
            if master_result:
                master_id = master_result[0]
                cursor.execute('''
                    SELECT * FROM service_requests 
                    WHERE master_id = ?
                    ORDER BY start_date DESC
                ''', (master_id,))
            else:
                # Если мастер не найден в таблице masters, показываем пустой список
                return jsonify([])
        else:  # admin, manager, operator
            cursor.execute('''
                SELECT * FROM service_requests 
                ORDER BY start_date DESC
            ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        return jsonify([dict(row) for row in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/requests/<int:request_id>')
def get_request(request_id):
    """Получение конкретной заявки"""
    try:
        conn = sqlite3.connect('service_requests.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM service_requests WHERE request_id = ?
        ''', (request_id,))
        
        request_data = cursor.fetchone()
        conn.close()
        
        if request_data:
            return jsonify(dict(request_data))
        else:
            return jsonify({"error": "Заявка не найдена"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/requests', methods=['POST'])
def create_request():
    """Создание новой заявки"""
    try:
        data = request.json
        
        # Проверяем авторизацию
        if 'user_id' not in session:
            return jsonify({"success": False, "error": "Требуется авторизация"}), 401
        
        # Генерируем новый request_id
        conn = sqlite3.connect('service_requests.db')
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(request_id) FROM service_requests")
        max_id = cursor.fetchone()[0] or 0
        new_request_id = max_id + 1
        
        # Добавляем заявку
        cursor.execute('''
            INSERT INTO service_requests (
                request_id, start_date, tech_type, tech_model, problem_description,
                request_status, client_fio, client_phone, client_login
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            new_request_id,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            data['tech_type'],
            data['tech_model'],
            data['problem_description'],
            'Новая заявка',
            data['client_fio'],
            data['client_phone'],
            session.get('user_login', '')
        ))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "request_id": new_request_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/requests/<int:request_id>', methods=['PUT'])
def update_request(request_id):
    """Обновление заявки"""
    try:
        data = request.json
        user_type = session.get('user_type')
        
        conn = sqlite3.connect('service_requests.db')
        cursor = conn.cursor()
        
        # Получаем текущую заявку
        cursor.execute("SELECT * FROM service_requests WHERE request_id = ?", (request_id,))
        request_data = cursor.fetchone()
        
        if not request_data:
            return jsonify({"success": False, "error": "Заявка не найдена"}), 404
        
        # Проверяем права доступа
        if user_type == 'client' and request_data[16] != session.get('user_login'):  # client_login в позиции 16
            return jsonify({"success": False, "error": "Нет доступа"}), 403
        
        # Для мастера проверяем, что заявка закреплена за ним
        if user_type == 'master':
            # Получаем ID мастера по логину
            cursor.execute("SELECT id FROM masters WHERE master_login = ?", (session.get('user_login'),))
            master_result = cursor.fetchone()
            if master_result:
                master_id = master_result[0]
                if request_data[12] != master_id:  # master_id в позиции 12
                    return jsonify({"success": False, "error": "Нет доступа к этой заявке"}), 403
        
        # Обновляем заявку
        update_fields = []
        update_values = []
        
        if 'problem_description' in data and user_type != 'client':
            update_fields.append("problem_description = ?")
            update_values.append(data['problem_description'])
        
        if 'request_status' in data and user_type in ['admin', 'manager', 'master', 'operator']:
            update_fields.append("request_status = ?")
            update_values.append(data['request_status'])
            
            # Если статус "Завершена", устанавливаем дату завершения
            if data['request_status'] == 'Завершена':
                update_fields.append("completion_date = ?")
                update_values.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        if 'repair_parts' in data and user_type in ['admin', 'manager', 'master', 'operator']:
            update_fields.append("repair_parts = ?")
            update_values.append(data['repair_parts'])
        
        if 'comment_message' in data and user_type in ['admin', 'manager', 'master', 'operator']:
            update_fields.append("comment_message = ?")
            update_fields.append("has_comment = ?")
            update_values.append(data['comment_message'])
            update_values.append(True)
        
        # Добавляем дату обновления
        update_fields.append("updated_at = ?")
        update_values.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        # Выполняем обновление
        if update_fields:
            update_values.append(request_id)
            sql = f"UPDATE service_requests SET {', '.join(update_fields)} WHERE request_id = ?"
            cursor.execute(sql, update_values)
            
            # Записываем в историю изменение статуса
            if 'request_status' in data:
                cursor.execute('''
                    INSERT INTO status_history (request_id, old_status, new_status, changed_by)
                    VALUES (?, ?, ?, ?)
                ''', (request_id, request_data[6], data['request_status'], session.get('user_name', 'Система')))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/requests/<int:request_id>/assign', methods=['PUT'])
def assign_master(request_id):
    """Назначение мастера на заявку"""
    try:
        data = request.json
        user_type = session.get('user_type')
        
        if user_type not in ['admin', 'manager', 'operator']:
            return jsonify({"success": False, "error": "Недостаточно прав"}), 403
        
        master_id = data.get('master_id')
        if not master_id:
            return jsonify({"success": False, "error": "Не указан ID мастера"}), 400
        
        conn = sqlite3.connect('service_requests.db')
        cursor = conn.cursor()
        
        # Получаем данные мастера
        cursor.execute("SELECT master_fio, master_phone, master_login FROM masters WHERE id = ?", (master_id,))
        master = cursor.fetchone()
        
        if not master:
            return jsonify({"success": False, "error": "Мастер не найден"}), 404
        
        # Обновляем заявку
        cursor.execute('''
            UPDATE service_requests 
            SET master_id = ?, master_fio = ?, master_phone = ?,
                request_status = 'В процессе ремонта'
            WHERE request_id = ?
        ''', (master_id, master[0], master[1], request_id))
        
        # Записываем в историю
        cursor.execute('''
            INSERT INTO status_history (request_id, old_status, new_status, changed_by, comment)
            VALUES (?, ?, ?, ?, ?)
        ''', (request_id, 'Новая заявка', 'В процессе ремонта', session.get('user_name', 'Система'), f'Назначен мастер: {master[0]}'))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Получение статистики"""
    try:
        conn = sqlite3.connect('service_requests.db')
        cursor = conn.cursor()
        
        # Общее количество заявок
        cursor.execute("SELECT COUNT(*) FROM service_requests")
        total_requests = cursor.fetchone()[0]
        
        # Количество завершенных заявок
        cursor.execute("SELECT COUNT(*) FROM service_requests WHERE request_status = 'Завершена'")
        completed_requests = cursor.fetchone()[0]
        
        # Количество заявок в процессе
        cursor.execute("SELECT COUNT(*) FROM service_requests WHERE request_status = 'В процессе ремонта'")
        in_process = cursor.fetchone()[0]
        
        # Среднее время выполнения (для завершенных заявок)
        cursor.execute('''
            SELECT AVG(JULIANDAY(completion_date) - JULIANDAY(start_date)) 
            FROM service_requests 
            WHERE request_status = 'Завершена' AND completion_date IS NOT NULL
        ''')
        avg_days = cursor.fetchone()[0]
        avg_days = round(avg_days, 1) if avg_days else 0
        
        # Распределение по статусам
        cursor.execute('''
            SELECT request_status, COUNT(*) as count 
            FROM service_requests 
            GROUP BY request_status
        ''')
        status_distribution = [{"status": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        # Распределение по типам оборудования
        cursor.execute('''
            SELECT tech_type, COUNT(*) as count 
            FROM service_requests 
            GROUP BY tech_type
        ''')
        type_distribution = [{"tech_type": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            "total_requests": total_requests,
            "completed_requests": completed_requests,
            "in_process": in_process,
            "avg_days": avg_days,
            "status_distribution": status_distribution,
            "type_distribution": type_distribution
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/masters')
def get_masters():
    """Получение списка мастеров"""
    try:
        conn = sqlite3.connect('service_requests.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT m.*, 
                   (SELECT COUNT(*) FROM service_requests sr 
                    WHERE sr.master_id = m.id AND sr.request_status = 'В процессе ремонта') as active_requests,
                   (SELECT COUNT(*) FROM service_requests sr 
                    WHERE sr.master_id = m.id) as total_requests
            FROM masters m
            ORDER BY m.master_fio
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        return jsonify([dict(row) for row in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/requests/search')
def search_requests():
    """Поиск заявок"""
    try:
        query = request.args.get('q', '')
        user_type = session.get('user_type')
        user_login = session.get('user_login')
        
        conn = sqlite3.connect('service_requests.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        search_pattern = f"%{query}%"
        
        if user_type == 'client':
            cursor.execute('''
                SELECT * FROM service_requests 
                WHERE client_login = ? AND (
                    request_id LIKE ? OR 
                    problem_description LIKE ? OR 
                    client_fio LIKE ? OR 
                    client_phone LIKE ? OR
                    tech_type LIKE ? OR
                    tech_model LIKE ?
                )
                ORDER BY start_date DESC
            ''', (user_login, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern))
        elif user_type == 'master':
            # Получаем ID мастера по его логину
            cursor.execute("SELECT id FROM masters WHERE master_login = ?", (user_login,))
            master_result = cursor.fetchone()
            
            if master_result:
                master_id = master_result[0]
                cursor.execute('''
                    SELECT * FROM service_requests 
                    WHERE master_id = ? AND (
                        request_id LIKE ? OR 
                        problem_description LIKE ? OR 
                        client_fio LIKE ? OR 
                        client_phone LIKE ? OR
                        tech_type LIKE ? OR
                        tech_model LIKE ?
                    )
                    ORDER BY start_date DESC
                ''', (master_id, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern))
            else:
                return jsonify([])
        else:  # admin, manager, operator
            cursor.execute('''
                SELECT * FROM service_requests 
                WHERE request_id LIKE ? OR 
                    problem_description LIKE ? OR 
                    client_fio LIKE ? OR 
                    client_phone LIKE ? OR
                    tech_type LIKE ? OR
                    tech_model LIKE ?
                ORDER BY start_date DESC
            ''', (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern))
        
        rows = cursor.fetchall()
        conn.close()
        
        return jsonify([dict(row) for row in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("="*60)
    print("Сервисный центр - Система учета заявок на ремонт")
    print("Сервер доступен по адресу: http://localhost:5000")
    print("="*60)
    print("Функциональность:")
    print("   • Аутентификация пользователей из файла inputDataUsers.xlsx")
    print("   • Создание и управление заявками")
    print("   • Назначение мастеров (с выбором из списка)")
    print("   • Редактирование заявок")
    print("   • Добавление комментариев и деталей ремонта")
    print("   • Статистика работы")
    print("   • Поиск заявок")
    print("="*60)
    print("Тестовые пользователи из файла:")
    print("   • Менеджер: login1 / pass1")
    print("   • Специалист: login2 / pass2 (видит только свои заявки)")
    print("   • Оператор: login4 / pass4")
    print("   • Заказчик: login7 / pass7 (видит только свои заявки)")
    print("="*60)
    app.run(debug=True, host='0.0.0.0', port=5000)