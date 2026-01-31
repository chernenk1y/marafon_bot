## 📁 database.py - СТРУКТУРА И ФУНКЦИИ

### 📦 ИМПОРТЫ
import sqlite3
import datetime
import json
import uuid
import requests
import pandas as pd
from datetime import datetime, timedelta

### 🗺️ ТАЙМЗОНЫ ГОРОДОВ
CITY_TIMEZONES = {
    "Калининград (+1)": -1,
    "Москва (+0)": 0,
    ...
}

### 📋 ТАБЛИЦЫ БАЗЫ ДАННЫХ
# 1. users - пользователи бота
#    Поля: user_id, username, first_name, fio, city, 
#          timezone_offset, is_blocked, created_at

# 2. courses - курсы
#    Поля: course_id, title, description

# 3. arcs - дуги курсов
#    Поля: arc_id, course_id, title, order_num, price,
#          дата_начала, дата_окончания, бесплатный_период,
#          status, is_available

# 4. days - дни дуг  
#    Поля: day_id, arc_id, title, order_num

# 5. assignments - задания
#    Поля: assignment_id, day_id, title, content_text,
#          content_files, доступно_до, тип, order_num

# 6. user_arc_access - доступы к дугам
#    Поля: user_id, arc_id, access_type, purchased_at, expires_at

# 7. user_progress_advanced - прогресс заданий
#    Поля: user_id, assignment_id, status, answer_text,
#          answer_files, submitted_at, teacher_comment, viewed_by_student

# 8. user_daily_stats - ежедневная статистика
#    Поля: user_id, arc_id, day_id, date, assignments_completed, is_skipped

# 9. free_access_grants - бесплатные доступы
#    Поля: id, user_id, arc_id, granted_by, granted_at

### 🛠️ ФУНКЦИИ БАЗЫ ДАННЫХ

#### ГРУППА 1: ИНИЦИАЛИЗАЦИЯ И ТЕСТИРОВАНИЕ
• init_db() - создает все таблицы, добавляет недостающие колонки
• init_assignments() - создает тестовые задания
• test_new_structure() - проверяет наличие таблиц и полей
• upgrade_database() - добавляет новые поля в существующие таблицы
• load_courses_from_excel() - загружает данные из Excel файла
• reload_courses_data() - обновляет таблицу arcs из Excel
• reload_full_from_excel() - ПОЛНАЯ перезагрузка всех данных курсов

#### ГРУППА 2: ПОЛЬЗОВАТЕЛИ И ПРОФИЛЬ
• add_user(user_id, username, first_name) - добавляет/обновляет пользователя
• set_user_timezone(user_id, city, timezone_offset) - устанавливает город и таймзону
• get_available_cities() - возвращает список доступных городов
• get_user_local_time(user_id) - возвращает местное время пользователя
• block_user(user_id) - блокирует пользователя
• unblock_user(user_id) - разблокирует пользователя

#### ГРУППА 3: ДОСТУП К ДУГАМ И КУРСАМ
• check_user_arc_access(user_id, arc_id) - проверяет доступ к дуге
• grant_arc_access(user_id, arc_id, access_type='paid') - выдает доступ
• grant_free_access(user_id, arc_id, granted_by) - выдает бесплатный доступ
• get_user_courses(user_id) - возвращает курсы доступные пользователю
• get_course_arcs(course_title) - возвращает дуги курса
• add_test_access(user_id) - добавляет тестовый доступ для тестирования

#### ГРУППА 4: ДНИ И ИХ ДОСТУПНОСТЬ
• get_current_arc_day(user_id, arc_id) - текущий день дуги для пользователя
• is_day_available_for_user(user_id, day_id) - доступен ли день
• get_available_days_for_user(user_id, arc_id) - доступные дни в дуге
• get_day_id_by_title_and_arc(day_title, arc_id) - находит ID дня
• mark_day_as_skipped(user_id, day_id) - отмечает день как пропущенный
• check_and_open_missed_days(user_id) - открывает пропущенные дни

#### ГРУППА 5: ЗАДАНИЯ И ОТВЕТЫ
• get_user_assignments_for_day(user_id, day_id) - задания для дня
• get_assignment_by_title_and_day(assignment_title, day_id) - находит задание
• get_day_assignments_count(day_id) - количество заданий в дне
• save_assignment_answer(user_id, assignment_id, answer_text, answer_files)
• save_assignment_answer_with_day(user_id, assignment_id, day_id, ...)

#### ГРУППА 6: СТАТИСТИКА И ПРОПУСКИ
• get_user_skip_statistics(user_id, arc_id) - статистика пропусков
• check_and_notify_skipped_days(user_id, arc_id) - проверка и уведомление
• update_daily_stats(user_id, arc_id, day_id, completed_count)
• get_user_skip_days(user_id, arc_id) - количество пропусков
• get_users_with_skipped_days() - пользователи с пропусками

#### ГРУППА 7: АДМИН-ФУНКЦИИ И ПРОВЕРКА
• get_students_with_submissions() - ученики с работами
• get_student_submissions(user_id) - работы ученика
• get_course_status(user_id) - статусы курсов ученика
• get_assignment_status(user_id, course_title) - статусы заданий

#### ГРУППА 8: УСТАРЕВШИЕ ФУНКЦИИ (для совместимости)
• get_current_assignment(user_id) - текущее задание (старая логика)
• save_submission(user_id, assignment_id, file_id) - сохранение отправки
• check_payment(user_id, course_id=1) - проверка оплаты
• add_payment(user_id, course_id=1) - имитация оплаты
• save_assignment_file(user_id, assignment_id, file_id) - сохранение файла

### еще фуекции сокращенные

#### 1. get_user_skip_statistics(user_id, arc_id)
def get_user_skip_statistics(user_id, arc_id):
    """Статистика - используем дату первого ответа как дату начала"""

#### 2. grant_arc_access(user_id, arc_id, access_type='paid')
def grant_arc_access(user_id, arc_id, access_type='paid'):
    """Простая версия - только добавляет доступ"""

#### 3. get_user_local_time(user_id)
def get_user_local_time(user_id):
    """Возвращает время пользователя с учетом его таймзоны (относительно МСК)"""

#### 4. get_current_arc_day(user_id, arc_id)
def get_current_arc_day(user_id, arc_id):
    """Возвращает текущий день дуги для пользователя"""

#### 5. check_and_open_missed_days(user_id)
def check_and_open_missed_days(user_id):
    """Открывает текущий день если он еще не открыт"""

#### 6. save_assignment_answer(user_id, assignment_id, answer_text, answer_files)
def save_assignment_answer(user_id, assignment_id, answer_text, answer_files):
    """Сохраняет ответ на задание (текст + файлы)"""

#### 7. check_user_arc_access(user_id, arc_id)
def check_user_arc_access(user_id, arc_id):
    """Проверяет доступ пользователя к дуге"""

####НОВЫЕ:
def get_user_offer_status(user_id):
    """Возвращает статус принятия оферты пользователем"""

def accept_offer(user_id, phone=None):
    """Сохраняет принятие оферты пользователем"""

def get_offer_text():
    """Читает текст оферты из файла"""

def get_service_offer_text():
    """Читает текст оферты на услуги из файла"""

def get_user_service_offer_status(user_id):
    """Возвращает статус принятия оферты на услуги"""

def accept_service_offer(user_id):
    """Сохраняет принятие оферты на услуги"""

def load_notifications_from_excel():
    """Загружает уведомления из Excel в БД"""

def get_notification(notification_type, day_num=None):

def get_mass_notification(notification_type, days_before=None):
    """Получает массовое уведомление"""

def check_notification_sent(user_id, notification_id, day_num=None):
    """Проверяет, отправлялось ли уже это уведомление пользователю"""

def mark_notification_sent(user_id, notification_id, day_num=None):
    """Отмечает уведомление как отправленное"""
    
# ПЛАТЕЖИ И ЮКАССА
def save_payment(user_id, arc_id, amount, yookassa_id, status='pending'):
    """Сохраняет платеж в БД"""

def update_payment_status(yookassa_id, status):
    """Обновляет статус платежа"""

def check_if_can_buy_arc(user_id, arc_id):
    """Проверяет можно ли купить дугу (до 10 дня)"""

def grant_trial_access(user_id, arc_id):
    """Выдает пробный доступ на 3 дня за 100₽ (первые 3 задания)"""

def create_yookassa_payment(user_id, arc_id, amount, trial=False, description=""):
    """Создает платеж в Юкассе и возвращает ссылку для оплаты"""

def handle_yookassa_webhook(data):
    """Обрабатывает webhook от Юкассы"""

def check_assignment_status(user_id, assignment_id):
    """Проверяет статус задания для пользователя"""

def can_access_assignment(user_id, assignment_id, arc_id=None):
    """Проверяет может ли пользователь получить доступ к заданию"""

def has_new_feedback(user_id):
    """Проверяет есть ли новые непросмотренные ответы"""

def get_arcs_with_feedback(user_id):
    """Возвращает части с ответами и кол-вом новых""" 

def get_feedback_counts(user_id, arc_id):
    """Возвращает количество новых и завершенных ответов"""
def decline_offer(user_id):
    """Упрощенная версия - без declined_offer_date"""
def get_users_for_notification(recipient_type='all'):
    """Упрощенный вариант - для 'full' берем всех кто есть в user_arc_access"""
def save_notification_log(admin_id, recipient_type, text, photo_id=None, success_count=0, fail_count=0):
    """Сохраняет лог отправки уведомлений"""
def is_admin(user_id):
    """Проверяет является ли пользователь админом"""
def set_user_as_admin(user_id):
    """Устанавливает пользователя как администратора"""
def get_user_active_arcs(user_id):
    """Получает ВСЕ активные части пользователя (дата_начала <= сегодня <= дата_окончания)"""
def save_assignment_answer_with_day_auto_approve(user_id, assignment_id, day_id, answer_text, answer_files):
    """Сохраняет ответ на задание с автоматическим принятием"""

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
            content_photos TEXT,
            content_audios TEXT,
            video_url TEXT,
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

def get_user_active_arcs(user_id):
    """Получает ВСЕ активные части пользователя (дата_начала <= сегодня <= дата_окончания)"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.arc_id, a.title, a.дата_начала, a.дата_окончания, uaa.access_type
        FROM user_arc_access uaa
        JOIN arcs a ON uaa.arc_id = a.arc_id
        WHERE uaa.user_id = ? 
          AND a.дата_начала <= DATE('now') 
          AND a.дата_окончания >= DATE('now')
          AND a.status = 'active'
        ORDER BY a.дата_начала
    ''', (user_id,))
    
    arcs = cursor.fetchall()
    conn.close()
    return arcs

def get_current_arc_day(user_id, arc_id):
    """Возвращает текущий день дуги для пользователя"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # 1. Получаем дату начала дуги
    cursor.execute('SELECT дата_начала FROM arcs WHERE arc_id = ?', (arc_id,))
    result = cursor.fetchone()
    
    if not result or not result[0]:
        conn.close()
        return {
            'day_id': None,
            'day_title': f"Ошибка: дата начала не указана",
            'day_number': 0,
            'total_days': 28,
            'arc_start_date': None
        }
    
    arc_start_date_str = result[0]
    
    # Преобразуем строку в дату
    try:
        if isinstance(arc_start_date_str, str):
            # Очищаем строку
            arc_start_date_str = arc_start_date_str.strip()
            if not arc_start_date_str:
                conn.close()
                return {
                    'day_id': None,
                    'day_title': f"Ошибка: пустая дата начала",
                    'day_number': 0,
                    'total_days': 28,
                    'arc_start_date': None
                }
            
            # Парсим дату
            if ' ' in arc_start_date_str:
                # ИСПРАВЛЕНИЕ: используем datetime, а не datetime.datetime
                arc_start_date = datetime.strptime(arc_start_date_str, '%Y-%m-%d %H:%M:%S').date()
            else:
                arc_start_date = datetime.strptime(arc_start_date_str, '%Y-%m-%d').date()
        else:
            # Уже datetime/date объект
            arc_start_date = arc_start_date_str
            if hasattr(arc_start_date, 'date'):
                arc_start_date = arc_start_date.date()
    except Exception as e:
        print(f"🚨 Ошибка парсинга даты '{arc_start_date_str}': {e}")
        conn.close()
        return {
            'day_id': None,
            'day_title': f"Ошибка формата даты",
            'day_number': 0,
            'total_days': 28,
            'arc_start_date': None
        }
    
    # 2. Получаем местное время пользователя
    user_time = get_user_local_time(user_id)
    user_date = user_time.date()
    
    # 3. Вычисляем текущий день дуги
    if user_date < arc_start_date:
        current_day = 0
    else:
        current_day = (user_date - arc_start_date).days + 1
    
    # Ограничиваем максимальным количеством дней
    current_day = min(max(current_day, 0), 28)
    
    # 4. Находим день в базе
    cursor.execute('''
        SELECT day_id, title FROM days 
        WHERE arc_id = ? AND order_num = ?
    ''', (arc_id, current_day))
    
    day_info = cursor.fetchone()
    conn.close()
    
    if day_info:
        day_id, day_title = day_info
        return {
            'day_id': day_id,
            'day_title': day_title,
            'day_number': current_day,
            'total_days': 28,
            'arc_start_date': arc_start_date
        }
    
    # Если дня нет в базе
    return {
        'day_id': None,
        'day_title': f"День {current_day}",
        'day_number': current_day,
        'total_days': 28,
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
                SELECT d.arc_id, d.order_num as day_order
                FROM assignments a
                JOIN days d ON a.day_id = d.day_id
                WHERE a.assignment_id = ?
            ''', (assignment_id,))
            result = cursor.fetchone()
            if result:
                arc_id, day_order = result
            else:
                return False, "Задание не найдено"
        else:
            # Получаем номер дня для этого задания
            cursor.execute('''
                SELECT d.order_num as day_order
                FROM assignments a
                JOIN days d ON a.day_id = d.day_id
                WHERE a.assignment_id = ? AND d.arc_id = ?
            ''', (assignment_id, arc_id))
            result = cursor.fetchone()
            if result:
                day_order = result[0]
            else:
                return False, "Задание не найдено"
        
        # Проверяем общий доступ к дуге
        cursor.execute('SELECT access_type FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                      (user_id, arc_id))
        access = cursor.fetchone()
        
        if not access:
            return False, "Нет доступа к этому марафону"
        
        access_type = access[0]
        
        # ★★★ ИСПРАВЛЕННАЯ ЛОГИКА: ★★★
        # Если это пробный доступ, проверяем что задание в первых 3 ДНЯХ
        if access_type == 'trial':
            if day_order > 3:  # Проверяем номер ДНЯ (не задания!)
                return False, "Пробный доступ ограничен первыми 3 днями. Купите полный доступ."
        
        return True, "Доступ разрешен"
        
    except Exception as e:
        return False, f"Ошибка проверки: {str(e)}"
    finally:
        conn.close()

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


def save_assignment_answer_with_day(user_id, assignment_id, day_id, answer_text, answer_files):
    """Сохраняет ответ с указанием дня"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Сохраняем файлы как JSON
    files_json = json.dumps(answer_files) if answer_files else None
    
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

def save_assignment_answer_with_day_auto_approve(user_id, assignment_id, day_id, answer_text, answer_files):
    """Сохраняет ответ на задание с автоматическим принятием"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Сохраняем файлы как JSON
    files_json = json.dumps(answer_files) if answer_files else None
    
    # Автоматический комментарий психолога
    auto_comment = "✅ Задание принято автоматически."
    
    # Сохраняем с статусом 'approved' сразу
    cursor.execute('''
        INSERT OR REPLACE INTO user_progress_advanced 
        (user_id, assignment_id, answer_text, answer_files, status, teacher_comment, viewed_by_student)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    ''', (user_id, assignment_id, answer_text, files_json, 'approved', auto_comment))
    
    # Обновляем статистику дня если есть day_id
    if day_id:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO user_daily_stats 
                (user_id, arc_id, day_id, date, assignments_completed, is_skipped)
                VALUES (?, 
                       (SELECT d.arc_id FROM days d JOIN assignments a ON d.day_id = a.day_id WHERE a.assignment_id = ?),
                       ?, DATE('now'), 1, 0)
            ''', (user_id, assignment_id, day_id))
        except Exception as e:
            print(f"⚠️ Ошибка обновления статистики дня: {e}")
    
    conn.commit()
    conn.close()
    print(f"✅ Задание {assignment_id} автоматически принято для пользователя {user_id}")

def get_user_skip_statistics(user_id, arc_id):
    """Статистика - используем дату первого ответа как дату начала"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # 1. Дата начала дуги
    cursor.execute('SELECT дата_начала FROM arcs WHERE arc_id = ?', (arc_id,))
    arc_start_result = cursor.fetchone()
    
    if not arc_start_result or not arc_start_result[0]:
        conn.close()
        return {'total_days': 0, 'completed_days': 0, 'skipped_days': 0, 
                'streak_days': 0, 'user_start_date': None, 'completion_rate': 0}
    
    arc_start_date_str = arc_start_result[0]
    
    # Преобразуем в дату
    try:
        if isinstance(arc_start_date_str, str):
            arc_start_date_str = arc_start_date_str.strip()
            if not arc_start_date_str:
                conn.close()
                return {'total_days': 0, 'completed_days': 0, 'skipped_days': 0, 
                        'streak_days': 0, 'user_start_date': None, 'completion_rate': 0}
            
            if ' ' in arc_start_date_str:
                # ИСПРАВЛЕНИЕ: используем datetime, а не datetime.datetime
                arc_start_date = datetime.strptime(arc_start_date_str, '%Y-%m-%d %H:%M:%S').date()
            else:
                arc_start_date = datetime.strptime(arc_start_date_str, '%Y-%m-%d').date()
        else:
            arc_start_date = arc_start_date_str
            if hasattr(arc_start_date, 'date'):
                arc_start_date = arc_start_date.date()
    except Exception as e:
        print(f"🚨 Ошибка парсинга даты в статистике: {e}")
        conn.close()
        return {'total_days': 0, 'completed_days': 0, 'skipped_days': 0, 
                'streak_days': 0, 'user_start_date': None, 'completion_rate': 0}
    
    
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

def get_user_active_arcs(user_id):
    """Получает активные части пользователя - УПРОЩЕННАЯ ВЕРСИЯ"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Для админов - показываем ВСЕ части к которым есть доступ
    # Проверяем является ли пользователь админом
    cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    is_admin = user and user[0] == 1
    
    if is_admin:
        # Для админа - все части к которым есть доступ
        cursor.execute('''
            SELECT DISTINCT a.arc_id, a.title, a.дата_начала, a.дата_окончания, uaa.access_type
            FROM user_arc_access uaa
            JOIN arcs a ON uaa.arc_id = a.arc_id
            WHERE uaa.user_id = ?
            AND (a.дата_начала IS NOT NULL AND a.дата_начала != '')
            ORDER BY a.дата_начала
        ''', (user_id,))
    else:
        # Для обычных пользователей - только активные по датам
        cursor.execute('''
            SELECT DISTINCT a.arc_id, a.title, a.дата_начала, a.дата_окончания, uaa.access_type
            FROM user_arc_access uaa
            JOIN arcs a ON uaa.arc_id = a.arc_id
            WHERE uaa.user_id = ? 
            AND a.дата_начала IS NOT NULL 
            AND a.дата_начала != ''
            AND (
                -- Часть активна СЕЙЧАС
                (DATE(a.дата_начала) <= DATE('now') AND DATE(a.дата_окончания) >= DATE('now'))
                OR
                -- ИЛИ у пользователя есть доступ к будущей части
                (DATE(a.дата_начала) > DATE('now') AND uaa.access_type = 'paid')
            )
            ORDER BY a.дата_начала
        ''', (user_id,))
    
    arcs = cursor.fetchall()
    conn.close()
    
    print(f"🔍 get_user_active_arcs: user_id={user_id}, is_admin={is_admin}, found={len(arcs)} arcs")
    for arc in arcs:
        print(f"   - {arc[1]} ({arc[2]} to {arc[3]})")
    
    return arcs


def save_assignment_media(assignment_id, photos=None, audios=None, video_url=None):
    """Сохраняет медиа-контент для задания"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        photos_json = json.dumps(photos) if photos else None
        audios_json = json.dumps(audios) if audios else None
        
        cursor.execute('''
            UPDATE assignments 
            SET content_photos = ?, content_audios = ?, video_url = ?
            WHERE assignment_id = ?
        ''', (photos_json, audios_json, video_url, assignment_id))
        
        conn.commit()
        print(f"✅ Медиа сохранены для задания {assignment_id}")
        return True
    except Exception as e:
        print(f"🚨 Ошибка сохранения медиа: {e}")
        return False
    finally:
        conn.close()

def get_assignment_media(assignment_id):
    """Получает медиа-контент задания"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT content_photos, content_audios, video_url
        FROM assignments 
        WHERE assignment_id = ?
    ''', (assignment_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        photos_json, audios_json, video_url = result
        
        # Парсим JSON
        photos = []
        audios = []
        
        if photos_json:
            try:
                photos = json.loads(photos_json)
            except:
                photos = []
        
        if audios_json:
            try:
                audios = json.loads(audios_json)
            except:
                audios = []
        
        return {
            'photos': photos,
            'audios': audios,
            'video_url': video_url
        }
    
    return {
        'photos': [],
        'audios': [],
        'video_url': None
    }

def update_assignment_with_media_from_excel(file_path='course_data.xlsx'):
    """Обновляет задания с медиа из Excel (новые колонки)"""
    try:
        df = pd.read_excel(file_path, sheet_name='Задания')
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        updated_count = 0
        
        for index, row in df.iterrows():
            assignment_id = row.get('id')
            
            # Проверяем наличие новых колонок
            if 'фото' in df.columns:
                photos_str = row.get('фото', '')
                if pd.notna(photos_str) and photos_str:
                    try:
                        photos = json.loads(photos_str)
                        photos_json = json.dumps(photos)
                    except:
                        photos_json = json.dumps([str(photos_str)])
                else:
                    photos_json = None
            else:
                photos_json = None
            
            if 'аудио' in df.columns:
                audios_str = row.get('аудио', '')
                if pd.notna(audios_str) and audios_str:
                    try:
                        audios = json.loads(audios_str)
                        audios_json = json.dumps(audios)
                    except:
                        audios_json = json.dumps([str(audios_str)])
                else:
                    audios_json = None
            else:
                audios_json = None
            
            if 'видео_ссылка' in df.columns:
                video_url = row.get('видео_ссылка', '')
                if pd.isna(video_url):
                    video_url = None
            else:
                video_url = None
            
            # Обновляем запись
            cursor.execute('''
                UPDATE assignments 
                SET content_photos = ?, content_audios = ?, video_url = ?
                WHERE assignment_id = ?
            ''', (photos_json, audios_json, video_url, assignment_id))
            
            if cursor.rowcount > 0:
                updated_count += 1
        
        conn.commit()
        conn.close()
        
        print(f"✅ Обновлено {updated_count} заданий с медиа-контентом")
        return updated_count
        
    except Exception as e:
        print(f"🚨 Ошибка загрузки медиа из Excel: {e}")
        return 0

def get_arcs_with_dates():
    """Возвращает дуги у которых указаны даты начала и окончания"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT arc_id, title, order_num, price, 
               дата_начала, дата_окончания, бесплатный_период
        FROM arcs 
        WHERE status = 'active'
        AND дата_начала IS NOT NULL 
        AND дата_окончания IS NOT NULL
        AND дата_начала != ''
        AND дата_окончания != ''
        ORDER BY order_num
    ''')
    
    arcs = cursor.fetchall()
    conn.close()
    return arcs

def get_current_and_future_arcs():
    """Получает текущие и будущие дуги"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # УБИРАЕМ status из WHERE
    cursor.execute('''
        SELECT arc_id, title, дата_начала, дата_окончания, price
        FROM arcs 
        WHERE дата_начала IS NOT NULL 
        AND дата_окончания IS NOT NULL
        ORDER BY дата_начала
    ''')
    
    arcs = cursor.fetchall()
    conn.close()
    return arcs


### 📝 ВАЖНЫЕ ЗАМЕЧАНИЯ
1. Все функции работают с БД: sqlite3.connect('mentor_bot.db')
2. Время хранится в UTC, конвертируется по timezone_offset пользователя
3. Основная таблица прогресса: user_progress_advanced (новый формат)
4. Даты начала/окончания частей в таблице arcs (используются для автоматического открытия дней)
5. Пробный период: 3 дня (бесплатный_период в таблице arcs)


важно: если для разработки нужна функция которой нет в полных функциях, то запроси, я скопирую и отправлю в част полную функцию
