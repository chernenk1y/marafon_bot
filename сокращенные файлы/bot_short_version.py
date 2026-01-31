import sqlite3
import json
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, JobQueue, CallbackQueryHandler
from telegram.helpers import escape_markdown
from datetime import datetime, timezone, timedelta, time
from database import (
    create_yookassa_payment,
    save_payment,  
    update_payment_status,
    check_if_can_buy_arc,
    grant_trial_access,
    init_db, add_user, init_assignments, get_submissions, 
    update_submission, get_submission_file, check_payment, 
    add_payment, upgrade_database, get_students_with_submissions, 
    get_student_submissions, create_test_submission, save_submission,
    save_assignment_file, get_assignment_files, get_assignment_file_count, 
    get_course_status, get_assignment_status, get_available_cities, 
    CITY_TIMEZONES, set_user_timezone,
    save_assignment_answer,
    check_user_arc_access,
    get_user_courses,
    grant_arc_access,
    is_day_available_for_user,
    get_available_days_for_user,
    mark_day_as_skipped,
    check_and_open_missed_days,
    get_day_id_by_title_and_arc,
    get_assignment_by_title_and_day,
    get_notification,
    get_mass_notification,
    get_user_local_time,
    set_user_as_admin
)
import uuid
import requests
import base64
import sys
import asyncio
from aiohttp import web
import logging
from urllib.parse import quote

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot_payments.log', encoding='utf-8'),
    ]
)

# Отключаем шумные библиотеки
for lib in ['httpx', 'httpcore', 'apscheduler', 'telegram']:
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.info("=== Бот запущен с логированием платежей ===")

from config import ADMIN_ID, ADMIN_IDS

def split_message(text, max_length=4096):
    """Разбивает длинное сообщение на части по max_length символов с учетом ссылок и Markdown"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    
    # Находим все ссылки в тексте и их позиции
    import re
    url_pattern = re.compile(r'https?://\S+')
    urls = list(url_pattern.finditer(text))
    
    # Находим все Telegram-ссылки отдельно (t.me, telegram.me)
    tg_pattern = re.compile(r'(?:t\.me|telegram\.me)/\S+')
    tg_urls = list(tg_pattern.finditer(text))
    
    # Объединяем все найденные ссылки
    all_links = urls + tg_urls
    
    current_pos = 0
    
    while current_pos < len(text):
        # Определяем, где можно безопасно разбить текст
        split_pos = min(current_pos + max_length, len(text))
        
        # Проверяем, не разрезаем ли мы ссылку
        for link in all_links:
            link_start, link_end = link.span()
            
            # Если ссылка пересекает границу разреза
            if link_start < split_pos < link_end:
                # Переносим разрез на конец ссылки
                split_pos = link_end
                break
        
        # Проверяем, не разрезаем ли мы посреди слова/предложения
        if split_pos < len(text):
            # Ищем хорошее место для разрыва
            for delimiter in ['\n\n', '\n', '. ', '! ', '? ', ' ', ', ']:
                # Ищем последнее вхождение разделителя ДО split_pos
                pos = text.rfind(delimiter, current_pos, split_pos - 100)
                if pos > current_pos:
                    split_pos = pos + len(delimiter)
                    break
        
        part = text[current_pos:split_pos].strip()
        if part:
            parts.append(part)
        
        current_pos = split_pos
    
    # Проверяем, не слишком ли длинные части
    final_parts = []
    for part in parts:
        if len(part) <= max_length:
            final_parts.append(part)
        else:
            # Если часть все еще слишком длинная, разбиваем жестко
            final_parts.extend([part[i:i+max_length] for i in range(0, len(part), max_length)])
    
    return final_parts

def is_admin(user_id):
    """Проверяет является ли пользователь админом"""
    return user_id == ADMIN_ID or user_id in ADMIN_IDS

TOKEN = "8556393148:AAFkH8aTmgScTQpFlm_9BiQO7lMijEHYU_E"
init_db()

def get_moscow_time():
    """Фиксированное московское время (UTC+3) без таймзоны"""
    utc_now = datetime.now(timezone.utc)
    moscow_time = utc_now + timedelta(hours=3)
    return moscow_time.replace(tzinfo=None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.message.from_user
    add_user(user.id, user.username, user.first_name)
    
    keyboard = [
        ["📚 Мои задания", "🎯 Купить тренинг"],
        ["👤 Профиль", "🛠 Тех.поддержка"]
    ]

    if has_any_access(user.id) or user.id == ADMIN_ID:
        keyboard.append(["👥 Перейти в сообщество"])
    
    if is_admin(user.id):
        keyboard.append(["👨‍🏫 Проверка заданий"])
        keyboard.append(["⚙️ Инструменты администратора"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Приветствую вас, {user.first_name}! Выбери действие:",
        reply_markup=reply_markup
    )

async def admin_tools_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню инструментов администратора"""
    context.user_data['current_section'] = 'admin_tools'
    
    keyboard = [
        ["🔧 Изменение доступа"],
        ["🔔 Отправить уведомление"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "⚙️ **Инструменты администратора**\n\n"
        "Выберите инструмент:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.message.from_user.id

    print(f"🔍 Кнопка нажата: '{text}'")
    
    # ДОБАВЬТЕ ЭТО ДЛЯ ОТЛАДКИ:
    if text.startswith(("🔄 ", "⏳ ", "✅ ")):
        print(f"🔍 Обрабатываем кнопку марафона: '{text}'")
        print(f"🔍 context.user_data: {context.user_data.get('available_arcs', {})}")

    current_section = context.user_data.get('current_section')
    if current_section == 'feedback' and context.user_data.get('in_feedback_detail'):
        pass

    if text.startswith("👤 ") and " - " in text and current_section == 'admin':
        print(f"🚨 Кнопка участника в админке: {text}")
        
        # Определяем по view_mode или тексту кнопки
        view_mode = context.user_data.get('view_mode', 'new')
        
        if view_mode == 'approved' or "принятых" in text:
            await show_student_part_approved(update, context)
        else:
            await show_student_part_assignments(update, context)
        return

    current_section = context.user_data.get('current_section')

    if text.startswith("📝 ") and current_section == 'admin':
        print(f"🚨 Кнопка 📝 в админке: {text}")
        await show_assignment_for_admin(update, context)
        return

    # 1. Сначала проверяем статистику
    if text == "📊 Мой прогресс":
        await show_statistics(update, context)
        return
    
    # 2. Если находимся в меню статистики И текст содержит эмодзи части
    if current_section == 'statistics_menu' and text.startswith(("🔄", "⏳", "✅")):
        await show_arc_statistics(update, context)
        return
    
    # 3. Если нажали "Выбрать другую часть" в статистике
    if text == "📊 К выбору марафона":
        await show_statistics(update, context)
        return

    # 1. Обработка кнопки "🎯 Купить тренинг"
    if text == "🎯 Купить тренинг":
        keyboard = [
            ["📖 Всё о марафоне"],
            ["💰 Купить доступ"],
            ["🔙 В главное меню"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🎯 **Тренинг 'Себя верни себе'**\n\n"
            "Выберите раздел:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    if text.startswith("🔄 ") or text.startswith("⏳ "):
        #Проверяем не находимся ли мы в админ-разделе
        current_section = context.user_data.get('current_section')
        if current_section == 'admin':
            # Это задание в админ-панели, обрабатываем отдельно
            await show_assignment_for_admin(update, context)
        else:
            # Это действительно дуга в каталоге
            await buy_arc_from_catalog(update, context)
        return
    
    # 5. Обработка кнопок покупки (используем существующие функции)
    if text == "💰 Купить полный доступ":
        # Проверяем выбрана ли дуга
        if 'current_arc_catalog' not in context.user_data:
            await update.message.reply_text("❌ Сначала выберите марафон")
            return
        # Вызываем существующую функцию покупки через Юкассу
        await buy_arc_with_yookassa(update, context, trial=False)
        return
    
    if text == "🎁 Пробный доступ(3 дня)":  # Обрати внимание на название!
        # 1. Проверяем выбрана ли часть
        if 'current_arc_catalog' not in context.user_data:
            await update.message.reply_text("❌ Сначала выберите марафон")
            return
    
        # 2. Проверяем что это ТЕКУЩАЯ часть
        part_status = context.user_data.get('part_status', '')
        if part_status != 'активный':
            await update.message.reply_text(
                "❌ **Пробный доступ доступен только для активных марафонов!**\n\n"
                "Для будущих марафонов доступен только полный доступ.",
                parse_mode='Markdown'
            )
            return
    
        await grant_free_trial_access(update, context)
        return

    # 0. Определяем обработчики для каждого раздела
    if text.startswith("🔙"):
        current_section = context.user_data.get('current_section')
        
        back_handlers = {
            'admin': {
                # Все "назад" ведут или к новым или к принятым заданиям
                "🔙 Назад к списку": lambda u, c: (
                    show_approved_assignments(u, c) 
                    if c.user_data.get('view_mode') == 'approved' 
                    else show_new_assignments(u, c)
                ),
                "🔙 Назад к новым заданиям": show_new_assignments,
                "🔙 Назад к принятым заданиям": show_approved_assignments,
                "🔙 Назад к списку участников": lambda u, c: (
                    show_approved_assignments(u, c) 
                    if c.user_data.get('view_mode') == 'approved' 
                    else show_new_assignments(u, c)
                ),
                "🔙 Назад к проверке": admin_panel,
                "🔙 Вернуться в меню проверки": admin_panel,
            },
        }
        
        if current_section in back_handlers and text in back_handlers[current_section]:
            await back_handlers[current_section][text](update, context)
            return

    # Обработка статистики админа
    if text == "📊 Прогресс участников":
        await show_users_stats(update, context)
        return
    
    # Если находимся в меню статистики админа
    if context.user_data.get('current_section') == 'admin_stats':
        # Выбор участника по цветным кнопкам
        if text.startswith(("🟢", "🟡", "🟠", "🔴")):
            await show_admin_user_statistics(update, context)
            return
        
        # Выбор части участника
        if text.startswith(("🔄", "⏳", "✅")):
            await show_admin_arc_statistics(update, context)
            return
        
        # Навигация
        if text == "👤 Выбрать другого участника":
            await show_users_stats(update, context)
            return
        
        if text == "📊 Посмотреть другой марафон этого участника":
            user_info = context.user_data.get('admin_current_user')
            if user_info:
                await show_admin_user_statistics(update, context)
            else:
                await show_users_stats(update, context)
            return

    # 1. Сначала ВСЕ уникальные кнопки которые точно определены
    unique_buttons = {
        "✅ Отправить задание": submit_assignment,
        "📝 Доступные задания": show_available_assignments,
        "👨‍🏫 Проверка заданий": admin_panel,
        "📚 Мои задания": my_assignments_menu,
        "🎯 Купить тренинг": show_training_catalog,
        "👤 Профиль": profile_menu,
        "🛠 Тех.поддержка": tech_support_menu,
        "🔙 В главное меню": start,
        "⏰ Часовой пояс": select_timezone,
        "👤 Изменить ФИО": start_fio_change,
        "🔙 Назад в кабинет": profile_menu,
        "🆕 Новые задания": show_new_assignments,
        "✅ Принятые задания": show_approved_assignments,
        "📁 Завершенные": lambda u, c: u.message.reply_text("📝 В разработке"),
        "⚠️ Пропущенные": lambda u, c: u.message.reply_text("📝 В разработке"),
        "🔙 Назад к проверке": admin_panel,
        "📎 Добавить файл": lambda u, c: (c.user_data.update({'waiting_for_file': True}), u.message.reply_text("📎 **Отправьте фото или файл:**\n\nФайл будет добавлен к вашему ответу.", parse_mode='Markdown')),
        "💬 Задать вопрос": ask_question_handler,
        "✅ Принять задание": finish_approval,
        "🔙 Вернуться в меню проверки": admin_panel,
        "💬 Личная консультация": request_personal_consultation,
        "💰 Купить доступ": show_course_main,
        "Перейти в каталог тренинга": show_course_main,
        "🔧 Изменение доступа": manage_access,
        "👥 Перейти в сообщество": go_to_community,
        "📊 Прогресс участников": show_users_stats,
        "🔙 Назад к тренингу": back_to_course_menu,
        "🔙 Выбор марафона": show_course_main,
        "📚 В меню заданий": my_assignments_menu,
        "📋 Принятые оферты": show_accepted_offers,
        "🔙 Назад в каталог": show_course_main,
        "📖 Инструкция": show_quick_guide,
        "💬 Задать вопрос о марафоне": contact_psychologist,
        "📷 Только фото": start_photo_only_answer,
        "📝 Только текст": start_text_only_answer, 
        "📷+📝 Фото и текст": start_photo_text_answer,
        "🔙 Назад к частям тренинга": show_events,
        "💰 Купить полный доступ": lambda u, c: buy_arc_with_yookassa(u, c, trial=False),
        "🎁 Пробный доступ(3 дня)": lambda u, c: buy_arc_with_yookassa(u, c, trial=True),
        "💰 Купить доступ заранее": lambda u, c: buy_arc_with_yookassa(u, c, trial=False),
        "🔙 Назад в меню заданий": show_available_assignments,
        "📚 В раздел Мои задания": my_assignments_menu,
        "💰 Купить заранее": lambda u, c: buy_arc_with_yookassa(u, c, trial=False),
        "📖 Всё о марафоне": show_about_course,
        "⚙️ Инструменты администратора": admin_tools_menu,
        "🔔 Отправить уведомление": start_notification,
        "🔙 Назад к инструментам": admin_tools_menu,
        "🔙 Назад": show_training_catalog,
    }

    
    if text in unique_buttons:
        await unique_buttons[text](update, context)
        return

    if text == "💬 Написать в поддержку":
        await write_to_support(update, context)
        return
    
    if text == "📖 Инструкции":
        await show_instructions(update, context)
        return
    
    if text == "👤 Авторы марафона":
        await show_author_info(update, context)
        return

    if text == "💰 Купить заранее":
        await buy_arc_with_yookassa(update, context, trial=False)
        return

    if text == "📂 Архив заданий" or text == "📂 Архив заданий":
        await show_feedback_parts(update, context)
        return

    if text in ["📢 Всем в бот", "✅ Только полный доступ", "🎁 Только пробный доступ"]:
        await handle_notification_creation(update, context)
        return

    if text in ["📤 Отправить", "✏️ Изменить", "❌ Отменить"]:
        await handle_notification_creation(update, context)
        return

    # В handle_buttons добавляем более надежную очистку:
    if text == "🔙 Отменить":
        # Очищаем ВСЕ данные уведомления
        keys_to_remove = []
        for key in context.user_data.keys():
            if key.startswith('notification_'):
                keys_to_remove.append(key)
    
        for key in keys_to_remove:
            context.user_data.pop(key, None)
    
        print(f"🔙 Отмена уведомления. Удалено ключей: {len(keys_to_remove)}")
        await admin_tools_menu(update, context)
        return

    # Обработка кнопок принятых заданий (✅ вместо 📝)
    if text.startswith("✅ ") and current_section == 'admin':
        print(f"🚨 Кнопка ✅ в админке: {text}")
        
        # Определяем view_mode
        view_mode = context.user_data.get('view_mode', 'new')
        print(f"🚨 view_mode: {view_mode}")
        
        if view_mode == 'approved':
            # Принятые задания -> show_assignment_approved
            await show_approved_assignment_simple(update, context)
        else:
            # Новые задания -> show_assignment_for_admin
            await show_assignment_for_admin(update, context)
        return

    if text.startswith("🏆"):  # 📚 вместо 🔄
        print(f"✅ Выбор части в feedback: {text}")
        await show_feedback_type(update, context)
        return

    # Обработка админки (оставляем 🔄)
    if context.user_data.get('current_section') == 'admin' and "🔄" in text:
        # Это админка - задания на проверке
        await show_assignment_for_admin(update, context)
        return

    if text == "✅ Завершенные задания":
        await show_feedback_list(update, context, viewed=1)
        return

    # Если нажали на задание в разделе "Доступные задания"
    if context.user_data.get('current_section') == 'available_assignments':
        # Проверяем, нажали ли на задание (начинается с 📝)
        if text.startswith("📝"):
            await show_assignment_from_list(update, context)
            return
        
        if text == "🟡 Задания на проверке":
            await show_in_progress_assignments(update, context)
            return

    if text == "📂 Тестирование":
        await update.message.reply_text(
            "Разде 'Тестирование' скоро появится!\n"
            "Здесь будут доступны еженедельные тесты для проверки вашего прогресса.\n",
            parse_mode='Markdown'
        )
        return

    elif text.startswith("🎯 Марафон"):
        await show_seminar_details(update, context)
        return

    # Кнопка "Назад к частям"  
    if text == "🔙 Назад к частям":
        await show_feedback_parts(update, context)
        return

    # 2. Обработка оферт
    if text == "✅ Принять оферту":
        await accept_offer_handler(update, context)
        return

    if text == "❌ Отказаться":
        await decline_offer_handler(update, context)
        return

    if text == "❌ Отказаться от оферты" and context.user_data.get('showing_service_offer'):
        await decline_service_offer_handler(update, context)
        return

    if text == "✅ Принять оферту услуг":
        await accept_service_offer_handler(update, context)
        return

    # 3. Обработка разделов каталога
    if text == "📅 Расписание марафонов":
        await show_events(update, context)
        return

    if text == "🗓 Расписание вебинаров":
        await show_schedule(update, context)
        return

    if text == "🔙 Назад к описанию марафона":
        await show_about_course(update, context)
        return

    if text.startswith("📝"):
        # Проверяем из какого раздела пришли
        if 'feedback_assignments_map' in context.user_data and text in context.user_data['feedback_assignments_map']:
            await show_feedback_assignment_detail(update, context)
        
    # 5. Обработка по разделам с current_section
    current_section = context.user_data.get('current_section')
    view_mode = context.user_data.get('view_mode')

    # 5.5 Обработка раздела admin_access (управление доступом)
    if current_section == 'admin_access' and text.startswith("👤"):
        # Только кнопки вида "👤 Имя (1)" для управления доступом
        if "(" in text and ")" in text:
            await show_user_arcs_access(update, context)
            return

    # 5.6 Обработка раздела admin_stats (прогресс)
    if current_section == 'admin_stats':
        if text.startswith(("🟢", "🟡", "🟠", "🔴")):
            await show_user_statistics_admin(update, context)
            return

    # 6. Обработка кнопок Назад (упрощенная)
    if text.startswith("🔙"):
        # Уже обработано в начале, если не сработало - игнорируем
        pass

    # 8. Выбор часового пояса (вместо города)
    from database import get_available_cities
    if text in get_available_cities():
        from database import set_user_timezone, CITY_TIMEZONES
        timezone_offset = CITY_TIMEZONES[text]
        set_user_timezone(user_id, text, timezone_offset)
    
        # Форматируем сообщение
        if timezone_offset > 0:
            offset_display = f"+{timezone_offset}"
        elif timezone_offset < 0:
            offset_display = f"{timezone_offset}"
        else:
            offset_display = "0"
    
        await update.message.reply_text(
            f"✅ **Часовой пояс установлен!**\n\n"
            f"Разница с Москвой: {offset_display} часа\n"
            f"Задание дня будет открываться в 6:00 по вашему местному времени."
            f"В случае если вы не успеете его сделать до 0:00, оно засчитается как пропущенное."
            f"Если пропустить задание, то доступ к нему останется, но прервется серия выполнения заданий подряд." ,
            parse_mode='Markdown'
        )
        await profile_menu(update, context)
        return

    # 9. Если ничего не сработало
    await handle_text(update, context)

async def back_to_arcs_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к списку разделов для покупки"""
    await show_buy_access(update, context)

async def back_to_course_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в меню тренинга"""
    course_title = context.user_data.get('current_course', 'СЕБЯ ВЕРНИ СЕБЕ')
    
    keyboard = [
        ["📖 Всё о тренинге"],
        ["💰 Купить доступ"],
        ["🔙 В главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📚 **{course_title}**\n\nВыберите часть:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_assignment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Универсальный обработчик для просмотра заданий"""
    view_mode = context.user_data.get('view_mode')
    if view_mode == 'approved':
        await show_assignment_approved(update, context)
    else:
        await show_assignment_for_admin(update, context)

async def view_submission_file(update: Update, context: ContextTypes.DEFAULT_TYPE):


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_section'] = 'admin'
    """Обновленная админ-панель"""
    keyboard = [
        ["✅ Принятые задания"],
        ["📊 Прогресс участников"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "👨‍🏫 **Проверка заданий**\n\n"
        "Выберите марафон:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    if context.user_data.get('notification_stage') == 'waiting_content':
        await process_notification_content(update, context)
        return
    
    if context.user_data.get('answering'):
        answer_type = context.user_data.get('answer_type', 'Фото_и_текст')
        
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            
            if 'answer_files' not in context.user_data:
                context.user_data['answer_files'] = []
            
            context.user_data['answer_files'].append(file_id)
            
            # Для "только фото" сразу показываем кнопку отправки
            if answer_type == 'Только_фото':
                await show_submit_button(update, context)
            # Для "фото+текст" показываем финальные кнопки
            elif answer_type == 'Фото_и_текст':
                await show_final_buttons(update, context)
            return


async def view_assignment_files(update: Update, context: ContextTypes.DEFAULT_TYPE):


async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Личный кабинет пользователя - ОБНОВЛЕННЫЙ"""

async def request_fio_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просит ввести ФИО если его нет"""

async def select_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор часового пояса"""

async def my_assignments_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_student_id'] = None
    """Главное меню раздела 'Мои задания'"""
    user_id = update.message.from_user.id

    from database import check_and_open_missed_days
    missed_days = check_and_open_missed_days(user_id)
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT city FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or not result[0]:
        await update.message.reply_text(
            "📝 **Чтобы начать обучение, нужно настроить профиль!**\n\n"
            "Перейди в 👤 Профиль → 🌍 Выбрать город → Так же введи ФИО(можно без отчества), чтобы психолог точно понимал от кого будует приходить ответы на задания",
            parse_mode='Markdown'
        )
        return

    from database import has_new_feedback
    user_id = update.message.from_user.id
    has_new = has_new_feedback(user_id)

    feedback_button = "📂 Архив заданий" if has_new else "📂 Архив заданий"
    
    keyboard = [
        ["📝 Доступные задания", "📂 Архив заданий"],
        ["📊 Мой прогресс", "📂 Тестирование"],
        ["🔙 В главное меню", "📖 Инструкция"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📚 **РАЗДЕЛ 'МОИ ЗАДАНИЯ'**\n\n"
        "**Здесь вы можете:**\n\n"
        "• **Доступные задания** — показывает задания активного марафона\n\n"
        "• **Архив заданий** — история ваших выполненных заданий\n\n"  
        "• **Мой прогресс** — статистика выполнения заданий\n\n"
        "• **Инструкция** — как работать с ботом\n\n"
        "• **Тестирование** — раздел с еженедельными тестами\n\n"
        "Выберите раздел:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_available_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📝 Показывает задания из ВСЕХ активных частей"""
    context.user_data['current_section'] = 'available_assignments'
    user_id = update.message.from_user.id
    
    # ИМПОРТ НОВЫХ ФУНКЦИЙ
    from database import get_user_active_arcs, get_current_arc_day
    
    # ПОЛУЧАЕМ ВСЕ АКТИВНЫЕ ЧАСТИ (не одну!)
    active_arcs = get_user_active_arcs(user_id)
    
    # ★★★ ДОБАВЬТЕ ЭТОТ КОД ДЛЯ ОТЛАДКИ:
    print(f"🔍 DEBUG get_user_active_arcs для user_id={user_id}:")
    print(f"  Вернулось: {active_arcs}")
    print(f"  Количество: {len(active_arcs)}")
    
    if not active_arcs:
        # Проверим доступы вручную
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT arc_id, access_type FROM user_arc_access WHERE user_id = ?', (user_id,))
        user_accesses = cursor.fetchall()
        print(f"🔍 user_arc_access для user_id={user_id}: {user_accesses}")
        
        # Проверим все части с датами
        cursor.execute('''
            SELECT arc_id, title, дата_начала, дата_окончания 
            FROM arcs 
            WHERE дата_начала IS NOT NULL AND дата_окончания IS NOT NULL
        ''')
        all_arcs = cursor.fetchall()
        print(f"🔍 Все части с датами: {all_arcs}")
        
        today = datetime.now().date()
        print(f"🔍 Сегодня: {today}")
        
        conn.close()
        
        # Создаем inline-клавиатуру
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        
        keyboard = [
            ["💰 Купить доступ"],
            ["📖 Всё о марафонах"],
            ["🔙 В главное меню"]
        ]
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "**У вас нет активных марафонов**\n\n"
            "**Как начать участвовать?**\n"
            "**Для начала перейдите в кардел 'купить доступ', там у вас будет выбор:**\n\n"
            "✅ **Бесплатный пробный период:**\n"
            "• Все задания первых трех дней марафона\n"
            "• Сопровождение психолога\n"
            "• Ответы на все вопросы по заданиям\n\n"
            "✅ **Покупая полный доступ:**\n"
            "• Полный доступ ко всем заданиям марафона\n"
            "• Поддержка на протяжении всего марафона\n"
            "• Доступ к сообществу участников\n\n",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    # 📊 СОБИРАЕМ ВСЕ ЗАДАНИЯ ИЗ ВСЕХ АКТИВНЫХ ЧАСТЕЙ
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    all_assignments_info = []
    total_available = 0
    total_in_progress = 0
    total_completed = 0
    
    # ДЛЯ КАЖДОЙ АКТИВНОЙ ЧАСТИ
    for arc_id, arc_title, arc_start, arc_end, access_type in active_arcs:
        # Проверяем доступ (если не админ)
        if user_id not in ADMIN_IDS:
            cursor.execute('SELECT access_type FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                          (user_id, arc_id))
            access_result = cursor.fetchone()
            
            if not access_result:
                continue  # Нет доступа к этой части
        
        # Получаем текущий день для этой части
        current_day_info = get_current_arc_day(user_id, arc_id)
        
        if not current_day_info or current_day_info['day_number'] == 0:
            continue  # Часть еще не началась
        
        current_day_num = current_day_info['day_number']
        
        # Получаем задания на текущий день этой части
        cursor.execute('''
            SELECT a.assignment_id, a.title, a.content_text
            FROM assignments a
            JOIN days d ON a.day_id = d.day_id
            WHERE d.arc_id = ? AND d.order_num = ?
            ORDER BY a.assignment_id
        ''', (arc_id, current_day_num))
        
        day_assignments = cursor.fetchall()
        
        # Для каждого задания проверяем статус
        for assignment_id, assignment_title, content_text in day_assignments:
            cursor.execute('''
                SELECT status FROM user_progress_advanced 
                WHERE user_id = ? AND assignment_id = ?
            ''', (user_id, assignment_id))
            
            status_result = cursor.fetchone()
            status = status_result[0] if status_result else 'new'
            
            # Сохраняем информацию
            assignment_info = {
                'arc_id': arc_id,
                'arc_title': arc_title[:20],  # Обрезаем длинное название
                'assignment_id': assignment_id,
                'title': assignment_title,
                'status': status,
                'day_num': current_day_num,
                'access_type': access_type
            }
            
            # Считаем статистику
            if status == 'new':
                all_assignments_info.append(assignment_info)
                total_available += 1
            elif status == 'submitted':
                total_in_progress += 1
            elif status == 'approved':
                total_completed += 1
    
    conn.close()
    
    # 📝 ФОРМИРУЕМ СООБЩЕНИЕ
    
    if not all_assignments_info:
        await update.message.reply_text(
            "✅ **Все задания выполнены!**\n\n"
            "Новые задания появятся завтра в 06:00 по вашему времени.",
            parse_mode='Markdown'
        )
        return
    
    message = "📝 **ДОСТУПНЫЕ ЗАДАНИЯ**\n\n"
    
    # Информация о потоках
    arcs_summary = []
    for arc_id, arc_title, arc_start, arc_end, access_type in active_arcs:
        day_info = get_current_arc_day(user_id, arc_id)
        if not day_info or day_info.get('day_number') is None or day_info['day_number'] == 0:
            print(f"⚠️ Часть {arc_title}: день не определен или равен 0")
            continue  # Пропускаем эту часть
        
        current_day_num = day_info['day_number']
    
    if arcs_summary:
        message += "**Активные марафоны:**\n" + "\n".join(arcs_summary) + "\n\n"
    
    # Статистика
    message += f"• 🔵 Доступно заданий: {total_available}\n\n"
    
    # Инструкция
    message += "💡 **Как работать:**\n\n"
    message += "1. Нажмите на задание из списка ниже\n\n"
    message += "2. Выберите подходящий способ ответа\n\n"
    message += "3. Выполните задание и отправьте на проверку\n\n"
    message += "4. Задания открываются последовательно: когда выполните задания одного дня, тогда откроются следующие\n\n"
    message += "5. Выполненное задание будет храниться в разделе 'Архив заданий'\n\n"
    message += "6. Новые задания открываются в 06:00 по вашему времени\n\n"
    message += "7. Важно: успейте выполнить задания до завершения марафона. После окончания задания доступны не будут\n\n"
    
    message += "Выберите задание:"
    
    # 🎹 СОЗДАЕМ КЛАВИАТУРУ
    
    keyboard = []
    assignments_mapping = []  # Для сохранения связи кнопка → задание
    
    # Группируем задания по 2 в ряд
    row = []
    for i, assignment in enumerate(all_assignments_info[:24]):  # Ограничиваем 24 заданиями
        # Формируем текст кнопки с указанием потока
        btn_text = f"📝 {assignment['title']}"
        
        row.append(btn_text)
        
        # Сохраняем mapping
        assignments_mapping.append({
            'btn_text': btn_text,
            'arc_id': assignment['arc_id'],
            'assignment_id': assignment['assignment_id'],
            'title': assignment['title']
        })
        
        if len(row) == 2 or i == len(all_assignments_info[:24]) - 1:
            keyboard.append(row)
            row = []
    
    # Добавляем служебные кнопки
    if total_in_progress > 0:
        keyboard.append(["🟡 Задания на проверке"])
    
    keyboard.append(["📚 В раздел Мои задания"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # 💾 СОХРАНЯЕМ ДАННЫЕ ДЛЯ ОБРАБОТКИ НАЖАТИЙ
    context.user_data['assignments_mapping'] = assignments_mapping
    context.user_data['available_assignments_stats'] = {
        'total_available': total_available,
        'total_in_progress': total_in_progress,
        'total_completed': total_completed
    }
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детали задания и ВЫБОР ТИПА ОТВЕТА"""
    user_id = update.message.from_user.id
    
    # 1. Получаем assignment_id из контекста (новый путь) или из текста (старый путь)
    assignment_id = context.user_data.get('current_assignment_id')
    
    if not assignment_id:
        # Старый путь: через текст кнопки
        assignment_title = update.message.text[2:].strip()
        
        day_title = context.user_data.get('current_day')
        arc_id = context.user_data.get('current_arc_id')
        
        if not day_title or not arc_id:
            await update.message.reply_text("❌ Ошибка: день не определен")
            return
        
        from database import get_day_id_by_title_and_arc, get_assignment_by_title_and_day
        
        day_id = get_day_id_by_title_and_arc(day_title, arc_id)
        if not day_id:
            await update.message.reply_text("❌ Ошибка: день не найден")
            return

        if " (до" in assignment_title:
            clean_title = assignment_title.split(" (до")[0].strip()
        else:
            clean_title = assignment_title

        assignment_id = get_assignment_by_title_and_day(clean_title, day_id)
        context.user_data['current_day_id'] = day_id
    
    if not assignment_id:
        await update.message.reply_text("❌ Задание не найдено")
        return
    
    # 2. Получаем данные задания
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.content_text, a.доступно_до, a.title, d.title as day_title, d.arc_id
        FROM assignments a
        JOIN days d ON a.day_id = d.day_id
        WHERE a.assignment_id = ?
    ''', (assignment_id,))

    result = cursor.fetchone()
    
    if not result:
        await update.message.reply_text("❌ Задание не найдено")
        conn.close()
        return

    content_text, available_until, assignment_title, day_title, arc_id = result
    
    # 3. Проверяем доступ (пробный 3 дня)
    from database import can_access_assignment
    can_access, access_message = can_access_assignment(user_id, assignment_id, arc_id)
    
    if not can_access:
        await update.message.reply_text(f"❌ {access_message}")
        conn.close()
        return
    
    # 4. Проверяем статус задания
    cursor.execute('''
        SELECT status FROM user_progress_advanced 
        WHERE user_id = ? AND assignment_id = ?
    ''', (user_id, assignment_id))
    
    progress = cursor.fetchone()
    
    if progress and progress[0] == 'submitted':
        await update.message.reply_text(
            "⏳ **Ваше задание уже на проверке!**\n\n"
            "Дождитесь обратной связи в разделе 'Ответ психолога'.",
            parse_mode='Markdown'
        )
        conn.close()
        return
    
    conn.close()
    
    # 5. Отправляем заголовок
    header = f"**📝 {assignment_title}**\n\n"
    
    if available_until and available_until != '22:00':
        header += f"⏰ **Сделать до:** {available_until} по вашему времени\n\n"
    
    await update.message.reply_text(header, parse_mode='Markdown')
    
    # 6. Отправляем текст задания через send_long_message
    if content_text:
        await send_long_message(update, content_text, "**Задание:**")
    
    # 7. Отправляем выбор типа ответа
    message = "**📤 Выберите вариант ответа:**"
    
    keyboard = [
        ["📷 Только фото"],
        ["📝 Только текст"],
        ["📷+📝 Фото и текст"],
        ["🔙 Назад в меню заданий"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['current_assignment'] = assignment_title
    context.user_data['current_assignment_id'] = assignment_id
    context.user_data['current_arc_id'] = arc_id
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def start_assignment_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, answer_type=None):
    """Начинает процесс ответа в зависимости от выбранного типа"""

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    # Обработка текста для уведомлений
    if context.user_data.get('notification_stage') == 'waiting_content':
        # Проверяем не нажата ли кнопка "Отменить"
        if text == "🔙 Отменить":
            # Очищаем данные
            for key in ['notification_stage', 'notification_recipients']:
                context.user_data.pop(key, None)
            await admin_tools_menu(update, context)
            return
        
        # Обрабатываем текст уведомления
        await process_notification_content(update, context)
        return
    
    # Обработка кнопок в предпросмотре уведомления
    if context.user_data.get('notification_stage') == 'preview':
        if text == "📤 Отправить":
            await send_notification_final(update, context)
            return
        elif text == "✏️ Изменить":
            context.user_data['notification_stage'] = 'waiting_content'
            # Очищаем старый контент
            for key in ['notification_text', 'notification_photo', 'notification_document']:
                context.user_data.pop(key, None)
            
            await update.message.reply_text(
                "✏️ Отправьте новое сообщение с уведомлением:\n"
                "(можно прикрепить фото или файл)",
                reply_markup=ReplyKeyboardMarkup([["🔙 Отменить"]], resize_keyboard=True),
                parse_mode='Markdown'
            )
            return
        elif text == "❌ Отменить":
            # Очищаем все данные уведомления
            for key in ['notification_stage', 'notification_recipients', 'notification_text',
                       'notification_photo', 'notification_document', 'notification_users']:
                context.user_data.pop(key, None)
            await admin_tools_menu(update, context)
            return

    # === 1. ОБРАБОТКА ОТКАЗА ОТ ОФЕРТЫ ===
    if text == "❌ Отказаться":
        await update.message.reply_text(
            "❌ **Вы отказались от оферты.**\n\n"
            "Для использования бота необходимо принять оферту.\n"
            "Вы можете вернуться к этому позже в разделе 'Профиль'.",
            reply_markup=ReplyKeyboardMarkup([["🔙 В главное меню"]], resize_keyboard=True)
        )
        return

    # === 3. ОБРАБОТКА ВВОДА ТЕЛЕФОНА ===
    if context.user_data.get('waiting_for_phone'):
        phone = update.message.text.strip()
        
        import re
        phone_clean = re.sub(r'[^\d+]', '', phone)
        
        if phone_clean.startswith('+'):
            phone_clean = phone_clean[1:]
        
        if len(phone_clean) == 11 and phone_clean.startswith(('7', '8')):
            formatted_phone = f"+7{phone_clean[1:]}"
            
            print(f"🔍 Введен телефон: {formatted_phone}")
            
            # Сохраняем телефон в БД
            from database import accept_offer
            accept_offer(user_id, phone=formatted_phone, fio=None)
            
            context.user_data['waiting_for_phone'] = False
            
            await update.message.reply_text(
                f"✅ **Телефон принят и сохранен!**\n\n"
                f"📝 **Теперь введите ваше ФИО:**\n"
                f"(Обязательно имя и фамилия, минимум 2 слова)\n\n"
                f"**Пример:** Иванов Иван\n"
                f"**Пример:** Анна Петрова",
                parse_mode='Markdown'
            )
            
            context.user_data['waiting_for_fio'] = True
            return
        
        elif len(phone_clean) == 10 and phone_clean.startswith('9'):
            formatted_phone = f"+7{phone_clean}"
            
            print(f"🔍 Введен телефон: {formatted_phone}")
            
            # Сохраняем телефон в БД
            from database import accept_offer
            accept_offer(user_id, phone=formatted_phone, fio=None)
            
            context.user_data['waiting_for_phone'] = False
            
            await update.message.reply_text(
                f"✅ **Телефон принят и сохранен!**\n\n"
                f"📝 **Теперь введите ваше ФИО:**\n"
                f"(Обязательно имя и фамилия, минимум 2 слова)\n\n"
                f"**Пример:** Иванов Иван\n"
                f"**Пример:** Анна Петрова",
                parse_mode='Markdown'
            )
            return
            
            context.user_data['waiting_for_fio'] = True
        
        else:
            await update.message.reply_text(
                "❌ **Некорректный номер телефона.**\n\n"
                "Номер должен содержать 11 цифр.\n"
                "**Примеры правильных форматов:**\n"
                "• +79001234567\n"
                "• 89001234567\n"
                "• 79001234567\n\n"
                "Пожалуйста, введите номер еще раз:",
                parse_mode='Markdown'
            )
            return
        return

    # === 4. ОБРАБОТКА ВВОДА ФИО ===
    if context.user_data.get('waiting_for_fio'):
        fio = update.message.text.strip()
        user_id = update.message.from_user.id
    
        print(f"🔍 Введено ФИО: '{fio}'")
    
        # Проверяем что минимум 2 слова
        words = fio.split()
        if len(words) < 2:
            await update.message.reply_text(
                "❌ **ФИО должно содержать имя и фамилию.**\n\n"
                "Пожалуйста, введите минимум 2 слова (имя и фамилию).\n"
                "**Примеры:**\n"
                "• Иванов Иван\n"
                "• Анна Петрова\n"
                "• Мария Сергеевна",
                parse_mode='Markdown'
            )
            return
    
        # Проверяем что каждое слово минимум 2 символа
        short_words = []
        for word in words:
            if len(word.strip()) < 2:
                short_words.append(word)
    
        if short_words:
            await update.message.reply_text(
                f"❌ **Слишком короткие слова:** {', '.join(short_words)}\n\n"
                "Каждое слово должно быть минимум 2 символа.",
                parse_mode='Markdown'
            )
            return
    
        # Проверяем общую длину
        if len(fio) < 5:
            await update.message.reply_text(
                "❌ **ФИО слишком короткое.**\n\n"
                "Общая длина должна быть минимум 5 символов.",
                parse_mode='Markdown'
            )
            return
    
        # Сохраняем ФИО в БД
        from database import accept_offer
        success = accept_offer(user_id, phone=None, fio=fio)
    
        if success:
            # Очищаем все флаги регистрации
            for key in ['waiting_for_fio', 'waiting_for_phone', 'showing_offer']:
                if key in context.user_data:
                    del context.user_data[key]
        
            await update.message.reply_text(
                f"🎉 **Регистрация завершена! Остался последний шаг - выбрать часовой пояс. Это необходимо, чтобы бот открывал задания и отправлял уведомления согласно вашему времени.**\n\n"
                f"✅ ФИО: {fio}\n\n"
                "Теперь вы можете пользоваться всеми функциями бота.",
                reply_markup=ReplyKeyboardMarkup([["⏰ Часовой пояс"]], resize_keyboard=True),
                parse_mode='Markdown'
            )
        
            # НЕ переходим в профиль - пусть нажмет кнопку
        else:
            await update.message.reply_text(
                "❌ **Ошибка сохранения ФИО.**\n\n"
                "Пожалуйста, попробуйте еще раз или обратитесь в поддержку.",
                parse_mode='Markdown'
            )
        return

    # === 5. ОБРАБОТКА ВОПРОСОВ К ЗАДАНИЯМ ===
    if context.user_data.get('waiting_for_question'):
        question = text
        
        if 'questions' not in context.user_data:
            context.user_data['questions'] = []
        
        context.user_data['questions'].append(question)
        context.user_data['waiting_for_question'] = False
        
        answer_type = context.user_data.get('answer_type', 'Фото_и_текст')
        if answer_type in ['Только_фото', 'Только_текст']:
            await show_submit_button(update, context)
        else:
            await show_final_buttons(update, context)
        
        await update.message.reply_text(
            f"✅ **Вопрос добавлен!**\n\n"
            f"*{question[:100]}...*",
            parse_mode='Markdown'
        )
        return

    # === 6. ОБРАБОТКА ОТВЕТОВ НА ЗАДАНИЯ ===
    if context.user_data.get('answering'):
        answer_type = context.user_data.get('answer_type', 'Фото_и_текст')
        
        if answer_type == 'Только_текст':
            context.user_data['answer_text'] = text
            await show_submit_button(update, context)
            return
        
        elif answer_type == 'Фото_и_текст':
            if not context.user_data.get('answer_text'):
                context.user_data['answer_text'] = text
                await update.message.reply_text(
                    "✅ **Текст сохранен!**\n\n"
                    "📎 **Теперь прикрепите фото к ответу:**",
                    parse_mode='Markdown'
                )
                return
            
            elif context.user_data.get('answer_files'):
                context.user_data['questions'].append(text)
                await show_final_buttons(update, context)
                return
        
        elif answer_type == 'Только_фото':
            await update.message.reply_text(
                "📷 **Вы выбрали вариант 'Только фото'.**\n\n"
                "Пожалуйста, отправьте фото для задания.",
                parse_mode='Markdown'
            )
            return

    # === 7. ОБРАБОТКА КОММЕНТАРИЕВ АДМИНА ===
    if context.user_data.get('waiting_for_comment') and is_admin(user_id):
        comment = update.message.text
        context.user_data['current_comment'] = comment
        context.user_data['waiting_for_comment'] = False
    
        keyboard = [
            ["✅ Принять задание"],
            ["🔙 Вернуться в меню проверки"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
        await update.message.reply_text(
            f"💬 **Комментарий сохранен!**\n\n*{comment}*\n\n**Нажмите кнопку чтобы принять задание:**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
 
    elif is_admin(user_id) and context.user_data.get('current_comment'):
        additional_text = update.message.text
        current_comment = context.user_data['current_comment']
        context.user_data['current_comment'] = current_comment + "\n\n" + additional_text
    
        keyboard = [
            ["✅ Принять задание"],
            ["🔙 Вернуться в меню проверки"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
        await update.message.reply_text(
            f"💬 **Дополнение добавлено к комментарию!**\n\n*{additional_text}*\n\n**Нажмите кнопку чтобы принять задание:**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
async def show_final_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает финальные кнопки после ответа (фото+текст)"""
    keyboard = [
        ["💬 Задать вопрос"],
        ["✅ Отправить задание"],
        ["🔙 Назад в меню заданий"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    files_count = len(context.user_data.get('answer_files', []))
    questions_count = len(context.user_data.get('questions', []))
    
    await update.message.reply_text(
        f"📊 **Готово!**\n\n"
        f"✅ Текст ответа: сохранен\n"
        f"📎 Фото: {files_count} шт.\n"
        f"💬 Вопросы: {questions_count} шт.\n\n"
        f"**Вы можете:**\n"
        f"• Добавить еще файлы\n"
        f"• Задать вопросы\n"
        f"• **Отправить задание на проверку**\n\n"
        f"После отправки изменить ответ будет нельзя!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    
async def finish_assignment_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает отправку ответа и сохраняет в БД"""

async def process_assignment_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает вопрос к заданию"""

async def finish_assignment_with_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает отправку задания с вопросами"""

async def show_new_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['view_mode'] = 'new'
    context.user_data['current_section'] = 'admin'
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Прямо получаем участников с новыми заданиями и их частями
    cursor.execute('''
        SELECT DISTINCT 
            u.user_id, 
            COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
            ar.title as part_title,
            ar.arc_id,
            COUNT(upa.assignment_id) as new_count
        FROM users u
        JOIN user_progress_advanced upa ON u.user_id = upa.user_id
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        JOIN arcs ar ON d.arc_id = ar.arc_id
        WHERE upa.status = 'submitted'
        GROUP BY u.user_id, ar.arc_id
        ORDER BY new_count DESC
    ''')
    
    students_data = cursor.fetchall()
    conn.close()
    
    if not students_data:
        await update.message.reply_text("✅ Нет новых заданий для проверки")
        return
    
    keyboard = []
    student_mapping = {}
    
    for user_id, display_name, part_title, arc_id, new_count in students_data:
        # Обрезаем длинные имена
        if len(display_name) > 20:
            display_name = display_name[:17] + "..."
        
        # Формат: 👤 Имя - Часть X (N новых)
        btn_text = f"👤 {display_name} - {part_title} ({new_count} новых)"
        keyboard.append([btn_text])
        
        # Сохраняем mapping: кнопка → (user_id, arc_id)
        student_mapping[btn_text] = {'user_id': user_id, 'arc_id': arc_id}
    
    context.user_data['student_mapping'] = student_mapping
    
    keyboard.append(["🔙 Назад к проверке"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🆕 **Новые задания для проверки:**\n\n"
        "Выберите участника и часть:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
async def show_student_part_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ВСЕ новые задания участника в выбранной части"""

async def show_student_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает тренинги выбранного участника"""
    
async def show_assignment_for_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_section'] = 'admin'
    text = update.message.text
    
    print(f"🚨 [1] show_assignment_for_admin: text='{text}'")
    
    # Определяем префикс (📝 или ✅)
    if text.startswith("📝 "):
        assignment_title = text[2:].strip()
    elif text.startswith("✅ "):
        assignment_title = text[2:].strip()
    else:
        assignment_title = text.strip()
    
    print(f"🚨 [2] assignment_title='{assignment_title}'")
    
    # Парсинг дня из скобок (одинаково для 📝 и ✅)
    day_title = None
    if "(" in assignment_title and ")" in assignment_title:
        import re
        match = re.search(r'\((.*?)\)', assignment_title)
        if match:
            day_title = match.group(1).strip()
            assignment_title = assignment_title.split("(")[0].strip()
    
    print(f"🚨 [3] clean assignment_title='{assignment_title}'")
    print(f"🚨 [4] extracted day_title='{day_title}'")
    
    # Если извлекли день из кнопки - используем его
    if day_title:
        context.user_data['current_day'] = day_title
        print(f"🚨 [5] Сохранили в контекст: current_day='{day_title}'")
    
    student_id = context.user_data.get('current_student_id')
    print(f"🚨 [6] student_id={student_id}")
 
    if not student_id:
        await update.message.reply_text("❌ Ошибка: участник не выбран")
        return
    
    day_id = context.user_data.get('current_day_id')
    
    if not day_id:
        day_title = context.user_data.get('current_day')
        arc_id = context.user_data.get('current_arc_id')
        
        if day_title and arc_id:
            from database import get_day_id_by_title_and_arc
            day_id = get_day_id_by_title_and_arc(day_title, arc_id)
    
    if not day_id:
        await update.message.reply_text("❌ Ошибка: день не определен")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT day_id, title FROM days WHERE day_id = ?', (day_id,))
    day_info = cursor.fetchone()
    
    cursor.execute('''
        SELECT assignment_id, title 
        FROM assignments 
        WHERE title = ? AND day_id = ?
    ''', (assignment_title, day_id))
    assignment_info = cursor.fetchone()
    
    cursor.execute('''
        SELECT COUNT(*) 
        FROM user_progress_advanced 
        WHERE assignment_id = ? AND user_id = ?
    ''', (assignment_info[0] if assignment_info else 0, student_id))
    answer_count = cursor.fetchone()[0]
    
    conn.close()
    
    if not assignment_info:
        import re
        clean_title = re.sub(r'^[^a-zA-Zа-яА-Я0-9]+', '', assignment_title)
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT assignment_id, title 
            FROM assignments 
            WHERE title = ? AND day_id = ?
        ''', (clean_title, day_id))
        assignment_info = cursor.fetchone()
        conn.close()
    
    if not assignment_info:
        await update.message.reply_text(f"❌ Задание '{assignment_title}' не найдено в дне {day_id}")
        return

    assignment_id, found_title = assignment_info
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''  
        SELECT a.assignment_id, a.content_text, 
               upa.answer_text, upa.answer_files, upa.status,
               u.fio, u.username, upa.teacher_comment
        FROM assignments a
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        JOIN users u ON upa.user_id = u.user_id
        WHERE a.title = ? AND upa.user_id = ? AND a.day_id = ?
    ''', (found_title, student_id, day_id))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ Задание не найдено")
        return
    
    
    assignment_id, content_text, answer_text, answer_files, status, fio, username, teacher_comment = result
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT d.title, ar.title 
        FROM days d
        JOIN arcs ar ON d.arc_id = ar.arc_id
        WHERE d.day_id = ?
    ''', (day_id,))
    day_info = cursor.fetchone()
    conn.close()
    
    day_title = day_info[0] if day_info else "Неизвестно"
    arc_title = day_info[1] if day_info else "Неизвестно"
    
    display_name = fio if fio else username
    message = f"**📝 Задание: {assignment_title}**\n\n"
    message += f"**Участник:** {display_name}\n"
    message += f"{arc_title}\n"
    message += f"**День:** {day_title}\n\n"

    await update.message.reply_text(message, parse_mode='Markdown')

    # ★ ИСПРАВЛЕНО: Получаем медиа-контент задания
    from database import get_assignment_media
    media_data = None

    try:
        media_data = get_assignment_media(assignment_id)
        print(f"🔍 Получены медиа для задания {assignment_id} в админке: {media_data}")
    except Exception as e:
        print(f"⚠️ Ошибка получения медиа в админке: {e}")
        media_data = {'photos': [], 'audios': [], 'video_url': None}

    if content_text:
        await send_long_message(update, content_text, "**Задание:**")

    # ★ ИСПРАВЛЕНО: Показываем медиа задания в админке
    # 1. Фото задания (если есть и не пустой список)
    if media_data and media_data.get('photos'):
        photos = media_data['photos']
        if isinstance(photos, list) and photos:
            for i, photo_id in enumerate(photos[:3], 1):
                try:
                    await update.message.reply_photo(
                        photo=photo_id,
                        caption=f"🖼️ Фото {i} к заданию"
                    )
                except Exception as e:
                    print(f"🚨 Ошибка отправки фото {i} в админке: {e}")

    # 2. Аудио задания (если есть и не пустой список)
    if media_data and media_data.get('audios'):
        audios = media_data['audios']
        if isinstance(audios, list) and audios:
            for i, audio_id in enumerate(audios[:2], 1):
                try:
                    await update.message.reply_audio(
                        audio=audio_id,
                        caption=f"🎵 Аудио {i} к заданию"
                    )
                except Exception as e:
                    print(f"🚨 Ошибка отправки аудио {i} в админке: {e}")

    # 3. Видео задания (если есть и не пустая ссылка)
    if media_data and media_data.get('video_url'):
        video_url = media_data['video_url']
        if video_url and video_url.strip():
            video_msg = "🎬 **Видео к заданию:**\n"
            video_msg += f"{video_url}"
            await update.message.reply_text(video_msg, parse_mode='Markdown')

    if answer_text:
        await send_long_message(update, answer_text, "**Ответ участника:**")
    
    if answer_files:
        try:
            files_list = json.loads(answer_files)
            for i, file_id in enumerate(files_list, 1):
                try:
                    await update.message.reply_photo(
                        photo=file_id,
                        caption=f"📎 Фото {i} от участника"
                    )
                except Exception as photo_error:
                    try:
                        await update.message.reply_document(
                            document=file_id,
                            caption=f"📎 Фото {i} от участника"
                        )
                    except Exception as doc_error:
                        print(f"🚨 Ошибка отправки файла: {doc_error}")
                        
        except Exception as e:
            print(f"🚨 Ошибка загрузки фото: {e}")

    if teacher_comment and teacher_comment.strip():
        message += f"**💬 Комментарий психолога:** {teacher_comment}\n\n"
    else:
        message += "**💬 Комментарий психолога:** не оставлен\n\n"
    
    keyboard = [
        ["🔙 Назад к заданиям"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['current_assignment_id'] = assignment_id

    view_mode = context.user_data.get('view_mode', 'new')
    print(f"🚨 [DEBUG] view_mode={view_mode}, status={status}")
    
    if view_mode == 'approved' or status == 'approved':
        # Для принятых заданий - не запрашиваем комментарий
        keyboard = [["🔙 Назад к заданиям"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ **Задание уже принято.**\n\n"
            "Комментарий психолога был оставлен ранее.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    keyboard = [["🔙 Вернуться в меню проверки"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "💬 **Оставьте обязательный комментарий к выполненному заданию:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    context.user_data['waiting_for_comment'] = True


async def finish_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает принятие задания с комментарием"""

async def submit_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # Проверка доступности дня (оставляем как есть)
    day_id = context.user_data.get('current_day_id')
    if day_id:
        from database import is_day_available_for_user
        if not is_day_available_for_user(user_id, day_id):
            await update.message.reply_text(
                f"⏰ **Время выполнения истекло!**\n\n"
                "Этот день уже закрыт для выполнения заданий.\n"
                "Задания должны быть выполнены до установленного времени.\n\n"
                "Этот день будет отмечен как пропущенный.",
                parse_mode='Markdown'
            )
            from database import mark_day_as_skipped
            mark_day_as_skipped(user_id, day_id)
            return
    
    assignment_id = context.user_data.get('current_assignment_id')
    
    if not assignment_id:
        await update.message.reply_text("❌ Ошибка: задание не выбрано")
        return

    answer_type = context.user_data.get('answer_type', 'Фото_и_текст')
    answer_text = context.user_data.get('answer_text')
    answer_files = context.user_data.get('answer_files', [])
    questions = context.user_data.get('questions', [])
    
    # Проверки на наличие ответа (оставляем как есть)
    if answer_type == 'Только_фото':
        if not answer_files:
            await update.message.reply_text(
                "❌ **Нельзя отправить задание!**\n\n"
                "Вы выбрали вариант 'Только фото'.\n"
                "Пожалуйста, отправьте хотя бы одно фото.",
                parse_mode='Markdown'
            )
            return
    
    elif answer_type == 'Только_текст':
        if not answer_text:
            await update.message.reply_text(
                "❌ **Нельзя отправить задание!**\n\n"
                "Вы выбрали вариант 'Только текст'.\n"
                "Пожалуйста, напишите текстовый ответ.",
                parse_mode='Markdown'
            )
            return
    
    elif answer_type == 'Фото_и_текст':
        if not answer_text or not answer_files:
            await update.message.reply_text(
                "❌ **Нельзя отправить задание!**\n\n"
                "Для варианта 'Фото и текст' нужны:\n"
                "• Текстовый ответ\n"  
                "• Хотя бы одно фото\n\n"
                "Дополните ответ и попробуйте снова.",
                parse_mode='Markdown'
            )
            return
    
    # Формируем полный ответ с вопросами
    full_answer = answer_text or "Ответ не содержит текста."
    if questions:
        full_answer += "\n\n**Вопросы:**\n" + "\n".join(f"- " + q for q in questions)
    
    # ⭐ ИЗМЕНЕНИЕ: сразу ставим статус 'approved' вместо 'submitted'
    from database import save_assignment_answer_with_day_auto_approve
    save_assignment_answer_with_day_auto_approve(
        user_id=user_id,
        assignment_id=assignment_id,
        day_id=day_id,
        answer_text=full_answer,
        answer_files=answer_files
    )
    
    # Очищаем данные
    context.user_data['answering'] = False
    context.user_data['answer_type'] = None
    context.user_data['answer_text'] = None
    context.user_data['answer_files'] = []
    context.user_data['questions'] = []
    
    # ⭐ ИЗМЕНЕНИЕ: сообщение об автоматическом принятии
    await update.message.reply_text(
        "🎉 **Задание принято автоматически!**\n\n"
        f"**Тип ответа:** {answer_type.replace('_', ' ').title()}\n"
        "✅ Ваш ответ сохранен и принят. У психолога есть возможность просмотреть все ваши ответы на задания.\n\n"
        "**📋 Задание завершено!**\n"
        "После завершения задания в него нельзя внести изменения.\n\n"
        "**💬 Если есть вопросы:**\n"
        "Вы можете проконсультироваться с психологом в разделе 'Личная консультация'.\n\n"
        "**📚 Чтобы посмотреть ваши ответы:**\n"
        "Перейдите в раздел 'Архив заданий' → 'Завершенные задания'",
        parse_mode='Markdown'
    )
    
    await my_assignments_menu(update, context)

async def show_approved_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
async def show_student_part_approved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ВСЕ принятые задания участника в выбранной части"""
    
async def show_assignment_approved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает принятое задание с комментарием психолога"""

async def show_approved_assignment_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает принятое задание (упрощенная версия для новой структуры)"""
    text = update.message.text
    print(f"🚨 [1] show_approved_assignment_simple: text='{text}'")
    
    # Парсим кнопку "✅ Задание X (День Y)"
    assignment_title = text[2:].strip()  # Убираем "✅ "
    
    # Извлекаем день из скобок
    day_title = None
    if "(" in assignment_title and ")" in assignment_title:
        import re
        match = re.search(r'\((.*?)\)', assignment_title)
        if match:
            day_title = match.group(1).strip()
            assignment_title = assignment_title.split("(")[0].strip()
    
    print(f"🚨 [2] assignment_title='{assignment_title}', day_title='{day_title}'")
    
    student_id = context.user_data.get('current_student_id')
    arc_id = context.user_data.get('current_arc_id')
    
    if not student_id or not arc_id:
        await update.message.reply_text("❌ Ошибка: данные не найдены")
        return
    
    # Получаем day_id
    from database import get_day_id_by_title_and_arc
    day_id = get_day_id_by_title_and_arc(day_title, arc_id)
    
    if not day_id:
        await update.message.reply_text(f"❌ День '{day_title}' не найден")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Ищем задание
    cursor.execute('''  
        SELECT a.assignment_id, a.content_text, 
               upa.answer_text, upa.answer_files, upa.teacher_comment,
               u.fio, u.username
        FROM assignments a
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        JOIN users u ON upa.user_id = u.user_id
        WHERE a.title = ? AND upa.user_id = ? AND a.day_id = ? AND upa.status = 'approved'
    ''', (assignment_title, student_id, day_id))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ Задание не найдено")
        return
    
    assignment_id, content_text, answer_text, answer_files, teacher_comment, fio, username = result
    
    # Формируем заголовок
    display_name = fio if fio else username
    header = f"**✅ Принятое задание: {assignment_title}**\n\n"
    header += f"**👤 Участник:** {display_name}\n"
    header += f"**📅 День:** {day_title}\n\n"
    
    # Отправляем заголовок
    await update.message.reply_text(header, parse_mode='Markdown')
    
    # 1. Отправляем текст задания (если есть)
    if content_text:
        await send_long_message(
            update, 
            content_text, 
            prefix="**📝 Задание:**",
            parse_mode='Markdown'
        )
    
    # 2. Отправляем ответ участника (если есть)
    if answer_text:
        await send_long_message(
            update,
            answer_text,
            prefix="**📋 Ответ участника:**",
            parse_mode='Markdown'
        )
    
    # 3. Отправляем комментарий психолога (если есть)
    if teacher_comment and teacher_comment.strip():
        await send_long_message(
            update,
            teacher_comment,
            prefix="**💬 Комментарий психолога:**",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "**💬 Комментарий психолога:** не оставлен\n",
            parse_mode='Markdown'
        )
    
    # 4. Отправляем фото если есть
    if answer_files:
        try:
            files_list = json.loads(answer_files)
            for i, file_id in enumerate(files_list, 1):
                try:
                    await update.message.reply_photo(
                        photo=file_id,
                        caption=f"📎 Фото {i} от участника"
                    )
                except Exception as photo_error:
                    try:
                        await update.message.reply_document(
                            document=file_id,
                            caption=f"📎 Файл {i} от участника"
                        )
                    except Exception as doc_error:
                        print(f"🚨 Ошибка отправки файла {i}: {doc_error}")
        except Exception as e:
            print(f"🚨 Ошибка загрузки файлов: {e}")
    
    # 5. Итоговое сообщение
    keyboard = [["🔙 Назад к списку участников"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "✅ **Задание принято**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_feedback_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_section'] = 'feedback'
    context.user_data['in_feedback_mode'] = True
    """Показывает задания с обратной связью, сгруппированные по разделам"""
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT ar.arc_id, ar.title,
               COUNT(CASE WHEN upa.viewed_by_student = 0 THEN 1 END) as new_count,
               COUNT(*) as total_count
        FROM arcs ar
        JOIN days d ON ar.arc_id = d.arc_id
        JOIN assignments a ON d.day_id = a.day_id
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        WHERE upa.user_id = ? AND upa.status = 'approved' AND upa.teacher_comment IS NOT NULL
        GROUP BY ar.arc_id
        ORDER BY ar.order_num
    ''', (user_id,))
    
    arcs = cursor.fetchall()
    conn.close()
    
    if not arcs:
        await update.message.reply_text("📝 Пока нет обратной связи по заданиям.")
        return
    
    keyboard = []
    total_new = 0
    
    for arc_id, arc_title, new_count, total_count in arcs:
        status_icon = "🟡" if new_count > 0 else "🔄"
        if new_count > 0:
            total_new += new_count
            
        btn_text = f"{status_icon} {arc_title} ({new_count}/{total_count})"
        keyboard.append([btn_text])
    
    keyboard.append(["🔙 Назад к заданиям"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    message = f"💬 **Обратная связь по заданиям**"
    if total_new > 0:
        message += f"\n\n🟡 **У вас {total_new} новых комментариев!**"
    
    message += "\n\nВыберите раздел:"
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def request_personal_consultation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос личной консультации - обновленная"""

async def start_fio_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['waiting_for_fio'] = True
    await update.message.reply_text("📝 Введите ваше ФИО:")

async def show_course_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Детали тренинга и список частей"""

def get_course_arcs(course_title):
    """Получает часть тренинга с проверкой доступности по датам"""

async def show_course_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Страница 'Купить доступ' - показывает все части с датами"""

async def contact_psychologist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переход к психологу с inline-кнопкой"""

def get_current_arc():
    """ОРИГИНАЛЬНАЯ версия с исправлением проблемы раздела 0"""

async def check_daily_openings(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и открывает новые дни в 06:00 местного времени"""

async def reload_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полная перезагрузка данных из Excel"""
    if update.message.from_user.id == ADMIN_ID:
        await update.message.reply_text("🔄 Начинаю ПОЛНУЮ перезагрузку из Excel...")
        
        from database import reload_full_from_excel
        success = reload_full_from_excel()
        
        if success:
            await update.message.reply_text(
                "✅ **ПОЛНАЯ ПЕРЕЗАГРУЗКА ЗАВЕРШЕНА!**\n\n"
                "Все данные тренингов обновлены из Excel файла.\n"
                "Пользователи и их прогресс сохранены.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Ошибка при перезагрузке")
    else:
        await update.message.reply_text("❌ Нет доступа")

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список частей для выбора статистики"""
    context.user_data['current_section'] = 'statistics_menu'
    user_id = update.message.from_user.id
    
    from database import get_user_active_arcs, get_current_arc_day
    
    # Получаем ВСЕ части пользователя (и активные, и завершенные)
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT a.arc_id, a.title, a.дата_начала, a.дата_окончания,
               CASE 
                   WHEN DATE('now') < a.дата_начала THEN 'future'
                   WHEN DATE('now') > a.дата_окончания THEN 'past' 
                   ELSE 'active'
               END as status
        FROM user_arc_access uaa
        JOIN arcs a ON uaa.arc_id = a.arc_id
        WHERE uaa.user_id = ?
        ORDER BY a.дата_начала DESC
    ''', (user_id,))
    
    user_arcs = cursor.fetchall()
    conn.close()
    
    if not user_arcs:
        await update.message.reply_text(
            "📊 **У вас пока нет доступа к частям тренинга.**\n\n"
            "Приобретите доступ в разделе 'Купить тренинг'.",
            parse_mode='Markdown'
        )
        return
    
    # Формируем клавиатуру
    keyboard = []
    
    for arc_id, arc_title, arc_start, arc_end, status in user_arcs:
        # Определяем эмодзи и текст для кнопки
        if status == 'active':
            emoji = "🔄"
            status_text = "идёт сейчас"
        elif status == 'future':
            emoji = "⏳"
            status_text = "начнётся"
        else:
            emoji = "✅"
            status_text = "завершена"
        
        # Форматируем дату начала
        if isinstance(arc_start, str):
            start_date = arc_start.split()[0] if ' ' in arc_start else arc_start
        else:
            start_date = str(arc_start)
        
        # Создаем текст кнопки
        btn_text = f"{emoji} {arc_title}"
        keyboard.append([btn_text])
        
        # Сохраняем mapping для обработки
        if 'statistics_arc_map' not in context.user_data:
            context.user_data['statistics_arc_map'] = {}
        
        context.user_data['statistics_arc_map'][btn_text] = {
            'arc_id': arc_id,
            'arc_title': arc_title,
            'status': status,
            'start_date': start_date
        }
    
    keyboard.append(["📚 Мои задания"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Формируем сообщение
    message = "📊 **МОЙ ПРОГРЕСС**\n\n"
    message += "Выберите марафон(дату) для просмотра статистики:\n\n"
    
    # Добавляем пояснение по статусам
    message += "**Обозначения:**\n"
    message += "• 🔄 - Марафон идёт сейчас\n"
    message += "• ✅ - Марафон завершен\n\n"
    
    # Краткая сводка по всем частям
    active_count = sum(1 for _, _, _, _, status in user_arcs if status == 'active')
    future_count = sum(1 for _, _, _, _, status in user_arcs if status == 'future')
    past_count = sum(1 for _, _, _, _, status in user_arcs if status == 'past')
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_arc_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику по выбранной части - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Получаем данные о выбранной части
    arc_map = context.user_data.get('statistics_arc_map', {})
    arc_info = arc_map.get(text)
    
    if not arc_info:
        await update.message.reply_text("❌ Часть не найдена")
        return
    
    arc_id = arc_info['arc_id']
    arc_title = arc_info['arc_title']
    status = arc_info['status']
    start_date = arc_info['start_date']
    
    # ★★★ ДОБАВЬТЕ ПРАВИЛЬНОЕ ОПРЕДЕЛЕНИЕ СТАТУСА:
    from datetime import datetime
    
    try:
        # Парсим дату начала
        if isinstance(start_date, str):
            if ' ' in start_date:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S').date()
            else:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        else:
            start_date_obj = start_date
        
        today = datetime.now().date()
        
        # Получаем дату окончания из БД
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT дата_окончания FROM arcs WHERE arc_id = ?', (arc_id,))
        end_date_result = cursor.fetchone()
        conn.close()
        
        end_date_str = end_date_result[0] if end_date_result else None
        
        if end_date_str:
            if isinstance(end_date_str, str):
                if ' ' in end_date_str:
                    end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d %H:%M:%S').date()
                else:
                    end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            else:
                end_date_obj = end_date_str
            
            # Правильное определение статуса
            if today < start_date_obj:
                status = 'future'
            elif start_date_obj <= today <= end_date_obj:
                status = 'active'
            else:
                status = 'past'
        else:
            status = 'future'  # Если нет даты окончания
            
    except Exception as e:
        print(f"🚨 Ошибка определения статуса части: {e}")
        status = arc_info.get('status', 'unknown')
    
    # Теперь используем ПРАВИЛЬНЫЙ статус
    message = f"📊 **СТАТИСТИКА: {arc_title}**\n\n"
    
    if status == 'active':
        message += f"🔄 **Статус:** Часть идёт сейчас\n"
    
    stats = None
    try:
        # Альтернатива: вызываем через существующий импорт в начале файла
        from database import get_user_skip_statistics
        stats = get_user_skip_statistics(user_id, arc_id)
    except Exception as e:
        print(f"⚠️ Ошибка получения статистики: {e}")
        stats = {
            'total_days': 0,
            'completed_days': 0,
            'skipped_days': 0,
            'streak_days': 0,
            'completion_rate': 0,
            'completed_assignments': 0,
            'skipped_assignments': 0,
            'skipped_list': [],
            'skipped_days_list': []
        }
    
    # Получаем текущий день для активной части
    current_day_info = None
    if status == 'active':
        try:
            from database import get_current_arc_day
            current_day_info = get_current_arc_day(user_id, arc_id)
        except Exception as e:
            print(f"⚠️ Ошибка получения текущего дня: {e}")
            current_day_info = None
    
    # Формируем сообщение
    message = f"📊 **МОЙ ПРОГРЕСС: {arc_title}**\n\n"
    
    # Информация о статусе части
    if status == 'active':
        message += f"Основные показатели:\n"
        if current_day_info and 'day_number' in current_day_info:
            message += f"**Текущий день:** {current_day_info['day_number']} из 28\n"
    elif status == 'future':
        message += f"**Статус:** Начнётся {start_date}\n"
    else:
        message += f"**Статус:** Марафон завершена\n"
    
    message += f"**Дата начала:** {start_date}\n\n"
    
    # Статистика выполнения (только для активных и завершенных частей)
    if status in ['active', 'past'] and stats:
        # ★ БЕЗОПАСНЫЙ ДОСТУП К ДАННЫМ
        completed_assignments = stats.get('completed_assignments', 0)
        skipped_assignments = stats.get('skipped_assignments', 0)
        skipped_list = stats.get('skipped_list', [])
        streak_days = stats.get('streak_days', 0)
        completion_rate = stats.get('completion_rate', 0)

        message += "**Статистика заданий:**\n"
        message += f"• **Всего:** 28 заданий\n"
        message += f"• **Выполнено:** {completed_assignments}\n"
        message += f"• Процент выполнения: {completion_rate}%\n"

        # Пропущенные задания
        if skipped_assignments > 0 and skipped_list:
            message += f"📋 **Пропущенные задания:**\n"
            for i, skipped in enumerate(skipped_list[:10], 1):
                # ★ БЕЗОПАСНЫЙ ДОСТУП К assignment
                assignment_name = skipped.get('assignment', f'Задание {i}')
                message += f"{assignment_name}\n"
            
            if skipped_assignments > 10:
                message += f"... и еще {skipped_assignments - 10} заданий\n"
        else:
            message += "**• Пропущенных заданий нет!**\n"
        
        if streak_days > 0:
            message += f"• Серия выполнения: {streak_days} дней подряд\n"
        
        message += "\n"
        
        # Пропущенные дни
        skipped_days_list = stats.get('skipped_days_list', [])
        if skipped_days_list:
            message += "📋 **Пропущенные дни:**\n"
            for day_title in skipped_days_list[:5]:
                message += f"• {day_title}\n"
            if len(skipped_days_list) > 5:
                message += f"• ... и ещё {len(skipped_days_list) - 5} дней\n"
            message += "\n"
    
    # Статистика по заданиям (если часть активна или завершена)
    if status in ['active', 'past']:
        conn = None
        try:
            conn = sqlite3.connect('mentor_bot.db')
            cursor = conn.cursor()
            
            # Считаем задания
            cursor.execute('''
                SELECT 
                    COUNT(DISTINCT a.assignment_id) as total_assignments,
                    SUM(CASE WHEN upa.status IN ('submitted', 'approved') THEN 1 ELSE 0 END) as completed_assignments,
                    SUM(CASE WHEN upa.status = 'submitted' THEN 1 ELSE 0 END) as in_progress_assignments,
                    SUM(CASE WHEN upa.status = 'approved' THEN 1 ELSE 0 END) as approved_assignments
                FROM assignments a
                JOIN days d ON a.day_id = d.day_id
                LEFT JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id AND upa.user_id = ?
                WHERE d.arc_id = ?
            ''', (user_id, arc_id))
            
            result = cursor.fetchone()
            
            if result:
                total_assignments, completed, in_progress, approved = result
                if total_assignments and total_assignments > 0:
                    completion_percent = int((completed / total_assignments) * 100) if completed else 0
                    
                    message += "**Дополнительная статистика:**\n"
                    message += f"• На проверке: {in_progress or 0}\n"
                    message += f"• Проверено: {approved or 0}\n\n"
                    
        except Exception as e:
            print(f"⚠️ Ошибка SQL запроса в статистике: {e}")
        finally:
            if conn:
                conn.close()
    
    # Рекомендации в зависимости от статуса
    if status == 'future':
        message += "💡 **Рекомендация:**\n"
        message += f"Часть начнётся {start_date}. Подготовьтесь к началу!\n"
    elif status == 'active':
        if stats and stats.get('completion_rate', 0) < 70:
            message += "💡 **Рекомендация:**\n"
            message += "Старайтесь выполнять задания регулярно для лучшего результата!\n"
        else:
            message += "💡 **Рекомендация:**\n"
            message += "Отличный прогресс! Продолжайте в том же духе!\n"
    elif status == 'past':
        if stats and stats.get('completion_rate', 0) >= 80:
            message += "🎉 **Поздравляем!**\n"
            message += "Вы успешно завершили эту часть тренинга!\n"
        else:
            message += "💡 **Рекомендация:**\n"
            message += "В следующей части постарайтесь выполнять больше заданий!\n"
    
    # Клавиатура
    keyboard = [
        ["📊 К выбору марафона"],
        ["📚 Мои задания"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # ★ БЕЗОПАСНАЯ ОТПРАВКА
    try:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"🚨 Ошибка отправки сообщения со статистикой: {e}")
        # Альтернатива без форматирования
        safe_message = message.replace('*', '').replace('_', '')
        await update.message.reply_text(
            safe_message[:4000],
            reply_markup=reply_markup
        )
        
async def manage_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление доступом - список пользователей"""

async def show_user_arcs_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает доступы пользователя с inline-кнопками И список пользователей"""

async def handle_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия inline-кнопок управления доступом"""

async def show_user_arcs_access_callback(query, context, user_id):
    """Обновляет сообщение с inline-клавиатурой"""

async def show_users_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список участников для просмотра статистики"""

async def show_admin_user_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора части для просмотра статистики пользователя (админ)"""
    text = update.message.text
    
    user_mapping = context.user_data.get('admin_stats_users', {})
    user_info = user_mapping.get(text)
    
    if not user_info:
        await update.message.reply_text("❌ Пользователь не найден")
        return
    
    user_id = user_info['user_id']
    display_name = user_info['display_name']
    
    # Получаем части пользователя
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT a.arc_id, a.title, a.дата_начала, a.дата_окончания,
               CASE 
                   WHEN DATE('now') < a.дата_начала THEN 'future'
                   WHEN DATE('now') > a.дата_окончания THEN 'past' 
                   ELSE 'active'
               END as status
        FROM user_arc_access uaa
        JOIN arcs a ON uaa.arc_id = a.arc_id
        WHERE uaa.user_id = ?
        ORDER BY a.дата_начала DESC
    ''', (user_id,))
    
    user_arcs = cursor.fetchall()
    conn.close()
    
    if not user_arcs:
        await update.message.reply_text(f"❌ У пользователя {display_name} нет доступа к частям")
        return
    
    # Сохраняем данные пользователя
    context.user_data['admin_current_user'] = {
        'user_id': user_id,
        'display_name': display_name
    }
    
    # Формируем клавиатуру
    keyboard = []
    arc_mapping = {}
    
    for arc_id, arc_title, arc_start, arc_end, status in user_arcs:
        # Определяем эмодзи
        if status == 'active':
            emoji = "🔄"
        elif status == 'future':
            emoji = "⏳"
        else:
            emoji = "✅"
        
        btn_text = f"{emoji} {arc_title}"
        keyboard.append([btn_text])
        
        arc_mapping[btn_text] = {
            'arc_id': arc_id,
            'arc_title': arc_title,
            'status': status
        }
    
    keyboard.append(["👤 Выбрать другого участника"])
    keyboard.append(["🔙 Назад к проверке"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['admin_user_arcs_map'] = arc_mapping
    
    message = f"👤 **Статистика участника:** {display_name}\n\n"
    message += "Выберите марафон для просмотра статистики:\n\n"
    message += "**Обозначения:**\n"
    message += "• 🔄 - часть идёт сейчас\n"
    message += "• ⏳ - часть начнётся в будущем\n"
    message += "• ✅ - часть завершена\n"
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_admin_arc_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детальную статистику пользователя по выбранной части (админ)"""
    text = update.message.text
    
    # Получаем данные пользователя
    user_info = context.user_data.get('admin_current_user')
    if not user_info:
        await update.message.reply_text("❌ Ошибка: пользователь не выбран")
        return
    
    user_id = user_info['user_id']
    display_name = user_info['display_name']
    
    # Получаем данные части
    arc_mapping = context.user_data.get('admin_user_arcs_map', {})
    arc_info = arc_mapping.get(text)
    
    if not arc_info:
        await update.message.reply_text("❌ Часть не найдена")
        return
    
    arc_id = arc_info['arc_id']
    arc_title = arc_info['arc_title']
    status = arc_info['status']
    
    from database import get_user_skip_statistics, get_current_arc_day
    
    # Получаем статистику пропусков
    stats = get_user_skip_statistics(user_id, arc_id)
    
    # Получаем информацию о дне
    current_day_info = None
    if status == 'active':
        current_day_info = get_current_arc_day(user_id, arc_id)
    
    # Получаем детальную статистику по заданиям
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Общая статистика по части
    cursor.execute('''
        SELECT 
            COUNT(DISTINCT d.day_id) as total_days,
            COUNT(DISTINCT a.assignment_id) as total_assignments
        FROM days d
        LEFT JOIN assignments a ON d.day_id = a.day_id
        WHERE d.arc_id = ?
    ''', (arc_id,))
    
    arc_stats = cursor.fetchone()
    total_days = arc_stats[0] if arc_stats else 0
    total_assignments = arc_stats[1] if arc_stats else 0
    
    # Статистика выполнения пользователем
    cursor.execute('''
        SELECT 
            COUNT(DISTINCT CASE WHEN upa.status IN ('submitted', 'approved') THEN d.order_num END) as completed_days,
            COUNT(CASE WHEN upa.status = 'submitted' THEN 1 END) as submitted_assignments,
            COUNT(CASE WHEN upa.status = 'approved' THEN 1 END) as approved_assignments,
            COUNT(CASE WHEN upa.status IS NULL THEN 1 END) as new_assignments,
            MIN(upa.submitted_at) as first_submission,
            MAX(upa.submitted_at) as last_submission
        FROM days d
        LEFT JOIN assignments a ON d.day_id = a.day_id
        LEFT JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id AND upa.user_id = ?
        WHERE d.arc_id = ?
    ''', (user_id, arc_id))
    
    user_stats = cursor.fetchone()
    
    # Статистика по дням
    cursor.execute('''
        SELECT d.order_num, d.title,
               COUNT(DISTINCT a.assignment_id) as total_day_assignments,
               COUNT(DISTINCT CASE WHEN upa.status IN ('submitted', 'approved') THEN a.assignment_id END) as completed_day_assignments
        FROM days d
        LEFT JOIN assignments a ON d.day_id = d.day_id
        LEFT JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id AND upa.user_id = ?
        WHERE d.arc_id = ?
        GROUP BY d.order_num, d.title
        ORDER BY d.order_num
    ''', (user_id, arc_id))
    
    days_stats = cursor.fetchall()
    
    conn.close()
    
    # Формируем сообщение
    message = f"📊 **СТАТИСТИКА (АДМИН)**\n\n"
    message += f"👤 **Участник:** {display_name}\n"
    message += f"🔄 **Часть:** {arc_title}\n"
    message += f"📊 **Статус:** {'Активна' if status == 'active' else 'Завершена' if status == 'past' else 'Будущая'}\n\n"
    
    if status == 'active' and current_day_info:
        message += f"📅 **Текущий день:** {current_day_info['day_number']} из {total_days}\n\n"
    
    # Общая статистика части
    message += "📈 **ОБЩАЯ СТАТИСТИКА ЧАСТИ**\n"
    message += f"• 📅 Всего дней: {total_days}\n"
    message += f"• 📝 Всего заданий: {total_assignments}\n\n"
    
    # Статистика пользователя
    if user_stats:
        completed_days, submitted, approved, new, first_sub, last_sub = user_stats
        
        message += "👤 **СТАТИСТИКА УЧАСТНИКА**\n"
        message += f"• ✅ Выполнено дней: {completed_days}/{total_days}\n"
        
        if total_assignments > 0:
            completed_total = submitted + approved
            completion_percent = int((completed_total / total_assignments) * 100)
            
            message += f"• 📝 Выполнено заданий: {completed_total}/{total_assignments} ({completion_percent}%)\n"
            message += f"  ├ 🟡 На проверке: {submitted}\n"
            message += f"  ├ 💬 Проверено: {approved}\n"
            message += f"  └ 🔵 Новых: {new}\n\n"
        
        if first_sub:
            message += f"• 🎯 Первая отправка: {first_sub[:10]}\n"
        if last_sub:
            message += f"• 🏁 Последняя отправка: {last_sub[:10]}\n"
        
        message += "\n"
    
    # Статистика из функции пропусков
    if stats:
        user_completed_days = stats.get('completed_days', 0)
        user_skipped_days = stats.get('skipped_days', 0)
        completion_rate = stats.get('completion_rate', 0)
        
        message += "📊 **СТАТИСТИКА ВЫПОЛНЕНИЯ**\n"
        message += f"• ✅ Выполнено дней: {user_completed_days}\n"
        message += f"• ❌ Пропущено дней: {user_skipped_days}\n"
        message += f"• 📊 Процент выполнения: {completion_rate}%\n\n"
        
        # Пропущенные дни
        skipped_list = stats.get('skipped_days_list', [])
        if skipped_list:
            message += "📋 **Пропущенные дни:**\n"
            for day_title in skipped_list[:10]:
                message += f"• {day_title}\n"
            if len(skipped_list) > 10:
                message += f"• ... и ещё {len(skipped_list) - 10} дней\n"
            message += "\n"
    
    # Детальная статистика по дням (первые 10)
    if days_stats:
        message += "📅 **СТАТИСТИКА ПО ДНЯМ (первые 10):**\n"
        for day_num, day_title, total_day, completed_day in days_stats[:10]:
            if total_day > 0:
                day_percent = int((completed_day / total_day) * 100) if total_day > 0 else 0
                status_icon = "✅" if completed_day == total_day else "🟡" if completed_day > 0 else "🔴"
                message += f"• {status_icon} День {day_num}: {completed_day}/{total_day} ({day_percent}%)\n"
        if len(days_stats) > 10:
            message += f"• ... и ещё {len(days_stats) - 10} дней\n"
        message += "\n"
    
    # Рекомендации для админа
    message += "💡 **АНАЛИЗ ДЛЯ АДМИНА:**\n"
    
    if status == 'active':
        if stats and stats.get('completion_rate', 0) < 50:
            message += "⚠️ Участник отстаёт от графика. Рекомендуется связаться с ним.\n"
        elif stats and stats.get('completion_rate', 0) > 80:
            message += "✅ Участник показывает хорошие результаты.\n"
        else:
            message += "📊 Участник в среднем темпе выполнения.\n"
    elif status == 'past':
        if stats and stats.get('completion_rate', 0) > 70:
            message += "🎉 Участник успешно завершил часть.\n"
        else:
            message += "📉 Участник завершил часть с низкой активностью.\n"
    
    # Клавиатура
    keyboard = [
        ["📊 Посмотреть другую часть этого участника"],
        ["👤 Выбрать другого участника"],
        ["🔙 Назад к проверке"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

def has_any_access(user_id):
    """Проверяет есть ли у пользователя доступ к любому разделу"""

async def go_to_community(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет inline-кнопку для перехода в сообщество"""

async def show_offer_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает оферту регистрации с inline-кнопкой"""

async def accept_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает принятие оферты - с ReplyKeyboardRemove"""

async def decline_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает отказ от оферты - с переходом в главное меню"""

async def decline_service_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает отказ от оферты - с переходом в главное меню"""

async def show_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список мероприятий тренинга"""

async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Расписание всего тренинга"""

async def show_service_offer_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает оферту на услуги с inline-кнопкой"""

async def accept_service_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Упрощенная версия - показывает кнопку для перехода"""

async def show_accepted_offers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список принятых оферт с ссылками"""
    
async def show_today_assignments_info(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    """Показывает информацию о заданиях на текущий день"""

async def show_quick_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает краткое руководство по работе с заданиями"""

async def start_photo_only_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает ответ ТОЛЬКО ФОТО"""
    context.user_data['answering'] = True
    context.user_data['answer_type'] = 'Только_фото'
    context.user_data['answer_text'] = None
    context.user_data['answer_files'] = []
    
    keyboard = [["🔙 Назад в меню заданий"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📷 **Отправьте фото для задания:**\n\n"
        "После отправки всех фото нажмите кнопку '✅ Отправить задание'.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def start_text_only_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает ответ ТОЛЬКО ТЕКСТ"""
    context.user_data['answering'] = True
    context.user_data['answer_type'] = 'Только_текст'
    context.user_data['answer_text'] = None
    context.user_data['answer_files'] = []
    
    keyboard = [["🔙 Назад в меню заданий"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 **Напишите текстовый ответ на задание:**\n\n"
        "После написания текста нажмите кнопку '✅ Отправить задание'.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def start_photo_text_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает ответ ФОТО + ТЕКСТ (старый вариант)"""
    context.user_data['answering'] = True
    context.user_data['answer_type'] = 'Фото_и_текст'
    context.user_data['answer_text'] = None
    context.user_data['answer_files'] = []
    context.user_data['questions'] = []
    
    keyboard = [["🔙 Назад в меню заданий"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 **Напишите текстовый ответ на задание:**\n\n"
        "После текста нужно будет прикрепить фото и затем нажмите кнопку '✅ Отправить задание' .",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_submit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает кнопки отправки с возможностью задать вопрос"""

async def ask_question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает добавление вопроса к заданию"""

async def show_training_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о тренинге или фестивале"""

async def send_scheduled_notifications(context: ContextTypes.DEFAULT_TYPE):
    """Отправка запланированных уведомлений"""

async def buy_arc_with_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE, trial=False):
    """Покупка доступа через Юкассу с улучшенной обработкой"""

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет статус платежа - ОБНОВЛЕННАЯ"""

async def send_long_message(update, text, prefix="", parse_mode='Markdown'):
    """Отправляет длинное сообщение частями"""
    if not text:
        return
    
    # Очищаем текст от проблемных Markdown символов
    if parse_mode == 'Markdown':
        text = clean_markdown_text(text)
    
    if prefix:
        # Очищаем и префикс
        clean_prefix = clean_markdown_text(prefix)
        full_text = f"{clean_prefix}\n\n{text}"
    else:
        full_text = text
    
    parts = split_message(full_text)
    
    for i, part in enumerate(parts):
        try:
            safe_part = part[:4090]
            
            if i > 0:
                safe_part = f"📋 (продолжение {i+1}/{len(parts)}):\n\n{safe_part}"
            
            await update.message.reply_text(safe_part, parse_mode=parse_mode)
        except Exception as e:
            print(f"🚨 Ошибка в send_long_message часть {i}: {e}")
            # Пробуем без форматирования
            try:
                await update.message.reply_text(part[:4000], parse_mode=None)
            except:
                await update.message.reply_text("❌ Не удалось отобразить текст")

def clean_markdown_text(text):
    """Очищает текст от проблемных Markdown символов, но сохраняет корректное форматирование"""

async def show_seminar_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детали выбранного семинара"""

async def show_assignment_from_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text
    
    assignment_text = text.replace("📝 ", "").strip()

    if " (" in assignment_text:
        assignment_title = assignment_text.split(" (")[0].strip()
    else:
        assignment_title = assignment_text

    available_assignments = context.user_data.get('available_assignments', {}).get('assignments', [])
    
    selected_assignment = None
    for assignment in available_assignments:
        if assignment['title'] == assignment_title:
            selected_assignment = assignment
            break
    
    if not selected_assignment:
        await update.message.reply_text("❌ Задание не найдено")
        return
    
    assignment_id = selected_assignment['assignment_id']
    day_id = None
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT day_id FROM assignments WHERE assignment_id = ?', (assignment_id,))
    result = cursor.fetchone()
    if result:
        day_id = result[0]
    conn.close()
    
    if not day_id:
        await update.message.reply_text("❌ Ошибка: день задания не найден")
        return
    
    from database import check_assignment_status
    status = check_assignment_status(user_id, assignment_id)
    
    if status == 'submitted':
        await update.message.reply_text(
            "🟡 **Это задание уже на проверке!**\n\n"
            "Ждите ответа психолога в разделе 'Ответ психолога'.",
            parse_mode='Markdown'
        )
        return
    
    if status == 'approved':
        await update.message.reply_text(
            "✅ **Это задание уже проверено!**\n\n"
            "Ответ психолога доступен в разделе 'Ответ психолога'.",
            parse_mode='Markdown'
        )
        return
    
    context.user_data['current_assignment'] = assignment_title
    context.user_data['current_assignment_id'] = assignment_id
    context.user_data['current_day_id'] = day_id
    context.user_data['answering'] = True
    context.user_data['answer_text'] = None
    context.user_data['answer_files'] = []
    context.user_data['questions'] = []
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT content_text, доступно_до
        FROM assignments 
        WHERE assignment_id = ?
    ''', (assignment_id,))
    result = cursor.fetchone()
    conn.close()
    
    content_text, available_until = result if result else (None, '22:00')
    
    header = f"**📝 {assignment_title}**\n\n"
    if available_until and available_until != '22:00':
        header += f"⏰ **Выполняя задание дня до:** {available_until}, вы сохраняете серию выполнений подряд\n\n"

    await update.message.reply_text(header, parse_mode='Markdown')

    if content_text:
        # ИСПРАВЛЕНИЕ ЗДЕСЬ: используем send_long_message вместо обрезки
        await send_long_message(
            update, 
            content_text, 
            prefix="📋 **Задание:**",
            parse_mode='Markdown'
        )

    choice_message = "**📤 Выберите вариант ответа:**"

    keyboard = [
        ["📷 Только фото"],
        ["📝 Только текст"], 
        ["📷+📝 Фото и текст"],
        ["🔙 Назад в меню заданий"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        choice_message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_in_progress_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает задания на проверке"""

async def show_feedback_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает части с ответами психолога"""
    # 🔥 ВАЖНО: УСТАНОВИТЬ current_section!
    context.user_data['current_section'] = 'feedback'
    
    user_id = update.message.from_user.id
    
    from database import get_arcs_with_feedback
    arcs = get_arcs_with_feedback(user_id)
    
    if not arcs:
        await update.message.reply_text(
            "📝 **Пока нет ответов психолога.**\n\n"
            "Как только психолог проверит ваши работы, они появятся здесь.",
            parse_mode='Markdown'
        )
        return
    
    # 🔥 ИНИЦИАЛИЗИРУЕМ mapping
    if 'feedback_arc_map' not in context.user_data:
        context.user_data['feedback_arc_map'] = {}
    
    keyboard = []
    for arc_id, arc_title, new_count, total_count in arcs:
        if new_count > 0:
            btn_text = f"📚 {arc_title} 🟡({new_count})"
        else:
            btn_text = f"📚 {arc_title} ({total_count})"
        keyboard.append([btn_text])
        
        # Сохраняем mapping
        context.user_data['feedback_arc_map'][btn_text] = arc_id
    
    keyboard.append(["📚 В раздел Мои задания"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "💬 **Ответ психолога**\n\n"
        "Выберите часть:\n"
        "🟡 - новые непросмотренные ответы",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_feedback_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает выбор типа ответов - ВСЕГДА ОБА ВАРИАНТА"""
    user_id = update.message.from_user.id
    arc_text = update.message.text
    
    print(f"🔍 show_feedback_type получен текст: '{arc_text}'")
    
    # 1. ОЧИЩАЕМ текст от эмодзи и счетчиков
    import re
    
    # Убираем эмодзи 📚
    clean_title = arc_text.replace("📚 ", "")
    
    # Убираем 🟡(X) или (X)
    clean_title = re.sub(r'\s*🟡\(\d+\)', '', clean_title)  # Убирает " 🟡(1)"
    clean_title = re.sub(r'\s*\(\d+\)', '', clean_title)    # Убирает " (3)"
    
    clean_title = clean_title.strip()
    
    print(f"🔍 Очищенное название: '{clean_title}'")
    
    # 2. ИЩЕМ часть в БД
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Вариант 1: Ищем точное совпадение
    cursor.execute('SELECT arc_id, title FROM arcs WHERE title = ?', (clean_title,))
    result = cursor.fetchone()
    
    # Вариант 2: Если не нашли, ищем по номеру
    if not result and "Часть" in clean_title:
        match = re.search(r'Часть\s*(\d+)', clean_title)
        if match:
            part_num = match.group(1)
            cursor.execute('SELECT arc_id, title FROM arcs WHERE title LIKE ?', (f'%{part_num}%',))
            result = cursor.fetchone()
    
    if not result:
        print(f"❌ Часть не найдена: '{clean_title}'")
        conn.close()
        
        # Показываем какие части есть
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT arc_id, title FROM arcs WHERE arc_id > 0')
        all_arcs = cursor.fetchall()
        conn.close()
        
        debug_msg = f"❌ Часть '{clean_title}' не найдена.\n\n**Доступные части:**\n"
        for arc_id, title in all_arcs:
            debug_msg += f"• {title}\n"
        
        await update.message.reply_text(debug_msg, parse_mode='Markdown')
        return
    
    arc_id, arc_title = result
    conn.close()
    
    print(f"✅ Найдена часть ID: {arc_id}, название: {arc_title}")
    
    # 3. СОХРАНЯЕМ в контекст
    context.user_data['current_feedback_arc'] = arc_id
    context.user_data['current_feedback_arc_title'] = arc_title
    context.user_data['current_section'] = 'feedback_type'
    
    # 4. ПОДСЧИТЫВАЕМ количество ответов
    from database import get_feedback_counts
    new_count, completed_count = get_feedback_counts(user_id, arc_id)
    
    print(f"📊 Статистика: новых={new_count}, завершенных={completed_count}")
    
    # 5. ФОРМИРУЕМ сообщение
    message = f"💬 **Ответы психолога**\n\n"
    message += f"**Часть:** {arc_title}\n\n"
    
    # Показываем статистику
    if new_count == 0 and completed_count == 0:
        message += "📭 **В этой части пока нет проверенных заданий.**\n\n"
    else:
        message += f"📊 **Статистика ответов:**\n"
        message += f"• 🟡 Новые ответы: {new_count}\n"
        message += f"• ✅ Завершенные задания: {completed_count}\n\n"
    
    message += "**Выберите раздел:**"
    
    # 6. СОЗДАЕМ клавиатуру - ВСЕГДА ОБЕ КНОПКИ!
    keyboard = []
    
    # 🔥 ВСЕГДА показываем обе кнопки, даже если count = 0
    keyboard.append(["🟡 Новые ответы"])
    keyboard.append(["✅ Завершенные задания"])
    
    keyboard.append(["🔙 Назад к частям"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # 7. ОТПРАВЛЯЕМ сообщение
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_feedback_list(update: Update, context: ContextTypes.DEFAULT_TYPE, viewed=0):
    """Показывает список заданий с ответами"""
    user_id = update.message.from_user.id
    arc_id = context.user_data.get('current_feedback_arc')
    arc_title = context.user_data.get('current_feedback_arc_title', f"Часть {arc_id}")
    
    if not arc_id:
        await update.message.reply_text("❌ Ошибка: часть не выбрана")
        return
    
    # Получаем задания
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.title, d.title as day_title, d.order_num,
               upa.teacher_comment, upa.answer_text,
               a.assignment_id
        FROM assignments a
        JOIN days d ON a.day_id = d.day_id
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        WHERE upa.user_id = ? 
          AND upa.status = 'approved'
          AND upa.teacher_comment IS NOT NULL
          AND upa.viewed_by_student = ?
          AND d.arc_id = ?
        ORDER BY d.order_num, a.assignment_id
    ''', (user_id, viewed, arc_id))
    
    assignments = cursor.fetchall()
    conn.close()
    
    # 🔥 ЕСЛИ НЕТ ЗАДАНИЙ - показываем сообщение и возвращаем
    if not assignments:
        type_name = "новых ответов" if viewed == 0 else "завершенных заданий"
        
        await update.message.reply_text(
            f"📭 **Нет {type_name} в части '{arc_title}'.**\n\n"
            f"Выберите другой раздел:",
            parse_mode='Markdown'
        )
        
        # Возвращаем к выбору типа
        keyboard = [
            ["🟡 Новые ответы"],
            ["✅ Завершенные задания"],
            ["🔙 Назад к частям"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "👇 Выберите действие:",
            reply_markup=reply_markup
        )
        return
    
    # Формируем сообщение
    type_name = "🟡 НОВЫЕ ОТВЕТЫ" if viewed == 0 else "✅ ЗАВЕРШЕННЫЕ ЗАДАНИЯ"
    message = f"**{type_name}**\n\n"
    message += f"**Часть:** {arc_title}\n"
    message += f"**Найдено:** {len(assignments)} заданий\n\n"
    
    # Создаем клавиатуру с заданиями
    keyboard = []
    
    for i, (assignment_title, day_title, day_num, comment, answer, assignment_id) in enumerate(assignments):
        # Формируем текст кнопки
        clean_title = assignment_title
        if assignment_title and " - " in assignment_title:
            # Формат "День 4 - Задание 1" → "Задание 1"
            parts = assignment_title.split(" - ")
            if len(parts) == 2 and "День" in parts[0]:
                clean_title = parts[1]
    
        btn_text = f"📝 {clean_title}"
        keyboard.append([btn_text])
        
        # Сохраняем mapping для быстрого доступа
        if 'feedback_assignments_map' not in context.user_data:
            context.user_data['feedback_assignments_map'] = {}
        context.user_data['feedback_assignments_map'][btn_text] = {
            'assignment_id': assignment_id,
            'assignment_title': assignment_title,
            'day_title': day_title,
            'day_num': day_num,
            'viewed': viewed
        }
    
    #keyboard.append(["🔙 Назад к выбору типа"])
    keyboard.append(["🔙 Назад к частям","🔙 В главное меню"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    # Сохраняем текущий тип просмотра
    context.user_data['current_feedback_viewed'] = viewed

async def show_feedback_assignment_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детали ответа психолога на задание - ОБНОВЛЕННАЯ"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    assignment_data = context.user_data['feedback_assignments_map'].get(text)
    
    if not assignment_data:
        await update.message.reply_text("❌ Задание не найдено")
        return
    
    assignment_id = assignment_data['assignment_id']
    assignment_title = assignment_data['assignment_title']
    day_title = assignment_data['day_title']
    day_num = assignment_data['day_num']
    viewed = assignment_data['viewed']
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT upa.answer_text, upa.answer_files, upa.teacher_comment,
               a.content_text, upa.submitted_at
        FROM user_progress_advanced upa
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        WHERE upa.user_id = ? AND upa.assignment_id = ?
    ''', (user_id, assignment_id))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ Данные ответа не найдены")
        return
    
    answer_text, answer_files, teacher_comment, content_text, submitted_at = result
    
    if viewed == 0:
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE user_progress_advanced 
            SET viewed_by_student = 1 
            WHERE assignment_id = ? AND user_id = ?
        ''', (assignment_id, user_id))
        conn.commit()
        conn.close()
    
    # СОБИРАЕМ ВСЕ В ОДНО СООБЩЕНИЕ
    full_message = f"📝 {assignment_title}\n\n"
    
    if content_text:
        full_message += f"Задание:\n{content_text}\n\n"
    
    if answer_text:
        full_message += f"Ваш ответ:\n{answer_text}\n\n"
    
    if teacher_comment:
        full_message += f"💬 Ответ психолога:\n{teacher_comment}\n\n"
    
    full_message += f"📅 Отправлено: {submitted_at[:10] if submitted_at else 'Не указано'}"
    
    # СОХРАНЯЕМ ДАННЫЕ ДЛЯ КОНСУЛЬТАЦИИ
    context.user_data['current_feedback_data'] = {
        'title': assignment_title,
        'day': day_title,
        'day_num': day_num,
        'arc_title': context.user_data.get('current_feedback_arc_title', '')
    }
    
    # СОЗДАЕМ КЛАВИАТУРУ
    keyboard = []
    
    if viewed == 0:
        keyboard.append(["🟡 Новые ответы"])
    else:
        keyboard.append(["✅ Завершенные задания"])
    
    keyboard.append(["💬 Личная консультация"])
    keyboard.append(["🔙 В главное меню"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # ОТПРАВЛЯЕМ ФОТО ОТДЕЛЬНО (если есть)
    if answer_files:
        try:
            files_list = json.loads(answer_files)
            for i, file_id in enumerate(files_list[:3], 1):
                try:
                    await update.message.reply_photo(
                        photo=file_id,
                        caption=f"📎 Ваше фото {i}"
                    )
                except:
                    try:
                        await update.message.reply_document(
                            document=file_id,
                            caption=f"📎 Файл {i} от вас"
                        )
                    except:
                        await update.message.reply_text(f"📎 Фото {i} (не удалось загрузить)")
        except:
            pass
    
    # ОТПРАВЛЯЕМ ОСНОВНОЕ СООБЩЕНИЕ С КЛАВИАТУРОЙ
    if len(full_message) > 4000:
        # Разбиваем на части БЕЗ клавиатуры в середине
        parts = split_message(full_message)
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                # Последняя часть - с клавиатурой
                clean_part = clean_markdown_text(part)
                await update.message.reply_text(clean_part, reply_markup=reply_markup, parse_mode=None)  # ← Без Markdown!
            else:
                # Промежуточные части
                clean_part = clean_markdown_text(part)
                await update.message.reply_text(clean_part, parse_mode=None)  # ← Без Markdown!
    else:
        # Короткое сообщение
        clean_message = clean_markdown_text(full_message)
        await update.message.reply_text(clean_message, reply_markup=reply_markup, parse_mode=None)

async def show_training_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Каталог тренинга - сразу выбор: Всё о курсе / Купить доступ"""

def get_current_and_future_arcs():
    """Получает текущую и будущие дуги для покупки"""

async def buy_arc_from_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о части и предлагает купить (обновленная логика)"""

# Webhook обработчик для Юкассы
async def yookassa_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик webhook от Юкассы"""

async def check_payment_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки статуса платежей - ИСПРАВЛЕННАЯ"""

async def test_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки платежей"""

async def test_payment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тест платежа - создает платеж 100₽ для тестирования"""

async def check_db_structure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает структуру таблицы payments (упрощенная)"""

async def create_payments_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создает таблицу payments если её нет"""

async def show_tables(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список всех таблиц в БД"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        
        message = "🗂️ **Таблицы в базе данных:**\n\n"
        
        for table in tables:
            table_name = table[0]
            
            # Получаем количество записей
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                message += f"• `{table_name}` - {count} записей\n"
            except:
                message += f"• `{table_name}` - ошибка подсчета\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в show_tables: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()

async def test_payment_system(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полный тест платежной системы"""

async def recreate_payments_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересоздает таблицу payments с правильной структурой"""

async def test_yookassa_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестирует подключение к Юкассе - ИСПРАВЛЕННАЯ"""

async def check_my_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет доступы пользователя"""

async def debug_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последний платеж пользователя"""

async def debug_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все активные колбэки"""

async def simple_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Простой тест колбэка"""

async def fix_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Исправляет доступ для пользователя"""

async def check_tables(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и создает таблицы если нужно"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Проверяем user_arc_access
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_arc_access (
            user_id INTEGER,
            arc_id INTEGER,
            access_type TEXT,
            PRIMARY KEY (user_id, arc_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (arc_id) REFERENCES arcs(arc_id)
        )
    ''')
    
    # Проверяем trial_assignments_access
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trial_assignments_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            arc_id INTEGER,
            max_assignment_order INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (arc_id) REFERENCES arcs(arc_id),
            UNIQUE(user_id, arc_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Таблицы доступа проверены/созданы")

async def debug_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус регистрации"""

async def reset_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает регистрацию для тестирования"""

async def debug_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий статус регистрации и user_data"""

async def handle_notification_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает создание уведомления"""

async def process_notification_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает контент уведомления (текст + медиа)"""

async def send_notification_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет уведомление всем получателям"""

async def update_database_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ПОЛНОЕ обновление БД: создает все таблицы, добавляет колонки, сохраняет данные"""

async def check_migration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет готовность к миграции"""

async def verify_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет сохранность критичных данных"""

async def check_yookassa_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет авторизацию в Юкассе"""

async def debug_last_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последний платеж"""

async def webhook_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус webhook"""

def send_payment_notification(user_id, arc_title, amount, payment_id):
    """Отправляет уведомление пользователю об успешной оплате"""

async def manage_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление webhook (только для админа)"""

def start_yookassa_webhook_server():
    """Запускает сервер для вебхуков ЮKассы"""

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Логирует ошибки и отправляет уведомление администратору"""

async def tech_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню технической поддержки"""
    context.user_data['current_section'] = 'tech_support'
    
    keyboard = [
        ["💬 Написать в поддержку"],
        ["📖 Инструкции"],  
        ["👤 Автор тренинга"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🛠️ **Техническая поддержка**\n\n"
        "Выберите раздел:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает инструкции (пока в разработке)"""

async def show_author_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию об авторе тренинга (пока в разработке)"""

async def write_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переход в бот поддержки"""
      
def main():
    application = Application.builder().token(TOKEN).build()

    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(
            send_scheduled_notifications,
            interval=60,
            first=10
        )

    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(
            check_daily_openings,
            interval=3600,
            first=10
        )

    init_db()
    print("✅ База данных инициализирована")
    # ГАРАНТИРОВАННО создаем правильную таблицу payments
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Удаляем старую таблицу если у нее неправильная структура
        cursor.execute("PRAGMA table_info(payments)")
        columns = cursor.fetchall()
        
        if columns:
            column_names = [col[1] for col in columns]
            # Проверяем наличие ключевых колонок
            required_columns = ['arc_id', 'amount', 'status', 'yookassa_payment_id']
            
            if not all(col in column_names for col in required_columns):
                print("⚠️ Обнаружена таблица payments со старой структурой, пересоздаем...")
                cursor.execute("DROP TABLE IF EXISTS payments")
        
        # Создаем/пересоздаем таблицу
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
        
        # Индексы для производительности
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_yookassa_id ON payments(yookassa_payment_id)')
        
        conn.commit()
        print("✅ Таблица payments гарантированно создана с правильной структурой")
        
    except Exception as e:
        print(f"⚠️ Ошибка создания таблицы payments: {e}")
    finally:
        conn.close()
        
    upgrade_database()
    from database import test_new_structure
    test_new_structure()
    
    application.add_handler(CommandHandler("start", start))
    application.add_error_handler(error_handler)
    application.add_handler(CallbackQueryHandler(check_payment_callback, pattern='^check_payment_'))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))
    application.add_handler(CommandHandler("reloadfull", reload_full))
    application.add_handler(CallbackQueryHandler(handle_access_callback))
    application.add_handler(CommandHandler("payments", check_payment_status))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^{.*}$'), yookassa_webhook))
    application.add_handler(CommandHandler("testpay", test_payment_flow))
    application.add_handler(CommandHandler("paystruct", check_db_structure))
    application.add_handler(CommandHandler("createpaytable", create_payments_table))
    application.add_handler(CommandHandler("tables", show_tables))
    application.add_handler(CommandHandler("fixpayments", recreate_payments_table))
    application.add_handler(CommandHandler("testpayment", test_payment_system))
    application.add_handler(CommandHandler("testkeys", test_yookassa_keys))
    application.add_handler(CommandHandler("myaccess", check_my_access))
    application.add_handler(CommandHandler("debugpay", debug_payment))
    application.add_handler(CommandHandler("debugcb", debug_callback))
    application.add_handler(CommandHandler("simpletest", simple_test))
    application.add_handler(CommandHandler("fixaccess", fix_access))
    application.add_handler(CommandHandler("checktables", check_tables))
    application.add_handler(CommandHandler("debugreg", debug_registration))
    application.add_handler(CommandHandler("resetreg", reset_registration))
    application.add_handler(CommandHandler("debugflow", debug_flow))
    application.add_handler(CommandHandler("updatedb", update_database_full))
    application.add_handler(CommandHandler("checkmigrate", check_migration))
    application.add_handler(CommandHandler("verify", verify_data))
    application.add_handler(CommandHandler("checkauth", check_yookassa_auth))
    application.add_handler(CommandHandler("lastpay", debug_last_payment))
    application.add_handler(CommandHandler("whstatus", webhook_status))
    application.add_handler(CommandHandler("webhook", manage_webhook))
    
    
    #application.add_handler(MessageHandler(
        #filters.TEXT & filters.Regex(r'^yookassa'),
        #yookassa_sbp_webhook
    #))
    
    
    print("Бот запущен...")
    
    webhook_mode = any(arg in sys.argv for arg in ['--webhook', 'webhook', '--wh'])
    
    if webhook_mode:
        print("🚀 Запуск в режиме WEBHOOK")
        WEBHOOK_HOST = "svs365bot.ru"
        TOKEN_PATH = f"bot/{TOKEN}"
        WEBHOOK_URL = f"https://{WEBHOOK_HOST}/{TOKEN_PATH}"
        LISTEN_IP = "127.0.0.1"
        PORT = 8083
    
        try:
            # Просто запускаем webhook
            application.run_webhook(
                listen=LISTEN_IP,
                port=PORT,
                webhook_url=WEBHOOK_URL,
                drop_pending_updates=True,
            )
        except Exception as e:
            print(f"❌ Ошибка webhook: {e}")
            print("🔄 Переключаюсь на polling как fallback...")
            # Нужно создать новый event loop для polling
            import asyncio
            asyncio.set_event_loop(asyncio.new_event_loop())
            application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
