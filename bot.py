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
        "📈 Тестирование": testing_menu,
        "📈 Пройти тест": show_available_tests,  # Теперь это выбор марафона
        "📊 Мои результаты": lambda u, c: show_test_results(u, c),
        "📋 Показать все ответы": show_all_test_answers,
        "🔙 Назад к тестированию": testing_menu,
        "🔙 Выбрать другой марафон": lambda u, c: show_test_results(u, c),
        "🔙 Назад к результатам": lambda u, c: show_test_results(u, c),
        "📈 Пройти другой тест": show_available_tests,
        "🔙 Назад к тестам марафона": back_to_arc_tests,
        "🔙 Назад к результату": back_to_test_result,
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
        ["📖 Всё о марафоне"],
        ["💰 Купить доступ"],
        ["🔙 В главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📚 **{course_title}**\n\nВыберите марафон:",
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
    text = update.message.text
    student_data = context.user_data.get('current_student')
    
    if not student_data:
        await update.message.reply_text("❌ Сначала выбери участника")
        return
    
    if " - файл " in text:
        parts = text.split(" - файл ")
        assignment_title = parts[0][2:].strip()
        file_number = int(parts[1])
        print(f"🚨 DEBUG: assignment_title = '{assignment_title}', file_number = {file_number}")
    else:
        # Старый формат для обратной совместимости
        assignment_title = text[2:].strip()
        file_number = 1
    
    # Находим конкретный файл по номеру
    submissions = get_student_submissions(student_data['user_id'])
    target_file = None
    current_file_num = 0
    
    for submission in submissions:
        file_db_id, assignment_id, title, status, telegram_file_id, created_at = submission
        if title == assignment_title:
            current_file_num += 1
            if current_file_num == file_number:
                target_file = submission
                break
    
    if not target_file:
        await update.message.reply_text("❌ Файл не найден")
        return
    
    file_db_id, assignment_id, title, status, telegram_file_id, created_at = target_file
    
    # Отправляем файл психолога
    status_icon = "🆕" if status == 'submitted' else "✅"
    await context.bot.send_document(
        chat_id=update.message.chat_id,
        document=telegram_file_id,
        caption=f"📎 Файл от @{student_data['username']}\n"
                f"📝 Задание: {title}\n"
                f"📁 Файл: {file_number}\n"
                f"📊 Статус: {status} {status_icon}\n"
                f"📅 Дата: {created_at}"
    )
    
    # Кнопки для проверки (только для новых файлов)
    if status == 'submitted':
        keyboard = [
            ["✅ Принять этот файл", "❌ Вернуть этот файл"],
            ["🔙 Назад к файлам", "🔙 Назад к работам участника"]
        ]
    else:
        keyboard = [
            ["🔙 Назад к файлам", "🔙 Назад к работам участника"]
        ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Выбери действие для файла {file_number}:",
        reply_markup=reply_markup
    )
    
    # Сохраняем данные для обработки решения
    context.user_data['current_review'] = {
        'file_db_id': file_db_id,
        'user_id': student_data['user_id'],
        'assignment_id': assignment_id,
        'username': student_data['username'],
        'assignment_title': title,
        'file_number': file_number
    }


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
    text = update.message.text
    student_data = context.user_data.get('current_student')
    
    if not student_data:
        await update.message.reply_text("❌ Сначала выбери участника")
        return
    
    if text == "🔙 Назад к файлам":
        assignment_title = context.user_data.get('current_assignment_title')
    else:
        # Обычный вызов - извлекаем из текста кнопки
        assignment_title = text[2:].split(" (")[0].strip()
        context.user_data['current_assignment_title'] = assignment_title
    
    # Находим файлы для этого задания
    submissions = get_student_submissions(student_data['user_id'])
    
    keyboard = []
    file_counter = {}
    
    for file_db_id, assignment_id, title, status, telegram_file_id, created_at in submissions:
        
        if title == assignment_title:
            if title not in file_counter:
                file_counter[title] = 1
            else:
                file_counter[title] += 1
                
            file_number = file_counter[title]
            
            if status == 'submitted':
                status_icon = "🆕"
            elif status == 'approved':
                status_icon = "✅"
            elif status == 'rejected':
                status_icon = "❌"
            else:
                status_icon = "⏳"
            
            btn_text = f"{status_icon} {title} - файл {file_number}"
            keyboard.append([btn_text])
    
    if not keyboard:
        await update.message.reply_text("❌ В этом задании нет файлов")
        return
    
    keyboard.append(["🔙 Назад к заданиям"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📋 Файлы задания '{assignment_title}':\nВыбери файл:",
        reply_markup=reply_markup
    )


async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Личный кабинет пользователя - ОБНОВЛЕННЫЙ"""
    user_id = update.message.from_user.id
    
    from database import get_user_offer_status
    offer_status = get_user_offer_status(user_id)
    
    print(f"🔍 profile_menu: accepted={offer_status['accepted_offer']}, "
          f"has_phone={offer_status['has_phone']}, has_fio={offer_status['has_fio']}")
    
    # Если нет оферты - показываем оферту
    if not offer_status['accepted_offer']:
        await show_offer_agreement(update, context)
        return
    
    # Если оферта есть, но нет телефона - просим телефон
    if offer_status['accepted_offer'] and not offer_status['has_phone']:
        await request_phone_number(update, context)
        return
    
    # Если есть телефон, но нет ФИО - просим ФИО
    if offer_status['accepted_offer'] and offer_status['has_phone'] and not offer_status['has_fio']:
        await request_fio_number(update, context)
        return
    
    # Только если ВСЁ есть - показываем профиль
    keyboard = [
        ["👤 Изменить ФИО"],
        ["⏰ Часовой пояс"],
        ["📋 Принятые оферты"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT fio, city, timezone_offset, phone FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    fio = result[0] if result and result[0] else "Не указано"
    city = result[1] if result and result[1] else "Не выбран"
    timezone_offset = result[2] if result and result[2] is not None else 0
    phone = result[3] if result and result[3] else "Не указан"
    
    if timezone_offset > 0:
        timezone_display = f"+{timezone_offset} часа от МСК"
    elif timezone_offset < 0:
        timezone_display = f"{timezone_offset} часа от МСК"
    else:
        timezone_display = "МСК (0)"
    
    await update.message.reply_text(
        f"👤 **Личный кабинет**\n\n"
        f"**ФИО:** {fio}\n"
        f"**Часовой пояс:** {timezone_display}\n"
        f"**Телефон:** {phone}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def request_fio_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просит ввести ФИО если его нет"""
    await update.message.reply_text(
        "📝 **Для завершения регистрации введите ваше ФИО:**\n\n"
        "Обязательно имя и фамилия (минимум 2 слова).\n"
        "**Примеры:**\n"
        "• Иванов Иван\n"
        "• Анна Петрова\n"
        "• Мария Сергеевна",
        parse_mode='Markdown'
    )
    
    context.user_data['waiting_for_fio'] = True
    
async def select_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор часового пояса"""
    from database import get_available_cities
    
    cities = get_available_cities()
    keyboard = []
    
    for i in range(0, len(cities), 2):
        row = cities[i:i+2]
        keyboard.append(row)
    
    keyboard.append(["🔙 Назад в кабинет"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "⏰ **Выберите ваш часовой пояс:**\n\n"
        "Цифра в скобках показывает разницу с Москвой:\n"
        "• Москва (+0) - ваш часовой пояс как в Москве\n"  
        "• Екатеринбург (+2) - на 2 часа ahead Москвы\n\n"
        "Это нужно для правильного отсчета времени выполнения заданий и отправки личных уведомлений.\n",
        reply_markup=reply_markup
    )

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
        ["📊 Мой прогресс", "📈 Тестирование"],
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
    if not answer_type:
        answer_type = update.message.text
    
    context.user_data['answer_type'] = answer_type
    
    if answer_type == "📷 Только фото":
        await update.message.reply_text(
            "📷 **Отправьте фото для задания:**\n\n"
            "Прикрепите одно или несколько фото.",
            parse_mode='Markdown'
        )
        context.user_data['waiting_for_photo'] = True
        
    elif answer_type == "📝 Только текст":
        await update.message.reply_text(
            "📝 **Напишите текстовый ответ:**\n\n"
            "Опишите свои мысли, чувства или выполнение упражнения.",
            parse_mode='Markdown'
        )
        context.user_data['waiting_for_text'] = True
        
    elif answer_type == "📷+📝 Фото и текст":
        await update.message.reply_text(
            "📝 **Сначала напишите текстовый ответ:**\n\n"
            "Опишите свои мысли, чувства или выполнение упражнения.\n"
            "После текста нужно будет прикрепить фото.",
            parse_mode='Markdown'
        )
        context.user_data['waiting_for_text'] = True
        context.user_data['need_photo_after_text'] = True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text


    # ★ НОВОЕ: Обработка отмены получения file_id
    if context.user_data.get('waiting_for_file_id'):
        if text in ['отмена', 'отменить', 'cancel', 'стоп', 'stop']:
            context.user_data.pop('waiting_for_file_id', None)
            await update.message.reply_text(
                "❌ **Режим получения File ID отменен.**",
                parse_mode='Markdown'
            )
            return

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

    # === ОБРАБОТКА ВЫБОРА МАРАФОНА ===
    if 'arc_selection_map' in context.user_data and update.message.text in context.user_data['arc_selection_map']:
        await show_tests_for_arc(update, context)
        return

    # === ОБРАБОТКА ОТВЕТОВ ТЕСТА ===
    if context.user_data.get('current_section') == 'testing' and update.message.text in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "⏹️ Прервать тест"]:
        await process_test_answer(update, context)
        return
    
    # === ОБРАБОТКА ВЫБОРА ТЕСТА ===
    if 'test_mapping' in context.user_data and update.message.text in context.user_data['test_mapping']:
        await start_test(update, context)
        return
    
    # === ОБРАБОТКА ВЫБОРА МАРАФОНА ДЛЯ РЕЗУЛЬТАТОВ ===
    if 'arc_results_mapping' in context.user_data and update.message.text in context.user_data['arc_results_mapping']:
        await show_tests_for_arc_results(update, context)
        return
    
    # === ОБРАБОТКА ВЫБОРА ТЕСТА ДЛЯ РЕЗУЛЬТАТОВ ===
    if 'test_results_mapping' in context.user_data and update.message.text in context.user_data['test_results_mapping']:
        test_info = context.user_data['test_results_mapping'][update.message.text]
        await show_test_results(update, context, 
                              update.message.from_user.id,
                              test_info['arc_id'],
                              test_info['week_num'])
        return

    # === ОБРАБОТКА ОТВЕТОВ ТЕСТА ===
    # Теперь проверяем не цифры, а наличие активного теста
    if context.user_data.get('current_test') and not context.user_data.get('waiting_for_question'):
        # ★★ ИСПРАВЛЕНИЕ: Проверяем не кнопки 1️⃣-5️⃣, а любой текст при активном тесте
        # (кроме специальных команд)
        if text != "⏹️ Прервать тест":
            await process_test_answer(update, context)
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
    user_id = update.message.from_user.id
    assignment_id = context.user_data.get('current_assignment_id')
    
    if not assignment_id:
        await update.message.reply_text("❌ Ошибка: задание не выбрано")
        return
    
    answer_text = context.user_data.get('current_answer_text')
    answer_files = context.user_data.get('current_answer_files', [])
    
    if not answer_text and not answer_files:
        await update.message.reply_text("❌ Нужно отправить хотя бы текст или фото")
        return
    
    from database import save_assignment_answer
    save_assignment_answer(user_id, assignment_id, answer_text, answer_files)
    
    day_id = context.user_data.get('current_day_id')
    arc_id = context.user_data.get('current_arc_id')
    if day_id and arc_id:
        from database import update_daily_stats
        update_daily_stats(user_id, arc_id, day_id, 1)
    
    context.user_data['answering_assignment'] = False
    context.user_data['current_answer_text'] = None
    context.user_data['current_answer_files'] = []
    
    await update.message.reply_text(
        "🎉 **Ответ успешно отправлен!**\n\n"
        "Психолог проверит твою работу и оставит обратную связь.\n"
        "Статус проверки можно отслеживать в разделе 'Отправленные задания'.",
        parse_mode='Markdown'
    )
    
    assignment_title = context.user_data.get('current_assignment')
    if assignment_title:
        await show_assignment(update, context)

async def process_assignment_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает вопрос к заданию"""
    question = update.message.text
    user_id = update.message.from_user.id
    
    if 'assignment_questions' not in context.user_data:
        context.user_data['assignment_questions'] = []
    
    context.user_data['assignment_questions'].append(question)
    context.user_data['waiting_for_question'] = False
    
    keyboard = [["✅ Завершить", "💬 Добавить еще вопрос"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"✅ **Вопрос добавлен!**\n\n"
        f"*{question}*\n\n"
        f"Хотите добавить еще вопрос или завершить отправку?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def finish_assignment_with_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает отправку задания с вопросами"""
    user_id = update.message.from_user.id
    assignment_id = context.user_data.get('current_assignment_id')
    answer_text = context.user_data.get('current_answer_text')
    answer_files = context.user_data.get('current_answer_files', [])
    questions = context.user_data.get('assignment_questions', [])
    
    full_answer = answer_text
    if questions:
        full_answer += "\n\n**Вопросы:**\n" + "\n".join(f"- " + q for q in questions)
    
    from database import save_assignment_answer
    save_assignment_answer(user_id, assignment_id, full_answer, answer_files)
    
    context.user_data['asking_questions'] = False
    context.user_data['waiting_for_question'] = False
    context.user_data['assignment_questions'] = []
    context.user_data['current_answer_text'] = None
    context.user_data['current_answer_files'] = []
    
    await update.message.reply_text(
        "🎉 **Ваш ответ отправлен психологу!**\n\n"
        "Он проверит вашу работу и оставит обратную связь.\n"
        "Статус можно отслеживать в 'Отправленные задания'.",
        parse_mode='Markdown'
    )
    
    await start(update, context)

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
        "Выберите участника и марафон:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
async def show_student_part_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ВСЕ новые задания участника в выбранной части"""
    context.user_data['view_mode'] = 'new'
    print(f"🚨 Установлен view_mode='new' в show_student_part_assignments")
    text = update.message.text
    
    # Извлекаем данные из mapping
    student_mapping = context.user_data.get('student_mapping', {})
    mapping_data = student_mapping.get(text)
    
    if not mapping_data:
        await update.message.reply_text("❌ Ошибка: не удалось определить участника")
        return
    
    user_id = mapping_data['user_id']
    arc_id = mapping_data['arc_id']
    
    # Сохраняем в контексте
    context.user_data['current_student_id'] = user_id
    context.user_data['current_arc_id'] = arc_id
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем имя участника и название части
    cursor.execute('SELECT fio, username FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()
    display_name = user_info[0] if user_info[0] else (user_info[1] if user_info[1] else f"ID: {user_id}")
    
    cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
    part_title = cursor.fetchone()[0]
    
    # Получаем ВСЕ новые задания участника в этой части
    cursor.execute('''
        SELECT a.assignment_id, a.title, d.title as day_title,
               a.content_text, upa.answer_text
        FROM assignments a
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? AND upa.status = 'submitted' AND d.arc_id = ?
        ORDER BY d.order_num, a.assignment_id
    ''', (user_id, arc_id))
    
    assignments = cursor.fetchall()
    conn.close()
    
    if not assignments:
        await update.message.reply_text("❌ В этой части нет новых заданий")
        return
    
    keyboard = []
    
    for assignment_id, assignment_title, day_title, content_text, answer_text in assignments:
        # Обрезаем длинные названия
        short_content = (content_text[:30] + "...") if content_text else "без описания"
        btn_text = f"📝 {assignment_title} ({day_title})"
        keyboard.append([btn_text])
    
    keyboard.append(["🔙 Назад к списку участников"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📋 **Новые задания участника:**\n\n"
        f"👤 **Участник:** {display_name}\n"
        f"🔄 {part_title}\n"
        f"📊 **Всего заданий:** {len(assignments)}\n\n"
        f"Выберите задание для проверки:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def show_student_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает тренинги выбранного участника"""
    text = update.message.text
    
    student_mapping = context.user_data.get('student_mapping', {})
    student_id = student_mapping.get(text)
    
    if not student_id:
        await update.message.reply_text("❌ Ошибка: не удалось определить участника")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT c.course_id, c.title
        FROM courses c
        JOIN arcs a ON c.course_id = a.course_id
        JOIN days d ON a.arc_id = d.arc_id
        JOIN assignments ass ON d.day_id = ass.day_id
        JOIN user_progress_advanced upa ON ass.assignment_id = upa.assignment_id
        WHERE upa.user_id = ? AND upa.status = 'submitted'
    ''', (student_id,))
    
    courses = cursor.fetchall()
    conn.close()
    
    if not courses:
        await update.message.reply_text("❌ У участника нет тренингов с новыми заданиями")
        return
    
    keyboard = []
    for course_id, course_title in courses:
        keyboard.append([f"📖 {course_title}"])
    
    keyboard.append(["🔙 Назад к новым заданиям"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['current_student_id'] = student_id
    
    await update.message.reply_text(
        "📚 **Тренинги участника:**\n\n"
        "Выберите тренинг:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


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
    assignment_id = context.user_data.get('current_assignment_id')
    student_id = context.user_data.get('current_student_id')
    comment = context.user_data.get('current_comment', '')
    
    if not assignment_id or not student_id:
        await update.message.reply_text("❌ Ошибка: данные задания не найдены")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE user_progress_advanced 
        SET status = 'approved', teacher_comment = ?
        WHERE assignment_id = ? AND user_id = ?
    ''', (comment, assignment_id, student_id))

    cursor.execute('''
        UPDATE user_progress_advanced 
        SET viewed_by_student = 0
        WHERE assignment_id = ? AND user_id = ?
    ''', (assignment_id, student_id))
    
    conn.commit()
    conn.close()
    
    context.user_data['waiting_for_comment'] = False
    context.user_data['current_comment'] = None
    context.user_data['current_assignment_id'] = None
    context.user_data['current_student_id'] = None
    
    await update.message.reply_text(
        "🎉 **Задание принято!**\n\n"
        f"💬 **Ваш комментарий:** {comment}\n\n"
        "Участник увидит ваш комментарий в разделе 'Ответ психолога'",
        parse_mode='Markdown'
    )
    
    await admin_panel(update, context)

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
    context.user_data['view_mode'] = 'approved'
    context.user_data['current_section'] = 'admin'
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем участников с принятыми заданиями по частям
    cursor.execute('''
        SELECT DISTINCT 
            u.user_id, 
            COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
            ar.title as part_title,
            ar.arc_id,
            COUNT(upa.assignment_id) as approved_count
        FROM users u
        JOIN user_progress_advanced upa ON u.user_id = upa.user_id
        JOIN assignments a ON upa.assignment_id = a.assignment_id
        JOIN days d ON a.day_id = d.day_id
        JOIN arcs ar ON d.arc_id = ar.arc_id
        WHERE upa.status = 'approved'
        GROUP BY u.user_id, ar.arc_id
        ORDER BY approved_count DESC
    ''')
    
    students_data = cursor.fetchall()
    conn.close()
    
    if not students_data:
        await update.message.reply_text("✅ Нет принятых заданий")
        return
    
    keyboard = []
    student_mapping_approved = {}  # Отдельный mapping для принятых
    
    for user_id, display_name, part_title, arc_id, approved_count in students_data:
        # Обрезаем длинные имена
        if len(display_name) > 20:
            display_name = display_name[:17] + "..."
        
        # Формат: 👤 Имя - Часть X (N принятых)
        btn_text = f"👤 {display_name} - {part_title} ({approved_count} принятых)"
        keyboard.append([btn_text])
        
        # Сохраняем mapping: кнопка → (user_id, arc_id)
        student_mapping_approved[btn_text] = {'user_id': user_id, 'arc_id': arc_id}
    
    context.user_data['student_mapping_approved'] = student_mapping_approved
    
    keyboard.append(["🔙 Назад к проверке"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "✅ **Принятые задания:**\n\n"
        "Выберите участника и марафон:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_student_part_approved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ВСЕ принятые задания участника в выбранной части"""
    context.user_data['view_mode'] = 'new'
    print(f"🚨 Установлен view_mode='new' в show_student_part_assignments")
    text = update.message.text
    
    # Извлекаем данные из mapping для принятых
    student_mapping = context.user_data.get('student_mapping_approved', {})
    mapping_data = student_mapping.get(text)
    
    if not mapping_data:
        await update.message.reply_text("❌ Ошибка: не удалось определить участника")
        return
    
    user_id = mapping_data['user_id']
    arc_id = mapping_data['arc_id']
    
    # Сохраняем в контексте
    context.user_data['current_student_id'] = user_id
    context.user_data['current_arc_id'] = arc_id
    context.user_data['view_mode'] = 'approved'  # Важно!
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем имя участника и название части
    cursor.execute('SELECT fio, username FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()
    display_name = user_info[0] if user_info[0] else (user_info[1] if user_info[1] else f"ID: {user_id}")
    
    cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
    part_title = cursor.fetchone()[0]
    
    # Получаем ВСЕ принятые задания участника в этой части
    cursor.execute('''
        SELECT a.assignment_id, a.title, d.title as day_title,
               a.content_text, upa.answer_text, upa.teacher_comment
        FROM assignments a
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        JOIN days d ON a.day_id = d.day_id
        WHERE upa.user_id = ? AND upa.status = 'approved' AND d.arc_id = ?
        ORDER BY d.order_num, a.assignment_id
    ''', (user_id, arc_id))
    
    assignments = cursor.fetchall()
    conn.close()
    
    if not assignments:
        await update.message.reply_text("❌ В этой части нет принятых заданий")
        return
    
    keyboard = []
    
    for assignment_id, assignment_title, day_title, content_text, answer_text, teacher_comment in assignments:
        # Обрезаем длинные названия
        short_content = (content_text[:30] + "...") if content_text else "без описания"
        btn_text = f"✅ {assignment_title} ({day_title})"  # ✅ вместо 📝
        keyboard.append([btn_text])
    
    keyboard.append(["🔙 Назад к списку участников"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📋 **Принятые задания участника:**\n\n"
        f"👤 **Участник:** {display_name}\n"
        f"🔄 {part_title}\n"
        f"📊 **Всего принято:** {len(assignments)}\n\n"
        f"Выберите задание для просмотра:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_assignment_approved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает принятое задание с комментарием психолога"""
    if context.user_data.get('view_mode') != 'approved':
        context.user_data['view_mode'] = 'approved'
        print(f"🚨 Исправлен view_mode на 'approved'")
    text = update.message.text
    assignment_title = text[2:].strip()
    
    student_id = context.user_data.get('current_student_id')
    day_title = context.user_data.get('current_day')
    
    if not day_title:
        await update.message.reply_text("❌ Ошибка: день не определен")
        return
    
    from database import get_day_id_by_title_and_arc
    arc_id = context.user_data.get('current_arc_id')
    day_id = get_day_id_by_title_and_arc(day_title, arc_id)
    
    if not day_id:
        await update.message.reply_text("❌ Ошибка: день не найден")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
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
    
    day_title_display = day_info[0] if day_info else day_title
    arc_title = day_info[1] if day_info else "Неизвестно"
    
    display_name = fio if fio else username

    header = f"**✅ Принятое задание: {assignment_title}**\n\n"
    header += f"**Участник:** {display_name}\n"
    header += f" {arc_title}\n"
    header += f"**День:** {day_title_display}\n\n"
    await update.message.reply_text(header, parse_mode='Markdown')

    if content_text:
        await send_long_message(update, content_text, "**Задание:**")

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

    if teacher_comment:
        await send_long_message(update, teacher_comment, "**💬 Комментарий психолога:**")

    final = "✅ **Задание принято!**\n\n"

    keyboard = [
        ["🔙 Назад к заданиям"],
        ["🔙 В главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(final, reply_markup=reply_markup, parse_mode='Markdown')

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
    # Получаем данные текущего задания
    feedback_data = context.user_data.get('current_feedback_data')
    
    if not feedback_data:
        # Попробуем получить из другого места
        assignment_title = context.user_data.get('current_feedback_assignment')
        if assignment_title:
            feedback_data = {
                'title': assignment_title,
                'day': context.user_data.get('current_feedback_day', 'Не указано')
            }
    
    keyboard = [
        [InlineKeyboardButton("💬 Написать психологу", url="https://t.me/Artem_Kasimov_psy")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = "👤 **Связь с психологом**\n\n"
    message += "Нажмите кнопку ниже чтобы написать Артему напрямую.\n\n"
    
    if feedback_data:
        message += f"📝 **Задание:** {feedback_data.get('title', 'Не указано')}\n"
        message += f"📅 **День:** {feedback_data.get('day', 'Не указано')}\n\n"
    
    message += "В сообщении укажите:\n"
    message += "1. Ваш вопрос по заданию\n"
    message += "2. Что именно непонятно\n"
    message += "3. Какую помощь требуется\n\n"
    message += "Психолог ответит в личных сообщениях."
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def start_fio_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['waiting_for_fio'] = True
    await update.message.reply_text("📝 Введите ваше ФИО:")

async def show_course_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Детали тренинга и список частей"""
    course_title = update.message.text[2:].strip()
    context.user_data['current_course'] = course_title
    
    from database import get_course_arcs
    arcs = get_course_arcs(course_title)
    
    keyboard = []
    keyboard.append(["📖 О тренинге"])
    
    for arc_id, arc_title, is_available in arcs:
        status = "🔓" if is_available else "🔒"
        keyboard.append([f"{status} {arc_title}"])
    
    keyboard.append(["🔙 Назад в каталог"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📚 **{course_title}**\n\n"
        "Выберите раздел для просмотра:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

def get_course_arcs(course_title):
    """Получает часть тренинга с проверкой доступности по датам - ИСПРАВЛЕННАЯ"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT arc_id, title, order_num, price, 
               дата_начала, дата_окончания, бесплатный_период, 
               status, is_available
        FROM arcs 
        WHERE course_id = (SELECT course_id FROM courses WHERE title = ?) 
        AND status = 'active'
        AND дата_начала IS NOT NULL 
        AND дата_окончания IS NOT NULL
        ORDER BY order_num
    ''', (course_title,))
    
    arcs = cursor.fetchall()
    conn.close()
    
    today = datetime.now().date()
    result = []
    
    for arc in arcs:
        arc_id, title, order_num, price, start_date_str, end_date_str, free_period, status, is_available = arc
        
        # Пропускаем если нет дат (уже отфильтровано, но на всякий случай)
        if not start_date_str or not end_date_str:
            print(f"⚠️ Пропущена часть '{title}' - отсутствуют даты")
            continue
            
        try:
            # Преобразуем строку в дату
            if isinstance(start_date_str, str):
                if ' ' in start_date_str:
                    start_date = datetime.strptime(start_date_str, '%Y-%m-%d %H:%M:%S').date()
                else:
                    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            else:
                start_date = start_date_str
            
            if isinstance(end_date_str, str):
                if ' ' in end_date_str:
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d %H:%M:%S').date()
                else:
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            else:
                end_date = end_date_str
            
            # Определяем статус
            if today < start_date:
                arc_status = 'future'
            elif start_date <= today <= end_date:
                arc_status = 'active'
            else:
                arc_status = 'past'
            
            result.append({
                'arc_id': arc_id,
                'title': title,
                'order_num': order_num,
                'price': price,
                'start_date': start_date_str,
                'end_date': end_date_str,
                'status': arc_status,
                'free_period': free_period,
                'is_available': is_available
            })
            
        except Exception as e:
            print(f"🚨 Ошибка обработки части '{title}': {e}")
            continue
    
    return result

async def show_about_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Страница 'Всё о марафоне' с подразделами и ссылкой на Телеграф"""
    
    message_text = """
Проект ТВОЁ ДЕЛО https://t.me/tvoedeloart представляет: 

🔥Марафон «РЕГУЛЯРНЫЙ МЕНЕДЖМЕНТ»: Стань архитектором своего времени!
🔥 Длительность: 4 недели интенсивной практики 
окончание регистрации 25 января,  
старт в вторник 27 января 
Финиш  24 февраля 

Регистрируйтесь на марафон «Регулярный менеджмент» по ссылке: https://kasimov.tb.ru


🌀 Вас когда-нибудь посещало чувство, что вы управляете не бизнесом или карьерой, а хаосом? Знакомо? 

Марофон «Регулярный менеджмент» познакомит вас сэфективными   инструментами управления, и научит  применять на их практике! 

📘 Матрица Эйзенхауэра, цикл Деминга, SMART если эти слова звучат как сухой менеджерский лексикон, вы не знаете как эти инструменты глубоки и прекрасны! 

Вы, возможно, читали о них в книгах, но они так и остались теорией, потому что их некому было оживить, встроить в плоть и кровь вашего ежедневного труда. Давайте это исправим! 

Знание, не ставшее привычкой: мертвое знание. Оживим информацию энергией опыта и действия! 

👨‍💼 Я, Артём Касимов, организационный психолог и предприниматель, приглашаю вас на интенсивный четырехнедельный марафон «Регулярный менеджмент». 

🏆 Что вы получите: 

🎯 Прочный навык, синтез информации и вашего опыта. Система мышления, которая будет работать на вас каждый день, помогая отделять зерна от плевел. 

🧩 Четкий личный алгоритм управления задачами и проектами от идеи до результата. 

✅ Конкретный результат в виде реализованных важных задач, на которые у вас «не было времени». 

🧭 Выход в стратегическое планирование. Ваше мышление перестроится с режима «реагирования», в режим «наблюдения», «коррекции» и а затем  режим «создания». 

⚙️ Почему этот марафон работает? Потому что он построен на принципах научно-доказательной психологии, а не на мотивационных лозунгах. 

🧠 Когнитивно-поведенческий подход поможет нам выявить и скорректировать те автоматические мысли и внутренние установки, которые саботируют вашу организованность: «Сделаю позже», «Надо сделать все идеально», «Это срочно!».


📅 Как устроен марафон: 

Длительность: 4 недели интенсивной практики. 

📝 Формат: Ежедневный персональный трекинг через вопросы и отчеты + еженедельная групповая онлайн-встреча. 

👥 Групповая встреча: Каждый вторник в 20:00 по Екатеринбургу. 30 минут разбора теории и 30 минут ответов на ваши личные вопросы. Встречи проходят на Яндекс.Телемосте, запись доступна всем участникам. 

⚖️ Особая атмосфера: Это рабочая группа для серьезных людей. Участники, которые пропускают встречи и не выполняют задания, будут исключаться из марафона без возврата средств. 

🏁 Ценность:
1. Все, кто дойдет до финиша, получат эксклюзивную возможность оформить контракт на внедрение регулярного менеджмента для своей команды на специальных условиях. И персональное предложение на сессию стратегического планирования. 

2. В подарок каждый получит персональную 60-минутную консультацию со мной, организационным психологом, Атёмом Касомовым, очно или дистанционно для решения вашего запроса. 

💰 Инвестиция и детали
👥 Ваше время и внимание! Работа каждый день! Размер группы: Не более 12 человек. Это гарантирует персональное внимание и качество групповой динамики. 

💳 Стоимость участия: 10 000 рублей. В эту сумму входят все материалы, ежедневное сопровождение, доступ к групповым встречам и их записям на весь период марафона. 

📲 Как зарегистрироваться: Перейдите на сайт https://kasimov.tb.ru, найдите в списке программ «Марафон РЕГУЛЯРНЫЙ МЕНЕДЖМЕНТ» и регистрируйтесь оплатой. 

ВОЗЬМУ В ПРОЕКТ ТОЛЬКО 12 ЧЕЛОВЕК! Набор открыт
🤝 Важный момент: По окончании марафона состоится общая встреча для всех участников, в том числе и для тех, кто сошел с дистанции. 

🧠 Четыре недели — это срок, за который формируется новая нейронная связь, закладывается фундамент привычки. 
Регистрируйтесь на марафон «Регулярный менеджмент» по ссылке: https://kasimov.tb.ru 

С уважением и верой в ваш результат, Артём Касимов, организационный психолог.
"""

    keyboard = [
        ["📅 Расписание марафонов"],
        ["🗓 Расписание вебинаров"],
        ["💬 Задать вопрос о марафоне"],
        ["🔙 Назад","🔙 В главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "Выберите марафон:",
        reply_markup=reply_markup
    )

async def show_course_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Страница 'Купить доступ' - показывает все части с датами ТОЛЬКО с указанными датами"""
    user_id = update.message.from_user.id
    
    # Получаем курс
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT course_id, title FROM courses LIMIT 1')
    course = cursor.fetchone()
    conn.close()
    
    if not course:
        await update.message.reply_text("❌ Активных марафонов не найдено")
        return
    
    course_id, course_title = course
    
    # Получаем только дуги с датами
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT arc_id, title, order_num, price, 
               дата_начала, дата_окончания, бесплатный_период
        FROM arcs 
        WHERE course_id = ? 
        AND дата_начала IS NOT NULL 
        AND дата_начала != ''
        AND дата_окончания IS NOT NULL
        AND дата_окончания != ''
        ORDER BY order_num
    ''', (course_id,))
    
    arcs = cursor.fetchall()
    conn.close()
    
    if not arcs:
        await update.message.reply_text(
            "📭 **На данный момент доступных марафонов.**\n\n"
            "Новые марафоныв появятся позже. Следите за обновлениями!",
            parse_mode='Markdown'
        )
        return
    
    # Группируем по статусам
    today = datetime.now().date()
    current_arcs = []
    future_arcs = []
    
    for arc in arcs:
        arc_id, title, order_num, price, start_date_str, end_date_str, free_period = arc
        
        try:
            # Парсим даты
            if isinstance(start_date_str, str):
                start_date = datetime.strptime(start_date_str.split()[0], '%Y-%m-%d').date()
            else:
                continue
                
            if isinstance(end_date_str, str):
                end_date = datetime.strptime(end_date_str.split()[0], '%Y-%m-%d').date()
            else:
                continue
            
            # Определяем статус
            if start_date <= today <= end_date:
                current_arcs.append({
                    'arc_id': arc_id,
                    'title': title,
                    'start_date': start_date_str,
                    'end_date': end_date_str,
                    'price': price,
                    'status': 'активный'
                })
            elif today < start_date:
                future_arcs.append({
                    'arc_id': arc_id,
                    'title': title,
                    'start_date': start_date_str,
                    'end_date': end_date_str,
                    'price': price,
                    'status': 'скоро начнётся'
                })
                
        except Exception as e:
            print(f"⚠️ Ошибка обработки части '{title}': {e}")
            continue
    
    # Формируем сообщение
    message = f"**КУПИТЬ ДОСТУП**\n\n"
    message += f"**Обозначения:**\n"
    message += f"• 🔄 - Авктивный марафон (уже начался)\n"
    message += f"• ⏳ - Марафон который скоро начнётся\n\n"
    message += f"**Для будущих марафонов доступна предрегистрация**\n\n"
    
    if not current_arcs and not future_arcs:
        message += "📭 **Нет доступных марафонов.**\n"
        message += "Новые марафоны появятся позже."
        
        keyboard = [["🔙 В главное меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    message += "👇 **Выберите марафон для покупки:**"
    
    # Создаем клавиатуру
    keyboard = []
    
    # Текущие части
    for arc in current_arcs:
        keyboard.append([f"🔄 {arc['title']} ({arc['status']})"])
    
    # Будущие части
    for arc in future_arcs[:5]:
        keyboard.append([f"⏳ {arc['title']} ({arc['status']})"])
    
    
    keyboard.append(["🔙 В главное меню"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    # Сохраняем информацию о дугах
    context.user_data['available_arcs'] = {
        'current': current_arcs,
        'future': future_arcs,
    }

async def contact_psychologist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переход к психологу с inline-кнопкой"""
    keyboard = [
        [InlineKeyboardButton("💬 Написать психологу", url="https://t.me/Artem_Kasimov_psy")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👤 **Связь с психологом**\n\n"
        "Нажмите кнопку ниже чтобы написать Артему:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


def get_current_arc():
    """ОРИГИНАЛЬНАЯ версия с исправлением проблемы раздела 0"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        from datetime import datetime
        today = datetime.now().date().isoformat()
        print(f"🔍 Поиск текущей части на дату: {today}")
        
        # Ищем часть с датами, включающими сегодня
        cursor.execute('''
            SELECT arc_id, title 
            FROM arcs 
            WHERE arc_id > 0
            AND дата_начала IS NOT NULL 
            AND дата_начала != ''
            AND дата_окончания IS NOT NULL 
            AND дата_окончания != ''
            AND DATE(дата_начала) <= DATE(?)
            AND DATE(дата_окончания) >= DATE(?)
            ORDER BY arc_id
            LIMIT 1
        ''', (today, today))
        
        current = cursor.fetchone()
        
        if current:
            print(f"✅ Найдена текущая часть: {current[1]} (ID: {current[0]})")
        else:
            print(f"⚠️ Текущая часть не найдена для даты {today}")
            # Покажем какие части есть
            cursor.execute('''
                SELECT arc_id, title, дата_начала, дата_окончания 
                FROM arcs 
                WHERE arc_id > 0 
                AND дата_начала IS NOT NULL
                ORDER BY дата_начала
            ''')
            all_arcs = cursor.fetchall()
            
            print(f"📋 Все части в БД:")
            for arc in all_arcs:
                print(f"  • {arc[1]} (ID:{arc[0]}) - {arc[2]} / {arc[3]}")
        
        return current
    
    except Exception as e:
        print(f"🚨 Ошибка в get_current_arc: {e}")
        cursor.execute('SELECT arc_id, title FROM arcs WHERE arc_id = 1')
        return cursor.fetchone()
    finally:
        conn.close()

async def check_daily_openings(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и открывает новые дни в 06:00 местного времени"""
    print("=" * 50)
    print("🕛 [JOB] Проверка открытия новых дней...")
    
    current_moscow = get_moscow_time()
    print(f"🕐 Текущее время МСК: {current_moscow}")
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, username, timezone_offset, city 
        FROM users 
        WHERE timezone_offset IS NOT NULL
    ''')
    
    users = cursor.fetchall()
    print(f"👥 Найдено пользователей: {len(users)}")
    
    opened_days_count = 0
    
    for user_id, username, timezone_offset, city in users:
        try:
            user_local_time = get_moscow_time() + timedelta(hours=timezone_offset)
            user_hour = user_local_time.hour
            user_minute = user_local_time.minute
            
            if user_hour == 6 and user_minute <= 5:
                print(f"👤 {username or user_id}: Время для открытия нового дня!")
                
                cursor.execute('''
                    SELECT uaa.arc_id, a.title
                    FROM user_arc_access uaa
                    JOIN arcs a ON uaa.arc_id = a.arc_id
                    WHERE uaa.user_id = ? AND a.status = 'active'
                ''', (user_id,))
                
                user_arcs = cursor.fetchall()
                
                for arc_id, arc_title in user_arcs:
                    cursor.execute('''
                        SELECT purchased_at FROM user_arc_access 
                        WHERE user_id = ? AND arc_id = ?
                    ''', (user_id, arc_id))
                    
                    purchase_result = cursor.fetchone()
                    if not purchase_result:
                        continue
                    
                    purchase_date = datetime.fromisoformat(purchase_result[0]).date()
                    days_since_start = (user_local_time.date() - purchase_date).days + 1
                    
                    cursor.execute('''
                        SELECT day_id, title 
                        FROM days 
                        WHERE arc_id = ? AND order_num = ?
                    ''', (arc_id, days_since_start))
                    
                    day_to_open = cursor.fetchone()
                    
            
            else:
                if user_hour == 6:
                    print(f"   ⏳ {username}: уже после 06:{user_minute:02d}")
                else:
                    print(f"   ⏳ {username}: сейчас {user_hour}:{user_minute:02d}")
                
        except Exception as e:
            print(f"❌ Ошибка пользователя {user_id}: {e}")
    
    conn.close()
    
    print(f"📊 Итог: отправлено уведомлений - {opened_days_count}")
    print("=" * 50)

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
    context.user_data['current_section'] = 'admin_access'
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.user_id, 
               COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
               COUNT(uaa.arc_id) as arc_count
        FROM users u
        LEFT JOIN user_arc_access uaa ON u.user_id = uaa.user_id
        GROUP BY u.user_id
        ORDER BY 
            CASE WHEN u.fio IS NOT NULL THEN 1 ELSE 2 END,
            u.user_id
        LIMIT 50
    ''')
    
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await update.message.reply_text("❌ Нет пользователей в системе")
        return
    
    keyboard = []
    for user_id, display_name, arc_count in users:
        if len(display_name) > 25:
            display_name = display_name[:22] + "..."
        
        btn_text = f"👤 {display_name} ({arc_count})"
        keyboard.append([btn_text])
        
        if 'access_user_map' not in context.user_data:
            context.user_data['access_user_map'] = {}
        context.user_data['access_user_map'][btn_text] = user_id
    
    keyboard.append(["🔙 В главное меню"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🔧 **Управление доступом**\n\n"
        "Выберите пользователя (число в скобках - кол-во доступов):",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_user_arcs_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает доступы пользователя с inline-кнопками И список пользователей"""
    user_text = update.message.text
    user_map = context.user_data.get('access_user_map', {})
    user_id = user_map.get(user_text)
    
    if not user_id:
        await update.message.reply_text("❌ Пользователь не найден")
        return
    
    context.user_data['current_access_user'] = user_id
    context.user_data['current_access_user_text'] = user_text
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT fio, username FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()
    fio, username = user_info if user_info else (None, None)
    display_name = fio if fio else (username if username else f"ID: {user_id}")
    
    cursor.execute('''
        SELECT a.arc_id, a.title, 
               CASE WHEN uaa.user_id IS NOT NULL THEN 1 ELSE 0 END as has_access
        FROM arcs a
        LEFT JOIN user_arc_access uaa ON a.arc_id = uaa.arc_id AND uaa.user_id = ?
        WHERE a.arc_id > 0
        ORDER BY a.arc_id
    ''', (user_id,))
    
    arcs = cursor.fetchall()
    
    cursor.execute('''
        SELECT u.user_id, 
               COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
               COUNT(uaa.arc_id) as arc_count
        FROM users u
        LEFT JOIN user_arc_access uaa ON u.user_id = uaa.user_id
        GROUP BY u.user_id
        ORDER BY 
            CASE WHEN u.fio IS NOT NULL THEN 1 ELSE 2 END,
            u.user_id
        LIMIT 20
    ''')
    
    users = cursor.fetchall()
    conn.close()
    
    inline_keyboard = []
    row = []
    
    for i, (arc_id, arc_title, has_access) in enumerate(arcs):
        emoji = "✅" if has_access else "❌"
        short_title = f"Д{arc_id}"
        button_text = f"{emoji} {short_title}"
        callback_data = f"access_toggle_{user_id}_{arc_id}_{1 if has_access else 0}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
        
        if len(row) == 4 or i == len(arcs) - 1:
            inline_keyboard.append(row)
            row = []
    
    inline_keyboard.append([
        InlineKeyboardButton("✅ Дать все доступы", callback_data=f"access_all_{user_id}_1"),
        InlineKeyboardButton("❌ Забрать все", callback_data=f"access_all_{user_id}_0")
    ])
    
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    
    reply_keyboard = []
    for u_id, u_name, u_arc_count in users:
        if len(u_name) > 25:
            u_name = u_name[:22] + "..."
        
        prefix = "👉 " if u_id == user_id else "👤 "
        btn_text = f"{prefix}{u_name} ({u_arc_count})"
        reply_keyboard.append([btn_text])
        
        if 'access_user_map' not in context.user_data:
            context.user_data['access_user_map'] = {}
        context.user_data['access_user_map'][btn_text] = u_id
    
    reply_keyboard.append(["🔙 В главное меню"])
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    
    total_arcs = len(arcs)
    accessed_arcs = sum(1 for _, _, has_access in arcs if has_access)
    
    message = f"🔧 **Управление доступом**\n\n"
    message += f"👉 **Текущий пользователь:** {escape_markdown(display_name, version=2)}\n"
    message += f"📊 Доступов: {accessed_arcs}/{total_arcs}\n\n"
    message += "**Быстрое управление разделами:**\n"
    message += "• Нажмите на кнопку части тренинга чтобы переключить доступ ✅/❌\n"
    message += "• '✅ Дать все' - доступ ко всем частям тренинга\n"
    message += "• '❌ Забрать все' - удалить все доступы\n\n"
    message += "**Выберите другого пользователя из списка ниже:**"
    
    await update.message.reply_text(
        message,
        reply_markup=inline_markup,
        parse_mode='Markdown'
    )
    
    await update.message.reply_text(
        "👥 **Список пользователей:**\n"
        "(👉 - текущий выбранный)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия inline-кнопок управления доступом"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("access_toggle_"):
        parts = data.split("_")
        user_id = int(parts[2])
        arc_id = int(parts[3])
        current_status = int(parts[4])
        
        from database import grant_arc_access
        
        if current_status == 1:
            conn = sqlite3.connect('mentor_bot.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                          (user_id, arc_id))
            conn.commit()
            conn.close()
            new_status = 0
            action = "удален"
        else:
            grant_arc_access(user_id, arc_id, 'manual')
            new_status = 1
            action = "добавлен"
        
        await show_user_arcs_access_callback(query, context, user_id)
        await query.message.reply_text(f"✅ Доступ к части тренинга {arc_id} {action}!")
        return
    
    if data.startswith("access_all_"):
        parts = data.split("_")
        user_id = int(parts[2])
        action = int(parts[3])
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        if action == 1:
            cursor.execute('SELECT arc_id FROM arcs WHERE arc_id > 0')
            arcs = cursor.fetchall()
            
            for (arc_id,) in arcs:
                cursor.execute('''
                    INSERT OR IGNORE INTO user_arc_access (user_id, arc_id, access_type)
                    VALUES (?, ?, 'manual')
                ''', (user_id, arc_id))
            
            conn.commit()
            await query.message.reply_text("✅ Выдан доступ ко всем частям тренинга!")
        else:
            cursor.execute('DELETE FROM user_arc_access WHERE user_id = ?', (user_id,))
            conn.commit()
            await query.message.reply_text("❌ Все доступы удалены!")
        
        conn.close()
        
        await show_user_arcs_access_callback(query, context, user_id)
        return

async def show_user_arcs_access_callback(query, context, user_id):
    """Обновляет сообщение с inline-клавиатурой"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT fio, username FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()
    fio, username = user_info if user_info else (None, None)
    display_name = fio if fio else (username if username else f"ID: {user_id}")
    
    cursor.execute('''
        SELECT a.arc_id, a.title, 
               CASE WHEN uaa.user_id IS NOT NULL THEN 1 ELSE 0 END as has_access
        FROM arcs a
        LEFT JOIN user_arc_access uaa ON a.arc_id = uaa.arc_id AND uaa.user_id = ?
        WHERE a.arc_id > 0
        ORDER BY a.arc_id
    ''', (user_id,))
    
    arcs = cursor.fetchall()
    conn.close()
    
    keyboard = []
    row = []
    
    for i, (arc_id, arc_title, has_access) in enumerate(arcs):
        emoji = "✅" if has_access else "❌"
        short_title = f"Д{arc_id}"
        button_text = f"{emoji} {short_title}"
        callback_data = f"access_toggle_{user_id}_{arc_id}_{1 if has_access else 0}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
        
        if len(row) == 4 or i == len(arcs) - 1:
            keyboard.append(row)
            row = []
    
    keyboard.append([
        InlineKeyboardButton("✅ Дать все доступы", callback_data=f"access_all_{user_id}_1"),
        InlineKeyboardButton("❌ Забрать все", callback_data=f"access_all_{user_id}_0")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    total_arcs = len(arcs)
    accessed_arcs = sum(1 for _, _, has_access in arcs if has_access)
    
    message = f"🔧 **Управление доступом**\n\n"
    message += f"👤 **Пользователь:** {display_name}\n"
    message += f"📊 Доступов: {accessed_arcs}/{total_arcs}\n\n"
    message += "**Быстрое управление:**\n"
    message += "• Нажмите на кнопку раздела чтобы переключить доступ ✅/❌\n"
    message += "• '✅ Дать все' - доступ ко всем разделам\n"
    message += "• '❌ Забрать все' - удалить все доступы\n\n"
    message += f"✅ - доступ есть\n❌ - доступа нет"
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_users_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список участников для просмотра статистики (админ)"""
    context.user_data['current_section'] = 'admin_stats'
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Получаем всех пользователей с ФИО или username
    cursor.execute('''
        SELECT u.user_id, 
               COALESCE(u.fio, u.username, 'ID:' || u.user_id) as display_name,
               COUNT(DISTINCT uaa.arc_id) as arc_count
        FROM users u
        LEFT JOIN user_arc_access uaa ON u.user_id = uaa.user_id
        GROUP BY u.user_id
        ORDER BY 
            CASE WHEN u.fio IS NOT NULL THEN 1 ELSE 2 END,
            display_name
        LIMIT 50
    ''')
    
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await update.message.reply_text("❌ Нет пользователей в системе")
        return
    
    keyboard = []
    user_mapping = {}
    
    for user_id, display_name, arc_count in users:
        # Обрезаем длинные имена
        if len(display_name) > 25:
            display_name = display_name[:22] + "..."
        
        # Определяем цвет по активности
        conn2 = sqlite3.connect('mentor_bot.db')
        cursor2 = conn2.cursor()
        cursor2.execute('''
            SELECT COUNT(*) FROM user_progress_advanced 
            WHERE user_id = ? AND status IN ('submitted', 'approved')
        ''', (user_id,))
        
        activity_count = cursor2.fetchone()[0]
        conn2.close()
        
        # Цвета по активности
        if activity_count == 0:
            emoji = "🔴"  # Нет активности
        elif activity_count < 5:
            emoji = "🟠"  # Мало активности
        elif activity_count < 20:
            emoji = "🟡"  # Средняя активность
        else:
            emoji = "🟢"  # Высокая активность
        
        btn_text = f"{emoji} {display_name} ({arc_count})"
        keyboard.append([btn_text])
        
        user_mapping[btn_text] = {
            'user_id': user_id,
            'display_name': display_name,
            'arc_count': arc_count,
            'activity_count': activity_count
        }
    
    keyboard.append(["🔙 Назад к проверке"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Пояснение по цветам
    message = "📊 **Статистика участников (админ)**\n\n"
    message += "**Цвета по активности:**\n"
    message += "• 🟢 Высокая активность (>20 заданий)\n"
    message += "• 🟡 Средняя активность (5-20 заданий)\n"
    message += "• 🟠 Низкая активность (1-5 заданий)\n"
    message += "• 🔴 Нет активности\n\n"
    message += "Число в скобках - количество доступов к частям\n\n"
    message += "Выберите участника:"
    
    context.user_data['admin_stats_users'] = user_mapping
    
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
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM user_arc_access WHERE user_id = ? LIMIT 1', (user_id,))
    has_access = cursor.fetchone() is not None
    conn.close()
    return has_access

async def go_to_community(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет inline-кнопку для перехода в сообщество"""
    GROUP_LINK = "https://t.me/+khUT5h-XYMFkMDJi"
    
    keyboard = [[InlineKeyboardButton("👥 Перейти в закрытое сообщество", url=GROUP_LINK)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Нажмите кнопку ниже чтобы перейти в закрытое сообщество:",
        reply_markup=reply_markup
    )

async def show_offer_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает оферту регистрации с inline-кнопкой"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    message_text = """📋 **СОГЛАШЕНИЕ С ОФЕРТОЙ (РЕГИСТРАЦИЯ)**

Политика в отношении обработки персональных данных

(политика конфиденциальности)

1. Общие положения

1.1. Настоящая политика обработки персональных данных составлена в соответствии с требованиями Федерального закона от 27.07.2006. №152-ФЗ «О персональных данных» и определяет порядок обработки персональных данных и меры по обеспечению безопасности персональных данных ИП Касимовым Артемом Равкатовичем (ИНН 661213624458, далее – Оператор).

*Полный текст оферты доступен по ссылке ниже.*"""
    
    inline_keyboard = [[
        InlineKeyboardButton("📄 Читать полную оферту",
                           url="https://telegra.ph/Politika-konfidencialnosti-12-15-55")
    ]]
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    
    reply_keyboard = [
        ["✅ Принять оферту"],
        ["❌ Отказаться"]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message_text,
        reply_markup=inline_markup,
        parse_mode='Markdown'
    )
    
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=reply_markup
    )
    
    context.user_data['showing_offer'] = True

async def accept_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает принятие оферты - с ReplyKeyboardRemove"""
    user_id = update.message.from_user.id
    
    from database import get_user_offer_status, accept_offer
    offer_status = get_user_offer_status(user_id)
    
    if offer_status['accepted_offer']:
        await update.message.reply_text(
            "✅ Вы уже приняли оферту ранее.",
            reply_markup=ReplyKeyboardRemove(),  # ← Удаляет клавиатуру
            parse_mode='Markdown'
        )
        return
    
    # Сохраняем
    #accept_offer(user_id, phone=None, fio=None)
    
    # УБИРАЕМ клавиатуру и просим телефон
    await update.message.reply_text(
        "✅ **Оферта принята!**\n\n"
        "📱 **Введите номер телефона:** в формате +7 или 8",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    
    context.user_data['waiting_for_phone'] = True
    context.user_data['showing_offer'] = False

async def decline_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает отказ от оферты - с переходом в главное меню"""
    user_id = update.message.from_user.id
    
    from database import decline_offer
    decline_offer(user_id)
    
    # Очищаем user_data
    context.user_data.clear()
    
    # Показываем сообщение и сразу переходим в главное меню
    keyboard = [["📚 Мои задания", "🎯 Купить тренинг"],
                ["👤 Профиль", "🛠 Тех.поддержка"]]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "❌ **Вы отказались от оферты.**\n\n"
        "Для регистрации и последующего использования бота необходимо принять пользовательское соглашение.\n",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    # Очищаем флаг показа оферты
    if 'showing_offer' in context.user_data:
        del context.user_data['showing_offer']

async def decline_service_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает отказ от оферты - с переходом в главное меню"""
    user_id = update.message.from_user.id
    
    from database import decline_offer
    decline_offer(user_id)
    
    # Очищаем user_data
    context.user_data.clear()
    
    # Показываем сообщение и сразу переходим в главное меню
    keyboard = [["📚 Мои задания", "🎯 Купить тренинг"],
                ["👤 Профиль", "🛠 Тех.поддержка"]]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "❌ **Вы отказались от оферты.**\n\n"
        "Для доступа к разделу покупки части тренинга необходимо принять оферту. Вы можете ознакомиться с полным текстом на этапе принятия оферты, либо позже в профиле в соответствующем разделе.\n",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    # Очищаем флаг показа оферты
    if 'showing_offer' in context.user_data:
        del context.user_data['showing_offer']

async def show_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список мероприятий тренинга"""

    schedule_text = """

**📅 Расписание марафонов**

Раздел на доработке, информация скоро появится!

    """
    
    keyboard = [
        ["🔙 Назад к описанию марафона"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        schedule_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Расписание всех марафона"""

    schedule_text = """

**📅 Расписание вебинаров**

Раздел на доработке, информация скоро появится!

    """
    
    keyboard = [
        ["🔙 Назад к описанию марафона"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        schedule_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_service_offer_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает оферту на услуги с inline-кнопкой"""
    user_id = update.message.from_user.id
    arc_text = context.user_data.get('pending_purchase_arc', '')
    
    print(f"🔍 show_service_offer_agreement: сохраняем arc '{arc_text}' для user {user_id}")
    
    # Сохраняем ВСЕ данные из контекста
    purchase_context = {
        'pending_purchase_arc': arc_text,
        'current_section': context.user_data.get('current_section'),
        'current_arc_catalog': context.user_data.get('current_arc_catalog'),
        'part_status': context.user_data.get('part_status'),
        'buy_arc_id': context.user_data.get('buy_arc_id'),
        'buy_arc_price': context.user_data.get('buy_arc_price'),
        'original_message_text': update.message.text if hasattr(update, 'message') else ''
    }
    
    # Сохраняем в user_data
    context.user_data['saved_purchase_context'] = purchase_context
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    message_text = """📋 **ОФЕРТА НА ОКАЗАНИЕ УСЛУГ**

1. ОБЩИЕ ПОЛОЖЕНИЯ 

Настоящая публичная оферта является официальным публичным предложением Индивидуального предпринимателя Касимова Артема Равкатовича, действующего на основании свидетельства о государственной регистрации физического лица в качестве индивидуального предпринимателя ОГРНИП: 322665800202689: от 1 ноября 2022 г., и действующего на основании Диплома о профессиональной переподготовке № 0005 от 12.07.2023г., именуемого в дальнейшем «Исполнитель», заключить публичный договор (далее – «Договор» или «Оферта») об оказании психологических консультационных услуг юридическим и дееспособным физическим лицам на перечисленных ниже условиях.

*Полный текст оферты доступен по ссылке ниже.*"""

    inline_keyboard = [[
        InlineKeyboardButton("📄 Читать полную оферту", 
                           url="https://telegra.ph/Oferta-okazaniya-uslug-12-16")
    ]]
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    
    reply_keyboard = [
        ["✅ Принять оферту услуг"],
        ["❌ Отказаться от оферты"]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message_text,
        reply_markup=inline_markup,
        parse_mode='Markdown'
    )
    
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=reply_markup
    )
    
    context.user_data['showing_service_offer'] = True

async def accept_service_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Упрощенная версия - показывает кнопку для перехода"""
    user_id = update.message.from_user.id
    
    # 1. Принимаем оферту
    from database import accept_service_offer
    accept_service_offer(user_id)
    
    # 2. Получаем сохраненную часть
    pending_arc = context.user_data.get('pending_purchase_arc')
    
    if pending_arc:
        # 3. Показываем сообщение с кнопкой
        keyboard = [[pending_arc]]
        keyboard.append(["🔙 Выбор марафона"])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "✅ **Оферта услуг принята!**\n\n"
            f"Теперь вы можете приобрести доступ к **{pending_arc}**.\n\n"
            "Нажмите на кнопку ниже чтобы продолжить покупку:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        # Очищаем сохраненную часть
        context.user_data.pop('pending_purchase_arc', None)
    else:
        # Если нет сохраненной части
        await update.message.reply_text(
            "✅ **Оферта услуг принята!**\n\n"
            "Теперь вы можете приобрести доступ к части тренинга.",
            parse_mode='Markdown'
        )
        await show_course_main(update, context)

async def show_accepted_offers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список принятых оферт с ссылками"""
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT accepted_offer, accepted_offer_date, 
               accepted_service_offer, accepted_service_offer_date
        FROM users WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ Данные не найдены")
        return
    
    accepted_offer, offer_date, accepted_service, service_date = result
    
    def format_moscow_date(date_str):
        if not date_str:
            return "дата не указана"
        try:
            from datetime import datetime, timedelta
            utc_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            msk_date = utc_date + timedelta(hours=3)
            return msk_date.strftime("%d.%m.%Y %H:%M (МСК)")
        except:
            return date_str
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = []
    message = "📋 **Ваши принятые оферты**\n\n"
    
    if accepted_offer:
        formatted_date = format_moscow_date(offer_date)
        message += f"✅ **Политика конфиденциальности**\n"
        message += f"📅 Принята: {formatted_date}\n\n"
        
        keyboard.append([
            InlineKeyboardButton("📄 Политика конфиденциальности", 
                               url="https://telegra.ph/Politika-konfidencialnosti-12-15-55")
        ])
    
    if accepted_service:
        formatted_date = format_moscow_date(service_date)
        message += f"✅ **Оферта оказания услуг**\n"
        message += f"📅 Принята: {formatted_date}\n\n"
        
        keyboard.append([
            InlineKeyboardButton("📄 Оферта оказания услуг)", 
                               url="https://telegra.ph/Oferta-okazaniya-uslug-12-16")
        ])
    
    if not keyboard:
        message += "❌ У вас нет принятых оферт.\n\n"
        message += "Примите оферты в соответствующих разделах."
    
    inline_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    reply_keyboard = [["🔙 Назад в кабинет"]]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    
    if inline_markup:
        await update.message.reply_text(
            message,
            reply_markup=inline_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message,
            parse_mode='Markdown'
        )
    
    await update.message.reply_text(
        "Нажмите кнопку чтобы вернуться:",
        reply_markup=reply_markup
    )

async def show_today_assignments_info(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    """Показывает информацию о заданиях на текущий день для ВСЕХ активных частей"""
    if not user_id:
        user_id = update.message.from_user.id
    
    from database import get_user_active_arcs, get_current_arc_day, get_user_local_time
    
    active_arcs = get_user_active_arcs(user_id)
    
    if not active_arcs:
        return "Сейчас нет активных потоков."
    
    messages = []
    
    for arc_id, arc_title, arc_start, arc_end, access_type in active_arcs:
        day_info = get_current_arc_day(user_id, arc_id)
        
        if not day_info or day_info['day_number'] == 0:
            continue
        
        day_id = day_info['day_id']
        day_title = day_info['day_title']
        day_number = day_info['day_number']
        
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT a.title, a.доступно_до, 
                   upa.status as user_status
            FROM assignments a
            LEFT JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id 
                AND upa.user_id = ?
            WHERE a.day_id = ? 
            ORDER BY a.assignment_id
        ''', (user_id, day_id))

        assignments = cursor.fetchall()
        
        deadline_hour, deadline_minute = 12, 0
        if assignments and assignments[0][1]:
            try:
                time_str = str(assignments[0][1])
                if ':' in time_str:
                    deadline_hour, deadline_minute = map(int, time_str.split(':'))
            except:
                pass
        
        conn.close()
        
        user_time = get_user_local_time(user_id)
        current_hour = user_time.hour
        current_minute = user_time.minute
        
        is_day_available = (current_hour < deadline_hour or 
                           (current_hour == deadline_hour and current_minute < deadline_minute))

        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT order_num FROM arcs WHERE arc_id = ?', (arc_id,))
        arc_result = cursor.fetchone()
        arc_number = arc_result[0] if arc_result else '?'
        conn.close()
        
        all_submitted_or_approved = True
        if assignments:
            for title, available_until, user_status in assignments:
                if user_status not in ['submitted', 'approved']:
                    all_submitted_or_approved = False
                    break

        message = f"📅 **{day_title}** (Поток: {arc_title})\n\n"

        if all_submitted_or_approved and assignments:
            message += "🎉 **Вы выполнили все задания на сегодня!**\n"
            message += "Новые задания откроются завтра в 06:00\n\n"
        
        elif is_day_available and assignments:
            message += "✅ **Задания на текущий день доступны!**\n"
            message += f"Дедлайн: до {deadline_hour:02d}:{deadline_minute:02d}\n\n"
        
        elif not is_day_available and assignments:
            message += f"⏰ **Время выполнения заданий на сегодня истекло!**\n"
            message += f"Задания текущего дня уже закрыты (дедлайн был до {deadline_hour:02d}:{deadline_minute:02d}).\n"
            message += "Новые задания откроются завтра в 06:00\n\n"

        if assignments and not all_submitted_or_approved:
            for i, (title, available_until, user_status) in enumerate(assignments, 1):
                status_icon = "✅" if user_status in ['submitted', 'approved'] else "📝"
                time_text = f" - доступно до {available_until or '12:00'}"
                message += f"{i}. {status_icon} **{title}**{time_text}\n"
        
            message += "\n"
        
        message += "💡 **Важно:**\n"
        message += "• Задания должны быть выполнены до указанного времени\n"
        message += "• Если задание не выполнено вовремя, оно засчитывается как пропущенное\n"
        message += "• Пропуски отображаются в разделе 'Мой прогресс'\n"
        message += "• Задания, завершившиеся до получения доступа, не считаются пропусками\n\n"
        
        messages.append(message)
    
    if not messages:
        return "На сегодня нет активных заданий в ваших потоках."
    
    return "\n" + "="*40 + "\n".join(messages)

async def show_quick_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает краткое руководство по работе с заданиями"""
    
    guide_text = """📖 **КРАТКОЕ РУКОВОДСТВО ПО РАБОТЕ С ЗАДАНИЯМИ**

🎯 **КАК РАБОТАТЬ С ЗАДАНИЯМИ:**
1. **Ежедневно** в 06:00 открывается новый день и задания для него в разделе 'Доступные задания'
2. **Выберите задание** → выберите способ отправки ответа (только текст, только фото или текст+фото)
   • В зависимости от выбранного способа отправки ответа на задание зависит что будет прикреплено к заданию при отправке на проверку.
   • К ответу, при необходимости, можете **добавить комментарий** нажав на соответствующую кнопку.
   • Можете отправить несколько фотографий и несколько комментариев при необходимости, количество того, что прикрепит к итоговому ответу будет отображена.
3. **Отправляете ответ** → он сохранится и учтется в 'Мой прогресс'
4. Выполненные задания можно просмотреть в любой момент, но изменить уже нельзя.
5. Если вы пропустите день, то он останется доступен для выполнения, но вы потеряете 'серию без пропусков' в разделе 'Мой прогресс'

❓ **ЕСТЬ ВОПРОСЫ по заданиям?**
• В разделе 'Ответ психолога' в каждом задании есть возможность связаться с психологом, нажав на 👤 Личная консультация
• В каждом задании при выполнении так же есть возможность связаться с психологом
"""

    # Создаем клавиатуру для возврата
    keyboard = [["📚 Мои задания"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Отправляем руководство
    await update.message.reply_text(
        guide_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

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
    answer_type = context.user_data.get('answer_type', 'Фото_и_текст')
    
    files_count = len(context.user_data.get('answer_files', []))
    questions_count = len(context.user_data.get('questions', []))
    
    message = f"📊 **Готово!**\n\n"
    
    if answer_type == 'Только_фото':
        message += f"📎 Фото: {files_count} шт.\n"
    elif answer_type == 'Только_текст':
        text_preview = context.user_data.get('answer_text', '')[:100]
        message += f"✅ Текст ответа: сохранен\n"
        message += f"📄 Предпросмотр: {text_preview}...\n"
    
    message += f"💬 Вопросы: {questions_count} шт.\n\n"
    message += f"**Вы можете:**\n"
    message += f"• Задать вопрос по заданию\n"
    message += f"• **Отправить задание на проверку**\n\n"
    message += f"После отправки изменить ответ будет нельзя!"
    
    keyboard = [
        ["💬 Задать вопрос"],
        ["✅ Отправить задание"],
        ["🔙 Назад в меню заданий"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def ask_question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает добавление вопроса к заданию"""
    answer_type = context.user_data.get('answer_type', 'Фото_и_текст')
    
    if answer_type == 'Только_фото' and not context.user_data.get('answer_files'):
        await update.message.reply_text(
            "📷 **Сначала отправьте фото для задания!**\n\n"
            "Вы выбрали вариант 'Только фото'.\n"
            "Пожалуйста, сначала отправьте фото, затем можете задать вопросы.",
            parse_mode='Markdown'
        )
        return
    
    if answer_type == 'Только_текст' and not context.user_data.get('answer_text'):
        await update.message.reply_text(
            "📝 **Сначала напишите текстовый ответ!**\n\n"
            "Вы выбрали вариант 'Только текст'.\n"
            "Пожалуйста, сначала напишите ответ, затем можете задать вопросы.",
            parse_mode='Markdown'
        )
        return
    
    if answer_type == 'Только_фото':
        files_count = len(context.user_data.get('answer_files', []))
        status = f"📎 Фото: {files_count} шт."
    elif answer_type == 'Только_текст':
        status = "✅ Текст ответа: сохранен"
    else:
        files_count = len(context.user_data.get('answer_files', []))
        status = f"✅ Текст + 📎 {files_count} фото"
    
    await update.message.reply_text(
        f"💬 **Задать вопрос по заданию**\n\n"
        f"Текущий статус: {status}\n\n"
        f"**Напишите ваш вопрос:**\n"
        f"(вопрос будет прикреплен к ответу на задание)",
        parse_mode='Markdown'
    )
    
    context.user_data['waiting_for_question'] = True

async def show_training_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о тренинге или фестивале"""
    training_text = update.message.text
    training_name = training_text[2:].strip()
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    if training_name == "Часть первая: Самонаблюдение и намеренье":
        message = """**Часть первая: Самонаблюдение и намеренье**
20 декабря - 1 февраля 2026 года
интенсивное погружение 19-21 декабря. Три дня живого контакта с собой и группой. Работа, фестиваль, шеринг. Мы создаем среду, где рушатся внутренние барьеры.
Полное погружение.Формат:
Пятница, 19.12 вечер, 19.00 заезд.
Размещение, подготовка к тренингу.
Суббота, 20.12, с 10.00 до 19.00 Основная часть тренинга
Фестиваль 20.00 до 24.00
Воскресенье, 21.12 10.00 до 17.00 Шеринг. Завершение

**Места сознательно ограничены до 12 участников. Это гарантия глубины работы для каждого участника.**

**более подробно прочитайте в статье нажав на кнопку ниже**"""
        
        inline_keyboard = [[
            InlineKeyboardButton("📄 Подробнее в статье", 
                               url="https://telegra.ph/Trening-pervyj-12-17")
        ]]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)
    
    else:
        message = f"🎯 **{training_name}**\n\n"
        message += "**Ожидайте новостей!**\n\n"
        
        if training_name == "Фестиваль":
            message += "ожидайте новостей\n"
        else:
            message += "Тренинг будет запланирован незадолго до старта.\n"
        
        message += "Дата и время будут объявлены за 7 дней."
        inline_markup = None
    
    keyboard = [["🔙 Назад к мероприятиям"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if inline_markup:
        await update.message.reply_text(
            message,
            reply_markup=inline_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message,
            parse_mode='Markdown'
        )
    
    await update.message.reply_text(
        "Нажмите кнопку чтобы вернуться:",
        reply_markup=reply_markup
    )

async def send_scheduled_notifications(context: ContextTypes.DEFAULT_TYPE):
    """Отправка запланированных уведомлений"""
    print("="*50)
    print("🔔 [JOB] Проверка уведомлений...")
    
    from datetime import datetime, time
    from database import (
        get_user_local_time, get_current_arc, get_user_offer_status,
        get_notification, check_notification_sent, mark_notification_sent,
        get_mass_notification, get_user_skip_statistics
    )
    
    current_moscow = get_moscow_time()
    print(f"🕐 Текущее время МСК: {current_moscow}")
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, username, timezone_offset, city, phone
        FROM users 
        WHERE timezone_offset IS NOT NULL 
        AND accepted_offer = 1 
        AND phone IS NOT NULL
    ''')
    
    users = cursor.fetchall()
    print(f"👥 Найдено пользователей: {len(users)}")
    
    total_sent = 0
    
    for user_id, username, timezone_offset, city, phone in users:
        try:
            user_time = get_user_local_time(user_id)
            user_hour = user_time.hour
            user_minute = user_time.minute
            
            print(f"👤 Пользователь: @{username or user_id} ({city})")
            print(f"   Местное время: {user_time.strftime('%H:%M')}")
            
            cursor.execute('''
                SELECT uaa.arc_id, a.title, a.дата_начала
                FROM user_arc_access uaa
                JOIN arcs a ON uaa.arc_id = a.arc_id
                WHERE uaa.user_id = ?
            ''', (user_id,))
            
            user_arcs = cursor.fetchall()
            
            if not user_arcs:
                continue
            
            for arc_id, arc_title, arc_start in user_arcs:

                # ПРОВЕРКА: arc_start может быть None!
                if not arc_start:
                    print(f"   ⚠️ У части {arc_title} нет даты начала, пропускаем")
                    continue
                
                # ПРЕОБРАЗОВАНИЕ ДАТЫ С ПРОВЕРКОЙ
                try:
                    if isinstance(arc_start, str):
                        arc_start_date = datetime.fromisoformat(arc_start).date()
                    else:
                        arc_start_date = arc_start
                    
                    # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА
                    if arc_start_date is None:
                        print(f"   ⚠️ Не удалось получить дату начала для {arc_title}")
                        continue
                        
                except Exception as e:
                    print(f"   ⚠️ Ошибка преобразования даты {arc_start}: {e}")
                    continue
                
                # ТЕПЕРЬ МОЖНО БЕЗОПАСНО СРАВНИВАТЬ
                if user_time.date() < arc_start_date:
                    continue
                
                if isinstance(arc_start, str):
                    arc_start_date = datetime.fromisoformat(arc_start).date()
                else:
                    arc_start_date = arc_start
                
                if user_time.date() < arc_start_date:
                    continue
                
                current_day = (user_time.date() - arc_start_date).days + 1
                current_day = min(max(current_day, 1), 40)
                
                print(f"   🔄 Часть тренинга: {arc_title}, день: {current_day}")
                
                if user_hour == 6 and user_minute == 0:
                    notification = get_notification(1, current_day)
                    if notification:
                        if not check_notification_sent(user_id, notification['id'], current_day):
                            message = notification['text']
                            
                            cursor.execute('''
                                SELECT COUNT(*) 
                                FROM assignments a
                                JOIN days d ON a.day_id = d.day_id
                                WHERE d.arc_id = ? AND d.order_num = ?
                            ''', (arc_id, current_day))
                            
                            assignment_count = cursor.fetchone()[0]

                            message += f"\n\n**Все шаги живут в разделе 'Мои задания'**\n"
                            
                            try:
                                if notification.get('image_url'):
                                    await context.bot.send_photo(
                                        chat_id=user_id,
                                        photo=notification['image_url'],
                                        caption=message,
                                        parse_mode='Markdown'
                                    )
                                else:
                                    await context.bot.send_message(
                                        chat_id=user_id,
                                        text=message,
                                        parse_mode='Markdown'
                                    )
                                
                                mark_notification_sent(user_id, notification['id'], current_day)
                                total_sent += 1
                                print(f"   ✅ Отправлено утреннее уведомление")
                            except Exception as e:
                                print(f"   ❌ Ошибка отправки: {e}")

                # ========== ВЕЧЕРНИЕ УВЕДОМЛЕНИЯ 19:00 (тип 7) ==========
                if user_hour == 19 and user_minute == 0:
                    notification = get_notification(7, current_day)
                    if notification and notification.get('text'):
                        if not check_notification_sent(user_id, notification['id'], current_day):
                            # Берем только текст из таблицы, добавляем заголовок
                            message_text = notification['text']
                            
                            # Формируем финальное сообщение с заголовком
                            message = "СЕБЯ ВЕРНИ СЕБЕ\n\n" + message_text
                            
                            try:
                                if notification.get('image_url'):
                                    await context.bot.send_photo(
                                        chat_id=user_id,
                                        photo=notification['image_url'],
                                        caption=message,
                                        parse_mode=None  # Без форматирования
                                    )
                                else:
                                    await context.bot.send_message(
                                        chat_id=user_id,
                                        text=message,
                                        parse_mode=None  # Без форматирования
                                    )
                                
                                mark_notification_sent(user_id, notification['id'], current_day)
                                total_sent += 1
                                print(f"   ✅ Отправлено вечернее уведомление (19:00)")
                            except Exception as e:
                                print(f"   ❌ Ошибка отправки: {e}")

                # ========== ВЕЧЕРНИЕ УВЕДОМЛЕНИЯ 21:00 (тип 8) ==========
                if user_hour == 21 and user_minute == 0:
                    notification = get_notification(8, current_day)
                    if notification and notification.get('text'):
                        if not check_notification_sent(user_id, notification['id'], current_day):
                            # Берем только текст из таблицы, добавляем заголовок
                            message_text = notification['text']
                            
                            # Формируем финальное сообщение с заголовком
                            message = "СЕБЯ ВЕРНИ СЕБЕ\n\n" + message_text
                            
                            try:
                                if notification.get('image_url'):
                                    await context.bot.send_photo(
                                        chat_id=user_id,
                                        photo=notification['image_url'],
                                        caption=message,
                                        parse_mode=None  # Без форматирования
                                    )
                                else:
                                    await context.bot.send_message(
                                        chat_id=user_id,
                                        text=message,
                                        parse_mode=None  # Без форматирования
                                    )
                                
                                mark_notification_sent(user_id, notification['id'], current_day)
                                total_sent += 1
                                print(f"   ✅ Отправлено вечернее уведомление (21:00)")
                            except Exception as e:
                                print(f"   ❌ Ошибка отправки: {e}")

                # ========== ВЕЧЕРНИЕ УВЕДОМЛЕНИЯ 10:00 (тип 9) ==========
                if user_hour == 10 and user_minute == 0:
                    notification = get_notification(9, current_day)
                    if notification and notification.get('text'):
                        if not check_notification_sent(user_id, notification['id'], current_day):
                            # Берем только текст из таблицы, добавляем заголовок
                            message_text = notification['text']
                            
                            # Формируем финальное сообщение с заголовком
                            message = "СЕБЯ ВЕРНИ СЕБЕ\n\n" + message_text
                            
                            try:
                                if notification.get('image_url'):
                                    await context.bot.send_photo(
                                        chat_id=user_id,
                                        photo=notification['image_url'],
                                        caption=message,
                                        parse_mode=None  # Без форматирования
                                    )
                                else:
                                    await context.bot.send_message(
                                        chat_id=user_id,
                                        text=message,
                                        parse_mode=None  # Без форматирования
                                    )
                                
                                mark_notification_sent(user_id, notification['id'], current_day)
                                total_sent += 1
                                print(f"   ✅ Отправлено вечернее уведомление (21:00)")
                            except Exception as e:
                                print(f"   ❌ Ошибка отправки: {e}") 
               
                
                if user_hour == 9 and user_minute == 0:
                
                    cursor.execute('''
                        SELECT дата_начала 
                        FROM arcs 
                        WHERE arc_id = ?
                    ''', (arc_id,))
                    
                    arc_start_date_result = cursor.fetchone()
                    if arc_start_date_result:
                        arc_start_date = arc_start_date_result[0]
                        if isinstance(arc_start_date, str):
                            arc_start_date = datetime.fromisoformat(arc_start_date).date()
                        
                        days_before_start = (arc_start_date - user_time.date()).days
                        
                        if days_before_start == 2:
                            mass_notif = get_mass_notification(6, 2)
                            if mass_notif:
                                message = mass_notif['text']
                                message = message.replace('[номер_части]', arc_title)
                                message = message.replace('[дата_начала]', arc_start_date.strftime('%d.%m.%Y'))
                                
                                cursor.execute('''
                                    SELECT DISTINCT u.user_id 
                                    FROM users u
                                    WHERE u.accepted_offer = 1 
                                    AND u.phone IS NOT NULL
                                    AND u.user_id NOT IN (
                                        SELECT user_id FROM user_arc_access WHERE arc_id = ?
                                    )
                                ''', (arc_id,))
                                
                                all_users = cursor.fetchall()
                                
                                for (uid,) in all_users:
                                    try:
                                        if not check_notification_sent(uid, mass_notif['id']):
                                            await context.bot.send_message(
                                                chat_id=uid,
                                                text=message,
                                                parse_mode='Markdown'
                                            )
                                            mark_notification_sent(uid, mass_notif['id'])
                                            print(f"   📢 Отправлено уведомление о старте части тренинга пользователю {uid}")
                                    except Exception as e:
                                        print(f"   ❌ Ошибка отправки: {e}")
            
        except Exception as e:
            print(f"❌ Ошибка обработки пользователя {user_id}: {e}")
    
    conn.close()
    
    print(f"📊 Итог: отправлено уведомлений - {total_sent}")
    print("="*50)

async def buy_arc_with_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE, trial=False):
    """Покупка доступа через Юкассу с улучшенной обработкой"""
    user_id = update.message.from_user.id
    logger.info(f"Начало покупки: user={user_id}, trial={trial}")
    
    arc_title = context.user_data.get('current_arc_catalog')
    if not arc_title:
        await update.message.reply_text("❌ Ошибка: часть тренинга не выбрана")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT arc_id, price FROM arcs WHERE title = ?', (arc_title,))
        result = cursor.fetchone()
        
        if not result:
            await update.message.reply_text("❌ Раздел не найден")
            return
            
        arc_id, arc_price = result
        
        # УБИРАЕМ ограничение на 10 дней - можно покупать в любое время!
        # can_buy, message = check_if_can_buy_arc(user_id, arc_id)
        # if not can_buy:
        #     await update.message.reply_text(f"❌ {message}")
        #     return
        
        # Проверяем, есть ли уже доступ
        cursor.execute('SELECT 1 FROM user_arc_access WHERE user_id = ? AND arc_id = ?', (user_id, arc_id))
        already_has = cursor.fetchone()
        
        if already_has:
            await update.message.reply_text(
                "❌ **У вас уже есть доступ к этой части!**\n\n"
                "Проверьте раздел 'Мои задания'.",
                parse_mode='Markdown'
            )
            return
        
        if trial:
            amount = 100
            description = f"Пробный доступ к части тренинга '{arc_title}' (3 задания)"
        else:
            amount = arc_price
            description = f"Полный доступ к части тренинга '{arc_title}'"
        
        from database import create_yookassa_payment
        payment_url, payment_id = create_yookassa_payment(
            user_id, arc_id, amount, trial, description
        )
        
        if not payment_url:
            await update.message.reply_text(f"❌ Ошибка создания платежа: {payment_id}")
            return
        
        # Сохраняем информацию о платеже в context для отслеживания
        context.user_data[f'payment_{user_id}'] = {
            'payment_id': payment_id,
            'arc_id': arc_id,
            'arc_title': arc_title,
            'amount': amount,
            'trial': trial,
            'timestamp': datetime.now().isoformat()
        }
        
        keyboard = [
            [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
            [InlineKeyboardButton("✅ Я оплатил", callback_data=f"check_payment_{payment_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f"💳 **Оплата доступа к разделу**\n\n"
        message_text += f"🔄 Часть тренинга: {arc_title}\n"
        message_text += f"💰 Сумма: {amount}₽\n"
        
        if trial:
            message_text += f"🎯 Тип: Пробный доступ (первые 3 задания)\n"
            message_text += f"⏰ Срок: бессрочно (3 задания открываются сразу)\n\n"
        else:
            message_text += f"🎯 Тип: Полный доступ\n"
            message_text += f"⏰ Срок: до окончания части тренинга\n\n"
            
        message_text += "**Инструкция:**\n"
        message_text += "1. Нажмите '💳 Перейти к оплате'\n"
        message_text += "2. Оплатите в открывшемся окне\n"
        message_text += "3. Вернитесь в бот и нажмите '✅ Я оплатил'\n\n"
        message_text += f"📝 ID платежа: `{payment_id}`\n\n"
        message_text += "💡 **После оплаты доступ откроется автоматически в течение 1-2 минут.**"
        
        await update.message.reply_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        logger.info(f"Создан платеж: user={user_id}, arc={arc_id}, amount={amount}, yookassa_id={payment_id}")
        
    except Exception as e:
        logger.error(f"Ошибка покупки: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет статус платежа - ОБНОВЛЕННАЯ"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('check_payment_'):
        payment_id = query.data.replace('check_payment_', '')
        
        logger.info(f"Проверка платежа {payment_id} пользователем {query.from_user.id}")
        
        try:
            # 1. Проверяем статус через API Юкассы
            import base64
            from database import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_API_URL
            
            headers = {
                "Authorization": f"Basic {base64.b64encode(f'{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}'.encode()).decode()}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(f"{YOOKASSA_API_URL}/{payment_id}", headers=headers)
            
            if response.status_code == 200:
                payment_info = response.json()
                status = payment_info.get("status")
                
                # 2. Обновляем статус в нашей БД
                from database import update_payment_status
                update_payment_status(payment_id, status)
                
                if status == 'succeeded':
                    # 3. Получаем информацию о платеже
                    conn = sqlite3.connect('mentor_bot.db')
                    cursor = conn.cursor()
                    cursor.execute('SELECT user_id, arc_id, amount FROM payments WHERE yookassa_payment_id = ?', (payment_id,))
                    payment_data = cursor.fetchone()
                    
                    if payment_data:
                        user_id, arc_id, amount = payment_data
                        
                        # 4. Получаем название части
                        cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
                        arc_title = cursor.fetchone()[0]
                        
                        # ЗАКРЫВАЕМ соединение перед выдачей доступа
                        conn.close()
                        
                        # 5. ВЫДАЕМ ДОСТУП (отдельная операция)
                        if amount == 100:
                            logger.info(f"Выдаем пробный доступ: user={user_id}, arc={arc_id}")
                            from database import grant_trial_access
                            access_granted = grant_trial_access(user_id, arc_id)
                            
                            if access_granted:
                                await query.edit_message_text(
                                    f"✅ **Оплата подтверждена!**\n\n"
                                    f"💰 Сумма: {amount}₽\n"
                                    f"🔄 Часть: {arc_title}\n"
                                    f"🎯 Доступ: пробный (3 задания)\n\n"
                                    f"Начните обучение в разделе 'Мои задания'.",
                                    parse_mode='Markdown'
                                )
                                logger.info(f"✅ Пробный доступ выдан пользователю {user_id}")
                            else:
                                await query.edit_message_text(
                                    f"✅ **Оплата подтверждена, но возникла проблема с доступом.**\n\n"
                                    f"Пожалуйста, нажмите /fixaccess чтобы получить доступ вручную.",
                                    parse_mode='Markdown'
                                )
                        else:
                            # Полный доступ
                            from database import grant_arc_access
                            grant_arc_access(user_id, arc_id, 'paid')
                            
                            await query.edit_message_text(
                                f"✅ **Оплата подтверждена!**\n\n"
                                f"💰 Сумма: {amount}₽\n"
                                f"🔄 Часть: {arc_title}\n"
                                f"🎯 Доступ: полный\n\n"
                                f"Начните обучение в разделе 'Мои задания'.",
                                parse_mode='Markdown'
                            )
                        
                    else:
                        await query.edit_message_text(
                            "❌ **Платеж найден в Юкассе, но не в нашей базе.**\n\n"
                            "Пожалуйста, обратитесь в поддержку.",
                            parse_mode='Markdown'
                        )
                
                elif status == 'pending':
                    await query.answer(
                        "⏳ Платеж еще не подтвержден банком.\n"
                        "Обычно это занимает 1-2 минуты. Попробуйте через минуту.",
                        show_alert=True
                    )
                
                elif status == 'canceled':
                    await query.edit_message_text(
                        "❌ **Платеж отменен.**\n\n"
                        "Попробуйте оплатить снова или обратитесь в поддержку.",
                        parse_mode='Markdown'
                    )
                
                else:
                    await query.answer(f"Статус платежа: {status}", show_alert=True)
            
            elif response.status_code == 404:
                await query.answer("Платеж не найден в системе Юкассы", show_alert=True)
            
            else:
                error_msg = f"Ошибка API Юкассы: {response.status_code}"
                logger.error(error_msg)
                await query.answer(error_msg, show_alert=True)
        
        except Exception as e:
            error_msg = f"Ошибка проверки платежа: {str(e)}"
            logger.error(error_msg)
            await query.answer(error_msg, show_alert=True)

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
    if not text:
        return text
    
    import re
    
    # 1. Заменяем множественные подчеркивания (3+) на дефисы
    # Это САМАЯ ВАЖНАЯ ЧАСТЬ - исправляет ошибку "Can't parse entities"
    text = re.sub(r'_{3,}', '---', text)
    
    # 2. НЕ экранируем корректные пары символов!
    # Вместо этого убираем сломанные форматирование
    
    # Считаем количество открывающих и закрывающих символов
    open_stars = text.count('**')
    close_stars = text.count('**')
    open_underscores = text.count('__')
    close_underscores = text.count('__')
    
    # Если форматирование сломано (нечетное количество) - убираем ВСЕ такие символы
    if (open_stars + close_stars) % 2 != 0:
        text = text.replace('**', '')
    if (open_underscores + close_underscores) % 2 != 0:
        text = text.replace('__', '')
    
    # 3. Проверяем и исправляем одиночные звездочки и подчеркивания
    # Считаем количество * и _
    single_stars = len(re.findall(r'(?<!\*)\*(?!\*)', text))
    single_underscores = len(re.findall(r'(?<!_)_(?!_)', text))
    
    # Если нечетное количество - убираем все одиночные
    if single_stars % 2 != 0:
        text = re.sub(r'(?<!\*)\*(?!\*)', '', text)
    if single_underscores % 2 != 0:
        text = re.sub(r'(?<!_)_(?!_)', '', text)
    
    # 4. Убираем обратные кавычки если они не парные
    backticks = text.count('`')
    if backticks % 2 != 0:
        text = text.replace('`', '')
    
    # 5. Проверяем квадратные скобки для ссылок
    # Если есть [ но нет ] - убираем
    if '[' in text and ']' not in text:
        text = text.replace('[', '')
    if ']' in text and '[' not in text:
        text = text.replace(']', '')
    
    return text

async def show_seminar_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детали выбранного семинара"""
    seminar_name = update.message.text
    
    seminars = {
        "🎯 Часть первая: Самонаблюдение и намеренье": {
            "dates": "📅 22.12.2025 -30.01.2025",
            "time": "⏰ задания доступны с 6:00-12:00 по вашему времени установленному в профиле",
            "description": """
Часть первая: Самонаблюдение и намеренье(добавить описание)
Эта часть включат в себя выполнение 1 заадния которое открывается в 6:00. Вы должны успеть его выполнить за установленное время.
Отвечать на задание можно в трех вариациях: текстом, фотографией или тект+фото. Ваше выполненное задание отправится на проверу.
Как только психолог проверит его, вы получете обратную связь по нему и сможете изучить ее в соответвующем разделе.
""",
        }}
    if seminar_name not in seminars:
        await update.message.reply_text("❌ Информация о части не найден - на доработке")
        return
    
    info = seminars[seminar_name]
    
    message = f"**{seminar_name}**\n\n"
    message += f"{info['dates']}\n"
    message += f"{info['time']}\n\n"
    message += f"{info['description']}\n\n"
    
    keyboard = [
        ["🔙 Назад к частям тренинга"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_assignment_from_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Ищем задание в mapping (а не в старом available_assignments)
    mapping = context.user_data.get('assignments_mapping', [])
    assignment_info = None
    
    for info in mapping:
        if info['btn_text'] == text:
            assignment_info = info
            break
    
    if not assignment_info:
        await update.message.reply_text("❌ Задание не найдено")
        return
    
    assignment_id = assignment_info['assignment_id']
    arc_id = assignment_info['arc_id']  # ← ВАЖНО!
    
    # Проверяем статус задания
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
    
    # Сохраняем данные (ВАЖНО: arc_id!)
    context.user_data['current_assignment'] = assignment_info['title']
    context.user_data['current_assignment_id'] = assignment_id
    context.user_data['current_arc_id'] = arc_id
    
    # Получаем day_id из базы данных
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Исправленный запрос: получаем day_id и другую информацию
    cursor.execute('''
        SELECT day_id, content_text, доступно_до, title 
        FROM assignments 
        WHERE assignment_id = ?
    ''', (assignment_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ Ошибка: задание не найдено в базе")
        return
    
    day_id, content_text, available_until, assignment_title = result
    
    # Сохраняем day_id
    context.user_data['current_day_id'] = day_id

    from database import get_assignment_media
    media_data = get_assignment_media(assignment_id)
    
    # Показываем заголовок задания
    header = f"**📝 {assignment_title}**\n\n"
    if available_until and available_until != '22:00':
        header += f"⏰ **Выполните задание до 0:00, иначе оно засчитается пропущенным\n\n"

    await update.message.reply_text(header, parse_mode='Markdown')

    from database import get_assignment_media
    media_data = None

    try:
        media_data = get_assignment_media(assignment_id)
        print(f"🔍 Получены медиа для задания {assignment_id}: {media_data}")
    except Exception as e:
        print(f"⚠️ Ошибка получения медиа: {e}")
        media_data = {'photos': [], 'audios': [], 'video_url': None}

    # Показываем текст задания (используем send_long_message для длинных текстов)
    if content_text:
        await send_long_message(
            update, 
            content_text, 
            prefix="📋 **Задание:**",
            parse_mode='Markdown'
        )

    # 2. Фото (если есть и не пустой список)
    if media_data and media_data.get('photos'):
        photos = media_data['photos']
        if isinstance(photos, list) and photos:
            for i, photo_id in enumerate(photos[:5], 1):
                try:
                    await update.message.reply_photo(
                        photo=photo_id,
                        caption=f"🖼️ Фото {i} к заданию"
                    )
                except Exception as e:
                    print(f"🚨 Ошибка отправки фото {i}: {e}")

    # 3. Аудио (если есть и не пустой список)
    if media_data and media_data.get('audios'):
        audios = media_data['audios']
        if isinstance(audios, list) and audios:
            for i, audio_id in enumerate(audios[:3], 1):
                try:
                    await update.message.reply_audio(
                        audio=audio_id,
                        caption=f"🎵 Аудио {i} к заданию"
                    )
                except Exception as e:
                    print(f"🚨 Ошибка отправки аудио {i}: {e}")

    # 4. Видео (ссылка, если есть и не пустая)
    if media_data and media_data.get('video_url'):
        video_url = media_data['video_url']
        if video_url and video_url.strip():
            # ПРОСТОЙ ТЕКСТ БЕЗ MARKDOWN
            if 'youtube.com' in video_url or 'youtu.be' in video_url:
                await update.message.reply_text(f"🎬 Видео к заданию:\n{video_url}")
            elif video_url.startswith(('BAACAgI', 'CgACAgI', 'BAACAgQ', 'AgACAgI')):
                try:
                    await update.message.reply_video(
                        video=video_url,
                        caption="🎬 Видео к заданию"  # Простой текст
                    )
                except Exception as e:
                    print(f"🚨 Ошибка отправки видео: {e}")
                    await update.message.reply_text("🎬 Видео к заданию")
            else:
                await update.message.reply_text(f"🎬 Видео к заданию:\n{video_url}")

    # Показываем варианты ответа
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
    
    # Устанавливаем флаг что пользователь отвечает
    context.user_data['answering'] = True
    context.user_data['answer_text'] = None
    context.user_data['answer_files'] = []
    context.user_data['questions'] = []

async def show_in_progress_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает задания на проверке"""
    user_id = update.message.from_user.id
    
    in_progress = context.user_data.get('available_assignments', {}).get('in_progress', [])
    
    if not in_progress:
        await update.message.reply_text(
            "🟡 **Нет заданий на проверке.**\n\n"
            "Все отправленные задания уже проверены.",
            parse_mode='Markdown'
        )
        return
    
    message = "🟡 **ЗАДАНИЯ НА ПРОВЕРКЕ**\n\n"
    message += "Эти задания ждут ответа психолога:\n\n"
    
    for assignment in in_progress[:10]:
        message += f"• {assignment['title']} (день {assignment['day_num']})\n"
    
    message += "\n💬 Ответы появятся в разделе 'Ответ психолога'"
    
    keyboard = [["🔙 Назад в меню заданий"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

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
            btn_text = f"🏆 {arc_title} 🟡({new_count})"
        else:
            btn_text = f"🏆 {arc_title} ({total_count})"
        keyboard.append([btn_text])
        
        # Сохраняем mapping
        context.user_data['feedback_arc_map'][btn_text] = arc_id
    
    keyboard.append(["📚 В раздел Мои задания"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "💬 **Ответ психолога**\n\n"
        "Выберите марафон:\n"
        "🟡 - новые непросмотренные ответы",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_feedback_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает только завершенные задания"""
    user_id = update.message.from_user.id
    arc_text = update.message.text
    
    import re
    
    clean_title = arc_text.replace("🏆 ", "")
    
    clean_title = re.sub(r'\s*🟡\(\d+\)', '', clean_title)
    clean_title = re.sub(r'\s*\(\d+\)', '', clean_title)
    
    clean_title = clean_title.strip()

    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT arc_id, title FROM arcs WHERE title = ?', (clean_title,))
    result = cursor.fetchone()
    
    if not result and "Часть" in clean_title:
        match = re.search(r'Часть\s*(\d+)', clean_title)
        if match:
            part_num = match.group(1)
            cursor.execute('SELECT arc_id, title FROM arcs WHERE title LIKE ?', (f'%{part_num}%',))
            result = cursor.fetchone()
    
    if not result:
        conn.close()
        
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
    
    context.user_data['current_feedback_arc'] = arc_id
    context.user_data['current_feedback_arc_title'] = arc_title
    context.user_data['current_section'] = 'feedback_type'
    
    from database import get_feedback_counts
    new_count, completed_count = get_feedback_counts(user_id, arc_id)
    
    message = f"💬 **Ответы психолога**\n\n"
    message += f"{arc_title}\n\n"
    
    # ⭐ ИЗМЕНЕНИЕ: показываем только завершенные
    if completed_count == 0:
        message += "📭 **В этой части пока нет завершенных заданий.**\n\n"
    else:
        message += f"📊 **Статистика ответов:**\n"
        message += f"• ✅ Завершенные задания: {completed_count}\n\n"
    
    message += "Выберите раздел:"
    
    keyboard = []
    
    # ⭐ ИЗМЕНЕНИЕ: убираем "🟡 Новые ответы", оставляем только завершенные
    keyboard.append(["✅ Завершенные задания"])
    
    keyboard.append(["🔙 Назад к частям"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_feedback_list(update: Update, context: ContextTypes.DEFAULT_TYPE, viewed=None):
    """Показывает список завершенных заданий"""
    user_id = update.message.from_user.id
    arc_id = context.user_data.get('current_feedback_arc')
    arc_title = context.user_data.get('current_feedback_arc_title', f"Часть {arc_id}")
    
    if not arc_id:
        await update.message.reply_text("❌ Ошибка: часть не выбрана")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # ⭐ ИЗМЕНЕНИЕ: всегда ищем только завершенные задания (viewed=1 или status='approved')
    cursor.execute('''
        SELECT a.title, d.title as day_title, d.order_num,
               upa.teacher_comment, upa.answer_text,
               a.assignment_id
        FROM assignments a
        JOIN days d ON a.day_id = d.day_id
        JOIN user_progress_advanced upa ON a.assignment_id = upa.assignment_id
        WHERE upa.user_id = ? 
          AND upa.status = 'approved'
          AND d.arc_id = ?
        ORDER BY d.order_num, a.assignment_id
    ''', (user_id, arc_id))
    
    assignments = cursor.fetchall()
    conn.close()
    
    if not assignments:
        await update.message.reply_text(
            f"📭 **Нет завершенных заданий в части '{arc_title}'.**\n\n"
            f"После отправки задания оно сразу появится здесь.",
            parse_mode='Markdown'
        )
        
        keyboard = [
            ["✅ Завершенные задания"],
            ["🔙 Назад к частям"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "👇 Выберите действие:",
            reply_markup=reply_markup
        )
        return
    
    message = f"**✅ ЗАВЕРШЕННЫЕ ЗАДАНИЯ**\n\n"
    message += f"{arc_title}\n"
    message += f"**Найдено:** {len(assignments)} заданий\n\n"
    
    keyboard = []
    
    for i, (assignment_title, day_title, day_num, comment, answer, assignment_id) in enumerate(assignments[:15]):
        clean_title = assignment_title
        if assignment_title and " - " in assignment_title:
            parts = assignment_title.split(" - ")
            if len(parts) == 2 and "День" in parts[0]:
                clean_title = parts[1]
    
        btn_text = f"📝 {clean_title}"
        keyboard.append([btn_text])
        
        if 'feedback_assignments_map' not in context.user_data:
            context.user_data['feedback_assignments_map'] = {}
        context.user_data['feedback_assignments_map'][btn_text] = {
            'assignment_id': assignment_id,
            'assignment_title': assignment_title,
            'day_title': day_title,
            'day_num': day_num,
            'viewed': 1
        }
    
    keyboard.append(["🔙 Назад к частям","🔙 В главное меню"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    context.user_data['current_feedback_viewed'] = 1

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
    context.user_data['current_section'] = 'training_catalog'
    
    keyboard = [
        ["📖 Всё о марафоне"],      # Существующая функция show_about_course
        ["💰 Купить доступ"],    # Существующая функция show_course_main
        ["🔙 В главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎯 **Каталог тренинга 'Себя верни себе'**\n\n"
        "Выберите раздел:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

def get_current_and_future_arcs():
    """Получает текущую и будущие дуги для покупки"""
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Получаем ВСЕ дуги, кроме "О курсе" (arc_id = 0)
        cursor.execute('''
            SELECT arc_id, title, дата_начала, дата_окончания, price
            FROM arcs 
            WHERE arc_id > 0
            ORDER BY arc_id
        ''')
        
        arcs = cursor.fetchall()
        
        # Определяем текущую дугу (по датам)
        current_arc = None
        future_arcs = []
        past_arcs = []
        
        today = datetime.now().date()
        
        for arc in arcs:
            arc_id, title, start_date, end_date, price = arc
            
            # Конвертируем даты если нужно
            if isinstance(start_date, str):
                start_date = datetime.fromisoformat(start_date).date()
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date).date()
            
            if start_date <= today <= end_date:
                current_arc = (arc_id, title, price, "ТЕКУЩАЯ")
            elif today < start_date:
                future_arcs.append((arc_id, title, price, "БУДУЩАЯ"))
            else:
                past_arcs.append((arc_id, title, price, "ПРОШЕДШАЯ"))
        
        return {
            'current': current_arc,
            'future': future_arcs,
            'past': past_arcs,
            'all': arcs
        }
        
    except Exception as e:
        print(f"🚨 Ошибка получения дуг: {e}")
        return None
    finally:
        conn.close()

async def buy_arc_from_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о части и предлагает купить (обновленная логика)"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    print(f"🔍 buy_arc_from_catalog вызван с текстом: '{text}'")
    
    # Извлекаем название части из текста кнопки
    # Новые форматы: 
    # "🔄 Название (активный)", "⏳ Название (скоро начнётся)"
    import re
    
    # Убираем эмодзи и статус в скобках
    if " (активный)" in text:
        clean_text = text.replace("🔄 ", "").replace(" (активный)", "").strip()
        arc_status = "активный"
    elif " (скоро начнётся)" in text:
        clean_text = text.replace("⏳ ", "").replace(" (скоро начнётся)", "").strip()
        arc_status = "скоро начнётся"
    else:
        # Старый формат (на всякий случай)
        clean_text = text.replace("🔄 ", "").replace("⏳ ", "").strip()
        arc_status = None
    
    print(f"🔍 Очищенное название: '{clean_text}'")
    print(f"🔍 Статус: {arc_status}")
    
    # Ищем часть в available_arcs
    available_arcs = context.user_data.get('available_arcs', {})
    selected_arc = None
    
    # Ищем во всех категориях
    for category in ['current', 'future']:
        arcs_list = available_arcs.get(category, [])
        for arc in arcs_list:
            if arc['title'] == clean_text:
                selected_arc = arc
                selected_arc['status'] = arc_status or arc.get('status', 'неизвестно')
                print(f"✅ Найдена часть в {category}: {arc}")
                break
        if selected_arc:
            break
    
    # Если не нашли в контексте, ищем в БД
    if not selected_arc:
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        # Ищем точное совпадение
        cursor.execute('''
            SELECT arc_id, title, price, дата_начала, дата_окончания
            FROM arcs 
            WHERE title = ? 
            AND дата_начала IS NOT NULL 
            AND дата_окончания IS NOT NULL
        ''', (clean_text,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            arc_id, title, price, start_date, end_date = result
            selected_arc = {
                'arc_id': arc_id,
                'title': title,
                'price': price,
                'start_date': start_date,
                'end_date': end_date,
                'status': arc_status or 'неизвестно'
            }
            print(f"✅ Найдена часть в БД: {selected_arc}")
    
    if not selected_arc:
        await update.message.reply_text(
            f"❌ **Марафон '{clean_text}' не найден.**\n\n"
            f"Пожалуйста, вернитесь в каталог и выберите марафон заново.",
            parse_mode='Markdown'
        )
        return
    
    # Сохраняем выбранную часть
    context.user_data['current_arc_catalog'] = selected_arc['arc_id']
    context.user_data['part_status'] = selected_arc['status']
    
    # Формируем сообщение
    message = f"🎯 **{selected_arc['title']}**\n\n"
    
    if selected_arc['start_date'] and selected_arc['end_date']:
        message += f"📅 **Даты проведения:**\n"
        
        try:
            # Функция для преобразования формата даты
            def format_date(date_str):
                if not date_str:
                    return "не указана"
                
                # Если есть время - берем только дату
                if ' ' in str(date_str):
                    date_part = str(date_str).split()[0]
                else:
                    date_part = str(date_str)
                
                try:
                    # Парсим дату из формата YYYY-MM-DD
                    date_obj = datetime.strptime(date_part, '%Y-%m-%d')
                    # Преобразуем в DD.MM.YYYY
                    return date_obj.strftime('%d.%m.%Y')
                except:
                    # Если не удалось распарсить, возвращаем как есть
                    return date_part
            
            start_formatted = format_date(selected_arc['start_date'])
            end_formatted = format_date(selected_arc['end_date'])
            
            message += f"• Начало: {start_formatted}\n"
            message += f"• Окончание: {end_formatted}\n\n"
            
        except Exception as e:
            print(f"🚨 Ошибка форматирования даты: {e}")
            message += f"• Начало: {selected_arc['start_date']}\n"
            message += f"• Окончание: {selected_arc['end_date']}\n\n"
    
    message += f"💰 **Цена:** {selected_arc['price']}₽\n\n"
    
    # Описание в зависимости от статуса
    if selected_arc['status'] == 'активный':
        message += "🔄 **Этот марафон уже начался!**\n\n"
        message += "• Вы можете присоединиться и начать выполнять задания.\n\n"
        message += "• Вам будут доступны задания всех прошедших дней если вы начали не в день старта\n\n"
        message += "• Важный момент: с окончанием марафона, доступ к заданиям закрывается, а статус марафона меняется на завершённый.\n"
        
        # Предлагаем пробный доступ для активного марафона
        keyboard = [
            ["💰 Купить полный доступ", "🎁 Пробный доступ(3 дня)"],
            ["🔙 Назад к списку марафонов", "🔙 В главное меню"]
        ]
        
    elif selected_arc['status'] == 'скоро начнётся':
        message += "⏳ **Этот марафон скоро начнётся.**\n"
        message += "Вы можете зарегистрироваться заранее.\n\n"
        
        keyboard = [
            ["💰 Купить доступ заранее"],
            ["🔙 Назад к списку марафонов", "🔙 В главное меню"]
        ]
        
    else:  # неизвестный статус
        message += "❓ **Статус марафона неизвестен.**\n\n"
        
        keyboard = [
            ["💰 Купить доступ"],
            ["🔙 Назад к списку марафонов", "🔙 В главное меню"]
        ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
# Webhook обработчик для Юкассы
async def yookassa_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик webhook от Юкассы"""
    try:
        data = json.loads(update.message.text)
        logger.info(f"Получен webhook от Юкассы: {data}")
        
        from database import handle_yookassa_webhook
        success, message = handle_yookassa_webhook(data)
        
        if success:
            logger.info(f"Webhook обработан успешно: {message}")
            return {'status': 'ok', 'message': message}
        else:
            logger.error(f"Ошибка обработки webhook: {message}")
            return {'status': 'error', 'message': message}
            
    except Exception as e:
        logger.error(f"Ошибка в webhook обработчике: {e}")
        return {'status': 'error', 'message': str(e)}

async def check_payment_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки статуса платежей - ИСПРАВЛЕННАЯ"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда только для администраторов")
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Сначала проверим какие колонки есть в таблице
        cursor.execute("PRAGMA table_info(payments)")
        columns = [col[1] for col in cursor.fetchall()]
        logger.info(f"Колонки в payments: {columns}")
        
        # Если есть колонка 'id' вместо 'payment_id'
        if 'id' in columns and 'payment_id' not in columns:
            # Используем 'id' как идентификатор
            cursor.execute('''
                SELECT id, user_id, arc_id, amount, status, yookassa_payment_id, created_at
                FROM payments 
                ORDER BY created_at DESC 
                LIMIT 10
            ''')
        elif 'payment_id' in columns:
            # Используем 'payment_id'
            cursor.execute('''
                SELECT payment_id, user_id, arc_id, amount, status, yookassa_payment_id, created_at
                FROM payments 
                ORDER BY created_at DESC 
                LIMIT 10
            ''')
        else:
            # Таблица может быть пустой или не создана
            await update.message.reply_text("📭 Таблица платежей не создана или пустая")
            conn.close()
            return
        
        payments = cursor.fetchall()
        
        if not payments:
            await update.message.reply_text("📭 Нет платежей в истории")
            conn.close()
            return
        
        message = "📋 **Последние платежи:**\n\n"
        
        for payment in payments:
            # Определяем структуру платежа
            if len(payment) >= 7:
                # Если первая колонка - id
                if isinstance(payment[0], int):
                    payment_id, user_id, arc_id, amount, status, yookassa_id, created_at = payment
                else:
                    # Пропускаем некорректные записи
                    continue
            else:
                continue
            
            # Получаем информацию о дуге
            cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
            arc_result = cursor.fetchone()
            arc_title = arc_result[0] if arc_result else f"Часть {arc_id}"
            
            status_icon = {
                'pending': '⏳',
                'succeeded': '✅',
                'canceled': '❌'
            }.get(status, '❓')
            
            message += f"{status_icon} **ID:** {payment_id}\n"
            message += f"👤 **User:** {user_id}\n"
            message += f"💰 **Сумма:** {amount}₽\n"
            message += f"🔄 **Часть:** {arc_title}\n"
            message += f"📊 **Статус:** {status}\n"
            message += f"📅 **Создан:** {created_at[:19] if created_at else 'N/A'}\n"
            if yookassa_id:
                message += f"🔗 **Юкасса:** `{yookassa_id[:15]}...`\n"
            message += "━━━━━━━━━━━━━━━━\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в check_payment_status: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()

async def test_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки платежей"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    # Создаем тестовый платеж
    test_arc_id = 1
    test_amount = 100  # Пробный доступ
    
    from database import create_yookassa_payment
    payment_url, payment_id = create_yookassa_payment(
        user_id, test_arc_id, test_amount, True, "Тестовый платеж"
    )
    
    if payment_url:
        await update.message.reply_text(
            f"✅ Тестовый платеж создан\n"
            f"💰 Сумма: {test_amount}₽\n"
            f"🔗 URL: {payment_url}\n"
            f"📝 ID: {payment_id}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"❌ Ошибка: {payment_id}")

async def test_payment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тест платежа - создает платеж 100₽ для тестирования"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    # Используем Часть 1 для теста
    test_arc_id = 1
    test_amount = 100  # Пробный доступ
    
    from database import create_yookassa_payment
    payment_url, payment_id = create_yookassa_payment(
        user_id, test_arc_id, test_amount, True, "ТЕСТОВЫЙ ПЛАТЕЖ"
    )
    
    if payment_url:
        keyboard = [
            [InlineKeyboardButton("💳 Тестовая оплата", url=payment_url)],
            [InlineKeyboardButton("✅ Я оплатил (тест)", callback_data=f"check_payment_{payment_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🧪 **ТЕСТОВЫЙ ПЛАТЕЖ**\n\n"
            f"💰 Сумма: {test_amount}₽ (пробный доступ)\n"
            f"🔗 Юкасса: {payment_url[:50]}...\n"
            f"📝 ID: `{payment_id}`\n\n"
            f"**Тестовая карта Юкассы:**\n"
            f"• Номер: `5555 5555 5555 4444`\n"
            f"• Срок: 12/34\n"
            f"• CVC: 123\n"
            f"• Имя: TEST TEST\n\n"
            f"После оплаты нажми '✅ Я оплатил (тест)'",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"❌ Ошибка: {payment_id}")

async def check_db_structure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает структуру таблицы payments (упрощенная)"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # Показываем только таблицу payments
        message = "📊 **Таблица payments:**\n\n"
        
        cursor.execute("PRAGMA table_info(payments)")
        columns = cursor.fetchall()
        
        if not columns:
            message += "❌ Таблица не существует\n"
        else:
            for col in columns:
                col_id, col_name, col_type, notnull, default_val, pk = col
                pk_mark = " 🔑" if pk else ""
                message += f"• `{col_name}` ({col_type}){pk_mark}\n"
        
        # Проверяем есть ли данные
        cursor.execute("SELECT COUNT(*) FROM payments")
        count = cursor.fetchone()[0]
        message += f"\n📊 Записей в таблице: {count}"
        
        if count > 0:
            cursor.execute("SELECT status, COUNT(*) FROM payments GROUP BY status")
            statuses = cursor.fetchall()
            message += "\n📈 По статусам:\n"
            for status, cnt in statuses:
                message += f"  • {status}: {cnt}\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в check_db_structure: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()

async def create_payments_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создает таблицу payments если её нет"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
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
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Таблица payments создана/проверена")

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
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    message = "🧪 **ТЕСТ ПЛАТЕЖНОЙ СИСТЕМЫ**\n\n"
    
    # 1. Проверяем таблицу payments
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Проверяем существует ли таблица
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payments'")
    payments_exists = cursor.fetchone()
    
    if not payments_exists:
        message += "❌ Таблица `payments` не существует\n"
        # Создаем таблицу
        try:
            cursor.execute('''
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    arc_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    yookassa_payment_id TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            ''')
            conn.commit()
            message += "✅ Таблица `payments` создана\n"
        except Exception as e:
            message += f"❌ Ошибка создания таблицы: {str(e)}\n"
    else:
        message += "✅ Таблица `payments` существует\n"
    
    # 2. Проверяем структуру
    cursor.execute("PRAGMA table_info(payments)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    
    message += f"📊 Колонки: {', '.join(column_names)}\n"
    
    # 3. Проверяем тестовые ключи Юкассы
    from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
    
    if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
        if "test_" in YOOKASSA_SECRET_KEY:
            message += "✅ Тестовые ключи Юкассы настроены\n"
        else:
            message += "⚠️ Ключи Юкассы могут быть рабочими (не тестовые)\n"
    else:
        message += "❌ Ключи Юкассы не настроены в config.py\n"
    
    # 4. Проверяем есть ли тестовые платежи
    cursor.execute("SELECT COUNT(*) FROM payments WHERE status = 'succeeded'")
    succeeded_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'")
    pending_count = cursor.fetchone()[0]
    
    message += f"\n📈 **Статистика платежей:**\n"
    message += f"• Успешных: {succeeded_count}\n"
    message += f"• Ожидающих: {pending_count}\n"
    message += f"• Всего: {succeeded_count + pending_count}\n"
    
    conn.close()
    
    # 5. Инструкция для теста
    message += "\n🎯 **Инструкция для теста:**\n"
    message += "1. Нажми `Пробный доступ (100₽)` в разделе покупки\n"
    message += "2. Оплати тестовой картой: `5555 5555 5555 4444`\n"
    message += "3. Нажми `✅ Я оплатил` в боте\n"
    message += "4. Проверь доступ командой `/myaccess`\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def recreate_payments_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересоздает таблицу payments с правильной структурой"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    try:
        # 1. Удаляем старую таблицу если существует
        cursor.execute("DROP TABLE IF EXISTS payments")
        
        # 2. Создаем новую таблицу с правильной структурой
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
        
        # 3. Создаем индекс для быстрого поиска
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_yookassa_id ON payments(yookassa_payment_id)')
        
        conn.commit()
        
        await update.message.reply_text(
            "✅ **Таблица payments пересоздана с правильной структурой!**\n\n"
            "Новые колонки:\n"
            "• `id` - идентификатор платежа\n"
            "• `user_id` - ID пользователя\n"  
            "• `arc_id` - ID части тренинга\n"
            "• `amount` - сумма платежа\n"
            "• `status` - статус (pending/succeeded/canceled)\n"
            "• `yookassa_payment_id` - ID платежа в Юкассе\n"
            "• `created_at` - дата создания\n"
            "• `completed_at` - дата завершения\n"
            "• `metadata` - дополнительные данные\n\n"
            "Теперь можно тестировать платежи!",
            parse_mode='Markdown'
        )
        
        logger.info("Таблица payments пересоздана с новой структурой")
        
    except Exception as e:
        logger.error(f"Ошибка пересоздания таблицы payments: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()

async def test_yookassa_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестирует подключение к Юкассе - ИСПРАВЛЕННАЯ"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    from database import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_API_URL
    import requests
    import base64
    
    message = "🔑 Тест ключей Юкассы:\n\n"
    message += f"Shop ID: {YOOKASSA_SHOP_ID}\n"
    message += f"Secret Key: {YOOKASSA_SECRET_KEY[:15]}...\n"
    message += f"API URL: {YOOKASSA_API_URL}\n\n"
    
    try:
        # Теперь тестируем создание МАЛЕНЬКОГО тестового платежа (1 рубль)
        auth_string = f'{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}'
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json",
            "Idempotence-Key": str(uuid.uuid4())
        }
        
        # Тестовые данные платежа (1 рубль)
        payment_data = {
            "amount": {
                "value": "1.00",
                "currency": "RUB"
            },
            "payment_method_data": {
                "type": "bank_card"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/PersonalityGrowth_bot"
            },
            "description": "Тестовый платеж для проверки подключения",
            "capture": True
        }
        
        response = requests.post(YOOKASSA_API_URL, json=payment_data, headers=headers, timeout=10)
        
        if response.status_code == 200:
            payment_info = response.json()
            payment_id = payment_info.get("id", "N/A")
            confirmation_url = payment_info.get("confirmation", {}).get("confirmation_url", "N/A")
            
            message += "✅ **Ключи рабочие! Платеж создан!**\n"
            message += f"ID платежа: {payment_id}\n"
            message += f"URL для оплаты: {confirmation_url[:50]}...\n\n"
            message += "⚠️ **ЭТО ТЕСТОВЫЙ ПЛАТЕЖ на 1 рубль!**\n"
            message += "Не оплачивай его, просто проверь что ссылка открывается.\n"
            
            # Сразу отменяем тестовый платеж
            try:
                cancel_headers = headers.copy()
                cancel_headers["Idempotence-Key"] = str(uuid.uuid4())
                cancel_response = requests.post(
                    f"{YOOKASSA_API_URL}/{payment_id}/cancel",
                    headers=cancel_headers,
                    timeout=5
                )
                if cancel_response.status_code == 200:
                    message += "✅ Тестовый платеж отменен\n"
            except:
                message += "⚠️ Не удалось отменить тестовый платеж (не страшно)\n"
                
        elif response.status_code == 401:
            message += "❌ **Ошибка авторизации (401)**\n"
            message += "Проверь Shop ID и Secret Key\n"
        else:
            message += f"❌ Ошибка: код {response.status_code}\n"
            
            # Показываем ошибку
            try:
                error_data = response.json()
                message += f"Описание: {error_data.get('description', 'N/A')}\n"
                message += f"Код: {error_data.get('code', 'N/A')}\n"
            except:
                message += f"Ответ: {response.text[:200]}\n"
            
    except requests.exceptions.Timeout:
        message += "❌ Таймаут подключения к Юкассе\n"
    except requests.exceptions.ConnectionError:
        message += "❌ Ошибка подключения к Юкассе\n"
    except Exception as e:
        message += f"❌ Ошибка: {str(e)[:100]}\n"
    
    # Отправляем частями если длинное
    if len(message) > 4000:
        parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(message)

async def check_my_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет доступы пользователя"""
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.arc_id, a.title, uaa.access_type
        FROM user_arc_access uaa
        JOIN arcs a ON uaa.arc_id = a.arc_id
        WHERE uaa.user_id = ?
        ORDER BY a.arc_id
    ''', (user_id,))
    
    accesses = cursor.fetchall()
    conn.close()
    
    if not accesses:
        await update.message.reply_text("📭 У вас нет доступов к частям тренинга")
        return
    
    message = "✅ **Ваши доступы:**\n\n"
    for arc_id, title, access_type in accesses:
        type_text = "пробный (3 задания)" if access_type == 'trial' else "полный"
        message += f"• {title} - {type_text}\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def debug_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последний платеж пользователя"""
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, arc_id, amount, status, yookassa_payment_id, created_at
        FROM payments 
        WHERE user_id = ?
        ORDER BY created_at DESC 
        LIMIT 1
    ''', (user_id,))
    
    payment = cursor.fetchone()
    conn.close()
    
    if payment:
        pid, arc_id, amount, status, yookassa_id, created_at = payment
        message = f"📋 **Последний платеж:**\n\n"
        message += f"💰 Сумма: {amount}₽\n"
        message += f"📊 Статус: {status}\n"
        message += f"📅 Дата: {created_at}\n"
        message += f"🔗 Юкасса ID: `{yookassa_id}`\n\n"
        
        # Проверяем доступ
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM user_arc_access WHERE user_id = ? AND arc_id = ?', (user_id, arc_id))
        has_access = cursor.fetchone()
        conn.close()
        
        if has_access:
            message += "✅ Доступ ВЫДАН в БД"
        else:
            message += "❌ Доступа НЕТ в БД"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text("📭 У вас нет платежей")

async def debug_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все активные колбэки"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    # Последние 5 платежей пользователя
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT yookassa_payment_id, status, created_at 
        FROM payments 
        WHERE user_id = ?
        ORDER BY created_at DESC 
        LIMIT 5
    ''', (user_id,))
    
    payments = cursor.fetchall()
    conn.close()
    
    message = "🔍 **Активные платежи для колбэков:**\n\n"
    
    for yookassa_id, status, created_at in payments:
        callback_data = f"check_payment_{yookassa_id}"
        message += f"• `{callback_data}`\n"
        message += f"  Статус: {status}, Дата: {created_at}\n\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def simple_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Простой тест колбэка"""
    keyboard = [[
        InlineKeyboardButton("✅ Тест оплаты", callback_data="check_payment_TEST123")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Нажмите кнопку чтобы проверить работу колбэка:",
        reply_markup=reply_markup
    )

async def fix_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Исправляет доступ для пользователя"""
    user_id = update.message.from_user.id
    
    # Можно сделать для админа или для себя
    target_user_id = user_id  # По умолчанию себе
    
    # Если админ, может указать другой ID
    if is_admin(user_id) and context.args:
        try:
            target_user_id = int(context.args[0])
        except:
            target_user_id = user_id
    
    from database import grant_trial_access
    success = grant_trial_access(target_user_id, 1)  # Часть 1
    
    if success:
        await update.message.reply_text(
            f"✅ Доступ к Части 1 выдан пользователю {target_user_id}\n"
            f"Проверь раздел 'Мои задания'"
        )
    else:
        await update.message.reply_text(
            f"❌ Не удалось выдать доступ. Проверь логи."
        )

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
    user_id = update.message.from_user.id
    
    from database import get_user_offer_status
    status = get_user_offer_status(user_id)
    
    message = f"🔍 **Статус регистрации user_id={user_id}:**\n\n"
    message += f"✅ Оферта: {'принята' if status['accepted_offer'] else 'не принята'}\n"
    message += f"📱 Телефон: {status['phone'] or 'нет'}\n"
    message += f"📝 ФИО: {'есть' if status['has_fio'] else 'нет'}\n"
    
    # Покажем что в БД
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT accepted_offer, phone, fio FROM users WHERE user_id = ?', (user_id,))
    db_data = cursor.fetchone()
    conn.close()
    
    if db_data:
        message += f"\n📊 **Данные в БД:**\n"
        message += f"accepted_offer: {db_data[0]}\n"
        message += f"phone: {db_data[1]}\n"
        message += f"fio: {db_data[2]}\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def reset_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает регистрацию для тестирования"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Сбрасываем данные регистрации
    cursor.execute('''
        UPDATE users 
        SET accepted_offer = 0,
            phone = NULL,
            fio = NULL
        WHERE user_id = ?
    ''', (user_id,))
    
    conn.commit()
    conn.close()
    
    # Очищаем user_data
    context.user_data.clear()
    
    await update.message.reply_text("✅ Регистрация сброшена. Начните заново.")

async def debug_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий статус регистрации и user_data"""
    user_id = update.message.from_user.id
    
    from database import get_user_offer_status
    status = get_user_offer_status(user_id)
    
    message = f"🧭 **Текущий поток регистрации:**\n\n"
    message += f"user_id: {user_id}\n"
    message += f"✅ Оферта: {'ДА' if status['accepted_offer'] else 'НЕТ'}\n"
    message += f"📱 Телефон: {'ДА' if status['has_phone'] else 'НЕТ'} ({status['phone']})\n"
    message += f"📝 ФИО: {'ДА' if status['has_fio'] else 'НЕТ'}\n\n"
    
    message += f"📋 **user_data:**\n"
    for key, value in context.user_data.items():
        message += f"  {key}: {value}\n"
    
    await update.message.reply_text(message)

async def start_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает процесс создания уведомления"""
    context.user_data['notification_stage'] = 'select_recipients'
    
    keyboard = [
        ["📢 Всем в бот"],
        ["✅ Только полный доступ"],
        ["🎁 Только пробный доступ"],
        ["🔙 Назад к инструментам"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🔔 **Отправка уведомления**\n\n"
        "Выберите получателей:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_notification_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает создание уведомления"""
    text = update.message.text
    
    # 1. Выбор получателей
    if context.user_data.get('notification_stage') == 'select_recipients':
        recipient_types = {
            "📢 Всем в бот": "all",
            "✅ Только полный доступ": "full",
            "🎁 Только пробный доступ": "trial"
        }
        
        if text in recipient_types:
            context.user_data['notification_recipients'] = recipient_types[text]
            context.user_data['notification_stage'] = 'waiting_content'
            
            await update.message.reply_text(
                "✏️ **Напишите уведомление одним сообщением.**\n\n"
                "Можно прикрепить:\n"
                "• Текст\n"
                "• Текст + фото\n"
                "• Текст + файл\n\n"
                "Отправьте сообщение как обычно в Telegram.",
                reply_markup=ReplyKeyboardMarkup([["🔙 Отменить"]], resize_keyboard=True),
                parse_mode='Markdown'
            )
            return
    
    # 2. Обработка отправленного контента
    elif context.user_data.get('notification_stage') == 'waiting_content':
        # Здесь будем обрабатывать текст/фото в отдельной функции
        await process_notification_content(update, context)
        return
    
    # 3. Подтверждение отправки
    elif context.user_data.get('notification_stage') == 'preview':
        if text == "📤 Отправить":
            await send_notification_final(update, context)
        elif text == "✏️ Изменить":
            context.user_data['notification_stage'] = 'waiting_content'
            await update.message.reply_text(
                "✏️ Отправьте новое сообщение с уведомлением:",
                reply_markup=ReplyKeyboardMarkup([["🔙 Отменить"]], resize_keyboard=True)
            )
        elif text == "❌ Отменить":
            await admin_tools_menu(update, context)

async def process_notification_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает контент уведомления (текст + медиа)"""

    # Если нажата кнопка "Отменить" - обрабатываем отдельно
    if update.message.text == "🔙 Отменить":
        # Очищаем все данные
        keys_to_remove = []
        for key in context.user_data.keys():
            if key.startswith('notification_'):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            context.user_data.pop(key, None)
        
        await admin_tools_menu(update, context)
        return
    
    # ВАЖНО: Проверяем caption (текст прикрепленный к фото/документу)
    if update.message.caption:
        context.user_data['notification_text'] = update.message.caption
    
    # Проверяем обычный текст (если отправлен без медиа)
    elif update.message.text and update.message.text != "🔙 Отменить":
        context.user_data['notification_text'] = update.message.text
    
    # Сохраняем фото если есть
    if update.message.photo:
        context.user_data['notification_photo'] = update.message.photo[-1].file_id
    
    # Сохраняем документ если есть  
    if update.message.document:
        context.user_data['notification_document'] = update.message.document.file_id
    
    # Проверяем, есть ли какой-то контент
    has_content = ('notification_text' in context.user_data or 
                   'notification_photo' in context.user_data or 
                   'notification_document' in context.user_data)
    
    if not has_content:
        await update.message.reply_text(
            "❌ Вы не отправили контент для уведомления. Попробуйте снова.",
            reply_markup=ReplyKeyboardMarkup([["🔙 Отменить"]], resize_keyboard=True)
        )
        return
    
    # Получаем количество получателей
    from database import get_users_for_notification
    recipient_type = context.user_data.get('notification_recipients', 'all')
    users = get_users_for_notification(recipient_type)
    
    # Показываем предпросмотр
    context.user_data['notification_stage'] = 'preview'
    context.user_data['notification_users'] = users
    
    keyboard = [
        ["📤 Отправить"],
        ["✏️ Изменить"],
        ["❌ Отменить"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Формируем сообщение о предпросмотре
    message_text = f"✅ **Уведомление зафиксировано!**\n\n"
    message_text += f"**Получателей:** {len(users)} человек\n"
    
    recipient_names = {
        'all': 'Все участники',
        'full': 'Только полный доступ',
        'trial': 'Только пробный доступ'
    }
    message_text += f"**Фильтр:** {recipient_names.get(recipient_type, 'Все участники')}\n"
    
    # Добавляем информацию о типе контента
    content_type = []
    if 'notification_text' in context.user_data:
        content_type.append("текст")
    if 'notification_photo' in context.user_data:
        content_type.append("фото")
    if 'notification_document' in context.user_data:
        content_type.append("файл")
    
    if content_type:
        message_text += f"**Контент:** {', '.join(content_type)}\n"
    
    message_text += "\n**Предпросмотр вашего уведомления:**"
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    # Показываем как выглядит уведомление
    try:
        if 'notification_photo' in context.user_data:
            caption = context.user_data.get('notification_text', '')
            await update.message.reply_photo(
                photo=context.user_data['notification_photo'],
                caption=caption if caption else None,
                parse_mode='Markdown' if caption else None
            )
        elif 'notification_document' in context.user_data:
            caption = context.user_data.get('notification_text', '')
            await update.message.reply_document(
                document=context.user_data['notification_document'],
                caption=caption if caption else None,
                parse_mode='Markdown' if caption else None
            )
        elif 'notification_text' in context.user_data:
            text = context.user_data['notification_text']
            # Разбиваем длинные тексты
            if len(text) > 4000:
                parts = split_message(text)
                for part in parts:
                    await update.message.reply_text(part, parse_mode='Markdown')
            else:
                await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        print(f"🚨 Ошибка при показе предпросмотра: {e}")
        await update.message.reply_text(
            "⚠️ Не удалось показать предпросмотр, но уведомление сохранено.",
            reply_markup=reply_markup
        )

async def send_notification_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет уведомление всем получателям"""
    users = context.user_data.get('notification_users', [])
    text = context.user_data.get('notification_text', '')
    photo = context.user_data.get('notification_photo')
    document = context.user_data.get('notification_document')
    
    if not users:
        await update.message.reply_text("❌ Нет получателей для отправки")
        return
    
    success = 0
    failed = 0
    failed_users = []  # Для логирования
    
    await update.message.reply_text(f"📤 Отправляю уведомление {len(users)} пользователям...")
    
    for user_id, fio, username in users:
        try:
            if photo:
                # Отправляем фото с текстом (caption)
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=text or None,  # caption может быть пустым
                    parse_mode='Markdown' if text else None
                )
            elif document:
                # Отправляем документ с текстом (caption)
                await context.bot.send_document(
                    chat_id=user_id,
                    document=document,
                    caption=text or None,
                    parse_mode='Markdown' if text else None
                )
            elif text:
                # Отправляем только текст
                if len(text) > 4000:
                    # Разбиваем длинные тексты
                    parts = split_message(text)
                    for i, part in enumerate(parts):
                        if i == 0:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=part,
                                parse_mode='Markdown'
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=f"📋 (продолжение)\n\n{part}",
                                parse_mode='Markdown'
                            )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode='Markdown'
                    )
            success += 1
            
            # Делаем небольшую задержку чтобы не превысить лимиты Telegram
            if success % 20 == 0:
                import asyncio
                await asyncio.sleep(1)
                
        except Exception as e:
            print(f"🚨 Ошибка отправки {user_id}: {e}")
            failed += 1
            failed_users.append(str(user_id))
    
    # Сохраняем лог
    from database import save_notification_log
    admin_id = update.message.from_user.id
    recipient_type = context.user_data.get('notification_recipients', 'all')
    
    save_notification_log(
        admin_id=admin_id,
        recipient_type=recipient_type,
        text=text,
        photo_id=photo,
        success_count=success,
        fail_count=failed
    )
    
    # Очищаем данные
    for key in ['notification_stage', 'notification_recipients', 'notification_text',
                'notification_photo', 'notification_document', 'notification_users']:
        context.user_data.pop(key, None)
    
    # Показываем результат
    result_text = f"✅ **Рассылка завершена!**\n\n"
    result_text += f"📊 **Результат:**\n"
    result_text += f"• ✅ Успешно: {success}\n"
    result_text += f"• ❌ Не доставлено: {failed}\n"
    result_text += f"• 👥 Всего: {len(users)}\n"
    
    if failed > 0 and len(failed_users) > 0:
        result_text += f"\n⚠️ **Не доставлено пользователям:**\n"
        result_text += f"{', '.join(failed_users[:10])}"  # Показываем только первые 10
        if len(failed_users) > 10:
            result_text += f" и еще {len(failed_users) - 10}"
    
    await update.message.reply_text(
        result_text,
        parse_mode='Markdown'
    )
    
    await admin_tools_menu(update, context)


async def update_database_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ПОЛНОЕ обновление БД: создает все таблицы, добавляет колонки, сохраняет данные"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Только для администраторов")
        return
    
    import os
    import time
    
    # Создаем backup перед изменениями
    backup_name = f"mentor_bot.db.backup_{int(time.time())}"
    
    try:
        import shutil
        shutil.copy2('mentor_bot.db', backup_name)
        logger.info(f"✅ Создан backup: {backup_name}")
    except Exception as e:
        logger.error(f"❌ Не удалось создать backup: {e}")
    
    conn = None
    try:
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        
        steps = []
        step_number = 1
        
        # === 1. ОСНОВНЫЕ ТАБЛИЦЫ ПОЛЬЗОВАТЕЛЕЙ И СТРУКТУРЫ ===
        
        # 1.1 Таблица users (добавляем недостающие колонки)
        cursor.execute("PRAGMA table_info(users)")
        user_columns = [col[1] for col in cursor.fetchall()]
        
        required_user_columns = [
            ('accepted_offer', 'BOOLEAN DEFAULT 0'),
            ('phone', 'TEXT'),
            ('accepted_service_offer', 'BOOLEAN DEFAULT 0'),
            ('accepted_offer_date', 'TIMESTAMP'),
            ('accepted_service_offer_date', 'TIMESTAMP'),
            ('is_blocked', 'BOOLEAN DEFAULT 0')
        ]
        
        for col_name, col_type in required_user_columns:
            if col_name not in user_columns:
                try:
                    cursor.execute(f'ALTER TABLE users ADD COLUMN {col_name} {col_type}')
                    steps.append(f"{step_number}. ✅ Добавлена колонка `{col_name}` в users")
                    step_number += 1
                except Exception as e:
                    steps.append(f"{step_number}. ⚠️ Не удалось добавить `{col_name}`: {str(e)[:50]}")
                    step_number += 1
        
        # === 2. ТАБЛИЦЫ ДОСТУПА И ПЛАТЕЖЕЙ ===
        
        # 2.1 Таблица user_arc_access
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_arc_access (
                user_id INTEGER,
                arc_id INTEGER,
                access_type TEXT,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                PRIMARY KEY (user_id, arc_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (arc_id) REFERENCES arcs(arc_id)
            )
        ''')
        steps.append(f"{step_number}. ✅ Таблица `user_arc_access` создана/проверена")
        step_number += 1
        
        # 2.2 Таблица trial_assignments_access
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
        steps.append(f"{step_number}. ✅ Таблица `trial_assignments_access` создана/проверена")
        step_number += 1
        
        # 2.3 Таблица payments (аккуратная миграция)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payments'")
        payments_exists = cursor.fetchone()
        
        if payments_exists:
            # Проверяем структуру существующей таблицы
            cursor.execute("PRAGMA table_info(payments)")
            payments_columns = [col[1] for col in cursor.fetchall()]
            
            required_payments_columns = ['arc_id', 'amount', 'status', 'yookassa_payment_id']
            
            if not all(col in payments_columns for col in required_payments_columns):
                # Сохраняем старые данные если есть
                cursor.execute("SELECT COUNT(*) FROM payments")
                old_count = cursor.fetchone()[0]
                
                if old_count > 0:
                    # Создаем временную таблицу для сохранения данных
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS payments_backup (
                            user_id INTEGER,
                            course_id INTEGER,
                            paid_at TIMESTAMP
                        )
                    ''')
                    
                    # Копируем данные
                    cursor.execute('INSERT INTO payments_backup SELECT * FROM payments')
                    steps.append(f"{step_number}. ⚠️ Сохранено {old_count} старых платежей в backup")
                    step_number += 1
                
                # Удаляем старую таблицу
                cursor.execute('DROP TABLE payments')
                steps.append(f"{step_number}. 🔄 Удалена старая таблица payments")
                step_number += 1
        
        # Создаем новую таблицу payments
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
                trial BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (arc_id) REFERENCES arcs(arc_id)
            )
        ''')
        steps.append(f"{step_number}. ✅ Таблица `payments` создана с новой структурой")
        step_number += 1
        
        # 2.4 Таблица free_access_grants
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS free_access_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                arc_id INTEGER,
                granted_by TEXT,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (arc_id) REFERENCES arcs(arc_id)
            )
        ''')
        steps.append(f"{step_number}. ✅ Таблица `free_access_grants` создана/проверена")
        step_number += 1
        
        # === 3. ТАБЛИЦЫ ДЛЯ УВЕДОМЛЕНИЙ ===
        
        # 3.1 Таблица notifications
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                day_num INTEGER,
                text TEXT,
                image_url TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        steps.append(f"{step_number}. ✅ Таблица `notifications` создана/проверена")
        step_number += 1
        
        # 3.2 Таблица mass_notifications
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mass_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                title TEXT,
                text TEXT,
                days_before INTEGER,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        steps.append(f"{step_number}. ✅ Таблица `mass_notifications` создана/проверена")
        step_number += 1
        
        # 3.3 Таблица sent_notifications
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                notification_id INTEGER,
                day_num INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        steps.append(f"{step_number}. ✅ Таблица `sent_notifications` создана/проверена")
        step_number += 1
        
        # === 4. ИНДЕКСЫ ДЛЯ ПРОИЗВОДИТЕЛЬНОСТИ ===
        
        indexes = [
            ('idx_user_arc_access_user', 'user_arc_access', 'user_id'),
            ('idx_user_arc_access_arc', 'user_arc_access', 'arc_id'),
            ('idx_payments_user', 'payments', 'user_id'),
            ('idx_payments_status', 'payments', 'status'),
            ('idx_payments_yookassa', 'payments', 'yookassa_payment_id'),
            ('idx_user_progress_user', 'user_progress_advanced', 'user_id'),
            ('idx_user_progress_assignment', 'user_progress_advanced', 'assignment_id'),
            ('idx_notifications_type', 'notifications', 'type, day_num'),
        ]
        
        for idx_name, table_name, column in indexes:
            try:
                cursor.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name}({column})')
                steps.append(f"{step_number}. 📈 Создан индекс `{idx_name}`")
                step_number += 1
            except:
                steps.append(f"{step_number}. ⚠️ Не удалось создать индекс `{idx_name}`")
                step_number += 1
        
        # === 5. ВКЛЮЧАЕМ WAL ДЛЯ ПАРАЛЛЕЛЬНОГО ДОСТУПА ===
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')
        steps.append(f"{step_number}. ⚡ Включен WAL режим для параллельного доступа")
        step_number += 1
        
        conn.commit()
        
        # === 6. ФИНАЛЬНАЯ ПРОВЕРКА ===
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        
        # Считаем записи в ключевых таблицах
        stats = []
        key_tables = ['users', 'user_progress_advanced', 'user_arc_access', 'payments']
        
        for table in key_tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            stats.append(f"• {table}: {count} зап.")
        
        # Формируем итоговое сообщение
        message = "🔄 **ПОЛНОЕ ОБНОВЛЕНИЕ БАЗЫ ДАННЫХ ЗАВЕРШЕНО**\n\n"
        message += "📋 **Выполненные шаги:**\n"
        message += "\n".join(steps)
        
        message += f"\n\n📊 **ИТОГОВАЯ СТРУКТУРА:**\n"
        message += f"• Таблиц: {len(tables)}\n"
        message += "\n".join(stats)
        
        message += f"\n\n💾 **Backup создан:** `{backup_name}`"
        message += "\n\n✅ **Готово к работе!**"
        
        # Отправляем частями если длинное
        if len(message) > 4000:
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, parse_mode='Markdown')
        
        logger.info("✅ Полное обновление БД завершено успешно")
        
    except Exception as e:
        error_msg = f"❌ КРИТИЧЕСКАЯ ОШИБКА при обновлении БД: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        if conn:
            conn.rollback()
        
        await update.message.reply_text(
            f"{error_msg}\n\n"
            f"⚠️ **Восстановите backup командой:**\n"
            f"`cp {backup_name} mentor_bot.db`",
            parse_mode='Markdown'
        )
        
    finally:
        if conn:
            conn.close()

async def check_migration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет готовность к миграции"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    message = "🔍 **ПРОВЕРКА ГОТОВНОСТИ К МИГРАЦИИ**\n\n"
    
    # 1. Проверяем ключевые таблицы
    required_tables = [
        'users', 'arcs', 'days', 'assignments', 
        'user_progress_advanced', 'user_arc_access', 'payments'
    ]
    
    missing_tables = []
    for table in required_tables:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cursor.fetchone():
            missing_tables.append(table)
    
    if missing_tables:
        message += "❌ **Отсутствуют таблицы:**\n"
        for table in missing_tables:
            message += f"• `{table}`\n"
    else:
        message += "✅ **Все ключевые таблицы присутствуют**\n"
    
    # 2. Проверяем данные
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    message += f"\n👤 **Пользователей:** {user_count}\n"
    
    cursor.execute("SELECT COUNT(*) FROM user_progress_advanced")
    progress_count = cursor.fetchone()[0]
    message += f"📝 **Записей прогресса:** {progress_count}\n"
    
    # 3. Проверяем платежную систему
    from database import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
    if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
        message += f"💰 **Ключи Юкассы:** настроены\n"
    else:
        message += f"💰 **Ключи Юкассы:** ❌ НЕ настроены!\n"
    
    conn.close()
    
    message += "\n🎯 **Рекомендации:**\n"
    if not missing_tables:
        message += "1. Создайте backup БД\n"
        message += "2. Выполните `/updatedb`\n"
        message += "3. Протестируйте платежи\n"
    else:
        message += "1. Выполните `/updatedb` для создания таблиц\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')


async def verify_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет сохранность критичных данных"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    message = "🔍 **Проверка данных после обновления:**\n\n"
    
    # 1. Пользователи
    cursor.execute("SELECT COUNT(*), COUNT(fio), COUNT(city) FROM users")
    users_count, users_fio, users_city = cursor.fetchone()
    message += f"👤 **Пользователи:** {users_count} чел.\n"
    message += f"• С ФИО: {users_fio}\n"
    message += f"• С городом: {users_city}\n"
    
    # 2. Прогресс заданий
    cursor.execute("SELECT COUNT(*), COUNT(DISTINCT user_id) FROM user_progress_advanced")
    progress_count, unique_users = cursor.fetchone()
    message += f"\n📝 **Прогресс заданий:** {progress_count} записей\n"
    message += f"• Уникальных пользователей: {unique_users}\n"
    
    # 3. Проверяем статусы прогресса
    cursor.execute("SELECT status, COUNT(*) FROM user_progress_advanced GROUP BY status")
    statuses = cursor.fetchall()
    message += f"• По статусам:\n"
    for status, count in statuses:
        message += f"  - {status}: {count}\n"
    
    # 4. Доступы (должны быть старые если есть)
    cursor.execute("SELECT COUNT(*) FROM user_arc_access")
    access_count = cursor.fetchone()[0]
    message += f"\n🔑 **Доступы к частям:** {access_count} записей\n"
    
    # 5. Платежи (должны быть 0 или старые)
    cursor.execute("SELECT COUNT(*) FROM payments")
    payments_count = cursor.fetchone()[0]
    message += f"💰 **Платежи:** {payments_count} записей\n"
    
    conn.close()
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def check_yookassa_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет авторизацию в Юкассе"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    from database import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_API_URL
    import requests
    import base64
    import json
    
    message = f"🔑 **Проверка ключей Юкассы**\n\n"
    message += f"Shop ID: `{YOOKASSA_SHOP_ID}`\n"
    message += f"Secret Key: `{YOOKASSA_SECRET_KEY[:20]}...`\n\n"
    
    # Проверяем формат ключа
    if YOOKASSA_SECRET_KEY.startswith('test_'):
        message += "🟡 **ТЕСТОВЫЙ ключ** (начинается с test_)\n"
    elif YOOKASSA_SECRET_KEY.startswith('live_'):
        message += "💰 **РАБОЧИЙ ключ** (начинается с live_)\n"
    else:
        message += "❌ **Неправильный формат ключа!**\n"
        message += "Должен начинаться с `test_` или `live_`\n"
    
    try:
        # Формируем авторизацию
        auth_string = f'{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}'
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json",
            "Idempotence-Key": "test-auth-check"
        }
        
        # Пробуем создать тестовый платеж на 1 рубль
        test_data = {
            "amount": {
                "value": "1.00",
                "currency": "RUB"
            },
            "payment_method_data": {
                "type": "bank_card"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://svs365bot.ru"
            },
            "description": "Тест авторизации",
            "capture": True
        }
        
        response = requests.post(YOOKASSA_API_URL, 
                               json=test_data, 
                               headers=headers, 
                               timeout=10)
        
        if response.status_code == 200:
            payment_info = response.json()
            payment_id = payment_info.get('id', 'N/A')
            message += f"✅ **Авторизация успешна!**\n"
            message += f"Создан тестовый платеж: `{payment_id}`\n"
            
            # Пробуем сразу отменить тестовый платеж
            try:
                cancel_headers = headers.copy()
                cancel_headers["Idempotence-Key"] = "cancel-test-payment"
                cancel_response = requests.post(
                    f"{YOOKASSA_API_URL}/{payment_id}/cancel",
                    headers=cancel_headers,
                    timeout=5
                )
                if cancel_response.status_code == 200:
                    message += "✅ Тестовый платеж отменен\n"
            except:
                message += "⚠️ Не удалось отменить тест платеж\n"
                
        elif response.status_code == 401:
            message += f"❌ **ОШИБКА 401: Неверные ключи!**\n"
            try:
                error_data = response.json()
                message += f"Код: {error_data.get('code', 'N/A')}\n"
                message += f"Описание: {error_data.get('description', 'N/A')}\n"
            except:
                message += f"Ответ: {response.text[:200]}\n"
            
            message += "\n**Проверь:**\n"
            message += "1. Shop ID в кабинете Юкассы\n"
            message += "2. Что ключ начинается с `live_`\n"
            message += "3. Что ключ скопирован полностью\n"
            
        else:
            message += f"⚠️ **Ошибка {response.status_code}**\n"
            message += f"Ответ: {response.text[:200]}\n"
            
    except requests.exceptions.Timeout:
        message += "❌ Таймаут подключения к Юкассе\n"
    except requests.exceptions.ConnectionError:
        message += "❌ Ошибка подключения к Юкассе\n"
    except Exception as e:
        message += f"❌ Ошибка: {str(e)[:100]}\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def debug_last_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последний платеж"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, user_id, arc_id, amount, status, yookassa_payment_id, created_at
        FROM payments 
        ORDER BY created_at DESC 
        LIMIT 1
    ''')
    
    payment = cursor.fetchone()
    
    if payment:
        pay_id, user_id_db, arc_id, amount, status, yookassa_id, created_at = payment
        
        cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
        arc_title = cursor.fetchone()
        arc_title = arc_title[0] if arc_title else f"Часть {arc_id}"
        
        message = f"**Последний платеж:**\n\n"
        message += f"ID: {pay_id}\n"
        message += f"User: {user_id_db}\n"
        message += f"{arc_title}\n"
        message += f"Сумма: {amount}₽\n"
        message += f"Статус: {status}\n"
        message += f"Юкасса ID: `{yookassa_id}`\n"
        message += f"Дата: {created_at}\n"
        
        # Проверяем есть ли доступ
        cursor.execute('SELECT 1 FROM user_arc_access WHERE user_id = ? AND arc_id = ?', (user_id_db, arc_id))
        has_access = cursor.fetchone()
        
        if has_access:
            message += f"\n✅ **Доступ выдан:** да"
        else:
            message += f"\n❌ **Доступ выдан:** нет"
    else:
        message = "📭 Нет платежей в базе"
    
    conn.close()
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def webhook_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус webhook"""
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        return
    
    import requests
    
    try:
        resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo", timeout=10)
        info = resp.json()
        
        msg = f"🌐 **Webhook Status**\n\n"
        msg += f"• URL: `{info.get('result', {}).get('url', 'None')}`\n"
        msg += f"• Ошибок: {info.get('result', {}).get('pending_update_count', 0)}\n"
        msg += f"• Последняя ошибка: {info.get('result', {}).get('last_error_message', 'None')[:50]}\n"
        
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

def send_payment_notification(user_id, arc_title, amount, payment_id):
    """Отправляет уведомление пользователю об успешной оплате"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from telegram import Bot
        from config import TOKEN
        
        bot = Bot(token=TOKEN)
        
        # Определяем тип доступа
        if float(amount) == 100:
            access_type = "пробный (3 задания)"
        else:
            access_type = "полный"
        
        message = (
            f"✅ **Оплата подтверждена!**\n\n"
            f"Сумма: {amount}₽\n"
            f"{arc_title}\n"
            f"Доступ: {access_type}\n"
            f"ID платежа: `{payment_id}`\n\n"
            f"Задания доступны в разделе **'Мои задания'**!"
        )
        
        # Отправляем сообщение
        bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode='Markdown'
        )
        
        logger.info(f"Уведомление отправлено пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

async def manage_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление webhook (только для админа)"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Только для администраторов")
        return
    
    import requests
    
    command = context.args[0] if context.args else "status"
    
    try:
        if command == "status":
            # Проверка статуса webhook
            resp = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo",
                timeout=10
            )
            info = resp.json().get('result', {})
            
            msg = (
                f"🌐 **Webhook Status**\n\n"
                f"• URL: `{info.get('url', 'Not set')}`\n"
                f"• Has custom cert: {info.get('has_custom_certificate', False)}\n"
                f"• Pending updates: {info.get('pending_update_count', 0)}\n"
                f"• Last error: {info.get('last_error_message', 'None')[:100]}\n"
                f"• Last sync: {info.get('last_synchronization_error_date', 'Never')}\n"
            )
            
        elif command == "set":
            # Установка webhook
            WEBHOOK_URL = f"https://svs365bot.ru/bot/{TOKEN}"
            
            resp = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/setWebhook",
                json={"url": WEBHOOK_URL},
                timeout=10
            )
            
            if resp.json().get('ok'):
                msg = f"✅ Webhook установлен: `{WEBHOOK_URL}`"
            else:
                msg = f"❌ Ошибка: {resp.json().get('description', 'Unknown')}"
                
        elif command == "delete":
            # Удаление webhook
            resp = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/deleteWebhook",
                timeout=10
            )
            
            if resp.json().get('ok'):
                msg = "✅ Webhook удален"
            else:
                msg = f"❌ Ошибка: {resp.json().get('description', 'Unknown')}"
                
        elif command == "test":
            # Тестовый запрос
            WEBHOOK_URL = f"https://svs365bot.ru/bot/{TOKEN}"
            resp = requests.get(WEBHOOK_URL, timeout=10)
            msg = f"Test response: {resp.status_code}"
            
        else:
            msg = (
                "📋 **Доступные команды:**\n"
                "• `/webhook status` - статус\n"
                "• `/webhook set` - установить\n"
                "• `/webhook delete` - удалить\n"
                "• `/webhook test` - тест\n"
            )
            
    except Exception as e:
        msg = f"❌ Ошибка: {str(e)}"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

def start_yookassa_webhook_server():
    """Запускает сервер для вебхуков ЮKассы"""
    app = web.Application()
    app.router.add_post('/yookassa-webhook/', yookassa_webhook)
    
    # Запускаем в отдельном потоке
    runner = web.AppRunner(app)
    return runner

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Логирует ошибки и отправляет уведомление администратору"""
    logger = logging.getLogger(__name__)
    
    # Логируем ошибку
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    # НЕ используем 'application' - её нет в scope!
    # Вместо этого используем context.bot напрямую
    
    try:
        if ADMIN_ID and context.bot:
            error_text = f"❌ Ошибка в боте:\n{context.error}"
            # Урезаем текст если слишком длинный
            if len(error_text) > 4000:
                error_text = error_text[:4000] + "..."
            await context.bot.send_message(chat_id=ADMIN_ID, text=error_text)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление об ошибке: {e}")

async def tech_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню технической поддержки"""
    context.user_data['current_section'] = 'tech_support'
    
    keyboard = [
        ["💬 Написать в поддержку"],
        ["📖 Инструкции"],  
        ["👤 Авторы марафона"],
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
    
    keyboard = [
        ["💬 Написать в поддержку"],
        ["👤 Авторы марафона"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📖 **Инструкции**\n\n"
        "⚠️ *Раздел в разработке*\n\n"
        "Скоро здесь появятся подробные инструкции "
        "по работе с ботом и выполнению заданий.\n\n"
        "Если у вас есть вопросы, напишите в поддержку:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_author_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию об авторе тренинга (пока в разработке)"""
    
    keyboard = [
        ["💬 Написать в поддержку"],
        ["📖 Инструкции"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "👤 **Авторы марафона**\n\n"
        "⚠️ *Раздел в разработке*\n\n"
        "Скоро здесь появится информация об авторах"
        "и создателе тренинга «Себя верни себе».\n\n"
        "Для связи используйте кнопку ниже:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def write_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переход в бот поддержки"""
    support_link = "https://t.me/SVS_helaper_bot"  # Просто ссылка без параметров
    
    keyboard = [[InlineKeyboardButton("💬 Перейти в поддержку", url=support_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🛠️ *Техническая поддержка*\n\n"
        "Нажмите кнопку ниже для перехода в чат поддержки.\n\n"
        "В боте поддержки вы сможете:\n"
        "• Создать обращение\n"
        "• Выбрать бот, в котором проблема\n"
        "• Отслеживать историю обращений",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def load_media_from_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Загружает медиа-контент из Excel"""
    if not is_admin(update.message.from_user.id):
        return
    
    await update.message.reply_text("🔄 Загружаю медиа-контент из Excel...")
    
    from database import update_assignment_with_media_from_excel
    count = update_assignment_with_media_from_excel()
    
    await update.message.reply_text(
        f"✅ Загружено медиа для {count} заданий\n\n"
        f"Теперь задания могут содержать:\n"
        f"• 🖼️ Фото\n"
        f"• 🎵 Аудио\n"
        f"• 🎬 Видео-ссылки",
        parse_mode='Markdown'
    )

async def load_media_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Простая загрузка медиа из Excel"""
    if not is_admin(update.message.from_user.id):
        return
    
    await update.message.reply_text("🔄 Загружаю медиа из Excel (простой формат)...")
    
    from database import update_assignment_with_media_simple
    count = update_assignment_with_media_simple()
    
    if count > 0:
        # ПРОСТОЙ ТЕКСТ БЕЗ MARKDOWN
        message = (
            f"✅ Загружено фото для {count} заданий!\n\n"
            f"Теперь медиа будут отображаться в заданиях.\n"
            f"Проверьте: откройте любое задание с фото."
        )
        await update.message.reply_text(message)
    else:
        # ПРОСТОЙ ТЕКСТ БЕЗ MARKDOWN
        message = (
            "❌ Не удалось загрузить медиа.\n\n"
            "Проверьте:\n"
            "1. Файл courses_data.xlsx в папке с ботом\n"
            "2. Колонка 'фото' в листе 'Задания'\n"
            "3. File ID в ячейках (просто текст, без скобок)"
        )
        await update.message.reply_text(message)

async def debug_current_arc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущую активную часть"""
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        return
    
    from database import get_current_arc
    current = get_current_arc()
    
    if current:
        arc_id, arc_title = current
        message = f"🔍 **Текущая активный марафон:**\n"
        message += f"• ID: {arc_id}\n"
        message += f"• Название: {arc_title}\n"
        
        # Проверяем доступ
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT access_type FROM user_arc_access WHERE user_id = ? AND arc_id = ?', 
                      (user_id, arc_id))
        access = cursor.fetchone()
        conn.close()
        
        message += f"• Ваш доступ: {'ЕСТЬ' if access else 'НЕТ'}\n"
    else:
        message = "❌ **Нет активных марафонов*\n\n"
        
        # Покажем все части
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT arc_id, title, дата_начала, дата_окончания FROM arcs WHERE arc_id > 0')
        all_arcs = cursor.fetchall()
        conn.close()
        
        if all_arcs:
            message += "📋 **Все части в БД:**\n"
            for arc_id, title, start_date, end_date in all_arcs:
                message += f"• {title} (ID:{arc_id}) - {start_date} / {end_date}\n"
        else:
            message += "В БД нет частей!"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def grant_free_trial_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдает бесплатный пробный доступ на 3 дня"""
    user_id = update.message.from_user.id
    
    # Получаем выбранный марафон
    arc_id = context.user_data.get('current_arc_catalog')
    part_status = context.user_data.get('part_status')
    
    if not arc_id:
        await update.message.reply_text("❌ Сначала выберите марафон")
        return
    
    # Проверяем что это активный марафон
    if part_status != 'активный':
        await update.message.reply_text(
            "❌ **Пробный доступ доступен только для активных марафонов!**\n\n"
            "Для будущих марафонов доступен только полный доступ.",
            parse_mode='Markdown'
        )
        return
    
    # Выдаем бесплатный доступ
    from database import grant_arc_access
    grant_arc_access(user_id, arc_id, access_type='trial')

    from telegram import ReplyKeyboardMarkup
    
    keyboard = [
        ["📝 Доступные задания"],
        ["🔙 В главное меню"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "**Бесплатный пробный доступ активирован!**\n\n"
        "**Что вы получили:**\n"
        "• Доступ ко всем заданиям первых 3 дней марафона\n"
        "• Доступ к заданиям открывается последовательно каждывй день\n"
        "• Если вы присоединились на 3 день и позже, то вам сразу будут доступны задания 3 дней\n"
        "• Это замечательня возможность оценить полезность марафона\n\n"
        "**Что дальше:**\n"
        "1. Перейдите в раздел '📝 Доступные задания'\n"
        "2. Выполните доступные задания\n"
        "3. Если понравится - купите полный доступ\n\n",
        parse_mode='Markdown'
    )

async def get_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Простая команда - показывает инструкцию"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    # Сохраняем ID сообщения с инструкцией
    message = await update.message.reply_text(
        "📎 **ОТПРАВЬТЕ МНЕ ФОТО/АУДИО КАК ОТВЕТ НА ЭТО СООБЩЕНИЕ**\n\n"
        "1. Нажмите и удерживайте это сообщение\n"
        "2. Выберите 'Ответить' (Reply)\n"
        "3. Отправьте фото или аудио файл\n"
        "4. Я верну File ID\n\n"
        "⚠️ Важно: отправляйте файл именно как ОТВЕТ на это сообщение!"
    )
    
    # Сохраняем ID сообщения в context
    context.user_data['getfileid_message_id'] = message.message_id


async def cancel_file_id_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выйти из режима получения file_id"""
    if 'waiting_for_file_id' in context.user_data:
        context.user_data.pop('waiting_for_file_id', None)
        await update.message.reply_text("✅ Режим получения File ID отменен.")
    else:
        await update.message.reply_text("⚠️ Режим получения File ID не активен.")


async def get_file_id_easy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Самый простой работающий вариант"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return
    
    # Отправляем инструкцию с кнопкой "Просто отправьте файл"
    await update.message.reply_text(
        "📎 **Просто отправьте мне фото или аудио файл!**\n\n"
        "Не нужно писать /getfileid в подписи.\n"
        "Просто отправьте файл - я сам определю, что вы хотите получить File ID."
    )

async def handle_admin_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все файлы от админов"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        return  # Не админ - пропускаем
    
    # Обрабатываем только если нет других активных режимов
    if (not context.user_data.get('answering') and 
        not context.user_data.get('notification_stage')):
        
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            await update.message.reply_text(
                f"🖼 File ID фото:\n`{file_id}`\n\n"
                f'📋 Для Excel (колонка "фото"):\n`{file_id}`',
            )
            return
        
        if update.message.audio:
            file_id = update.message.audio.file_id
            await update.message.reply_text(
                f"🎵 File ID аудио:\n`{file_id}`\n\n"
                f'📋 Для Excel (колонка "аудио"):\n`{file_id}`',
            )
            return
        
        if update.message.video:
            file_id = update.message.video.file_id
            duration = update.message.video.duration
            file_size_mb = update.message.video.file_size / (1024*1024) if update.message.video.file_size else 0
            
            # ПРОСТОЙ ТЕКСТ БЕЗ MARKDOWN
            message = (
                f"🎬 File ID видео получен!\n\n"
                f"🆔 Код: {file_id}\n"
                f"⏱ Длительность: {duration} секунд\n"
                f"📏 Размер: {file_size_mb:.1f} MB\n\n"
                f"📋 Для Excel (колонка 'видео_ссылка'):\n{file_id}\n\n"
                f"✅ Видео будет показываться прямо в Telegram!"
            )
            
            await update.message.reply_text(message)  # БЕЗ parse_mode='Markdown'
            return
                
        if update.message.document:
            file_id = update.message.document.file_id
            file_name = update.message.document.file_name or "Документ"
            
            await update.message.reply_text(
                f"📄 File ID документа:\n`{file_id}`\n\n"
                f"📝 Название: {file_name}\n\n"
                f'📋 Для Excel:\n`{file_id}`',
            )
            return

async def check_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверить медиа в базе данных"""
    if not is_admin(update.message.from_user.id):
        return
    
    conn = sqlite3.connect('mentor_bot.db')
    cursor = conn.cursor()
    
    # Проверяем первое задание
    cursor.execute('''
        SELECT assignment_id, title, content_photos, content_audios, video_url
        FROM assignments 
        WHERE assignment_id = 1
    ''')
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        assignment_id, title, photos_json, audios_json, video_url = result
        
        message = f"🔍 **Задание {assignment_id}: {title}**\n\n"
        
        if photos_json:
            try:
                photos = json.loads(photos_json)
                message += f"🖼️ **Фото:** {len(photos)} шт.\n"
                for i, photo_id in enumerate(photos[:3], 1):
                    message += f"  {i}. `{photo_id[:30]}...`\n"
            except:
                message += f"🖼️ **Фото (RAW):** `{photos_json[:50]}...`\n"
        else:
            message += "🖼️ **Фото:** нет\n"
        
        message += f"\n📏 Длина данных фото: {len(photos_json) if photos_json else 0} символов"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Задание не найдено")

async def add_photo_to_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить фото к заданию напрямую"""
    if not is_admin(update.message.from_user.id):
        return
    
    # Формат: /addphoto 1 file_id_here
    if context.args and len(context.args) >= 2:
        try:
            assignment_id = int(context.args[0])
            file_id = context.args[1]
            
            # Формируем JSON
            photos_json = json.dumps([file_id])
            
            conn = sqlite3.connect('mentor_bot.db')
            cursor = conn.cursor()
            
            # Проверяем существует ли задание
            cursor.execute('SELECT title FROM assignments WHERE assignment_id = ?', (assignment_id,))
            if cursor.fetchone():
                # Обновляем
                cursor.execute('''
                    UPDATE assignments 
                    SET content_photos = ?
                    WHERE assignment_id = ?
                ''', (photos_json, assignment_id))
                
                conn.commit()
                
                await update.message.reply_text(
                    f"✅ **Фото добавлено к заданию {assignment_id}!**\n\n"
                    f"📸 File ID: `{file_id}`\n"
                    f"📋 JSON: {photos_json}\n\n"
                    f"Теперь откройте задание как пользователь для проверки.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(f"❌ Задание {assignment_id} не найдено")
            
            conn.close()
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    elif update.message.photo and len(context.args) == 1:
        # Если отправлено фото + ID задания
        try:
            assignment_id = int(context.args[0])
            file_id = update.message.photo[-1].file_id
            photos_json = json.dumps([file_id])
            
            conn = sqlite3.connect('mentor_bot.db')
            cursor = conn.cursor()
            
            cursor.execute('UPDATE assignments SET content_photos = ? WHERE assignment_id = ?', 
                          (photos_json, assignment_id))
            
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"✅ **Фото добавлено!**\n\n"
                f"📝 Задание: {assignment_id}\n"
                f"🖼️ File ID: `{file_id}`\n\n"
                f"Теперь проверьте: откройте задание {assignment_id} как пользователь.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    else:
        await update.message.reply_text(
            "📝 **Использование:**\n\n"
            "**Способ 1:**\n"
            "`/addphoto <ID_задания> <file_id>`\n\n"
            "**Способ 2:**\n"
            "1. Напишите `/addphoto <ID_задания>`\n"
            "2. Отправьте фото как ответ на это сообщение\n\n"
            "**Пример:** `/addphoto 1 AgACAgIAAxkBAAIJuml7o8cOswb-rXwZCAuL8P2vQEZcAAIJE2sbbOrZS8KL5JWUSu69AQADAgADeQADOAQ`"
        )

async def load_all_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Загрузить ВСЕ медиа из Excel (фото, аудио, видео)"""
    if not is_admin(update.message.from_user.id):
        return
    
    await update.message.reply_text("🔄 Загружаю ВСЕ медиа из Excel...")
    
    from database import load_all_media_from_excel
    result = load_all_media_from_excel()
    
    if result['status'] == 'success':
        stats = result['stats']
        
        # ПРОСТОЙ ТЕКСТ БЕЗ MARKDOWN
        message = (
            f"✅ ВСЕ МЕДИА ЗАГРУЖЕНЫ УСПЕШНО!\n\n"
            f"📊 Статистика:\n"
            f"• 📝 Обработано строк: {stats['total_rows']}\n"
            f"• ✅ Обновлено заданий: {stats['updated_assignments']}\n"
            f"• 🖼️ Загружено фото: {stats['photos_loaded']}\n"
            f"• 🎵 Загружено аудио: {stats['audios_loaded']}\n"
            f"• 🎬 Загружено видео: {stats['videos_loaded']}\n"
            f"• ❌ Ошибок: {stats['errors']}\n\n"
            f"Теперь в заданиях будут отображаться:\n"
            f"• Фото\n• Аудио\n• Видео-ссылки"
        )
        
        await update.message.reply_text(message)  # Без parse_mode
    
    else:
        message = (
            f"❌ Ошибка загрузки медиа!\n\n"
            f"Проблема: {result['message']}\n\n"
            f"Проверьте:\n"
            f"1. Файл courses_data.xlsx в папке с ботом\n"
            f"2. Лист 'Задания' в файле\n"
            f"3. Колонки: 'фото', 'аудио', 'видео_ссылка'\n"
            f"4. Формат данных: просто file_id или URL"
        )
        
        await update.message.reply_text(message)

async def load_tests_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для загрузки тестов из Excel (только админ)"""
    if not is_admin(update.message.from_user.id):
        return
    
    await update.message.reply_text("🔄 Загружаю тесты из Excel...")
    
    from database import load_tests_from_excel
    count = load_tests_from_excel()
    
    await update.message.reply_text(
        f"✅ Загружено {count} вопросов для тестов\n\n"
        f"Теперь доступны:\n"
        f"• 📈 Тестирование по неделям\n"
        f"• 📊 Сохранение результатов\n"
        f"• 🔄 Прогресс прохождения",
        parse_mode='Markdown'
    )


# ==================== ТЕСТИРОВАНИЕ ====================

async def testing_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню тестирования"""
    context.user_data['current_section'] = 'testing'
    user_id = update.message.from_user.id
    
    # Проверяем есть ли активные марафоны
    from database import get_user_active_arcs
    active_arcs = get_user_active_arcs(user_id)
    
    if not active_arcs:
        await update.message.reply_text(
            "📈 **Тестирование**\n\n"
            "У вас нет активных марафонов.\n"
            "Тесты доступны только во время прохождения марафонов.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([["🔙 В главное меню"]], resize_keyboard=True)
        )
        return
    
    keyboard = [
        ["📈 Пройти тест"],
        ["📊 Мои результаты"],
        ["📚 В раздел Мои задания"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📈 **ТЕСТИРОВАНИЕ**\n\n"
        "Еженедельные тесты для закрепления материала:\n\n"
        "• **Неделя 1** - дни 1-7\n"
        "• **Неделя 2** - дни 8-14\n"
        "• **Неделя 3** - дни 15-21\n"
        "• **Неделя 4** - дни 22-28\n\n"
        "Каждый тест: 15 вопросов, 5 вариантов ответа.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_available_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает выбор марафона для тестирования"""
    user_id = update.message.from_user.id
    
    # Получаем активные марафоны
    from database import get_user_active_arcs
    active_arcs = get_user_active_arcs(user_id)
    
    if not active_arcs:
        await update.message.reply_text(
            "📭 **Нет активных марафонов.**\n\n"
            "Тесты доступны только во время прохождения марафонов.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([["🔙 Назад к тестированию"]], resize_keyboard=True)
        )
        return
    
    keyboard = []
    
    for arc_id, arc_title, start_date, end_date, access_type in active_arcs:
        # Форматируем даты
        start_str = start_date.strftime('%d.%m') if hasattr(start_date, 'strftime') else str(start_date)[:10]
        end_str = end_date.strftime('%d.%m') if hasattr(end_date, 'strftime') else str(end_date)[:10]
        
        btn_text = f"🏁 {arc_title}"
        keyboard.append([btn_text])
        
        # Сохраняем маппинг
        if 'arc_selection_map' not in context.user_data:
            context.user_data['arc_selection_map'] = {}
        context.user_data['arc_selection_map'][btn_text] = {
            'arc_id': arc_id,
            'arc_title': arc_title,
            'start_date': start_date,
            'end_date': end_date
        }
    
    keyboard.append(["🔙 Назад к тестированию"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🏁 **ВЫБОР МАРАФОНА**\n\n"
        "Выберите марафон для прохождения теста:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_tests_for_arc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает доступные тесты для выбранного марафона"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Получаем информацию о выбранном марафоне
    arc_selection_map = context.user_data.get('arc_selection_map', {})
    arc_info = arc_selection_map.get(text)
    
    if not arc_info:
        # Может это прямой выбор теста (старая логика)?
        if 'test_mapping' in context.user_data and text in context.user_data['test_mapping']:
            await start_test(update, context)
            return
        
        await update.message.reply_text("❌ Марафон не найден")
        return
    
    arc_id = arc_info['arc_id']
    arc_title = arc_info['arc_title']
    
    # Получаем доступные тесты для этого марафона
    from database import get_available_tests, get_current_arc_day
    available_tests = get_available_tests(user_id, arc_id)
    
    if not available_tests:
        # Получаем текущий день для информативного сообщения
        current_day_info = get_current_arc_day(user_id, arc_id)
        current_day = current_day_info['day_number'] if current_day_info else 0
        
        await update.message.reply_text(
            f"📭 **Нет доступных тестов в марафоне '{arc_title}'.**\n\n"
            f"Ваш текущий день: {current_day}\n\n"
            f"Тесты доступны по неделям:\n"
            f"• Неделя 1: дни 1-7\n"
            f"• Неделя 2: дни 8-14\n"
            f"• Неделя 3: дни 15-21\n"
            f"• Неделя 4: дни 22-28",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup([["🔙 Выбрать другой марафон"]], resize_keyboard=True)
        )
        return
    
    keyboard = []
    
    for test_info in available_tests:
        week_num = test_info['week_num']
        status = test_info['status']
        completed = test_info['completed']
        
        if completed:
            btn_text = f"✅ Неделя {week_num} (пройден)"
        else:
            btn_text = f"📈 Неделя {week_num} (доступен)"
        
        keyboard.append([btn_text])
        
        # Сохраняем маппинг теста с информацией о марафоне
        if 'test_mapping' not in context.user_data:
            context.user_data['test_mapping'] = {}
        
        context.user_data['test_mapping'][btn_text] = {
            'arc_id': arc_id,
            'arc_title': arc_title,
            'week_num': week_num,
            'completed': completed
        }
    
    keyboard.append(["🔙 Выбрать другой марафон"])
    keyboard.append(["🔙 Назад к тестированию"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📈 **ТЕСТЫ МАРАФОНА: {arc_title}**\n\n"
        f"Выберите тест для прохождения:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает тест - ИСПРАВЛЕННАЯ ЛОГИКА ДОСТУПНОСТИ"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Получаем информацию о выбранном тесте
    test_mapping = context.user_data.get('test_mapping', {})
    test_info = test_mapping.get(text)
    
    if not test_info:
        await update.message.reply_text("❌ Тест не найден")
        return
    
    arc_id = test_info['arc_id']
    arc_title = test_info['arc_title']
    week_num = test_info['week_num']
    completed = test_info['completed']
    
    if completed:
        # Показываем результаты
        await show_test_results(update, context, user_id, arc_id, week_num)
        return
    
    # ★★ НОВАЯ ПРОВЕРКА: находится ли текущий день в диапазоне дней теста ★★
    from database import get_current_arc_day
    current_day_info = get_current_arc_day(user_id, arc_id)
    current_day = current_day_info['day_number'] if current_day_info else 0
    
    # Определяем диапазон дней для этого теста
    range_start = (week_num - 1) * 7 + 1  # Для недели 1: 1, для недели 2: 8 и т.д.
    range_end = week_num * 7  # Для недели 1: 7, для недели 2: 14 и т.д.
    
    if not (range_start <= current_day <= range_end):
        await update.message.reply_text(
            f"⏳ **Тест недели {week_num} доступен только с {range_start} по {range_end} день марафона.**\n\n"
            f"Ваш текущий день: {current_day}\n\n"
            f"Этот тест проверяет знания за дни {range_start}-{range_end}.\n"
            f"Продолжайте выполнять ежедневные задания!",
            parse_mode='Markdown'
        )
        return
    
    # Проверяем есть ли вопросы для этого теста
    from database import get_tests_for_week
    questions = get_tests_for_week(week_num)
    
    if not questions or len(questions) < 15:
        await update.message.reply_text(
            f"❌ **Тест недели {week_num} временно недоступен.**\n\n"
            f"Вопросы для теста еще не загружены.",
            parse_mode='Markdown'
        )
        return
    
    # Сохраняем данные теста
    context.user_data['current_test'] = {
        'arc_id': arc_id,
        'arc_title': arc_title,
        'week_num': week_num,
        'questions': questions,
        'current_question': 1,
        'answers': {},
        'started_at': datetime.now().isoformat()
    }
    
    # Показываем первый вопрос
    await show_question(update, context)

async def show_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question_num=None):
    """Показывает вопрос теста - ФИНАЛЬНАЯ ВЕРСИЯ"""
    test_data = context.user_data.get('current_test')
    if not test_data:
        await update.message.reply_text("❌ Нет активного теста")
        return
    
    if question_num is None:
        question_num = test_data['current_question']
    
    questions = test_data['questions']
    
    if question_num > len(questions):
        # Тест завершен
        await finish_test(update, context)
        return
    
    # Получаем вопрос
    question = questions[question_num - 1]
    test_id, question_text, option1, option2, option3, option4, option5, correct_option, explanation = question
    
    # Формируем сообщение БЕЗ вариантов ответа в тексте
    message = f"📈 **ТЕСТ: Неделя {test_data['week_num']}**\n\n"
    message += f"📝 **Вопрос {question_num} из 15**\n\n"
    message += f"{question_text}\n"
    
    # ★★ ИСПРАВЛЕНИЕ: Создаем клавиатуру 2 кнопки в ряд
    keyboard = []
    option_mapping = {}  # Для сопоставления текста кнопки с optionX
    
    # Собираем непустые варианты
    options = []
    if option1 and str(option1).strip():
        options.append((option1, 'option1'))
    if option2 and str(option2).strip():
        options.append((option2, 'option2'))
    if option3 and str(option3).strip():
        options.append((option3, 'option3'))
    if option4 and str(option4).strip():
        options.append((option4, 'option4'))
    if option5 and str(option5).strip():
        options.append((option5, 'option5'))
    
    # Располагаем по 2 кнопки в ряд
    current_row = []
    for i, (option_text, option_key) in enumerate(options, 1):
        # Обрезаем текст для кнопки
        display_text = option_text
        if len(display_text) > 20:
            display_text = display_text[:17] + "..."
        
        current_row.append(display_text)
        option_mapping[display_text] = option_key
        
        # Каждые 2 кнопки или последняя кнопка
        if i % 2 == 0 or i == len(options):
            keyboard.append(current_row)
            current_row = []
    
    # Кнопка прерывания отдельной строкой
    keyboard.append(["⏹️ Прервать тест"])
    
    # ★★ ВАЖНО: Сохраняем маппинг для обработки ответа
    context.user_data['current_question_options'] = option_mapping
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Сохраняем текущий вопрос
    test_data['current_question'] = question_num
    context.user_data['current_test'] = test_data
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def process_test_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ответ на вопрос теста - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Проверяем активный тест
    test_data = context.user_data.get('current_test')
    if not test_data:
        # Может это выбор теста?
        if text in ["📈 Пройти тест", "📊 Мои результаты"]:
            await handle_buttons(update, context)
            return
        
        await update.message.reply_text("❌ Нет активного теста")
        return
    
    # Обработка прерывания теста
    if text == "⏹️ Прервать тест":
        # Сохраняем прогресс
        from database import save_test_progress
        save_test_progress(
            user_id=user_id,
            arc_id=test_data['arc_id'],
            week_num=test_data['week_num'],
            current_question=test_data['current_question'],
            answers=test_data['answers']
        )
        
        context.user_data.pop('current_test', None)
        context.user_data.pop('current_question_options', None)
        
        await update.message.reply_text(
            "⏸️ **Тест прерван.**\n\n"
            "Ваш прогресс сохранен.\n"
            "Можете продолжить позже.",
            reply_markup=ReplyKeyboardMarkup([["📈 Пройти тест"], ["📊 Мои результаты"]], resize_keyboard=True),
            parse_mode='Markdown'
        )
        return
    
    # ★★ ИСПРАВЛЕНИЕ: Получаем выбранный вариант из маппинга
    option_mapping = context.user_data.get('current_question_options', {})
    
    # Пробуем найти точное совпадение
    selected_option_key = None
    for option_text, option_value in option_mapping.items():
        # Сравниваем текст кнопки (возможно обрезанный)
        if text in option_text or option_text.startswith(text[:30]):
            selected_option_key = option_value
            break
    
    # Если не нашли точное совпадение, ищем по префиксу (1., 2., и т.д.)
    if not selected_option_key and text.startswith(('1.', '2.', '3.', '4.', '5.')):
        prefix = text.split('.')[0]
        option_map_prefix = {
            '1': 'option1',
            '2': 'option2',
            '3': 'option3',
            '4': 'option4',
            '5': 'option5'
        }
        selected_option_key = option_map_prefix.get(prefix)
    
    if not selected_option_key:
        await update.message.reply_text("❌ Выберите вариант ответа из предложенных")
        return
    
    # Получаем текущий вопрос
    question_num = test_data['current_question']
    questions = test_data['questions']
    
    if question_num > len(questions):
        await finish_test(update, context)
        return
    
    question = questions[question_num - 1]
    test_id, question_text, option1, option2, option3, option4, option5, correct_option, explanation = question
    
    # Проверяем правильность
    is_correct = (selected_option_key == correct_option)
    
    # Получаем текст выбранного варианта
    option_texts = {
        'option1': option1,
        'option2': option2,
        'option3': option3,
        'option4': option4,
        'option5': option5
    }
    selected_text = option_texts.get(selected_option_key, 'не указан')
    
    # Сохраняем ответ
    test_data['answers'][str(test_id)] = {
        'selected': selected_option_key,
        'selected_text': selected_text,  # ★★ Добавляем текст выбранного варианта
        'correct': is_correct,
        'question_text': question_text
    }
    
    # Переходим к следующему вопросу
    test_data['current_question'] += 1
    context.user_data['current_test'] = test_data
    
    # Очищаем маппинг текущего вопроса
    context.user_data.pop('current_question_options', None)
    
    # Сохраняем промежуточный прогресс
    from database import save_test_progress
    save_test_progress(
        user_id=user_id,
        arc_id=test_data['arc_id'],
        week_num=test_data['week_num'],
        current_question=test_data['current_question'],
        answers=test_data['answers']
    )
    
    # Показываем следующий вопрос или завершаем
    if test_data['current_question'] <= len(questions):
        await show_question(update, context)
    else:
        await finish_test(update, context)

async def finish_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает тест и показывает результаты"""
    test_data = context.user_data.get('current_test')
    if not test_data:
        await update.message.reply_text("❌ Нет активного теста")
        return
    
    user_id = update.message.from_user.id
    arc_id = test_data['arc_id']
    week_num = test_data['week_num']
    answers = test_data['answers']
    
    # Подсчет результатов
    total_questions = len(test_data['questions'])
    correct_answers = sum(1 for answer in answers.values() if answer.get('correct', False))
    score = int((correct_answers / total_questions) * 100) if total_questions > 0 else 0
    
    # Сохраняем результат
    from database import save_test_result
    result_id = save_test_result(
        user_id=user_id,
        arc_id=arc_id,
        week_num=week_num,
        answers=answers,
        score=score
    )
    
    # Очищаем данные теста
    context.user_data.pop('current_test', None)
    
    # Показываем результаты
    await show_test_results(update, context, user_id, arc_id, week_num)

async def show_test_results(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None, arc_id=None, week_num=None):
    """Показывает результаты теста - НОВАЯ ВЕРСИЯ: сначала выбор марафона"""
    if user_id is None:
        user_id = update.message.from_user.id
    
    # ★★ ИСПРАВЛЕНИЕ 1: Если переданы arc_id и week_num, показываем конкретный тест
    if arc_id and week_num:
        from database import get_test_result, get_tests_for_week
        result_data = get_test_result(user_id, arc_id, week_num)
        
        if not result_data:
            await update.message.reply_text("❌ Результаты теста не найдены")
            return
        
        # Получаем название марафона
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
        arc_title_result = cursor.fetchone()
        arc_title = arc_title_result[0] if arc_title_result else f"Марафон {arc_id}"
        conn.close()
        
        score = result_data['score']
        answers = result_data['answers']
        
        # Получаем вопросы теста для деталей
        questions = get_tests_for_week(week_num)
        question_map = {str(q[0]): q for q in questions}  # test_id -> question data
        
        # Формируем сообщение с ошибками
        await show_test_result_details(update, context, arc_id, arc_title, week_num, score, answers, question_map)
        return
    
    # ★★ ИСПРАВЛЕНИЕ 2: Показываем сначала список марафонов с результатами
    from database import get_all_test_results
    results = get_all_test_results(user_id)
    
    if not results:
        await update.message.reply_text(
            "📭 **Результаты тестов отсутствуют**\n\n"
            "Вы еще не проходили тесты.",
            parse_mode='Markdown'
        )
        return
    
    # Группируем результаты по марафонам
    arc_results = {}
    for result_id, res_arc_id, res_week_num, score, completed_at in results:
        if res_arc_id not in arc_results:
            arc_results[res_arc_id] = []
        arc_results[res_arc_id].append({
            'week_num': res_week_num,
            'score': score,
            'completed_at': completed_at,
            'result_id': result_id
        })
    
    # ★★ НОВОЕ: Сначала показываем выбор марафона
    keyboard = []
    arc_mapping = {}
    
    for arc_id, tests in arc_results.items():
        # Получаем название марафона
        conn = sqlite3.connect('mentor_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT title FROM arcs WHERE arc_id = ?', (arc_id,))
        arc_title_result = cursor.fetchone()
        arc_title = arc_title_result[0] if arc_title_result else f"Марафон {arc_id}"
        conn.close()
        
        # Формируем текст кнопки
        tests_count = len(tests)
        btn_text = f"📚 {arc_title} ({tests_count} тест{'ов' if tests_count > 1 else ''})"
        keyboard.append([btn_text])
        
        # Сохраняем маппинг
        arc_mapping[btn_text] = {
            'arc_id': arc_id,
            'arc_title': arc_title,
            'tests': tests
        }
    
    context.user_data['arc_results_mapping'] = arc_mapping
    
    keyboard.append(["🔙 Назад к тестированию"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📊 **ВАШИ РЕЗУЛЬТАТЫ ТЕСТОВ**\n\n"
        "Выберите марафон для просмотра результатов:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_tests_for_arc_results(update: Update, context: ContextTypes.DEFAULT_TYPE, arc_id=None, arc_title=None):
    """Показывает тесты выбранного марафона для просмотра результатов"""
    user_id = update.message.from_user.id
    
    # Если переданы arc_id и arc_title, используем их
    if arc_id and arc_title:
        pass  # Используем переданные значения
    else:
        # Ищем в сохраненных данных
        arc_data = context.user_data.get('current_arc_for_results', {})
        if arc_data:
            arc_id = arc_data.get('arc_id')
            arc_title = arc_data.get('arc_title')
        else:
            # Получаем из mapping
            text = update.message.text
            arc_mapping = context.user_data.get('arc_results_mapping', {})
            arc_info = arc_mapping.get(text)
            
            if not arc_info:
                await update.message.reply_text("❌ Марафон не найден")
                return
            
            arc_id = arc_info['arc_id']
            arc_title = arc_info['arc_title']
    
    # Получаем результаты тестов для этого марафона
    from database import get_all_test_results
    all_results = get_all_test_results(user_id, arc_id)
    
    if not all_results:
        await update.message.reply_text(
            f"📭 **Нет результатов тестов для марафона '{arc_title}'.**",
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    results_mapping = {}
    
    for result_id, res_arc_id, week_num, score, completed_at in all_results:
        date_str = completed_at[:10] if completed_at else "??"
        
        btn_text = f"📊 Неделя {week_num} ({score}%) - {date_str}"
        keyboard.append([btn_text])
        
        results_mapping[btn_text] = {
            'arc_id': arc_id,
            'week_num': week_num
        }
    
    context.user_data['test_results_mapping'] = results_mapping
    
    keyboard.append(["🔙 Выбрать другой марафон"])
    keyboard.append(["🔙 Назад к тестированию"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📊 **РЕЗУЛЬТАТЫ МАРАФОНА: {arc_title}**\n\n"
        f"Выберите тест для просмотра деталей:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_test_result_details(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                   arc_id, arc_title, week_num, score, answers, question_map):
    """Показывает детали результатов теста - ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    total_questions = len(question_map)
    correct_answers = sum(1 for answer in answers.values() if answer.get('correct', False))
    
    # Формируем сообщение с результатами
    message = f"📈 **РЕЗУЛЬТАТ ТЕСТА: {arc_title} - Неделя {week_num}**\n\n"
    message += f"📊 **Результат: {score}% ({correct_answers} из {total_questions})**\n\n"
    
    # Находим ошибочные ответы
    wrong_answers = []
    for test_id_str, answer_data in answers.items():
        if not answer_data.get('correct', False):
            wrong_answers.append({
                'test_id': test_id_str,
                'selected': answer_data.get('selected'),
                'question_text': answer_data.get('question_text', '')
            })
    
    if wrong_answers:
        message += "❌ **Ошибки в вопросах:**\n\n"
        
        for i, wrong in enumerate(wrong_answers, 1):
            test_id = wrong['test_id']
            question_data = question_map.get(test_id)
            
            if question_data:
                test_id_full, question_text, option1, option2, option3, option4, option5, correct_option, explanation = question_data
                
                # Находим текст выбранного варианта
                selected_option = wrong['selected']
                option_texts = {
                    'option1': option1,
                    'option2': option2,
                    'option3': option3,
                    'option4': option4,
                    'option5': option5
                }
                
                selected_text = answer_data.get('selected_text', 'не указан')
                correct_text = option_texts.get(correct_option, 'не указан')
                
                message += f"{i}. **Вопрос:** {test_id}\n"
                message += f"   **Текст:** {question_text[:100]}...\n"
                message += f"   **Ваш ответ:** {selected_text}\n"
                message += f"   **💡 Верный ответ:** {correct_text}\n"
                
                if explanation:
                    message += f"   **Объяснение:** {explanation[:150]}...\n"
                
                message += "\n"
    
    else:
        message += "🎉 **Отличный результат! Все ответы верные!**\n\n"
    
    # Кнопки
    keyboard = [
        ["📋 Показать все ответы"],
        ["🔙 Назад к тестам марафона"],
        ["📈 Пройти другой тест"]
    ]
    
    # Сохраняем данные для показа всех ответов
    context.user_data['current_test_details'] = {
        'arc_id': arc_id,
        'arc_title': arc_title,
        'week_num': week_num,
        'score': score,
        'answers': answers,
        'question_map': question_map
    }
    
    context.user_data['current_arc_for_results'] = {
        'arc_id': arc_id,
        'arc_title': arc_title
    }
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Отправляем сообщение частями если слишком длинное
    if len(message) > 4000:
        parts = split_message(message)
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                await update.message.reply_text(part, reply_markup=reply_markup, parse_mode='Markdown')
            else:
                await update.message.reply_text(part, parse_mode='Markdown')
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_all_test_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все ответы теста (15 вопросов) - ПОЛНАЯ ВЕРСИЯ"""
    test_details = context.user_data.get('current_test_details')
    if not test_details:
        await update.message.reply_text("❌ Нет данных для отображения")
        return
    
    arc_title = test_details['arc_title']
    week_num = test_details['week_num']
    score = test_details['score']
    answers = test_details['answers']
    question_map = test_details['question_map']
    
    total_questions = len(question_map)
    correct_answers = sum(1 for answer in answers.values() if answer.get('correct', False))
    
    # Заголовок
    message = f"📋 **ВСЕ ОТВЕТЫ ТЕСТА: {arc_title} - Неделя {week_num}**\n\n"
    message += f"📊 Результат: {score}% ({correct_answers} из {total_questions})\n\n"
    
    # Сортируем вопросы по test_id
    sorted_test_ids = sorted(question_map.keys(), key=lambda x: int(x))
    
    question_count = 0
    
    for test_id_str in sorted_test_ids:
        question_count += 1
        question_data = question_map.get(test_id_str)
        if not question_data:
            continue
        
        test_id_full, question_text, option1, option2, option3, option4, option5, correct_option, explanation = question_data
        
        # Находим ответ пользователя
        user_answer = answers.get(test_id_str, {})
        selected_option = user_answer.get('selected')
        selected_text = user_answer.get('selected_text', 'нет ответа')
        is_correct = user_answer.get('correct', False)
        
        # Тексты всех вариантов
        option_texts = {
            'option1': option1,
            'option2': option2,
            'option3': option3,
            'option4': option4,
            'option5': option5
        }
        
        correct_text = option_texts.get(correct_option, 'не указан')
        
        message += f"**{question_count}. {question_text}**\n\n"
        message += f"**Ваш ответ:** {selected_text} "
        
        if is_correct:
            message += "✅\n"
        else:
            message += f"❌\n"
            message += f"**💡 Верный ответ:** {correct_text}\n"
            
            # ★★ ПОЯСНЕНИЕ если есть и ответ неверный
            if explanation and str(explanation).strip():
                message += f"   **📝 Пояснение:** {explanation[:150]}...\n"
        
        message += "\n" + "─" * 30 + "\n\n"
    
    keyboard = [
        ["🔙 Назад к результату"],
        ["📈 Пройти другой тест"]
    ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Отправляем частями (сообщение будет очень длинное)
    if len(message) > 4000:
        parts = split_message(message)
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                await update.message.reply_text(part, reply_markup=reply_markup, parse_mode='Markdown')
            else:
                await update.message.reply_text(part, parse_mode='Markdown')
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def back_to_test_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возвращает к результату теста из просмотра всех ответов"""
    if 'current_test_details' not in context.user_data:
        await update.message.reply_text("❌ Нет данных о тесте")
        return
    
    test_details = context.user_data['current_test_details']
    user_id = update.message.from_user.id
    
    # Получаем результат теста заново
    from database import get_test_result, get_tests_for_week
    result_data = get_test_result(user_id, test_details['arc_id'], test_details['week_num'])
    
    if not result_data:
        await update.message.reply_text("❌ Результаты теста не найдены")
        return
    
    score = result_data['score']
    answers = result_data['answers']
    
    # Получаем вопросы теста
    questions = get_tests_for_week(test_details['week_num'])
    question_map = {str(q[0]): q for q in questions}
    
    # Показываем результат
    await show_test_result_details(
        update, context,
        test_details['arc_id'],
        test_details['arc_title'],
        test_details['week_num'],
        score,
        answers,
        question_map
    )

async def back_to_arc_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возвращает к тестам марафона из просмотра результата"""
    if 'current_arc_for_results' in context.user_data:
        arc_data = context.user_data['current_arc_for_results']
        await show_tests_for_arc_results(update, context, arc_data['arc_id'], arc_data['arc_title'])
    elif 'current_test_details' in context.user_data:
        # Используем данные из текущего теста
        test_details = context.user_data['current_test_details']
        await show_tests_for_arc_results(update, context, test_details['arc_id'], test_details['arc_title'])
    else:
        await show_test_results(update, context)


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

    application.add_handler(MessageHandler(
        filters.PHOTO | filters.AUDIO | filters.VIDEO | filters.Document.ALL,
        handle_admin_files
    ))
    
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
    application.add_handler(CommandHandler("loadmedia", load_media_from_excel))
    application.add_handler(CommandHandler("debugarc", debug_current_arc))
    application.add_handler(CommandHandler("getfileid", get_file_id))
    application.add_handler(CommandHandler("cancelfileid", cancel_file_id_mode))
    application.add_handler(CommandHandler("checkmedia", check_media))
    application.add_handler(CommandHandler("addphoto", add_photo_to_assignment))
    application.add_handler(CommandHandler("loadmediasimple", load_media_simple))
    application.add_handler(CommandHandler("loadallmedia", load_all_media))
    application.add_handler(CommandHandler("loadtests", load_tests_command))
    
    
    print("Бот запущен...")
    
    
    #webhook_mode = any(arg in sys.argv for arg in ['--webhook', 'webhook', '--wh'])
    
    #if webhook_mode:
        #print("🚀 Запуск в режиме WEBHOOK")
        #WEBHOOK_HOST = "svs365bot.ru"
        #TOKEN_PATH = f"bot/{TOKEN}"
        #WEBHOOK_URL = f"https://{WEBHOOK_HOST}/{TOKEN_PATH}"
        #LISTEN_IP = "127.0.0.1"
        #PORT = 8083
    
        #try:
            # Просто запускаем webhook
            #application.run_webhook(
                #listen=LISTEN_IP,
                #port=PORT,
                #webhook_url=WEBHOOK_URL,
                #drop_pending_updates=True,
            #)
        #except Exception as e:
            #print(f"❌ Ошибка webhook: {e}")
            #print("🔄 Переключаюсь на polling как fallback...")
            ## Нужно создать новый event loop для polling
            #import asyncio
            #asyncio.set_event_loop(asyncio.new_event_loop())
            #application.run_polling(allowed_updates=Update.ALL_TYPES)

    print("🚀 Запуск в режиме POLLING (локальный)")
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
