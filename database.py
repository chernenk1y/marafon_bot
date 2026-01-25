import sqlite3
from datetime import time, datetime, timedelta
import json
import uuid
import requests
import pandas as pd
import logging

db_logger = logging.getLogger('database')

# Добавь в начало database.py после импортов:

# === ЮКАССА КОНФИГ ===
YOOKASSA_SHOP_ID = "1237681"
YOOKASSA_SECRET_KEY = "live_-Qdq_6lyDp0c1ck5HkZ_xLw5ZFtO5s7oyJquVI7hweA"
YOOKASSA_RETURN_URL = "https://t.me/MarafonRM_bot"
YOOKASSA_WEBHOOK_URL = "https://svs365bot.ru/webhook/yookassa"
YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"

# Базовые заголовки для запросов
yookassa_headers = {
    "Content-Type": "application/json",
    "Idempotence-Key": "",
    "Authorization": ""
}

# Словарь городов и их таймзон (смещение от МСК)
CITY_TIMEZONES = {
    "Калининград (-1)": -1,      # МСК-1
    "Москва (+0)": 0,           # МСК+0
    "Самара (+1)": 1,           # МСК+1
    "Екатеринбург (+2)": 2,     # МСК+2
    "Омск (+3)": 3,             # МСК+3
    "Новосибирск (+4)": 4,      # МСК+4
    "Красноярск (+4)": 4,       # МСК+4
    "Иркутск (+5)": 5,          # МСК+5
    "Якутск (+6)": 6,           # МСК+6
    "Владивосток (+7)": 7,     # МСК+7
    "Магадан (+8)": 8,         # МСК+8
    "Камчатка (+9)": 9         # МСК+9
}

def get_available_cities():
    """Возвращает список доступных городов"""
    return list(CITY_TIMEZONES.keys())

def get_user_local_time(user_id):
    """Возвращает время пользователя с учетом его таймзоны (относительно МСК)"""
    from bot import get_moscow_time  # Импортируем из bot.py
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT timezone_offset FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0] is not None:
        timezone_offset = result[0]
        # Берем московское время как базовое
        moscow_time = get_moscow_time()
        return moscow_time + timedelta(hours=timezone_offset)
    else:
        return get_moscow_time()

def set_user_timezone(user_id, city, timezone_offset):
    """Устанавливает город и таймзону пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users SET city = ?, timezone_offset = ? 
        WHERE user_id = ?
    ''', (city, timezone_offset, user_id))
    
    conn.commit()
    conn.close()

def is_day_available(user_id, day_id):
    """Проверяет доступен ли день пользователю"""
    user_time = get_user_local_time(user_id)
    return user_time.hour >= 0  # Доступно с 00:00 местного времени

def is_assignment_available(user_id, assignment_id):
    """Проверяет доступно ли задание до 12:00 местного времени"""
    user_time = get_user_local_time(user_id)
    return user_time.hour < 23  # Доступно до 22:00

def get_user_current_day(user_id, arc_id):
    """Определяет текущий день дуги для пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем дату начала доступа к дуге
    cursor.execute('''
        SELECT purchased_at FROM user_arc_access 
        WHERE user_id = ? AND arc_id = ?
    ''', (user_id, arc_id))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        start_date = datetime.fromisoformat(result[0])
        user_time = get_user_local_time(user_id)
        days_passed = (user_time.date() - start_date.date()).days
        return min(days_passed + 1, 40)  # Не больше 40 дней
    else:
        return 1  # Первый день по умолчанию

def save_assignment_answer(user_id, assignment_id, answer_text, answer_files):
    """Сохраняет ответ на задание (текст + файлы)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Сохраняем файлы как JSON
    files_json = json.dumps(answer_files) if answer_files else None
    
    cursor.execute('''
        INSERT OR REPLACE INTO user_progress_advanced 
        (user_id, assignment_id, answer_text, answer_files, status, viewed_by_student)
        VALUES (?, ?, ?, ?, ?, 0)
    ''', (user_id, assignment_id, answer_text, files_json, 'submitted'))
    
    conn.commit()
    conn.close()

def get_user_assignments_for_day(user_id, day_id):
    """Получает все задания для дня пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.assignment_id, a.title, a.content_text,
               up.status, up.teacher_comment
        FROM assignments a
        LEFT JOIN user_progress_advanced up ON a.assignment_id = up.assignment_id AND up.user_id = ?
        WHERE a.day_id = ?
        ORDER BY a.assignment_id
    ''', (user_id, day_id))
    
    results = cursor.fetchall()
    conn.close()
    return results

def update_daily_stats(user_id, arc_id, day_id, completed_count):
    """Обновляет статистику дня (пропуск/выполнение)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    total_assignments = get_day_assignments_count(day_id)
    is_skipped = completed_count < total_assignments / 2
    
    cursor.execute('''
        INSERT OR REPLACE INTO user_daily_stats 
        (user_id, arc_id, day_id, date, assignments_completed, is_skipped)
        VALUES (?, ?, ?, DATE('now'), ?, ?)
    ''', (user_id, arc_id, day_id, completed_count, is_skipped))
    
    conn.commit()
    conn.close()

def get_day_assignments_count(day_id):
    """Возвращает количество заданий в дне"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM assignments WHERE day_id = ?', (day_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def init_db():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()

    # ★★★ ОБНОВЛЕННАЯ ТАБЛИЦА ARCS ★★★
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS arcs (
            arc_id INTEGER PRIMARY KEY,
            course_id INTEGER,
            title TEXT,
            order_num INTEGER,
            price INTEGER,
            дата_начала DATE,
            дата_окончания DATE,
            бесплатный_период INTEGER DEFAULT 7,
            status TEXT DEFAULT 'active',
            is_available BOOLEAN DEFAULT 1,
            FOREIGN KEY (course_id) REFERENCES courses(course_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            fio TEXT,
            city TEXT,
            timezone_offset INTEGER DEFAULT 0,
            phone TEXT,
            accepted_offer BOOLEAN DEFAULT 0,
            accepted_offer_date TEXT,
            accepted_service_offer BOOLEAN DEFAULT 0,
            accepted_service_offer_date TEXT,
            is_admin BOOLEAN DEFAULT 0,
            is_blocked BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            course_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            arc_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            yookassa_payment_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            metadata TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (arc_id) REFERENCES arcs(arc_id)
        )
    ''')

    # ★★★ СОЗДАЕМ ТАБЛИЦУ ASSIGNMENTS ЗДЕСЬ ★★★
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            order_num INTEGER UNIQUE,
            course_id INTEGER DEFAULT 1,
            day_id INTEGER,
            content_text TEXT,
            content_files TEXT,
            FOREIGN KEY (course_id) REFERENCES courses (course_id),
            FOREIGN KEY (day_id) REFERENCES days (day_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assignment_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            assignment_id INTEGER,
            file_id TEXT,
            status TEXT DEFAULT 'submitted',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (assignment_id) REFERENCES assignments (assignment_id)
        )
    ''')

    

    # ★★★ НОВЫЕ ПОЛЯ ДЛЯ USERS ★★★
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN fio TEXT')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN city TEXT')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN timezone_offset INTEGER')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    
    # ★★★ НОВЫЕ ТАБЛИЦЫ СТРУКТУРЫ КУРСОВ ★★★
    # УДАЛИЛИ ДУБЛИРОВАННЫЙ CREATE TABLE arcs (уже создана выше)
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS days (
            day_id INTEGER PRIMARY KEY AUTOINCREMENT,
            arc_id INTEGER,
            title TEXT NOT NULL,
            order_num INTEGER,
            FOREIGN KEY (arc_id) REFERENCES arcs (arc_id)
        )
    ''')
    
    
    # ★★★ ТАБЛИЦЫ ДОСТУПА И ПРОГРЕССА ★★★
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_arc_access (
            user_id INTEGER,
            arc_id INTEGER,
            access_type TEXT DEFAULT 'paid', -- 'paid', 'free', 'trial'
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (arc_id) REFERENCES arcs (arc_id),
            PRIMARY KEY (user_id, arc_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress_advanced (
            user_id INTEGER,
            assignment_id INTEGER,
            status TEXT DEFAULT 'submitted', -- 'submitted', 'approved', 'rejected'
            answer_text TEXT,
            answer_files TEXT, -- JSON с file_id
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            teacher_comment TEXT,
            viewed_by_student BOOLEAN DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (assignment_id) REFERENCES assignments (assignment_id),
            PRIMARY KEY (user_id, assignment_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_daily_stats (
            user_id INTEGER,
            arc_id INTEGER,
            day_id INTEGER,
            date DATE,
            assignments_completed INTEGER DEFAULT 0,
            is_skipped BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (arc_id) REFERENCES arcs (arc_id),
            FOREIGN KEY (day_id) REFERENCES days (day_id),
            PRIMARY KEY (user_id, day_id)
        )
    ''')
    
    # ★★★ ТАБЛИЦЫ АДМИНКИ ★★★
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS free_access_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            arc_id INTEGER,
            granted_by INTEGER,
            granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (arc_id) REFERENCES arcs (arc_id)
        )
    ''')

    # ★★★ ТАБЛИЦА ЛОГОВ УВЕДОМЛЕНИЙ ★★★
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notification_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            recipient_type TEXT,
            text TEXT,
            photo_id TEXT,
            success_count INTEGER,
            fail_count INTEGER,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES users(user_id)
        )
    ''')

    # ★★★ ОБНОВЛЯЕМ ТАБЛИЦУ ASSIGNMENTS ★★★
    try:
        cursor.execute('ALTER TABLE assignments ADD COLUMN day_id INTEGER')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE assignments ADD COLUMN content_text TEXT')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE assignments ADD COLUMN content_files TEXT')
    except sqlite3.OperationalError:
        pass
    
    # ★★★ ОБНОВЛЯЕМ ТАБЛИЦУ USERS ★★★
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()

# В функции add_user добавляем новое поле
def add_user(user_id, username, first_name):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Сначала проверяем, есть ли пользователь
    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    exists = cursor.fetchone()
    
    if not exists:
        # Новый пользователь
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, accepted_offer, created_at)
            VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
        ''', (user_id, username, first_name))
        print(f"✅ Новый пользователь: {user_id}")
    else:
        # Существующий - обновляем только username/first_name
        cursor.execute('''
            UPDATE users 
            SET username = ?, first_name = ?
            WHERE user_id = ?
        ''', (username, first_name, user_id))
        print(f"🔄 Обновлен пользователь: {user_id}")
    
    conn.commit()
    conn.close()

def init_assignments():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            order_num INTEGER UNIQUE
        )
    ''')
    
    # Добавляем тестовые задания
    assignments = [
        ("Задание 1: Психология", "Работа первая", 1),
        ("Задание 2: Психология", "Работа вторая", 2),
        ("Задание 3: Психология", "Работа третья", 3)
    ]
    
    cursor.executemany('''
        INSERT OR IGNORE INTO assignments (title, description, order_num)
        VALUES (?, ?, ?)
    ''', assignments)
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER,
            assignment_id INTEGER,
            status TEXT DEFAULT 'locked',
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (assignment_id) REFERENCES assignments (assignment_id)
        )
    ''')

    # ★★★ ДОБАВЛЯЕМ ТЕСТОВЫЙ КУРС ★★★
    cursor.execute('''
        INSERT OR IGNORE INTO courses (course_id, title, description)
        VALUES (1, 'Психология', 'Курс по основам психологии')
    ''')
    
    # ★★★ ДОБАВЛЯЕМ ПОЛЕ course_id В ТАБЛИЦУ ЗАДАНИЙ ★★★
    try:
        cursor.execute('ALTER TABLE assignments ADD COLUMN course_id INTEGER DEFAULT 1')
    except sqlite3.OperationalError:
        pass  # Поле уже существует

    # ★★★ СОЗДАЕМ ТЕСТОВЫЕ ДНИ ДЛЯ ДУГ ★★★
    # Получаем ID дуг
    cursor.execute('SELECT arc_id FROM arcs')
    arcs = cursor.fetchall()
    
    for arc_id, in arcs:
        # Создаем 5 тестовых дней для каждой дуги
        for day_num in range(1, 6):
            cursor.execute('''
                INSERT OR IGNORE INTO days (arc_id, title, order_num)
                VALUES (?, ?, ?)
            ''', (arc_id, f"День {day_num}", day_num))

    # ★★★ ДОБАВЛЯЕМ ТЕСТОВЫЕ ЗАДАНИЯ ★★★
    # Получаем ID дней
    cursor.execute('SELECT day_id FROM days LIMIT 5')  # Первые 5 дней
    days = cursor.fetchall()
    
    for day_id, in days:
        # Создаем 2 задания для каждого дня
        cursor.execute('''
            INSERT OR IGNORE INTO assignments (day_id, title, content_text, content_files)
            VALUES (?, ?, ?, ?)
        ''', (day_id, "Задание 1", "Опиши свои чувства и мысли сегодня...", None))
        
        cursor.execute('''
            INSERT OR IGNORE INTO assignments (day_id, title, content_text, content_files)
            VALUES (?, ?, ?, ?)
        ''', (day_id, "Задание 2", "Сделай упражнение на осознанность...", None))
    
    conn.commit()
    conn.close()

def get_current_assignment(user_id):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.assignment_id, a.title, a.description 
        FROM assignments a
        LEFT JOIN user_progress up ON a.assignment_id = up.assignment_id AND up.user_id = ?
        WHERE up.status IS NULL OR up.status != 'approved'
        ORDER BY a.order_num
        LIMIT 1
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    return result

def save_submission(user_id, assignment_id, file_id):
    print("=== DEBUG SAVE_SUBMISSION START ===")
    print("Params:", user_id, assignment_id, file_id)
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO user_progress 
        (user_id, assignment_id, status, file_id) 
        VALUES (?, ?, 'submitted', ?)
    ''', (user_id, assignment_id, file_id))
    
    conn.commit()
    conn.close()
    print("=== DEBUG SAVE_SUBMISSION END ===")

def get_submissions():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.user_id, u.username, a.title, up.assignment_id
        FROM user_progress up
        JOIN users u ON up.user_id = u.user_id
        JOIN assignments a ON up.assignment_id = a.assignment_id
        WHERE up.status = 'submitted'
    ''')
    
    results = cursor.fetchall()
    conn.close()
    return results

def update_submission(user_id, assignment_id, status):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE user_progress 
        SET status = ?
        WHERE user_id = ? AND assignment_id = ?
    ''', (status, user_id, assignment_id))
    
    conn.commit()
    conn.close()

def get_submission_file(user_id, assignment_id):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT file_id FROM user_progress 
        WHERE user_id = ? AND assignment_id = ? AND status = 'submitted'
    ''', (user_id, assignment_id))
    
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# Новая функция проверки оплаты
def check_payment(user_id, course_id=1):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 1 FROM payments 
        WHERE user_id = ? AND course_id = ?
    ''', (user_id, course_id))
    
    result = cursor.fetchone()
    conn.close()
    return result is not None

# Функция имитации оплаты
def add_payment(user_id, course_id=1):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR IGNORE INTO payments (user_id, course_id)
        VALUES (?, ?)
    ''', (user_id, course_id))
    
    conn.commit()
    conn.close()

def get_students_with_submissions():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            u.user_id,
            u.username,
            u.first_name,
            COUNT(af.id) as total_files,
            -- ★★★ ПРОСТАЯ ЛОГИКА: есть ли непроверенные файлы ★★★
            EXISTS(SELECT 1 FROM assignment_files WHERE user_id = u.user_id AND status = 'submitted') as has_new_files,
            -- ★★★ Все ли файлы приняты ★★★
            NOT EXISTS(SELECT 1 FROM assignment_files WHERE user_id = u.user_id AND status != 'approved') as all_approved
        FROM users u
        JOIN assignment_files af ON u.user_id = af.user_id
        WHERE af.file_id IS NOT NULL
        GROUP BY u.user_id
        HAVING COUNT(af.id) > 0
        ORDER BY has_new_files DESC, u.user_id
    ''')
    
    results = cursor.fetchall()
    conn.close()
    return results

def upgrade_database():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('ALTER TABLE user_progress ADD COLUMN submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    except sqlite3.OperationalError:
        pass  # Поле уже существует
    
    conn.commit()
    conn.close()

def get_student_submissions(user_id):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # ★★★ УПРОЩЕННЫЙ ЗАПРОС БЕЗ СЛОЖНЫХ ПОДЗАПРОСОВ ★★★
    cursor.execute('''
        SELECT 
            af.id as file_db_id,
            a.assignment_id,
            a.title,
            af.status,
            af.file_id as telegram_file_id,
            af.created_at
        FROM assignments a
        JOIN assignment_files af ON a.assignment_id = af.assignment_id 
        WHERE af.user_id = ? AND af.file_id IS NOT NULL
        ORDER BY a.order_num, af.created_at
    ''', (user_id,))
    
    results = cursor.fetchall()
    conn.close()
    return results

def upgrade_database():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('ALTER TABLE user_progress ADD COLUMN submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE user_progress ADD COLUMN file_id TEXT')  # ← ДОБАВЬ ЭТУ СТРОКУ
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()

def create_test_submission():
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Просто берем первого пользователя
    cursor.execute('SELECT user_id FROM users LIMIT 1')
    user_result = cursor.fetchone()
    
    if user_result:
        user_id = user_result[0]
        cursor.execute('SELECT assignment_id FROM assignments ORDER BY order_num LIMIT 1')
        assignment_result = cursor.fetchone()
        
        if assignment_result:
            assignment_id = assignment_result[0]
            cursor.execute('''
                INSERT OR REPLACE INTO user_progress 
                (user_id, assignment_id, status, file_id) 
                VALUES (?, ?, ?, ?)
            ''', (user_id, assignment_id, 'submitted', 'test_file_id'))
    
    conn.commit()
    conn.close()

def save_assignment_file(user_id, assignment_id, file_id):
    """Сохраняет файл в новую таблицу для нескольких файлов"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO assignment_files (user_id, assignment_id, file_id)
        VALUES (?, ?, ?)
    ''', (user_id, assignment_id, file_id))
    
    conn.commit()
    conn.close()
    print(f"✅ Файл сохранен в assignment_files: user={user_id}, assignment={assignment_id}")

def get_assignment_files(user_id, assignment_id):
    """Получает все файлы для конкретного задания пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, file_id, status, created_at
        FROM assignment_files 
        WHERE user_id = ? AND assignment_id = ?
        ORDER BY created_at DESC
    ''', (user_id, assignment_id))
    
    results = cursor.fetchall()
    conn.close()
    return results

def get_assignment_file_count(user_id, assignment_id):
    """Получает количество файлов для задания"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*) FROM assignment_files 
        WHERE user_id = ? AND assignment_id = ?
    ''', (user_id, assignment_id))
    
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def get_course_status(user_id):
    """Получает статусы курсов для ученика"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            c.course_id,
            c.title,
            -- Есть ли непроверенные файлы в курсе
            EXISTS(SELECT 1 
                  FROM assignment_files af 
                  JOIN assignments a ON af.assignment_id = a.assignment_id 
                  WHERE af.user_id = ? AND a.course_id = c.course_id AND af.status = 'submitted') as has_new_files,
            -- Все ли файлы приняты в курсе
            NOT EXISTS(SELECT 1 
                      FROM assignment_files af 
                      JOIN assignments a ON af.assignment_id = a.assignment_id 
                      WHERE af.user_id = ? AND a.course_id = c.course_id AND af.status != 'approved') as all_approved,
            COUNT(af.id) as total_files
        FROM courses c
        LEFT JOIN assignments a ON c.course_id = a.course_id
        LEFT JOIN assignment_files af ON a.assignment_id = af.assignment_id AND af.user_id = ?
        WHERE af.id IS NOT NULL
        GROUP BY c.course_id
        HAVING COUNT(af.id) > 0
    ''', (user_id, user_id, user_id))
    
    results = cursor.fetchall()
    conn.close()
    return results

def get_assignment_status(user_id, course_title):
    """Получает статусы заданий в курсе"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            a.assignment_id,
            a.title,
            -- Есть ли непроверенные файлы в задании
            EXISTS(SELECT 1 
                  FROM assignment_files af 
                  WHERE af.user_id = ? AND af.assignment_id = a.assignment_id AND af.status = 'submitted') as has_new_files,
            -- Все ли файлы приняты в задании
            NOT EXISTS(SELECT 1 
                      FROM assignment_files af 
                      WHERE af.user_id = ? AND af.assignment_id = a.assignment_id AND af.status != 'approved') as all_approved,
            COUNT(af.id) as total_files
        FROM assignments a
        JOIN courses c ON a.course_id = c.course_id
        LEFT JOIN assignment_files af ON a.assignment_id = af.assignment_id AND af.user_id = ?
        WHERE c.title = ? AND af.id IS NOT NULL
        GROUP BY a.assignment_id
        HAVING COUNT(af.id) > 0
    ''', (user_id, user_id, user_id, course_title))
    
    results = cursor.fetchall()
    conn.close()
    return results


def check_user_arc_access(user_id, arc_id):
    """Проверяет доступ пользователя к дуге - ИСПРАВЛЕННАЯ"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT 1 FROM user_arc_access 
            WHERE user_id = ? AND arc_id = ?
        ''', (user_id, arc_id))
        
        result = cursor.fetchone()
        
        # Логирование для отладки
        if result:
            print(f"🔍 DEBUG: Доступ ЕСТЬ - user={user_id}, arc={arc_id}")
        else:
            print(f"🔍 DEBUG: Доступа НЕТ - user={user_id}, arc={arc_id}")
            # Покажем что есть в таблице
            cursor.execute('SELECT user_id, arc_id FROM user_arc_access WHERE user_id = ?', (user_id,))
            all_access = cursor.fetchall()
            print(f"🔍 Все доступы пользователя {user_id}: {all_access}")
        
        return result is not None
        
    except Exception as e:
        print(f"🚨 Ошибка проверки доступа: {e}")
        return False
    finally:
        conn.close()

def get_user_skip_days(user_id, arc_id):
    """Возвращает количество пропущенных дней в дуге"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*) FROM user_daily_stats 
        WHERE user_id = ? AND arc_id = ? AND is_skipped = 1
    ''', (user_id, arc_id))
    
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def get_users_with_skipped_days():
    """Возвращает учеников с пропущенными днями"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.user_id, u.fio, u.username, u.is_blocked,
               COUNT(CASE WHEN uds.is_skipped = 1 THEN 1 END) as skip_days
        FROM users u
        JOIN user_daily_stats uds ON u.user_id = uds.user_id
        WHERE uds.is_skipped = 1
        GROUP BY u.user_id
        HAVING skip_days > 0
        ORDER BY skip_days DESC
    ''')
    
    results = cursor.fetchall()
    conn.close()
    return results

def block_user(user_id):
    """Блокирует пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET is_blocked = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id):
    """Разблокирует пользователя и сбрасывает пропуски"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET is_blocked = 0 WHERE user_id = ?', (user_id,))
    
    # Сбрасываем пропуски для текущей дуги
    cursor.execute('''
        UPDATE user_daily_stats SET is_skipped = 0 
        WHERE user_id = ? AND date >= DATE('now', '-30 days')
    ''', (user_id,))
    
    conn.commit()
    conn.close()
def test_new_structure():
    """Тестирует новую структуру БД"""
    print("🧪 Тестирование новой структуры БД...")
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Проверяем существование новых таблиц
    tables = ['arcs', 'days', 'user_arc_access', 'user_progress_advanced', 'user_daily_stats', 'free_access_grants']
    
    for table in tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        exists = cursor.fetchone()
        print(f"✅ Таблица {table}: {'СОЗДАНА' if exists else 'ОТСУТСТВУЕТ'}")
    
    # Проверяем новые поля в users
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    new_fields = ['fio', 'city', 'timezone_offset', 'is_blocked']
    
    for field in new_fields:
        print(f"✅ Поле {field} в users: {'ЕСТЬ' if field in columns else 'ОТСУТСТВУЕТ'}")
    
    conn.close()
    print("🧪 Тестирование завершено!")

# ★★★ ВЫЗЫВАЕМ ПРИ ЗАПУСКЕ ★★★
if __name__ == "__main__":
    init_db()
    init_assignments()
    test_new_structure()

def add_test_access(user_id):
    """Добавляет тестовый доступ к первой дуге для тестирования"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем ID первой дуги
    cursor.execute('SELECT arc_id FROM arcs ORDER BY arc_id LIMIT 1')
    arc_result = cursor.fetchone()
    
    if arc_result:
        arc_id = arc_result[0]
        # Добавляем доступ
        cursor.execute('''
            INSERT OR REPLACE INTO user_arc_access (user_id, arc_id, access_type)
            VALUES (?, ?, 'free')
        ''', (user_id, arc_id))
    
    conn.commit()
    conn.close()


def load_courses_from_excel():
    """Загружает данные курсов из Excel файла - ПОЛНАЯ ВЕРСИЯ"""
    print("📥 Загружаем данные из Excel...")
    
    try:
        excel_file = 'courses_data.xlsx'
        
        # 1. Загружаем курсы
        df_courses = pd.read_excel(excel_file, sheet_name='Курсы')
        print(f"📚 Найдено курсов: {len(df_courses)}")
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        # Очищаем таблицы
        cursor.execute('DELETE FROM courses')
        cursor.execute('DELETE FROM arcs')
        cursor.execute('DELETE FROM days')
        cursor.execute('DELETE FROM assignments')
        
        # 2. Загружаем курсы
        for _, row in df_courses.iterrows():
            cursor.execute('''
                INSERT INTO courses (course_id, title, description, status)
                VALUES (?, ?, ?, ?)
            ''', (row['id'], row['название'], row['описание'], row['статус']))
        
        print(f"✅ Загружено {len(df_courses)} курсов")
        
        # 3. Загружаем дуги
        df_arcs = pd.read_excel(excel_file, sheet_name='Дуги')
        print(f"🔄 Найдено дуг: {len(df_arcs)}")
        
        for _, row in df_arcs.iterrows():
            cursor.execute('''
                INSERT INTO arcs 
                (arc_id, course_id, title, order_num, price, 
                 дата_начала, дата_окончания, бесплатный_период, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['id'], 
                row['id_курса'], 
                row['название'],
                row['порядок'], 
                row['цена'],
                row['дата_начала'], 
                row['дата_окончания'],
                row['бесплатный_период'],
                row['статус']
            ))
        
        print(f"✅ Загружено {len(df_arcs)} дуг")
        
        # 4. ★★★ ЗАГРУЖАЕМ ДНИ ★★★
        try:
            df_days = pd.read_excel(excel_file, sheet_name='Дни')
            print(f"📅 Найдено дней: {len(df_days)}")
            
            days_loaded = 0
            for _, row in df_days.iterrows():
                try:
                    cursor.execute('''
                        INSERT INTO days (day_id, arc_id, title, order_num)
                        VALUES (?, ?, ?, ?)
                    ''', (
                        row['id'],
                        row['id_дуги'],
                        row['название'],
                        row['порядок']
                    ))
                    days_loaded += 1
                except Exception as e:
                    print(f"⚠️ Ошибка загрузки дня {row['id']}: {e}")
            
            print(f"✅ Загружено {days_loaded} дней")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки дней: {e}")
        
        # 5. ★★★ ЗАГРУЖАЕМ ЗАДАНИЯ ★★★
        try:
            df_assignments = pd.read_excel(excel_file, sheet_name='Задания')
            print(f"📝 Найдено заданий: {len(df_assignments)}")
            
            assignments_loaded = 0
            for _, row in df_assignments.iterrows():
                try:
                    cursor.execute('''
                        INSERT INTO assignments 
                        (assignment_id, day_id, title, content_text, доступно_до, тип)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        row['id'],
                        row['id_дня'],
                        row['название'],
                        row['текст_задания'],
                        row['доступно_до'],
                        row['тип']
                    ))
                    assignments_loaded += 1
                except Exception as e:
                    print(f"⚠️ Ошибка загрузки задания {row['id']}: {e}")
            
            print(f"✅ Загружено {assignments_loaded} заданий")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки заданий: {e}")
        
        conn.commit()
        conn.close()
        
        print("🎉 Загрузка из Excel завершена!")
        
    except Exception as e:
        print(f"❌ Критическая ошибка загрузки из Excel: {e}")
        import traceback
        traceback.print_exc()

def reload_courses_data():
    """Перезагружает данные курсов из Excel - ОБНОВЛЕННАЯ ВЕРСИЯ"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Читаем Excel файл
        df_arcs = pd.read_excel('courses_data.xlsx', sheet_name='Дуги')
        
        # ★★★ ОБНОВЛЯЕМ ТАБЛИЦУ ARCS С ВСЕМИ КОЛОНКАМИ ★★★
        cursor.execute('DROP TABLE IF EXISTS arcs')
        cursor.execute('''
            CREATE TABLE arcs (
                arc_id INTEGER PRIMARY KEY,
                course_id INTEGER,
                title TEXT,
                order_num INTEGER,
                price INTEGER,
                дата_начала DATE,
                дата_окончания DATE,
                бесплатный_период INTEGER,
                status TEXT,
                is_available BOOLEAN DEFAULT 1
            )
        ''')
        
        # Загружаем данные с ВСЕМИ колонками
        for _, row in df_arcs.iterrows():
            cursor.execute('''
                INSERT INTO arcs 
                (arc_id, course_id, title, order_num, price, 
                 дата_начала, дата_окончания, бесплатный_период, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['id'], row['id_курса'], row['название'],
                row['порядок'], row['цена'],
                row['дата_начала'], row['дата_окончания'],
                row['бесплатный_период'], row['статус']
            ))
        
        conn.commit()
        print(f"✅ Загружено {len(df_arcs)} дуг с датами")
        
    except Exception as e:
        print(f"❌ Ошибка загрузки дуг: {e}")
    
    finally:
        conn.close()

def check_database_structure():
    """Проверяет текущую структуру базы данных"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    print("🧪 ПРОВЕРКА СТРУКТУРЫ БАЗЫ ДАННЫХ:")
    
    # Проверяем таблицы
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"📊 Таблицы в базе: {[table[0] for table in tables]}")
    
    # Проверяем данные в таблицах
    for table in ['courses', 'arcs', 'days', 'assignments']:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"📋 {table}: {count} записей")

        if count > 0:
            cursor.execute(f"SELECT * FROM {table} LIMIT 3")
            sample = cursor.fetchall()
            print(f"   Пример: {sample}")

    # Проверим поля user_progress_advanced
    cursor.execute("PRAGMA table_info(user_progress_advanced)")
    columns = cursor.fetchall()
    print(f"📋 Поля user_progress_advanced: {[col[1] for col in columns]}")
    
    conn.close()

def get_user_courses(user_id):
    """Получает курсы доступные пользователю"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT c.course_id, c.title 
        FROM courses c
        LEFT JOIN user_arc_access uaa ON c.course_id = uaa.arc_id AND uaa.user_id = ?
        WHERE c.course_id = 1 OR uaa.user_id IS NOT NULL
    ''', (user_id,))
    
    results = cursor.fetchall()
    conn.close()
    return results


def get_course_arcs(course_title):
    """Получает дуги курса (заглушка)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT arc_id, title, is_available
        FROM arcs 
        WHERE course_id = 1
        ORDER BY order_num
    ''')
    
    arcs = cursor.fetchall()
    conn.close()
    return arcs

def grant_arc_access(user_id, arc_id, access_type='paid'):
    """Простая версия - только добавляет доступ"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Просто добавляем или обновляем запись
        cursor.execute('''
            INSERT OR REPLACE INTO user_arc_access (user_id, arc_id, access_type)
            VALUES (?, ?, ?)
        ''', (user_id, arc_id, access_type))
        
        conn.commit()
        print(f"✅ Доступ добавлен: user {user_id} -> arc {arc_id}")
    
    except Exception as e:
        print(f"🚨 Ошибка при добавлении доступа: {e}")
        
        # Если ошибка - пробуем создать таблицу
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_arc_access (
                    user_id INTEGER,
                    arc_id INTEGER,
                    access_type TEXT,
                    PRIMARY KEY (user_id, arc_id)
                )
            ''')
            
            # Пробуем снова
            cursor.execute('''
                INSERT OR REPLACE INTO user_arc_access (user_id, arc_id, access_type)
                VALUES (?, ?, ?)
            ''', (user_id, arc_id, access_type))
            
            conn.commit()
            print(f"✅ Таблица создана и доступ добавлен")
        
        except Exception as e2:
            print(f"🚨 Критическая ошибка: {e2}")
    
    finally:
        conn.close()

def is_day_available(user_id, arc_id, day_order):
    """Проверяет, доступен ли день для пользователя"""
    # ★★★ ВРЕМЕННАЯ ЗАГЛУШКА - ВСЕ ДНИ ДОСТУПНЫ ★★★
    # Позже реализуем логику открытия дней по расписанию
    return True

def check_user_arc_access(user_id, arc_id):
    """Проверяет доступ пользователя к дуге"""
    # ★★★ АДМИНУ ВСЕГДА ДОСТУП ★★★
    from config import ADMIN_ID
    if user_id == ADMIN_ID:
        return True
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 1 FROM user_arc_access 
        WHERE user_id = ? AND arc_id = ?
    ''', (user_id, arc_id))
    
    has_access = cursor.fetchone() is not None
    conn.close()
    
    return has_access

def check_assignments_structure():
    """Проверяет структуру заданий и их связь с днями"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    print("🧪 ПРОВЕРКА СВЯЗИ ЗАДАНИЙ И ДНЕЙ:")
    
    # Проверяем, есть ли у заданий day_id
    cursor.execute("PRAGMA table_info(assignments)")
    columns = cursor.fetchall()
    print(f"📋 Поля таблицы assignments:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    # Проверяем несколько заданий
    cursor.execute('''
        SELECT a.assignment_id, a.title, a.day_id, d.title as day_title, ar.title as arc_title
        FROM assignments a
        LEFT JOIN days d ON a.day_id = d.day_id
        LEFT JOIN arcs ar ON d.arc_id = ar.arc_id
        WHERE a.assignment_id <= 10
        ORDER BY a.assignment_id
    ''')
    
    assignments = cursor.fetchall()
    print(f"\n📝 Первые 10 заданий:")
    for assignment in assignments:
        print(f"  - ID:{assignment[0]} '{assignment[1]}' -> День:{assignment[2]} '{assignment[3]}' -> Дуга:'{assignment[4]}'")
    
    conn.close()

def get_day_id_by_title(day_title, arc_id):
    """Находит ID дня по его названию и ID дуги"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT day_id FROM days WHERE title = ? AND arc_id = ?', 
                   (day_title, arc_id))
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None

def save_assignment_answer_with_day(user_id, assignment_id, day_id, answer_text, answer_files):
    """Сохраняет ответ с указанием дня"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Сохраняем файлы как JSON
    files_json = json.dumps(answer_files) if answer_files else None
    
    # ★★★ ВАЖНО: Добавляем day_id в таблицу прогресса ★★★
    try:
        # Сначала добавляем колонку day_id если её нет
        cursor.execute("PRAGMA table_info(user_progress_advanced)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'day_id' not in columns:
            cursor.execute('ALTER TABLE user_progress_advanced ADD COLUMN day_id INTEGER')
    except:
        pass
    
    # Сохраняем с day_id
    cursor.execute('''
        INSERT OR REPLACE INTO user_progress_advanced 
        (user_id, assignment_id, day_id, answer_text, answer_files, status, viewed_by_student)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    ''', (user_id, assignment_id, day_id, answer_text, files_json, 'submitted'))
    
    conn.commit()
    conn.close()
    print(f"✅ Ответ сохранен: user={user_id}, assignment={assignment_id}, day={day_id}")

def get_day_id_by_title_and_arc(day_title, arc_id):
    """Находит ID дня по названию и ID дуги"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT day_id FROM days 
        WHERE title = ? AND arc_id = ?
    ''', (day_title, arc_id))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None

def get_assignment_by_title_and_day(assignment_title, day_id):
    """Находит задание по названию и ID дня"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT assignment_id FROM assignments 
        WHERE title = ? AND day_id = ?
    ''', (assignment_title, day_id))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None

def is_day_available_for_user(user_id, day_id):
    """Проверяет доступен ли день для выполнения заданий"""
    print(f"🚨 DEBUG is_day_available: user_id={user_id}, day_id={day_id}")
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем время дедлайна из заданий дня
    cursor.execute('''
        SELECT доступно_до 
        FROM assignments 
        WHERE day_id = ?
        LIMIT 1
    ''', (day_id,))
    
    deadline_result = cursor.fetchone()
    
    if not deadline_result or not deadline_result[0]:
        # Если время не указано - используем 12:00 по умолчанию
        deadline_hour = 12
        deadline_minute = 0
    else:
        # Парсим время из формата "22:00"
        try:
            time_str = str(deadline_result[0])
            if ':' in time_str:
                deadline_hour, deadline_minute = map(int, time_str.split(':'))
            else:
                deadline_hour, deadline_minute = 23, 59
        except:
            deadline_hour, deadline_minute = 23, 59
    
    # Получаем местное время пользователя
    user_time = get_user_local_time(user_id)
    user_hour = user_time.hour
    user_minute = user_time.minute
    
    # Проверяем не истекло ли время
    if user_hour > deadline_hour or (user_hour == deadline_hour and user_minute >= deadline_minute):
        # Время истекло
        conn.close()
        return False
    
    conn.close()
    return True
    print(f"🚨 DEBUG: user_time={user_time.strftime('%H:%M')}, deadline={deadline_hour}:{deadline_minute:02d}")
    print(f"🚨 DEBUG: result={not (user_hour > deadline_hour or (user_hour == deadline_hour and user_minute >= deadline_minute))}")
    
    return not (user_hour > deadline_hour or (user_hour == deadline_hour and user_minute >= deadline_minute))

def get_available_days_for_user(user_id, arc_id):
    """Возвращает доступные дни для пользователя в дуге"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем дату начала доступа
    cursor.execute('''
        SELECT purchased_at FROM user_arc_access 
        WHERE user_id = ? AND arc_id = ?
    ''', (user_id, arc_id))
    
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return []
    
    purchased_at = result[0]
    purchase_date = datetime.fromisoformat(purchased_at).date()
    user_time = get_user_local_time(user_id)
    days_since_start = (user_time.date() - purchase_date).days + 1
    
    # Получаем дни дуги
    cursor.execute('''
        SELECT day_id, title, order_num 
        FROM days 
        WHERE arc_id = ? 
        ORDER BY order_num
    ''', (arc_id,))
    
    all_days = cursor.fetchall()
    conn.close()
    
    # Фильтруем по доступности
    available_days = []
    for day_id, title, order_num in all_days:
        if order_num <= days_since_start:
            available_days.append((day_id, title, order_num))
    
    return available_days

def mark_day_as_skipped(user_id, day_id):
    """Отмечает день как пропущенный"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем arc_id для дня
    cursor.execute('SELECT arc_id FROM days WHERE day_id = ?', (day_id,))
    arc_result = cursor.fetchone()
    
    if arc_result:
        arc_id = arc_result[0]
        
        cursor.execute('''
            INSERT OR REPLACE INTO user_daily_stats 
            (user_id, arc_id, day_id, date, is_skipped)
            VALUES (?, ?, ?, DATE('now'), 1)
        ''', (user_id, arc_id, day_id))
    
    conn.commit()
    conn.close()
    print(f"✅ День {day_id} отмечен как пропущенный для user {user_id}")

def check_and_open_missed_days(user_id):
    """Открывает текущий день если он еще не открыт"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # 1. Получаем активные дуги и их даты начала
    cursor.execute("PRAGMA table_info(arcs)")
    columns = [col[1] for col in cursor.fetchall()]
    start_col = next((col for col in ['дата_начала', 'date_start'] if col in columns), 'дата_начала')
    
    cursor.execute(f'''
        SELECT uaa.arc_id, a.title, a.{start_col} as arc_start
        FROM user_arc_access uaa
        JOIN arcs a ON uaa.arc_id = a.arc_id
        WHERE uaa.user_id = ? AND a.status = 'active'
    ''', (user_id,))
    
    arcs = cursor.fetchall()
    total_opened = 0
    
    for arc_id, arc_title, arc_start in arcs:
        if not arc_start:
            continue
            
        # 2. Конвертируем дату начала
        if isinstance(arc_start, str):
            arc_start_date = datetime.fromisoformat(arc_start).date()
        else:
            arc_start_date = arc_start
        
        user_time = get_user_local_time(user_id)
        
        # 3. Текущий день от начала дуги
        if user_time.date() < arc_start_date:
            continue  # Дуга еще не началась
        
        current_day = (user_time.date() - arc_start_date).days + 1
        
        if current_day <= 0:
            continue
        
        # Находим день
        cursor.execute('''
            SELECT d.day_id, d.title 
            FROM days d
            WHERE d.arc_id = ? AND d.order_num = ?
        ''', (arc_id, current_day))
        
        day_info = cursor.fetchone()
        
        if day_info:
            day_id, day_title = day_info
            
            # Проверяем не открыт ли уже день
            cursor.execute('''
                SELECT 1 FROM user_daily_stats 
                WHERE user_id = ? AND day_id = ?
            ''', (user_id, day_id))
            
            already_opened = cursor.fetchone()
            
            if not already_opened:
                cursor.execute('''
                    INSERT INTO user_daily_stats 
                    (user_id, arc_id, day_id, date, is_skipped)
                    VALUES (?, ?, ?, DATE('now'), 0)
                ''', (user_id, arc_id, day_id))
                total_opened += 1
                print(f"✅ Открыт текущий день: {day_title} (дуга: {arc_title})")
    
    conn.commit()
    conn.close()
    return total_opened

def get_current_arc_day(user_id, arc_id):
    """Возвращает текущий день дуги для пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # 1. Получаем дату начала дуги из таблицы arcs
    cursor.execute("PRAGMA table_info(arcs)")
    columns = [col[1] for col in cursor.fetchall()]
    
    # Ищем колонку с датой начала
    date_cols = ['дата_начала', 'date_start', 'start_date']
    start_col = next((col for col in date_cols if col in columns), None)
    
    if not start_col:
        # Fallback - используем purchased_at
        cursor.execute('SELECT purchased_at FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                      (user_id, arc_id))
        result = cursor.fetchone()
        if result:
            arc_start_date = datetime.fromisoformat(result[0]).date()
        else:
            conn.close()
            return None
    else:
        # Берем дату начала из arcs
        cursor.execute(f'SELECT {start_col} FROM arcs WHERE arc_id = ?', (arc_id,))
        result = cursor.fetchone()
        if result:
            arc_start_date = result[0]
            if isinstance(arc_start_date, str):
                arc_start_date = datetime.fromisoformat(arc_start_date).date()
        else:
            conn.close()
            return None
    
    # 2. Получаем местное время пользователя
    user_time = get_user_local_time(user_id)
    
    # 3. Вычисляем текущий день дуги
    # Если дуга еще не началась - день 0
    # Преобразуем arc_start_date в date если это datetime
    if isinstance(arc_start_date, datetime):
        arc_start_date_only = arc_start_date.date()
    elif isinstance(arc_start_date, str):
        # Если строка вида "2025-11-29"
        arc_start_date_only = datetime.fromisoformat(arc_start_date).date()
    else:
        arc_start_date_only = arc_start_date

    user_date = user_time.date()

    # Всегда добавляем +1, если дата >= дате начала
    days_diff = (user_date - arc_start_date_only).days
    if days_diff < 0:
        current_day = 0
    else:
        current_day = days_diff + 1

    print(f"📅 Расчет: {user_date} - {arc_start_date_only} = {days_diff} дней, день {current_day}")
    
    # Ограничиваем 40 днями
    current_day = min(max(current_day, 0), 40)
    
    print(f"🔍 DEBUG get_current_arc_day: arc_start_date={arc_start_date}, user_date={user_time.date()}, current_day={current_day}") 
    print(f"🔍 DEBUG: arc_start_date={arc_start_date}, user_date={user_time.date()}, current_day={current_day}")
    
    # Если день 0 - дуга еще не началась
    if current_day == 0:
        conn.close()
        return {
            'day_id': None,
            'day_title': f"Дуга начнется {arc_start_date}",
            'day_number': 0,
            'total_days': 40,
            'arc_start_date': arc_start_date
        }
    
    # 4. Находим день в базе
    cursor.execute('''
        SELECT day_id, title FROM days 
        WHERE arc_id = ? AND order_num = ?
    ''', (arc_id, current_day))
    
    day_info = cursor.fetchone()
    print(f"🔍 DEBUG: Запрос дня: arc_id={arc_id}, current_day={current_day}")
    print(f"🔍 DEBUG: Результат запроса: {day_info}")

    conn.close()

    if day_info:
        day_id, day_title = day_info
        print(f"✅ День найден: id={day_id}, title='{day_title}'")
        return {
            'day_id': day_id,
            'day_title': day_title,
            'day_number': current_day,
            'total_days': 40,
            'arc_start_date': arc_start_date
        }
    else:
        print(f"❌ День НЕ найден! arc_id={arc_id}, order_num={current_day}")
        print(f"   Проверь таблицу days: есть ли запись с arc_id={arc_id} и order_num={current_day}?")
    
    # Если дня нет в базе (например, день > 40)
    return {
        'day_id': None,
        'day_title': f"День {current_day}",
        'day_number': current_day,
        'total_days': 40,
        'arc_start_date': arc_start_date
    }


def get_current_arc():
    """Всегда возвращает дугу 1 для тестирования (до 10 января 2026)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # ВРЕМЕННО: всегда дуга 1
    cursor.execute('SELECT arc_id, title FROM arcs WHERE arc_id = 1')
    result = cursor.fetchone()
    
    if result:
        print(f"✅ get_current_arc() возвращает: {result}")
        conn.close()
        return result
    else:
        # Даже если в БД нет - возвращаем заглушку
        conn.close()
        print(f"⚠️ get_current_arc(): дуга 1 не найдена в БД, возвращаем заглушку")
        return (1, 'Дуга 1')

def reload_full_from_excel():
    """ПОЛНАЯ перезагрузка всех данных из Excel (удаление старых + создание новых)"""
    print("🔄 ПОЛНАЯ ПЕРЕЗАГРУЗКА ИЗ EXCEL...")
    
    try:
        excel_file = 'courses_data.xlsx'
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        # ★★★ СОХРАНИМ ПОЛЬЗОВАТЕЛЕЙ И ИХ ПРОГРЕСС ★★★
        print("📊 Сохраняем пользователей и их прогресс...")
        
        # 1. Сохраняем пользователей (временная таблица)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users_backup AS 
            SELECT * FROM users
        ''')
        
        # 2. Сохраняем доступы к дугам
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_arc_access_backup AS 
            SELECT * FROM user_arc_access
        ''')
        
        # 3. Сохраняем прогресс заданий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_progress_advanced_backup AS 
            SELECT * FROM user_progress_advanced
        ''')
        
        conn.commit()

        print("🗂️ Создаем таблицы уведомлений...")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type INTEGER NOT NULL,
                day_num INTEGER NOT NULL,
                text TEXT NOT NULL,
                image_url TEXT,
                is_active BOOLEAN DEFAULT 1,
                UNIQUE(type, day_num)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mass_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type INTEGER NOT NULL,
                title TEXT,
                text TEXT NOT NULL,
                days_before INTEGER,
                is_active BOOLEAN DEFAULT 1
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                notification_id INTEGER NOT NULL,
                day_num INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        
        # ★★★ УДАЛЯЕМ СТАРЫЕ ДАННЫЕ КУРСОВ ★★★
        print("🗑️ Удаляем старые данные курсов...")
        tables_to_clear = ['courses', 'arcs', 'days', 'assignments']
        
        for table in tables_to_clear:
            try:
                cursor.execute(f'DELETE FROM {table}')
                print(f"   ✅ Очищена таблица: {table}")
            except Exception as e:
                print(f"   ⚠️ Не удалось очистить {table}: {e}")
        
        # ★★★ ЗАГРУЖАЕМ НОВЫЕ ДАННЫЕ ★★★
        print("📥 Загружаем новые данные из Excel...")
        
        # 1. Курсы
        df_courses = pd.read_excel(excel_file, sheet_name='Курсы')
        for _, row in df_courses.iterrows():
            cursor.execute('''
                INSERT INTO courses (course_id, title, description)
                VALUES (?, ?, ?)
            ''', (row['id'], row['название'], row['описание']))
        print(f"✅ Загружено курсов: {len(df_courses)}")
        
        # 2. Дуги
        df_arcs = pd.read_excel(excel_file, sheet_name='Дуги')
        for _, row in df_arcs.iterrows():
            cursor.execute('''
                INSERT INTO arcs 
                (arc_id, course_id, title, order_num, price, 
                 дата_начала, дата_окончания, бесплатный_период, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['id'], row['id_курса'], row['название'],
                row['порядок'], row['цена'],
                row['дата_начала'], row['дата_окончания'],
                row['бесплатный_период'], row['статус']
            ))
        print(f"✅ Загружено дуг: {len(df_arcs)}")
        
        # 3. Дни
        df_days = pd.read_excel(excel_file, sheet_name='Дни')
        days_count = 0
        for _, row in df_days.iterrows():
            try:
                cursor.execute('''
                    INSERT INTO days (day_id, arc_id, title, order_num)
                    VALUES (?, ?, ?, ?)
                ''', (row['id'], row['id_дуги'], row['название'], row['порядок']))
                days_count += 1
            except Exception as e:
                print(f"⚠️ Ошибка дня {row['id']}: {e}")
        print(f"✅ Загружено дней: {days_count}")
        
        # 4. Задания
        df_assignments = pd.read_excel(excel_file, sheet_name='Задания')

        # ★★★ ДОБАВЛЯЕМ НЕДОСТАЮЩИЕ КОЛОНКИ ЕСЛИ НЕТ ★★★
        for col_name, col_type in [('доступно_до', 'TEXT'), ('тип', 'TEXT')]:
            try:
                cursor.execute(f'ALTER TABLE assignments ADD COLUMN {col_name} {col_type}')
                print(f"✅ Добавлена колонка: {col_name}")
            except sqlite3.OperationalError:
                pass  # Уже существует
    
        assignments_count = 0

        print(f"🔍 Загружаем {len(df_assignments)} заданий")

        for _, row in df_assignments.iterrows():
            try:
                available_until = row.get('доступно_до', '12:00')
                if isinstance(available_until, time):
                    available_until = available_until.strftime('%H:%M')
                elif isinstance(available_until, str) and available_until.count(':') == 2:
                    available_until = available_until.rsplit(':', 1)[0]
    
                cursor.execute('''
                    INSERT INTO assignments 
                    (assignment_id, day_id, title, content_text, доступно_до, тип)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    row['id'], 
                    row['id_дня'], 
                    row['название'],
                    row.get('текст_задания', ''),
                    available_until,   # Значение по умолчанию
                    row.get('тип', 'text')  # Значение по умолчанию
                ))
                assignments_count += 1
        
            except Exception as e:
                print(f"⚠️ Ошибка задания {row['id']}: {e}")
                print(f"   Данные: id={row['id']}, день={row['id_дня']}, название='{row['название']}'")

        print(f"✅ Загружено заданий: {assignments_count}")

        # ★★★ ЗАГРУЖАЕМ УВЕДОМЛЕНИЯ ★★★
        print("📨 Загружаем уведомления...")
        
        # Уведомления для дней
        df_notifications = pd.read_excel(excel_file, sheet_name='Уведомления')
        cursor.execute('DELETE FROM notifications')
        for _, row in df_notifications.iterrows():
            cursor.execute('''
                INSERT INTO notifications (type, day_num, text, image_url, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                int(row['type']),
                int(row['day_num']),
                str(row['text']),
                str(row['image_url']) if pd.notna(row.get('image_url')) else None,
                int(row['is_active']) if pd.notna(row.get('is_active')) else 1
            ))
        print(f"✅ Загружено уведомлений: {len(df_notifications)}")
        
        # Массовые уведомления
        df_mass = pd.read_excel(excel_file, sheet_name='Массовые уведомления')
        cursor.execute('DELETE FROM mass_notifications')
        for _, row in df_mass.iterrows():
            cursor.execute('''
                INSERT INTO mass_notifications (type, title, text, days_before, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                int(row['type']),
                str(row['title']) if pd.notna(row.get('title')) else None,
                str(row['text']),
                int(row['days_before']) if pd.notna(row.get('days_before')) else None,
                int(row['is_active']) if pd.notna(row.get('is_active')) else 1
            ))
        print(f"✅ Загружено массовых уведомлений: {len(df_mass)}") 
        
        # ★★★ ВОССТАНАВЛИВАЕМ ПОЛЬЗОВАТЕЛЕЙ ★★★
        print("👥 Восстанавливаем пользователей...")
        
        # Восстанавливаем пользователей (только если их нет)
        cursor.execute('''
            INSERT OR IGNORE INTO users 
            SELECT * FROM users_backup
        ''')
        
        # Восстанавливаем доступы к дугам
        cursor.execute('''
            INSERT OR IGNORE INTO user_arc_access 
            SELECT * FROM user_arc_access_backup
        ''')
        
        # Восстанавливаем прогресс (только для существующих заданий)
        cursor.execute('''
            INSERT OR IGNORE INTO user_progress_advanced 
            SELECT upb.* 
            FROM user_progress_advanced_backup upb
            JOIN assignments a ON upb.assignment_id = a.assignment_id
        ''')
        
        # ★★★ ОЧИСТКА ВРЕМЕННЫХ ТАБЛИЦ ★★★
        cursor.execute('DROP TABLE IF EXISTS users_backup')
        cursor.execute('DROP TABLE IF EXISTS user_arc_access_backup')
        cursor.execute('DROP TABLE IF EXISTS user_progress_advanced_backup')
        
        conn.commit()
        conn.close()
        
        print("🎉 ПОЛНАЯ ПЕРЕЗАГРУЗКА ЗАВЕРШЕНА!")
        print(f"📊 Итог: {len(df_courses)} курсов, {len(df_arcs)} дуг, {days_count} дней, {assignments_count} заданий")
        
        return True
        
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПЕРЕЗАГРУЗКИ: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_user_skip_statistics(user_id, arc_id):
    """Статистика по ЗАДАНИЯМ с определением пропущенных"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # 1. Дата начала дуги
    cursor.execute('SELECT дата_начала FROM arcs WHERE arc_id = ?', (arc_id,))
    arc_start_result = cursor.fetchone()
    
    if not arc_start_result or not arc_start_result[0]:
        conn.close()
        return {'total_assignments': 0, 'completed_assignments': 0, 
                'submitted_assignments': 0, 'completion_rate': 0,
                'skipped_assignments': 0, 'skipped_list': []}
    
    arc_start_date = arc_start_result[0]
    if isinstance(arc_start_date, str):
        arc_start_date = datetime.fromisoformat(arc_start_date).date()
    
    # 2. Находим дату первого ответа или доступа
    cursor.execute('''
        SELECT MIN(DATE(upa.submitted_at))
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? AND d.arc_id = ? 
        AND upa.submitted_at IS NOT NULL
    ''', (user_id, arc_id))
    
    first_answer_result = cursor.fetchone()
    
    if not first_answer_result or not first_answer_result[0]:
        cursor.execute('''
            SELECT MIN(purchased_at) 
            FROM user_arc_access 
            WHERE user_id = ? AND arc_id = ?
        ''', (user_id, arc_id))
        first_access_result = cursor.fetchone()
        
        if not first_access_result or not first_access_result[0]:
            user_start_date = arc_start_date
        else:
            user_start_date = datetime.fromisoformat(first_access_result[0]).date()
    else:
        user_start_date = first_answer_result[0]
        if isinstance(user_start_date, str):
            user_start_date = datetime.fromisoformat(user_start_date).date()
    
    # 3. Сколько ВСЕГО заданий в дуге
    cursor.execute('''
        SELECT COUNT(*) 
        FROM assignments a
        JOIN days d ON a.day_id = d.day_id
        WHERE d.arc_id = ?
    ''', (arc_id,))
    total_assignments = cursor.fetchone()[0]
    
    # 4. Выполненные задания (approved)
    cursor.execute('''
        SELECT a.assignment_id, a.title, d.title as day_title
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? AND d.arc_id = ? 
        AND upa.status = 'approved'
    ''', (user_id, arc_id))
    completed_assignments_data = cursor.fetchall()
    completed_assignments = len(completed_assignments_data)
    completed_ids = {row[0] for row in completed_assignments_data}
    
    # 5. Задания на проверке (submitted)
    cursor.execute('''
        SELECT COUNT(*) 
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? AND d.arc_id = ? 
        AND upa.status = 'submitted'
    ''', (user_id, arc_id))
    submitted_assignments = cursor.fetchone()[0]
    
    # 6. ВСЕ задания дуги с днями
    cursor.execute('''
        SELECT a.assignment_id, a.title, d.title as day_title, d.order_num
        FROM assignments a
        JOIN days d ON a.day_id = d.day_id
        WHERE d.arc_id = ?
        ORDER BY d.order_num, a.assignment_id
    ''', (arc_id,))
    all_assignments = cursor.fetchall()
    
    # 7. Определяем пропущенные задания
    skipped_list = []
    today = datetime.now().date()
    
    for assignment_id, assignment_title, day_title, day_order in all_assignments:
        # Вычисляем дату, когда задание должно было быть выполнено
        # Задание доступно до дня user_start_date + (day_order - 1)
        assignment_due_date = user_start_date + timedelta(days=(day_order - 1))
        
        # Пропущенным считаем если:
        # 1. Дедлайн прошел (сегодня > due_date)
        # 2. Задание НЕ выполнено (нет в completed_ids)
        # 3. И не на проверке (submitted)
        
        if today > assignment_due_date and assignment_id not in completed_ids:
            # Проверяем не на проверке ли
            cursor.execute('''
                SELECT 1 FROM user_progress_advanced 
                WHERE assignment_id = ? AND user_id = ? AND status = 'submitted'
            ''', (assignment_id, user_id))
            is_submitted = cursor.fetchone()
            
            if not is_submitted:
                skipped_list.append({
                    'day': day_title,
                    'assignment': assignment_title,
                    'day_number': day_order,
                    'due_date': assignment_due_date
                })
    
    skipped_assignments = len(skipped_list)
    
    # 8. Процент выполнения
    completion_rate = 0
    if total_assignments > 0:
        completion_rate = round((completed_assignments / total_assignments) * 100)
    
    # 9. СЕРИЯ БЕЗ ПРОПУСКОВ (новый расчет)
    cursor.execute('''
        SELECT d.order_num, upa.status
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? AND d.arc_id = ? 
        AND upa.status IN ('approved', 'submitted')
        ORDER BY d.order_num
    ''', (user_id, arc_id))
    
    completed_days_data = cursor.fetchall()
    
    # Считаем максимальную серию подряд выполненных дней
    max_streak = 0
    current_streak = 0
    last_day = -1
    
    for day_order, status in completed_days_data:
        if day_order == last_day + 1:
            current_streak += 1
        else:
            current_streak = 1
        
        max_streak = max(max_streak, current_streak)
        last_day = day_order
    
    # 10. Сколько дней пользователь участвует
    cursor.execute('SELECT purchased_at FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                  (user_id, arc_id))
    access_result = cursor.fetchone()
    
    participation_days = 0
    if access_result and access_result[0]:
        purchase_date = datetime.fromisoformat(access_result[0]).date()
        participation_days = (datetime.now().date() - purchase_date).days + 1
        if participation_days < 0:
            participation_days = 0
    
    # 11. Текущий день дуги для пользователя
    current_day_info = get_current_arc_day(user_id, arc_id)
    current_day = current_day_info['day_number'] if current_day_info else 0
    
    conn.close()
    
    return {
        'total_assignments': total_assignments,
        'completed_assignments': completed_assignments,
        'submitted_assignments': submitted_assignments,
        'skipped_assignments': skipped_assignments,
        'completion_rate': completion_rate,
        'remaining_assignments': total_assignments - completed_assignments - submitted_assignments - skipped_assignments,
        'skipped_list': skipped_list[:10],
        'start_date': user_start_date,
        'streak_days': max_streak,  # ← СЕРИЯ БЕЗ ПРОПУСКОВ
        'participation_days': participation_days,  # ← Участвуете дней
        'current_day': current_day  # ← Текущий день
    }

def check_and_notify_skipped_days(user_id, arc_id):
    """Проверяет пропуски и возвращает сообщение для пользователя"""
    stats = get_user_skip_statistics(user_id, arc_id)
    
    if stats['skipped_days'] == 0:
        return None
    
    messages = []
    
    if stats['skipped_days'] == 1:
        messages.append(f"⚠️ У вас 1 пропущенный день.")
    elif stats['skipped_days'] <= 3:
        messages.append(f"⚠️ У вас {stats['skipped_days']} пропущенных дня.")
    else:
        messages.append(f"🚨 У вас {stats['skipped_days']} пропущенных дней!")
    
    messages.append(f"✅ Выполнено дней: {stats['completed_days']}/{stats['total_days']}")
    messages.append(f"📊 Процент выполнения: {stats['completion_rate']}%")
    
    # Для первой дуги - только информирование
    if arc_id == 1 and stats['skipped_days'] >= 3:
        messages.append("\n💡 *На первой дуге блокировки нет, но старайтесь не пропускать!*")
    
    return "\n".join(messages)


def get_user_offer_status(user_id):
    """Возвращает статус принятия оферты пользователем - ФИКС БАГА С 'None'"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT accepted_offer, phone, fio 
        FROM users 
        WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        accepted_offer, phone, fio = result
        
        # ДЕБАГ
        print(f"🔍 get_user_offer_status RAW: accepted={accepted_offer}, "
              f"phone={repr(phone)} (тип: {type(phone)}), "
              f"fio={repr(fio)} (тип: {type(fio)})")
        
        # БАГ: phone может быть строкой 'None' вместо None
        # Исправляем:
        if phone is not None:
            phone_str = str(phone).strip()
            if phone_str.lower() in ['none', 'null', '']:
                phone_str = ""
                phone = None
            else:
                phone = phone_str
        else:
            phone_str = ""
        
        # Аналогично для ФИО
        if fio is not None:
            fio_str = str(fio).strip()
            if fio_str.lower() in ['none', 'null', '']:
                fio_str = ""
                fio = None
            else:
                fio = fio_str
        else:
            fio_str = ""
        
        # Проверки
        has_phone = bool(phone and len(str(phone)) >= 10)
        has_fio = bool(fio and len(str(fio)) >= 3 and len(str(fio).split()) >= 1)  # Минимум 1 слово
        
        print(f"🔍 Проверка: has_phone={has_phone} (phone='{phone}'), "
              f"has_fio={has_fio} (fio='{fio}')")
        
        return {
            'accepted_offer': bool(accepted_offer) if accepted_offer is not None else False,
            'phone': phone if has_phone else None,
            'has_fio': has_fio,
            'has_phone': has_phone,
            'fio_raw': fio_str
        }
    
    return {'accepted_offer': False, 'phone': None, 'has_fio': False, 'has_phone': False, 'fio_raw': ''}

def accept_offer(user_id, phone=None, fio=None):
    """Сохраняет принятие оферты пользователем - ИСПРАВЛЕННАЯ (не перезаписывает)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    print(f"⚡ accept_offer: user={user_id}, phone={phone}, fio={fio}")
    
    # 1. Сначала получаем текущие значения
    cursor.execute('SELECT phone, fio FROM users WHERE user_id = ?', (user_id,))
    current = cursor.fetchone()
    current_phone, current_fio = current if current else (None, None)
    
    print(f"🔍 Текущие в БД: phone={current_phone}, fio={current_fio}")
    
    # 2. Готовим обновление
    updates = ["accepted_offer = 1", "accepted_offer_date = CURRENT_TIMESTAMP"]
    params = []
    
    # 3. Телефон: обновляем только если передан и не None
    if phone is not None:
        phone_str = str(phone).strip()
        if phone_str and phone_str.lower() not in ['none', 'null', '']:
            updates.append("phone = ?")
            params.append(phone_str)
            print(f"📱 Обновляем телефон: {phone_str}")
        else:
            print(f"⚠️ phone пустое, оставляем текущий: {current_phone}")
    else:
        print(f"📱 phone=None, оставляем текущий: {current_phone}")
    
    # 4. ФИО: обновляем только если передан и не None
    if fio is not None:
        fio_str = str(fio).strip()
        if fio_str and fio_str.lower() not in ['none', 'null', '']:
            updates.append("fio = ?")
            params.append(fio_str)
            print(f"👤 Обновляем ФИО: {fio_str}")
        else:
            print(f"⚠️ fio пустое, оставляем текущий: {current_fio}")
    else:
        print(f"👤 fio=None, оставляем текущий: {current_fio}")
    
    # 5. Добавляем user_id в параметры
    params.append(user_id)
    
    # 6. Выполняем обновление
    sql = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
    print(f"🔧 SQL: {sql}")
    print(f"🔧 Params: {params}")
    
    cursor.execute(sql, params)
    conn.commit()
    
    # 7. Проверяем результат
    cursor.execute('SELECT accepted_offer, phone, fio FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if result:
        accepted, saved_phone, saved_fio = result
        print(f"✅ Результат в БД: accepted={accepted}, phone={saved_phone}, fio={saved_fio}")

    cursor.execute('SELECT accepted_offer, phone, fio FROM users WHERE user_id = ?', (user_id,))
    after_update = cursor.fetchone()
    print(f"🔍 После UPDATE в БД: accepted={after_update[0]}, phone={repr(after_update[1])}, fio={repr(after_update[2])}")
    
    conn.close()
    return True

def get_offer_text():
    """Читает текст оферты из файла"""
    try:
        with open('offer.txt', 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        return "Текст оферты не найден. Свяжитесь с администратором."

def get_service_offer_text():
    """Читает текст оферты на услуги из файла"""
    try:
        with open('offer_service.txt', 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        return "Текст оферты на услуги не найден. Свяжитесь с администратором."

def get_user_service_offer_status(user_id):
    """Возвращает статус принятия оферты на услуги"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT accepted_service_offer 
        FROM users 
        WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return bool(result[0]) if result and result[0] is not None else False

def accept_service_offer(user_id):
    """Сохраняет принятие оферты на услуги"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE users 
        SET accepted_service_offer = 1, 
            accepted_service_offer_date = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (user_id,))
    
    conn.commit()
    conn.close()
    print(f"✅ Оферта услуг принята пользователем {user_id}")

def load_notifications_from_excel():
    """Загружает уведомления из Excel в БД"""
    try:
        excel_path = 'courses_data.xlsx'
        
        # Уведомления для дней
        df_notifications = pd.read_excel(excel_path, sheet_name='Уведомления')
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        # Очищаем таблицу
        cursor.execute('DELETE FROM notifications')
        
        # Загружаем данные
        for _, row in df_notifications.iterrows():
            cursor.execute('''
                INSERT INTO notifications (type, day_num, text, image_url, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                int(row['type']),
                int(row['day_num']),
                str(row['text']),
                str(row['image_url']) if pd.notna(row.get('image_url')) else None,
                int(row['is_active']) if pd.notna(row.get('is_active')) else 1
            ))
        
        # Массовые уведомления
        df_mass = pd.read_excel(excel_path, sheet_name='Массовые уведомления')
        
        cursor.execute('DELETE FROM mass_notifications')
        
        for _, row in df_mass.iterrows():
            cursor.execute('''
                INSERT INTO mass_notifications (type, title, text, days_before, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                int(row['type']),
                str(row['title']) if pd.notna(row.get('title')) else None,
                str(row['text']),
                int(row['days_before']) if pd.notna(row.get('days_before')) else None,
                int(row['is_active']) if pd.notna(row.get('is_active')) else 1
            ))
        
        conn.commit()
        conn.close()
        
        print(f"✅ Загружено {len(df_notifications)} уведомлений и {len(df_mass)} массовых уведомлений")
        return True
        
    except Exception as e:
        print(f"🚨 Ошибка загрузки уведомлений: {e}")
        return False

def get_notification(notification_type, day_num=None):
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        if day_num is not None:
            cursor.execute('''
                SELECT id, text, image_url 
                FROM notifications 
                WHERE type = ? AND (day_num = ? OR day_num = 0) AND is_active = 1
                ORDER BY day_num DESC
                LIMIT 1
            ''', (notification_type, day_num))
        else:
            cursor.execute('''
                SELECT id, text, image_url 
                FROM notifications 
                WHERE type = ? AND is_active = 1
                LIMIT 1
            ''', (notification_type,))
        
        result = cursor.fetchone()
        
        if result:
            # Очищаем текст
            text = result[1]
            if text:
                # Убираем проблемные символы, сохраняя смайлики
                try:
                    text = text.encode('utf-8', 'ignore').decode('utf-8')
                except:
                    text = str(text)
            
            return {
                'id': result[0],
                'text': text,
                'image_url': result[2]
            }
        return None
        
    except Exception as e:
        print(f"🚨 Ошибка получения уведомления: {e}")
        return None
    finally:
        conn.close()

def get_mass_notification(notification_type, days_before=None):
    """Получает массовое уведомление"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    if days_before is not None:
        cursor.execute('''
            SELECT id, title, text 
            FROM mass_notifications 
            WHERE type = ? AND days_before = ? AND is_active = 1
        ''', (notification_type, days_before))
    else:
        cursor.execute('''
            SELECT id, title, text 
            FROM mass_notifications 
            WHERE type = ? AND is_active = 1
        ''', (notification_type,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            'id': result[0],
            'title': result[1],
            'text': result[2]
        }
    return None

def check_notification_sent(user_id, notification_id, day_num=None):
    """Проверяет, отправлялось ли уже это уведомление пользователю"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    if day_num is not None:
        cursor.execute('''
            SELECT 1 FROM sent_notifications 
            WHERE user_id = ? AND notification_id = ? AND day_num = ?
        ''', (user_id, notification_id, day_num))
    else:
        cursor.execute('''
            SELECT 1 FROM sent_notifications 
            WHERE user_id = ? AND notification_id = ?
        ''', (user_id, notification_id))
    
    result = cursor.fetchone()
    conn.close()
    
    return result is not None

def mark_notification_sent(user_id, notification_id, day_num=None):
    """Отмечает уведомление как отправленное"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO sent_notifications (user_id, notification_id, day_num)
        VALUES (?, ?, ?)
    ''', (user_id, notification_id, day_num))
    
    conn.commit()
    conn.close()

def save_payment(user_id, arc_id, amount, yookassa_id, status='pending'):
    """Сохраняет платеж в БД - СТАРАЯ РАБОЧАЯ ВЕРСИЯ"""
    import logging
    logger = logging.getLogger(__name__)
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Проверяем существует ли таблица с правильной структурой
        cursor.execute("PRAGMA table_info(payments)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        # Если таблица имеет старую структуру - создаем новую
        if 'arc_id' not in column_names:
            logger.warning("Таблица payments имеет старую структуру, пересоздаем...")
            cursor.execute("DROP TABLE IF EXISTS payments")
            cursor.execute('''
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    arc_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    yookassa_payment_id TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    metadata TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (arc_id) REFERENCES arcs(arc_id)
                )
            ''')
            conn.commit()
            logger.info("Таблица payments пересоздана")
        
        # Сохраняем платеж
        cursor.execute('''
            INSERT INTO payments (user_id, arc_id, amount, status, yookassa_payment_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, arc_id, amount, status, yookassa_id))
        
        conn.commit()
        payment_id = cursor.lastrowid
        
        logger.info(f"✅ Платеж сохранен: ID {payment_id}, user={user_id}, arc={arc_id}, amount={amount}₽, yookassa={yookassa_id}")
        return payment_id
        
    except Exception as e:
        logger.error(f"🚨 Ошибка сохранения платежа: {e}", exc_info=True)
        return None
    finally:
        conn.close()

def update_payment_status(yookassa_id, status):
    """Обновляет статус платежа - БЕЗ автоматической выдачи доступа"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        conn = sqlite3.connect('mentor_bot.db', timeout=10)
        cursor = conn.cursor()
        
        completed_at = datetime.now().isoformat() if status == 'succeeded' else None
        
        # ТОЛЬКО обновляем статус платежа
        cursor.execute('''
            UPDATE payments 
            SET status = ?, completed_at = ?
            WHERE yookassa_payment_id = ?
        ''', (status, completed_at, yookassa_id))
        
        conn.commit()
        logger.info(f"Статус платежа {yookassa_id} обновлен на '{status}'")
        
        # НЕ выдаем доступ здесь! Это сделает check_payment_callback
        # через отдельную транзакцию
        
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
    finally:
        if conn:
            conn.close()

def check_if_can_buy_arc(user_id, arc_id):
    """Проверяет можно ли купить дугу (до 10 дня)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Получаем дату начала дуги
        cursor.execute('SELECT дата_начала FROM arcs WHERE arc_id = ?', (arc_id,))
        result = cursor.fetchone()
        
        if not result:
            return False, "Дуга не найдена"
        
        arc_start_date = datetime.fromisoformat(result[0]).date()
        today = datetime.now().date()
        
        # Вычисляем день дуги
        day_of_arc = (today - arc_start_date).days + 1
        
        if day_of_arc <= 10:
            # Проверяем не куплен ли уже доступ
            cursor.execute('SELECT 1 FROM user_arc_access WHERE user_id = ? AND arc_id = ?', (user_id, arc_id))
            already_has = cursor.fetchone()
            
            if already_has:
                return False, "У вас уже есть доступ к этой дуге"
            return True, f"Можно купить (день {day_of_arc} из 10)"
        else:
            return False, "Срок покупки истек (можно купить только до 10 дня дуги)"
            
    except Exception as e:
        return False, f"Ошибка проверки: {str(e)}"
    finally:
        conn.close()

def grant_trial_access(user_id, arc_id):
    """УПРОЩЕННАЯ: выдает пробный доступ - одна транзакция, минимум операций"""
    import logging
    import time
    logger = logging.getLogger(__name__)
    
    logger.info(f"⚡ Упрощенная выдача доступа: user={user_id}, arc={arc_id}")
    
    # Попытки с паузами
    for attempt in range(5):
        try:
            # Подключаемся с таймаутом и отключаем журналирование для скорости
            conn = sqlite3.connect('mentor_bot.db', timeout=30, isolation_level=None)
            cursor = conn.cursor()
            
            # ВКЛЮЧАЕМ WAL режим для лучшей параллельности
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')
            
            # ВСЁ в одной транзакции
            cursor.execute('BEGIN IMMEDIATE')
            
            # 1. Просто добавляем доступ (без проверок)
            cursor.execute('''
                INSERT OR REPLACE INTO user_arc_access (user_id, arc_id, access_type)
                VALUES (?, ?, 'trial')
            ''', (user_id, arc_id))
            
            # 2. Таблица trial_assignments_access - только если очень нужно
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO trial_assignments_access 
                    (user_id, arc_id, max_assignment_order)
                    VALUES (?, ?, 3)
                ''', (user_id, arc_id))
            except:
                pass  # Не критично
            
            # КОММИТ и сразу закрываем
            cursor.execute('COMMIT')
            conn.close()
            
            logger.info(f"✅ Доступ ВЫДАН успешно (попытка {attempt + 1})")
            return True
            
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                wait_time = (attempt + 1) * 0.3  # 0.3, 0.6, 0.9, 1.2, 1.5 секунд
                logger.warning(f"БД занята, ждем {wait_time}с (попытка {attempt + 1}/5)")
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"Ошибка SQL: {e}")
                break
        except Exception as e:
            logger.error(f"Общая ошибка: {e}")
            break
    
    # Если не удалось - пробуем САМЫЙ ПРОСТОЙ вариант
    logger.warning("Пробуем самый простой вариант...")
    try:
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO user_arc_access (user_id, arc_id, access_type)
            VALUES (?, ?, 'trial')
        ''', (user_id, arc_id))
        conn.commit()
        conn.close()
        logger.info("✅ Самый простой вариант сработал")
        return True
    except Exception as e:
        logger.error(f"❌ Даже простой вариант не сработал: {e}")
        return False

def create_yookassa_payment(user_id, arc_id, amount, trial=False, description=""):
    """Создает платеж в Юкассе - С ВСЕМИ МЕТОДАМИ ОПЛАТЫ"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"Создание платежа: user={user_id}, arc={arc_id}, amount={amount}")
    
    import requests
    import base64
    import uuid
    
    auth_string = f'{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}'
    encoded_auth = base64.b64encode(auth_string.encode()).decode()
    
    idempotence_key = str(uuid.uuid4())
    
    headers = {
        "Authorization": f"Basic {encoded_auth}",
        "Content-Type": "application/json",
        "Idempotence-Key": idempotence_key
    }
    
    # Получаем данные
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
    arc_title_result = cursor.fetchone()
    arc_title = arc_title_result[0] if arc_title_result else f"Часть {arc_id}"
    
    # Данные пользователя для чека
    cursor.execute('SELECT phone, fio FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    user_phone = user_data[0] if user_data and user_data[0] else None
    user_fio = user_data[1] if user_data and user_data[1] else f"Пользователь {user_id}"
    
    conn.close()
    
    if not description:
        if trial:
            description = f"Пробный доступ к части '{arc_title}' (3 задания)"
        else:
            description = f"Полный доступ к части '{arc_title}'"
    
    # ✅ ВСЕ МЕТОДЫ ОПЛАТЫ
    payment_data = {
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB"
        },
        "payment_method_data": {
            "type": "bank_card"  # Базовый метод, но Юкасса покажет все доступные
        },
        "confirmation": {
            "type": "redirect",
            "return_url": YOOKASSA_RETURN_URL
        },
        "description": description,
        "capture": True,
        "metadata": {
            "user_id": user_id,
            "arc_id": arc_id,
            "trial": trial,
            "arc_title": arc_title
        },
        "receipt": {
            "customer": {
                "full_name": user_fio[:256]
            },
            "items": [
                {
                    "description": f"Доступ к части тренинга: {arc_title}"[:128],
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": "1",
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                    "country_of_origin_code": "643"
                }
            ]
        }
    }
    
    # Добавляем телефон если есть
    if user_phone:
        payment_data["receipt"]["customer"]["phone"] = user_phone
    
    # ✅ ВАЖНО: Убираем payment_method_data чтобы Юкасса показывала ВСЕ методы
    # или указываем несколько методов явно
    payment_data.pop("payment_method_data", None)
    
    # ✅ Альтернатива: указываем несколько методов явно
    # payment_data["payment_method_types"] = ["bank_card", "sbp", "yoo_money", "sberbank", "tinkoff_bank"]
    
    logger.info(f"Создание платежа со всеми методами оплаты")
    
    try:
        response = requests.post(
            YOOKASSA_API_URL, 
            json=payment_data, 
            headers=headers, 
            timeout=30
        )
        
        if response.status_code == 200:
            payment_info = response.json()
            payment_id = payment_info["id"]
            confirmation_url = payment_info["confirmation"]["confirmation_url"]
            
            logger.info(f"✅ Платеж создан: {payment_id}")
            
            # Сохраняем в БД
            save_payment(user_id, arc_id, amount, payment_id, 'pending')
            
            return confirmation_url, payment_id
        else:
            error_msg = f"Ошибка {response.status_code}: {response.text}"
            logger.error(error_msg)
            return None, error_msg
            
    except Exception as e:
        error_msg = f"Исключение: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg

def create_yookassa_payment_simple(user_id, arc_id, amount, trial=False, description=""):
    """Резервная функция БЕЗ чека (для тестов или если основная не работает)"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.warning("⚠️ Используем УПРОЩЕННУЮ версию платежа (без чека)")
    
    import requests
    import base64
    import uuid
    
    auth_string = f'{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}'
    encoded_auth = base64.b64encode(auth_string.encode()).decode()
    
    idempotence_key = str(uuid.uuid4())
    
    headers = {
        "Authorization": f"Basic {encoded_auth}",
        "Content-Type": "application/json",
        "Idempotence-Key": idempotence_key
    }
    
    # Получаем название части
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
    arc_title = cursor.fetchone()[0]
    conn.close()
    
    if not description:
        if trial:
            description = f"Пробный доступ к части '{arc_title}' (3 задания)"
        else:
            description = f"Полный доступ к части '{arc_title}'"
    
    # ✅ УПРОЩЕННЫЕ ДАННЫЕ БЕЗ receipt
    payment_data = {
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB"
        },
        "payment_method_data": {
            "type": "bank_card"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": YOOKASSA_RETURN_URL
        },
        "description": description,
        "capture": True,
        "metadata": {
            "user_id": user_id,
            "arc_id": arc_id,
            "trial": trial,
            "arc_title": arc_title
        }
    }
    
    try:
        response = requests.post(YOOKASSA_API_URL, json=payment_data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            payment_info = response.json()
            payment_id = payment_info["id"]
            confirmation_url = payment_info["confirmation"]["confirmation_url"]
            
            logger.info(f"✅ Упрощенный платеж создан: {payment_id}")
            
            # Сохраняем в БД
            save_payment(user_id, arc_id, amount, payment_id, 'pending')
            
            return confirmation_url, payment_id
        else:
            error_msg = f"Ошибка упрощенного платежа {response.status_code}: {response.text}"
            logger.error(error_msg)
            return None, error_msg
            
    except Exception as e:
        error_msg = f"Исключение в упрощенной версии: {str(e)}"
        logger.error(error_msg)
        return None, error_msg

def handle_yookassa_webhook(data):
    """Обрабатывает webhook от Юкассы и отправляет уведомления"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        event = data.get("event")
        payment_obj = data.get("object")
        
        logger.info(f"Обработка webhook: event={event}")
        
        if event == "payment.succeeded":
            payment_id = payment_obj.get("id")
            status = payment_obj.get("status")
            amount = payment_obj.get("amount", {}).get("value")
            
            # Обновляем статус
            update_payment_status(payment_id, status)
            
            # Получаем информацию о платеже
            conn = sqlite3.connect('mentor_bot.db')
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, arc_id FROM payments WHERE yookassa_payment_id = ?', (payment_id,))
            payment_data = cursor.fetchone()
            
            if payment_data:
                user_id, arc_id = payment_data
                
                # Получаем название части
                cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
                arc_title = cursor.fetchone()[0]
                
                conn.close()
                
                # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ ПОЛЬЗОВАТЕЛЮ
                send_payment_notification(user_id, arc_title, amount, payment_id)
                
                logger.info(f"✅ Платеж {payment_id} обработан, уведомление отправлено user={user_id}")
                return True, f"Платеж {payment_id} обработан"
            else:
                logger.error(f"Платеж {payment_id} не найден в БД")
                return False, "Платеж не найден"
                
        elif event == "payment.canceled":
            payment_id = payment_obj.get("id")
            update_payment_status(payment_id, "canceled")
            return True, f"Платеж {payment_id} отменен"
            
        else:
            logger.warning(f"Неизвестное событие: {event}")
            return False, f"Неизвестное событие: {event}"
            
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}", exc_info=True)
        return False, f"Ошибка: {str(e)}"


def check_assignment_status(user_id, assignment_id):
    """Проверяет статус задания для пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT status FROM user_progress_advanced 
        WHERE user_id = ? AND assignment_id = ?
    ''', (user_id, assignment_id))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]  # 'submitted', 'approved'
    return 'new'  # Новое задание

def can_access_assignment(user_id, assignment_id, arc_id=None):
    """Проверяет может ли пользователь получить доступ к заданию"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Если не передан arc_id, находим его
        if not arc_id:
            cursor.execute('''
                SELECT d.arc_id 
                FROM assignments a
                JOIN days d ON a.day_id = d.day_id
                WHERE a.assignment_id = ?
            ''', (assignment_id,))
            result = cursor.fetchone()
            if result:
                arc_id = result[0]
        
        # Проверяем общий доступ к дуге
        cursor.execute('SELECT access_type FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                      (user_id, arc_id))
        access = cursor.fetchone()
        
        if not access:
            return False, "Нет доступа к этой части"
        
        access_type = access[0]
        
        # Если это пробный доступ, проверяем ограничение в 3 задания
        if access_type == 'trial':
            # Проверяем порядковый номер задания
            cursor.execute('''
                SELECT a.order_num 
                FROM assignments a
                JOIN days d ON a.day_id = d.day_id
                WHERE a.assignment_id = ? AND d.arc_id = ?
            ''', (assignment_id, arc_id))
            
            result = cursor.fetchone()
            
            if result:
                assignment_order = result[0]
                if assignment_order > 3:  # Только первые 3 задания
                    return False, "Пробный доступ ограничен первыми 3 заданиями. Купите полный доступ."
        
        return True, "Доступ разрешен"
        
    except Exception as e:
        return False, f"Ошибка проверки: {str(e)}"
    finally:
        conn.close()

def has_new_feedback(user_id):
    """Проверяет есть ли новые непросмотренные ответы"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*) 
        FROM user_progress_advanced upa
        WHERE upa.user_id = ? 
        AND upa.status = 'approved'
        AND upa.teacher_comment IS NOT NULL
        AND upa.viewed_by_student = 0
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] > 0 if result else False

def get_arcs_with_feedback(user_id):
    """Возвращает части с ответами и кол-вом новых"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT ar.arc_id, ar.title,
               COUNT(CASE WHEN upa.viewed_by_student = 0 THEN 1 END) as new_count,
               COUNT(*) as total_count
        FROM arcs ar
        JOIN days d ON ar.arc_id = d.arc_id
        JOIN assignments a ON d.day_id = a.day_id
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        WHERE upa.user_id = ? AND upa.status = 'approved' 
          AND upa.teacher_comment IS NOT NULL
        GROUP BY ar.arc_id
        ORDER BY ar.order_num
    ''', (user_id,))
    
    arcs = cursor.fetchall()
    conn.close()
    return arcs

def get_feedback_counts(user_id, arc_id):
    """Возвращает количество новых и завершенных ответов"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Новые (viewed_by_student = 0)
    cursor.execute('''
        SELECT COUNT(*)
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? 
          AND upa.status = 'approved'
          AND upa.teacher_comment IS NOT NULL
          AND upa.viewed_by_student = 0
          AND d.arc_id = ?
    ''', (user_id, arc_id))
    
    new_count = cursor.fetchone()[0] or 0
    
    # Завершенные (viewed_by_student = 1)
    cursor.execute('''
        SELECT COUNT(*)
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? 
          AND upa.status = 'approved'
          AND upa.teacher_comment IS NOT NULL
          AND upa.viewed_by_student = 1
          AND d.arc_id = ?
    ''', (user_id, arc_id))
    
    completed_count = cursor.fetchone()[0] or 0
    
    conn.close()
    return new_count, completed_count

def decline_offer(user_id):
    """Упрощенная версия - без declined_offer_date"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            UPDATE users 
            SET accepted_offer = 0
            WHERE user_id = ?
        ''', (user_id,))
        
        conn.commit()
        print(f"❌ Оферта отклонена пользователем {user_id}")
        
    except Exception as e:
        print(f"🚨 Ошибка при отклонении оферты: {e}")
        
        # Пробуем еще проще
        try:
            cursor.execute('''
                UPDATE users 
                SET accepted_offer = 0
                WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
            print(f"✅ Упрощенная запись выполнена")
        except Exception as e2:
            print(f"❌ Даже упрощенная запись не удалась: {e2}")
    finally:
        conn.close()

def get_users_for_notification(recipient_type='all'):
    """Упрощенный вариант - для 'full' берем всех кто есть в user_arc_access"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # ID админов
    cursor.execute('SELECT user_id FROM users WHERE is_admin = 1')
    admin_ids = [row[0] for row in cursor.fetchall()]
    admin_ids_str = ','.join(map(str, admin_ids)) if admin_ids else '0'
    
    if recipient_type == 'full':
        # ВСЕ пользователи у которых есть хоть один доступ в user_arc_access
        cursor.execute(f'''
            SELECT DISTINCT u.user_id, 
                   COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
                   u.username
            FROM users u
            WHERE u.user_id NOT IN ({admin_ids_str})
              AND u.user_id IN (SELECT DISTINCT user_id FROM user_arc_access)
        ''')
        
    elif recipient_type == 'trial':
        # Только пользователи с типом доступа 'trial'
        cursor.execute(f'''
            SELECT DISTINCT u.user_id, 
                   COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
                   u.username
            FROM users u
            WHERE u.user_id NOT IN ({admin_ids_str})
              AND u.user_id IN (
                  SELECT DISTINCT user_id 
                  FROM user_arc_access 
                  WHERE access_type = 'trial'
              )
        ''')
        
    else:
        # Все пользователи (кроме админов)
        cursor.execute(f'''
            SELECT DISTINCT u.user_id, 
                   COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
                   u.username
            FROM users u
            WHERE u.user_id NOT IN ({admin_ids_str})
        ''')
    
    users = cursor.fetchall()
    conn.close()
    
    print(f"📊 Найдено пользователей для уведомления ({recipient_type}): {len(users)}")
    return users

def save_notification_log(admin_id, recipient_type, text, photo_id=None, success_count=0, fail_count=0):
    """Сохраняет лог отправки уведомлений"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                recipient_type TEXT,
                text TEXT,
                photo_id TEXT,
                success_count INTEGER,
                fail_count INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Обрезаем текст если слишком длинный
        short_text = text[:500] + "..." if text and len(text) > 500 else text
        
        cursor.execute('''
            INSERT INTO notification_logs 
            (admin_id, recipient_type, text, photo_id, success_count, fail_count)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (admin_id, recipient_type, short_text, photo_id, success_count, fail_count))
        
        conn.commit()
        print(f"✅ Лог уведомления сохранен: {recipient_type}, успешно {success_count}")
        
    except Exception as e:
        print(f"🚨 Ошибка сохранения лога: {e}")
    finally:
        conn.close()

def is_admin(user_id):
    """Проверяет является ли пользователь админом"""
    try:
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        # Проверяем поле is_admin
        cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        # Если есть запись и is_admin = 1
        if result and result[0] == 1:
            conn.close()
            return True
        
        # Проверяем конфиг
        from config import ADMIN_ID, ADMIN_IDS
        conn.close()
        return user_id == ADMIN_ID or (hasattr(ADMIN_IDS, '__contains__') and user_id in ADMIN_IDS)
        
    except Exception as e:
        print(f"🚨 Ошибка проверки админа {user_id}: {e}")
        from config import ADMIN_ID, ADMIN_IDS
        return user_id == ADMIN_ID or (hasattr(ADMIN_IDS, '__contains__') and user_id in ADMIN_IDS)

def set_user_as_admin(user_id):
    """Устанавливает пользователя как администратора"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_admin = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    print(f"✅ Пользователь {user_id} установлен как администратор")



